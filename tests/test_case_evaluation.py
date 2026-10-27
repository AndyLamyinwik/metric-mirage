"""Synthetic, offline checks for the research case's bounded evaluation."""

import csv
import hashlib
import json
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.reproduce_xiaomi_case import load_labels, reproduce, score_candidates, validate_sources, verify_evidence


class CaseEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="metric-mirage-case-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def labels(self, rows: list[dict]) -> list[dict]:
        path = self.directory / "labels.csv"
        fields = ["report_id", "pdf_page", "canonical_item", "support_status", "target_level"]
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fields)
            writer.writeheader()
            writer.writerows(rows)
        return load_labels(path)

    @staticmethod
    def label(page: int, item: str, status: str = "supported", level: str = "direct", report: str = "a") -> dict:
        return {"report_id": report, "pdf_page": page, "canonical_item": item,
                "support_status": status, "target_level": level}

    @staticmethod
    def candidate(page: int, item: str) -> dict:
        return {"page_number": page, "canonical_name": item,
                "matched_text": "Original source — 股份支付", "evidence_excerpt": "Evidence"}

    def test_precision_recall_and_negative_control_with_per_report_totals(self) -> None:
        labels = self.labels([
            self.label(3, "share_based_payments"),
            self.label(4, "impairment_losses", level="nested"),
            self.label(8, "fair_value_changes", "negative", "negative"),
            self.label(2, "listing_expenses", report="b"),
        ])
        result = score_candidates(labels, {
            "a": {"candidates": [self.candidate(3, "share_based_payments"),
                                 self.candidate(8, "fair_value_changes")]},
            "b": {"candidates": [self.candidate(2, "listing_expenses")]},
        })
        self.assertEqual(result["total"]["counts"], {"tp": 2, "fp": 1, "fn": 1})
        self.assertAlmostEqual(result["total"]["precision"], 2 / 3)
        self.assertAlmostEqual(result["total"]["recall"], 2 / 3)
        self.assertEqual(result["by_report"]["a"]["counts"], {"tp": 1, "fp": 1, "fn": 1})
        self.assertEqual(result["total"]["false_positives"][0]["review_label"]["support_status"], "negative")

    def test_empty_denominators_are_null(self) -> None:
        result = score_candidates([], {"a": {"candidates": []}})
        for summary in (result["total"], result["by_report"]["a"]):
            self.assertEqual(summary["counts"], {"tp": 0, "fp": 0, "fn": 0})
            self.assertIsNone(summary["precision"])
            self.assertIsNone(summary["recall"])
            self.assertIsNone(summary["by_target_level"]["nested"]["recall"])

    def test_nested_miss_counts_against_total_and_nested_recall(self) -> None:
        labels = self.labels([
            self.label(3, "share_based_payments"),
            self.label(3, "impairment_losses", level="nested"),
        ])
        summary = score_candidates(labels, {"a": {"candidates": [
            self.candidate(3, "share_based_payments"),
        ]}})["total"]
        self.assertEqual(summary["counts"], {"tp": 1, "fp": 0, "fn": 1})
        self.assertEqual(summary["recall"], .5)
        self.assertEqual(summary["by_target_level"]["direct"]["recall"], 1)
        self.assertEqual(summary["by_target_level"]["nested"]["recall"], 0)
        self.assertEqual(summary["missing"][0]["target_level"], "nested")

    def test_primary_key_dedupes_labels_and_candidate_occurrences(self) -> None:
        label = self.label(3, "share_based_payments")
        labels = self.labels([label, label])
        candidate = self.candidate(3, "share_based_payments")
        summary = score_candidates(labels, {"a": {"candidates": [candidate, candidate]}})["total"]
        self.assertEqual(summary["counts"], {"tp": 1, "fp": 0, "fn": 0})
        self.assertEqual(summary["candidate_counts"]["raw_rows"], 2)
        self.assertEqual(summary["candidate_counts"]["unique_keys"], 1)
        self.assertEqual(summary["review_counts"]["label_rows"], 2)
        self.assertEqual(summary["review_counts"]["unique_supported_keys"], 1)

    def test_definition_item_set_keeps_page_occurrence_misses_visible(self) -> None:
        labels = self.labels([
            self.label(3, "share_based_payments"), self.label(4, "share_based_payments"),
            self.label(4, "impairment_losses", level="nested"),
        ])
        summary = score_candidates(labels, {"a": {"candidates": [
            self.candidate(3, "share_based_payments"),
        ]}})["total"]
        self.assertEqual(summary["counts"], {"tp": 1, "fp": 0, "fn": 2})
        self.assertEqual(summary["report_item_set"]["counts"], {"matched": 1, "expected": 2, "missing": 1})
        self.assertEqual(summary["report_item_set"]["by_target_level"]["direct"]["recall"], 1)
        self.assertEqual(summary["report_item_set"]["by_target_level"]["nested"]["recall"], 0)

    def test_unreviewed_pages_do_not_become_automatic_false_positives(self) -> None:
        labels = self.labels([self.label(3, "share_based_payments")])
        summary = score_candidates(labels, {"a": {"candidates": [
            self.candidate(3, "share_based_payments"),
            self.candidate(3, "listing_expenses"),
            self.candidate(99, "fair_value_changes"),
        ]}})["total"]
        self.assertEqual(summary["counts"], {"tp": 1, "fp": 1, "fn": 0})
        self.assertEqual(summary["scoring_pages"], {"a": [3]})
        self.assertEqual(summary["outside_reviewed_pages"], {"a": [99]})
        self.assertEqual(summary["unreviewed"][0]["pdf_page"], 99)
        self.assertEqual(summary["candidate_counts"]["unreviewed_keys"], 1)

    def test_outside_dictionary_labels_and_predictions_are_disclosed_separately(self) -> None:
        labels = self.labels([
            self.label(3, "share_based_payments"), self.label(3, "", "unsupported"),
        ])
        summary = score_candidates(labels, {"a": {"candidates": [
            self.candidate(3, "share_based_payments"), self.candidate(3, "other_tax_effect"),
        ]}})["total"]
        self.assertEqual(summary["counts"], {"tp": 1, "fp": 0, "fn": 0})
        self.assertEqual(len(summary["outside_dictionary_labels"]), 1)
        self.assertEqual(summary["outside_dictionary_predictions"][0]["canonical_item"], "other_tax_effect")

    def test_conflicting_label_judgments_fail(self) -> None:
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            self.labels([self.label(3, "share_based_payments"),
                         self.label(3, "share_based_payments", "negative", "negative")])

    def manifest(self, wrong_hash: bool = False) -> dict:
        reports = []
        for year in (2023, 2024):
            content = f"synthetic PDF bytes {year}".encode()
            (self.directory / f"FY{year}.pdf").write_bytes(content)
            reports.append({"report_id": f"a-{year}", "ticker": "01810", "company": "Synthetic",
                            "fiscal_period": f"FY{year}", "report_type": "annual", "url": "https://example.test/report",
                            "sha256": "0" * 64 if wrong_hash else hashlib.sha256(content).hexdigest(),
                            "pdf_pages": 5, "local_filename": f"FY{year}.pdf"})
        return {"schema_version": 1, "reports": reports}

    def test_hash_mismatch_fails_before_creating_run_or_invoking_cli(self) -> None:
        manifest = self.manifest(wrong_hash=True)
        case_directory = self.directory / "case"
        case_directory.mkdir()
        (case_directory / "source_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (case_directory / "review_labels.csv").write_text(
            "report_id,pdf_page,canonical_item,support_status,target_level\n"
            "a-2023,3,share_based_payments,supported,direct\n", encoding="utf-8",
        )
        output = self.directory / "new-run"
        with patch("scripts.reproduce_xiaomi_case.CASE_DIRECTORY", case_directory), \
                patch("scripts.reproduce_xiaomi_case.subprocess.run") as run:
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                reproduce(self.directory, output)
        self.assertFalse(output.exists())
        run.assert_not_called()

    def test_manifest_validation_hashes_sources_without_modifying_them(self) -> None:
        manifest = self.manifest()
        before = {path.name: path.read_bytes() for path in self.directory.glob("*.pdf")}
        paths = validate_sources(manifest, self.directory)
        self.assertEqual(set(paths), {"a-2023", "a-2024"})
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.directory.glob("*.pdf")})
        manifest["reports"][0]["local_filename"] = "../FY2023.pdf"
        with self.assertRaisesRegex(ValueError, "plain filename"):
            validate_sources(manifest, self.directory)

    def traceability_inputs(self) -> tuple[Path, Path, Path]:
        pdf_path = self.directory / "synthetic.pdf"
        definition_path = self.directory / "definition.json"
        snapshot_path = self.directory / "evidence.json"
        pdf_path.write_bytes(b"Synthetic PDF placeholder; PdfReader is mocked.")
        definition_path.write_text(json.dumps({"adjustments": ["share_based_payments"]}), encoding="utf-8")
        snapshot_path.write_text(json.dumps({
            "report_sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "definition_sha256": hashlib.sha256(definition_path.read_bytes()).hexdigest(),
            "items": [{"canonical_name": "share_based_payments", "evidence": [{
                "page_number": 1, "matched_text": "share-based compensation",
                "evidence_excerpt": "excludes share-based compensation",
            }]}],
        }), encoding="utf-8")
        return pdf_path, definition_path, snapshot_path

    def test_traceability_checks_raw_page_substrings_and_all_confirmed_items(self) -> None:
        inputs = self.traceability_inputs()
        page = SimpleNamespace(extract_text=lambda: "Raw text: excludes share-based compensation. Other text.")
        reader = SimpleNamespace(is_encrypted=False, pages=[page])
        with patch("pypdf.PdfReader", return_value=reader) as parser:
            result = verify_evidence(*inputs, expected_pages=1)
        self.assertEqual(result["verified_evidence_entries"], 1)
        self.assertEqual(result["verified_pages"], [1])
        self.assertTrue(parser.call_args.kwargs["strict"])
        page.extract_text = lambda: "share-based compensation exists but this excerpt does not"
        with patch("pypdf.PdfReader", return_value=reader):
            with self.assertRaisesRegex(ValueError, "exact PDF page substring"):
                verify_evidence(*inputs, expected_pages=1)
        snapshot = json.loads(inputs[2].read_text(encoding="utf-8"))
        snapshot["items"] = []
        inputs[2].write_text(json.dumps(snapshot), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Every confirmed adjustment"):
            verify_evidence(*inputs, expected_pages=1)

    def test_traceability_does_not_suppress_pdf_parser_warnings(self) -> None:
        inputs = self.traceability_inputs()
        def warn(*args, **kwargs):
            logging.getLogger("pypdf").warning("Synthetic font warning")
        with patch("pypdf.PdfReader", side_effect=warn):
            with self.assertRaisesRegex(ValueError, "parser warning"):
                verify_evidence(*inputs, expected_pages=1)


if __name__ == "__main__":
    unittest.main()
