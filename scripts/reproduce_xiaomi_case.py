"""Reproduce the bounded, AI-reviewed Xiaomi PDF case using the actual local CLI.

No downloads or product registry changes are performed. Input PDFs must already
exist, match the manifest, and remain outside the public results directory.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
from importlib import metadata
import io
import json
import logging
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import subprocess
import sys
import time
import warnings
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CASE_DIRECTORY = PROJECT_ROOT / "docs" / "cases" / "xiaomi-2023-2024"
BASELINE_REF = "85e8429967d402b1572ca4ac5ed0cc9e34fa622a"
CANONICAL_ITEMS = frozenset({
    "share_based_payments", "fair_value_changes", "impairment_losses",
    "listing_expenses", "acquisition_related_expenses",
    "amortisation_of_intangible_assets",
})
LABEL_COLUMNS = {
    "report_id", "pdf_page", "canonical_item", "support_status", "target_level",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_labels(path: Path) -> list[dict]:
    """Read frozen review rows; reject contradictory page/concept judgments."""
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")))
    if not LABEL_COLUMNS.issubset(reader.fieldnames or ()):
        raise ValueError("Review CSV is missing required columns.")
    rows = []
    judgments = {}
    for line, raw in enumerate(reader, 2):
        row = {name: (value or "").strip() for name, value in raw.items()}
        try:
            row["pdf_page"] = int(row["pdf_page"])
        except ValueError as error:
            raise ValueError(f"Invalid PDF page on label line {line}.") from error
        status, level = row["support_status"], row["target_level"]
        if not row["report_id"] or row["pdf_page"] < 1:
            raise ValueError(f"Invalid label identity on line {line}.")
        if status not in {"supported", "unsupported", "negative"}:
            raise ValueError(f"Invalid support_status on label line {line}.")
        if level not in {"direct", "nested", "negative"}:
            raise ValueError(f"Invalid target_level on label line {line}.")
        if status == "supported" and (
            level == "negative" or row["canonical_item"] not in CANONICAL_ITEMS
        ):
            raise ValueError(f"Supported row needs an in-scope concept and level: line {line}.")
        if status == "negative" and level != "negative":
            raise ValueError(f"Negative row needs target_level=negative: line {line}.")
        if row["canonical_item"]:
            key = (row["report_id"], row["pdf_page"], row["canonical_item"])
            judgment = (status, level)
            if key in judgments and judgments[key] != judgment:
                raise ValueError(f"Conflicting review labels for {key}.")
            judgments[key] = judgment
        rows.append(row)
    return rows


def validate_sources(manifest: dict, source_directory: Path) -> dict[str, Path]:
    """Validate all files before creating the output directory or running a CLI."""
    reports = manifest.get("reports")
    if manifest.get("schema_version") != 1 or not isinstance(reports, list) or len(reports) != 2:
        raise ValueError("Expected schema_version=1 and exactly two manifest reports.")
    paths = {}
    for report in reports:
        for field in ("report_id", "ticker", "company", "fiscal_period", "report_type", "url", "sha256", "pdf_pages"):
            if not report.get(field):
                raise ValueError(f"Manifest report is missing {field}.")
        report_id = report["report_id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", report_id) or report_id in paths:
            raise ValueError("Manifest report_id must be unique and safe for filenames.")
        filename = report.get("local_filename", report["fiscal_period"] + ".pdf")
        if (
            not isinstance(filename, str) or Path(filename).name != filename
            or PureWindowsPath(filename).name != filename or filename in {".", ".."}
        ):
            raise ValueError("Manifest local_filename must be a plain filename.")
        path = (source_directory / filename).resolve(strict=True)
        if path.parent != source_directory.resolve() or not path.is_file():
            raise ValueError(f"Source file must be within --source-dir: {filename}")
        expected = report["sha256"]
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(f"Invalid manifest SHA256 for {report_id}.")
        if sha256_file(path) != expected.lower():
            raise ValueError(f"PDF SHA256 mismatch for {report_id}; no run was started.")
        paths[report_id] = path
    return paths


def _key(row: dict) -> tuple[str, int, str]:
    return row["report_id"], row["pdf_page"], row["canonical_item"]


def _counts(matched: list[dict], missing: list[dict], false_positives: list[dict]) -> dict:
    tp, fn, fp = len(matched), len(missing), len(false_positives)
    return {
        "counts": {"tp": tp, "fp": fp, "fn": fn},
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "matched": matched, "missing": missing, "false_positives": false_positives,
    }


def _recall_summary(matched: list[dict], missing: list[dict]) -> dict:
    total = len(matched) + len(missing)
    return {"counts": {"matched": len(matched), "expected": total, "missing": len(missing)},
            "recall": len(matched) / total if total else None,
            "matched": matched, "missing": missing}


def score_candidates(labels: list[dict], candidates_by_report: dict[str, dict]) -> dict:
    """Score raw candidates on labeled pages, deduplicating the prescribed key.

    An unlabelled concept on a reviewed page is a false positive. An output on an
    unreviewed page remains unreviewed. No confirmed selection enters scoring.
    """
    expected = {_key(row): row for row in labels if row["support_status"] == "supported"}
    reviews = {_key(row): row for row in labels if row["canonical_item"]}
    pages = {(row["report_id"], row["pdf_page"]) for row in labels}
    predicted = {}
    raw_counts = Counter()
    for report_id, payload in candidates_by_report.items():
        candidates = payload.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError(f"Missing candidates list for {report_id}.")
        for candidate in candidates:
            page = candidate.get("page_number")
            name = candidate.get("canonical_name")
            if not isinstance(page, int) or isinstance(page, bool) or page < 1 or not isinstance(name, str) or not name:
                raise ValueError(f"Invalid candidate identity for {report_id}.")
            raw_counts[report_id] += 1
            row = {"report_id": report_id, "pdf_page": page, "canonical_item": name,
                   "matched_text": candidate.get("matched_text", ""),
                   "evidence_excerpt": candidate.get("evidence_excerpt", "")}
            predicted.setdefault(_key(row), row)

    def summary(report_ids: set[str]) -> dict:
        relevant_labels = [row for row in labels if row["report_id"] in report_ids]
        relevant_predictions = {key: row for key, row in predicted.items() if key[0] in report_ids}
        supported = {key: row for key, row in expected.items() if key[0] in report_ids}
        bounded, unreviewed, outside_predictions = {}, [], []
        for key, row in sorted(relevant_predictions.items()):
            if (key[0], key[1]) not in pages:
                unreviewed.append(row)
            elif key[2] not in CANONICAL_ITEMS:
                outside_predictions.append(row)
            else:
                bounded[key] = row
        matched = [supported[key] for key in sorted(supported.keys() & bounded.keys())]
        missing = [supported[key] for key in sorted(supported.keys() - bounded.keys())]
        false_positives = [
            {**bounded[key], "review_label": reviews.get(key)}
            for key in sorted(bounded.keys() - supported.keys())
        ]
        result = _counts(matched, missing, false_positives)
        result["by_target_level"] = {
            level: _recall_summary(
                [row for row in matched if row["target_level"] == level],
                [row for row in missing if row["target_level"] == level],
            ) for level in ("direct", "nested")
        }
        # A definition concept is recalled only after a correctly supported page
        # occurrence is matched, never merely because it appears on a negative page.
        def item_key(row: dict) -> tuple[str, str, str]:
            return row["report_id"], row["canonical_item"], row["target_level"]

        expected_items = {item_key(row) for row in supported.values()}
        matched_items = {item_key(row) for row in matched}
        def item_rows(keys: set[tuple[str, str, str]]) -> list[dict]:
            return [{"report_id": report_id, "canonical_item": item, "target_level": level}
                    for report_id, item, level in sorted(keys)]

        matched_item_rows = item_rows(matched_items)
        missing_item_rows = item_rows(expected_items - matched_items)
        result["report_item_set"] = _recall_summary(matched_item_rows, missing_item_rows)
        result["report_item_set"]["primary_key"] = ["report_id", "canonical_item", "target_level"]
        result["report_item_set"]["by_target_level"] = {
            level: _recall_summary(
                [row for row in matched_item_rows if row["target_level"] == level],
                [row for row in missing_item_rows if row["target_level"] == level],
            ) for level in ("direct", "nested")
        }
        result["review_counts"] = {
            "label_rows": len(relevant_labels),
            "unique_supported_keys": len(supported),
            "support_status": dict(Counter(row["support_status"] for row in relevant_labels)),
            "target_level": dict(Counter(row["target_level"] for row in relevant_labels)),
            "reviewed_pages": len({(row["report_id"], row["pdf_page"]) for row in relevant_labels}),
        }
        result["candidate_counts"] = {
            "raw_rows": sum(raw_counts[report_id] for report_id in report_ids),
            "unique_keys": len(relevant_predictions), "scored_keys": len(bounded),
            "unreviewed_keys": len(unreviewed), "outside_dictionary_keys": len(outside_predictions),
        }
        result["scoring_pages"] = {
            report_id: sorted(page for report, page in pages if report == report_id)
            for report_id in sorted(report_ids)
        }
        result["outside_reviewed_pages"] = {
            report_id: sorted({row["pdf_page"] for row in unreviewed if row["report_id"] == report_id})
            for report_id in sorted(report_ids)
        }
        result["unreviewed"] = unreviewed
        result["outside_dictionary_labels"] = [
            row for row in relevant_labels if row["support_status"] == "unsupported"
            or (row["canonical_item"] and row["canonical_item"] not in CANONICAL_ITEMS)
        ]
        result["outside_dictionary_predictions"] = outside_predictions
        return result

    report_ids = sorted({row["report_id"] for row in labels} | candidates_by_report.keys())
    return {
        "primary_key": ["report_id", "pdf_page", "canonical_item"],
        "scope": "Only pages represented in review_labels.csv are scored; other output pages are unreviewed.",
        "supported_scope": "All supported labels, including nested components, enter total TP/FP/FN.",
        "stratum_policy": "Direct and nested strata report recall only; all false positives remain in the primary page-occurrence score because candidates do not predict a target level.",
        "report_item_set_policy": "Deduplicate supported (report_id, canonical_item, target_level) keys; a set item is recalled only if at least one supported page occurrence is matched. Page-occurrence errors remain in the primary score.",
        "dictionary_scope": sorted(CANONICAL_ITEMS),
        "by_report": {report_id: summary({report_id}) for report_id in report_ids},
        "total": summary(set(report_ids)),
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _runtime_versions() -> dict:
    packages = {}
    for package in ("pypdf", "fonttools"):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "operating_system": platform.system(), "packages": packages}


def verify_evidence(pdf_path: Path, definition_path: Path, snapshot_path: Path,
                    *, expected_pages: int) -> dict:
    """Independently compare every snapshot excerpt to strict pypdf page text.

    Use the project's installed pypdf dependency without importing or changing
    the extractor. Parser diagnostics remain fatal during this separate check.
    """
    from pypdf import PdfReader

    definition = json.loads(definition_path.read_text(encoding="utf-8"))
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if snapshot["report_sha256"] != sha256_file(pdf_path):
        raise ValueError("Traceability PDF hash does not match its snapshot.")
    if snapshot["definition_sha256"] != sha256_file(definition_path):
        raise ValueError("Traceability definition hash does not match its snapshot.")
    names = set(definition["adjustments"])
    items = snapshot["items"]
    if not names or {item["canonical_name"] for item in items} != names or len(items) != len(names):
        raise ValueError("Every confirmed adjustment must have exactly one evidence item.")

    class FatalPdfWarning(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise ValueError("Traceability PDF parser warning: " + record.getMessage())

    logger = logging.getLogger("pypdf")
    previous = (logger.handlers[:], logger.level, logger.propagate, logger.disabled)
    handler = FatalPdfWarning(logging.WARNING)
    logger.handlers = [handler]
    logger.setLevel(logging.WARNING)
    logger.propagate = False
    logger.disabled = False
    verified = 0
    page_text = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with pdf_path.open("rb") as stream:
                reader = PdfReader(stream, strict=True)
                if reader.is_encrypted and not reader.decrypt(""):
                    raise ValueError("Traceability PDF cannot be opened with an empty password.")
                if len(reader.pages) != expected_pages:
                    raise ValueError("Traceability PDF page count differs from the manifest.")
                for item in items:
                    if not item["evidence"]:
                        raise ValueError("Confirmed adjustment has no source evidence.")
                    for entry in item["evidence"]:
                        page = entry["page_number"]
                        if type(page) is not int or not 1 <= page <= expected_pages:
                            raise ValueError("Evidence page_number is outside the PDF.")
                        if page not in page_text:
                            raw = reader.pages[page - 1].extract_text()
                            if not isinstance(raw, str) or "\ufffd" in raw:
                                raise ValueError("Traceability PDF page has unreliable text.")
                            page_text[page] = raw
                        for field in ("matched_text", "evidence_excerpt"):
                            value = entry[field]
                            if not isinstance(value, str) or not value or value not in page_text[page]:
                                raise ValueError(f"Evidence {field} is not an exact PDF page substring.")
                        if entry["matched_text"] not in entry["evidence_excerpt"]:
                            raise ValueError("Evidence matched_text is absent from its excerpt.")
                        verified += 1
    finally:
        logger.handlers, logger.level, logger.propagate, logger.disabled = previous
        handler.close()
    return {
        "pdf_pages": expected_pages, "confirmed_adjustments": sorted(names),
        "verified_pages": sorted(page_text), "verified_evidence_entries": verified,
        "report_sha256": snapshot["report_sha256"],
        "definition_sha256": snapshot["definition_sha256"],
        "snapshot_sha256": sha256_file(snapshot_path),
    }


def reproduce(source_directory: Path, output_directory: Path, baseline_ref: str = BASELINE_REF) -> dict:
    """Validate inputs, freeze labels, run both source versions, and publish scores."""
    manifest_path = CASE_DIRECTORY / "source_manifest.json"
    labels_path = CASE_DIRECTORY / "review_labels.csv"
    manifest_bytes, labels_bytes = manifest_path.read_bytes(), labels_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    labels = load_labels(labels_path)
    sources = validate_sources(manifest, source_directory)
    reports = sorted(manifest["reports"], key=lambda row: row["fiscal_period"])
    report_ids = {report["report_id"] for report in reports}
    if any(row["report_id"] not in report_ids for row in labels):
        raise ValueError("Review CSV contains an unknown report_id.")
    for row in labels:
        report = next(report for report in reports if report["report_id"] == row["report_id"])
        if row["pdf_page"] > report["pdf_pages"]:
            raise ValueError("Review page exceeds manifest PDF page count.")
    if not labels:
        raise ValueError("Review CSV must not be empty.")
    # Restrict refs to revision names; a leading option must never reach git.
    if not baseline_ref or baseline_ref.startswith("-"):
        raise ValueError("Invalid baseline ref.")
    checkout_head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True, encoding="utf-8",
    ).strip()
    baseline_commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", baseline_ref + "^{commit}"],
        cwd=PROJECT_ROOT, text=True, encoding="utf-8",
    ).strip()
    output_directory = output_directory.absolute()
    if output_directory.exists() or output_directory.is_symlink():
        raise ValueError("--output-dir already exists; choose a new run directory.")
    output_directory.mkdir(parents=True, exist_ok=False)
    results = output_directory / "results"
    private = output_directory / "private_logs"
    results.mkdir()
    private.mkdir()
    (results / "review_labels.frozen.csv").write_bytes(labels_bytes)
    (results / "source_manifest.frozen.json").write_bytes(manifest_bytes)
    registry = output_directory / "registry.json"
    candidate_module = PROJECT_ROOT / "metric_mirage" / "candidates.py"
    module_digest = sha256_file(candidate_module)
    execution = {
        "schema_version": 1, "case_id": manifest.get("case_id", "xiaomi-2023-2024"),
        "source_version": {"checkout_head_at_run": checkout_head,
                           "candidate_module_sha256": module_digest,
                           "note": "HEAD identifies the checkout base; candidate module hash identifies the executed working-tree file."},
        "baseline_version": {"requested_ref": baseline_ref, "resolved_commit": baseline_commit},
        "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "runtime_versions": _runtime_versions(),
        "human_review_time_seconds": None,
        "review_method": manifest.get("review_method"),
        "steps": [], "status": "running",
    }

    def run(step: str, arguments: list[str], public_arguments: list[str], *,
            cwd: Path = PROJECT_ROOT, output: Path | None = None,
            written_output: Path | None = None) -> bytes:
        start = time.perf_counter()
        completed = subprocess.run(
            arguments, cwd=cwd, capture_output=True, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            timeout=1800,
        )
        elapsed = time.perf_counter() - start
        (private / (step + ".stdout.log")).write_bytes(completed.stdout)
        (private / (step + ".stderr.log")).write_bytes(completed.stderr)
        produced = output or written_output
        entry = {"step": step, "args": public_arguments,
                 "exit_code": completed.returncode, "elapsed_seconds": round(elapsed, 6),
                 "output": produced.relative_to(output_directory).as_posix() if produced else None}
        execution["steps"].append(entry)
        if completed.returncode:
            raise RuntimeError(f"CLI step {step} failed with exit code {completed.returncode}; see private_logs.")
        if output is not None:
            # Save original stdout bytes; no excerpt or source normalization.
            completed.stdout.decode("utf-8")
            output.write_bytes(completed.stdout)
        if produced is not None:
            entry["output_sha256"] = sha256_file(produced)
        return completed.stdout

    def cli(step: str, arguments: list[str], *, cwd: Path = PROJECT_ROOT,
            output: Path | None = None) -> bytes:
        replacements = {str(registry): "registry.json", str(sys.executable): "python"}
        replacements.update({str(path): "<source-dir>/" + path.name for path in sources.values()})
        replacements.update({str(path): path.relative_to(output_directory).as_posix()
                             for path in results.iterdir() if path.is_file()})
        for argument in arguments:
            path = Path(argument)
            if path.is_absolute() and str(path).startswith(str(output_directory) + os.sep):
                replacements[str(path)] = path.relative_to(output_directory).as_posix()
        command = [sys.executable, "-m", "metric_mirage", *arguments]
        public = [replacements.get(argument, argument) for argument in command]
        written_output = Path(arguments[arguments.index("--output") + 1]) if "--output" in arguments else None
        return run(step, command, public, cwd=cwd, output=output, written_output=written_output)

    try:
        # Freeze the baseline source from Git, then use the exact same interpreter.
        archive_path = output_directory / "baseline_source.zip"
        run("baseline_archive", ["git", "archive", "--format=zip", baseline_commit,
                                "--output", str(archive_path)],
            ["git", "archive", "--format=zip", baseline_commit, "--output", "baseline_source.zip"],
            written_output=archive_path)
        baseline_source = output_directory / "baseline_source"
        baseline_source.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            for member in archive.infolist():
                target = (baseline_source / member.filename).resolve()
                if not target.is_relative_to(baseline_source.resolve()):
                    raise ValueError("Unsafe path in baseline archive.")
            archive.extractall(baseline_source)
        execution["baseline_version"]["candidate_module_sha256"] = sha256_file(
            baseline_source / "metric_mirage" / "candidates.py"
        )
        for report in reports:
            report_id = report["report_id"]
            cli(report_id + "_add", [
                "reports", "add", str(sources[report_id]), "--ticker", report["ticker"],
                "--company", report["company"], "--type", report["report_type"],
                "--period", report["fiscal_period"], "--source", report.get("source_name", report["url"]),
                "--registry", str(registry),
            ])
        all_candidates = {"current": {}, "baseline": {}}
        for version, cwd in (("baseline", baseline_source), ("current", PROJECT_ROOT)):
            for report in reports:
                report_id = report["report_id"]
                payload = cli(f"{version}_{report_id}_candidates", [
                    "reports", "candidates", report["ticker"], "--period", report["fiscal_period"],
                    "--type", report["report_type"], "--format", "json", "--registry", str(registry),
                ], cwd=cwd, output=results / f"{version}.{report_id}.candidates.json")
                all_candidates[version][report_id] = json.loads(payload)
        for report in reports:
            report_id = report["report_id"]
            selected = sorted({row["canonical_item"] for row in labels
                               if row["report_id"] == report_id and row["support_status"] == "supported"
                               and row["target_level"] == "direct"})
            if not selected:
                raise ValueError(f"No supported direct labels to confirm for {report_id}.")
            definition = results / f"{report_id}.definition.json"
            selections = [part for item in selected for part in ("--select", item)]
            cli(report_id + "_confirm", [
                "reports", "confirm", report["ticker"], "--period", report["fiscal_period"],
                "--type", report["report_type"], *selections, "--registry", str(registry),
                "--output", str(definition),
            ])
            cli(report_id + "_evidence", [
                "reports", "evidence", report["ticker"], "--period", report["fiscal_period"],
                "--type", report["report_type"], "--definition", str(definition),
                "--output", str(results / f"{report_id}.evidence.json"), "--registry", str(registry),
            ])
        traceability_start = time.perf_counter()
        traceability = {"method": "Independent strict pypdf extraction; exact matched_text and evidence_excerpt page substrings; all parser warnings are fatal.",
                        "reports": {}}
        for report in reports:
            report_id = report["report_id"]
            traceability["reports"][report_id] = verify_evidence(
                sources[report_id], results / f"{report_id}.definition.json",
                results / f"{report_id}.evidence.json", expected_pages=report["pdf_pages"],
            )
        traceability["verified_evidence_entries"] = sum(
            report["verified_evidence_entries"] for report in traceability["reports"].values()
        )
        _write_json(results / "traceability.json", traceability)
        execution["traceability_elapsed_seconds"] = round(time.perf_counter() - traceability_start, 6)
        execution["verified_evidence_entries"] = traceability["verified_evidence_entries"]
        old, new = (report["report_id"] for report in reports)
        for format_name, suffix in (("markdown", "md"), ("json", "json")):
            cli("drift_" + format_name, [
                "drift", str(results / f"{old}.definition.json"), str(results / f"{new}.definition.json"),
                "--old-evidence", str(results / f"{old}.evidence.json"),
                "--new-evidence", str(results / f"{new}.evidence.json"), "--format", format_name,
            ], output=results / ("drift." + suffix))
        evaluation = {version: score_candidates(labels, payload)
                      for version, payload in all_candidates.items()}
        evaluation["labels_sha256"] = execution["labels_sha256"]
        evaluation["human_review_time_seconds"] = None
        _write_json(results / "evaluation.json", evaluation)
        if sha256_file(candidate_module) != module_digest:
            raise RuntimeError("Candidate source changed during the run; discard this run and repeat.")
        if labels_path.read_bytes() != labels_bytes:
            raise RuntimeError("Review labels changed during the run; discard this run and repeat.")
        execution["result_sha256"] = {
            path.name: sha256_file(path) for path in sorted(results.iterdir()) if path.is_file()
        }
        execution["status"] = "complete"
        return execution
    except Exception as error:
        execution["status"] = "failed"
        # Exceptions may contain private absolute paths; publish only the class.
        execution["error_type"] = type(error).__name__
        raise
    finally:
        _write_json(results / "execution.json", execution)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--baseline-ref", default=BASELINE_REF)
    arguments = parser.parse_args(argv)
    try:
        execution = reproduce(arguments.source_dir, arguments.output_dir, arguments.baseline_ref)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"Case reproduction failed: {error}", file=sys.stderr)
        return 1
    print(f"Case reproduction {execution['status']}; results: {arguments.output_dir / 'results'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
