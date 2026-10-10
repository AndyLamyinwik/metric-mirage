"""Cross-period reports about user-confirmed adjustment sets."""

import json
import re
from html import escape
from typing import Literal, NotRequired, TypedDict

from .core import MetricDefinition, compare_metrics
from .evidence import EvidenceExcerpt, EvidenceSnapshot, validate_evidence_snapshot


class ReportEvidence(EvidenceExcerpt):
    """An original excerpt with the PDF's recorded content identifier."""

    report_sha256: str


class AdjustmentEvidence(TypedDict):
    canonical_name: str
    status: Literal["ADDED", "REMOVED", "UNCHANGED"]
    old_evidence: list[ReportEvidence]
    new_evidence: list[ReportEvidence]


class DriftReport(TypedDict):
    """Report metadata and the existing comparison engine's set differences."""

    company: str
    metric_name: str
    old_period: str
    new_period: str
    added_adjustments: list[str]
    removed_adjustments: list[str]
    unchanged_adjustments: list[str]
    has_definition_drift: bool
    old_source: str
    new_source: str
    evidence: NotRequired[list[AdjustmentEvidence]]


def _fiscal_year(period: str) -> int:
    if re.fullmatch(r"FY[0-9]{4}", period) is None:
        raise ValueError(
            f"Unsupported fiscal period {period!r}: drift requires FY followed by "
            "four digits, for example FY2024."
        )
    return int(period[2:])


def build_drift_report(
    old: MetricDefinition,
    new: MetricDefinition,
    *,
    old_evidence: EvidenceSnapshot | None = None,
    new_evidence: EvidenceSnapshot | None = None,
) -> DriftReport:
    """Reuse metric comparison, requiring strictly increasing FY year labels.

    Load optional snapshots with load_evidence_snapshot first to bind them to
    the actual definition file bytes. In-memory snapshots are also checked for
    valid structure, source, metadata and complete confirmed adjustment sets.
    The report does not independently verify complete financial formulas.
    """
    diff = compare_metrics(old, new)
    old_year = _fiscal_year(old.fiscal_period)
    new_year = _fiscal_year(new.fiscal_period)
    if new_year <= old_year:
        raise ValueError(
            f"New period {new.fiscal_period!r} must be later than old period "
            f"{old.fiscal_period!r}; same-year and reversed comparisons are not supported."
        )
    report: DriftReport = {
        "company": new.company,
        "metric_name": new.metric_name,
        "old_period": old.fiscal_period,
        "new_period": new.fiscal_period,
        "added_adjustments": list(diff.added_adjustments),
        "removed_adjustments": list(diff.removed_adjustments),
        "unchanged_adjustments": list(diff.unchanged_adjustments),
        "has_definition_drift": diff.has_definition_drift,
        "old_source": old.source,
        "new_source": new.source,
    }
    if (old_evidence is None) != (new_evidence is None):
        raise ValueError("Provide both old and new evidence snapshots, or neither.")
    if old_evidence is not None and new_evidence is not None:
        snapshots: list[EvidenceSnapshot] = []
        for snapshot, definition in ((old_evidence, old), (new_evidence, new)):
            if not isinstance(snapshot, dict):
                raise ValueError("Evidence snapshot must be a JSON object.")
            snapshots.append(validate_evidence_snapshot(
                snapshot, definition=definition,
                definition_sha256=snapshot.get("definition_sha256", ""),
            ))
        old_snapshot, new_snapshot = snapshots
        if old_snapshot["ticker"] != new_snapshot["ticker"]:
            raise ValueError("Old and new evidence must refer to the same Hong Kong ticker.")
        report["evidence"] = _adjustment_evidence(report, old_snapshot, new_snapshot)
    return report


def _adjustment_evidence(
    report: DriftReport, old: EvidenceSnapshot, new: EvidenceSnapshot
) -> list[AdjustmentEvidence]:
    """Attach original evidence according to existing comparison categories."""
    old_items = {item["canonical_name"]: item["evidence"] for item in old["items"]}
    new_items = {item["canonical_name"]: item["evidence"] for item in new["items"]}
    statuses: dict[str, Literal["ADDED", "REMOVED", "UNCHANGED"]] = {}
    for names, status in (
        (report["added_adjustments"], "ADDED"),
        (report["removed_adjustments"], "REMOVED"),
        (report["unchanged_adjustments"], "UNCHANGED"),
    ):
        statuses.update({name: status for name in names})
    return [
        {
            "canonical_name": name,
            "status": status,
            "old_evidence": [
                {**entry, "report_sha256": old["report_sha256"]}
                for entry in old_items.get(name, [])
            ],
            "new_evidence": [
                {**entry, "report_sha256": new["report_sha256"]}
                for entry in new_items.get(name, [])
            ],
        }
        for name, status in sorted(statuses.items())
    ]


def render_json(report: DriftReport) -> str:
    """Produce only deterministic JSON, retaining Unicode source text."""
    return json.dumps(report, ensure_ascii=False, indent=2)


def _markdown_text(value: str) -> str:
    """Keep literal labels from changing Markdown structure or table columns."""
    escaped = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", escape(value, quote=False))
    return escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")


def _source_block(source: str) -> str:
    """Display a complete source verbatim without interpreting links or SHA256."""
    longest_run = max((len(run) for run in re.findall(r"`+", source)), default=0)
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}text\n{source}\n{fence}"


def _evidence_section(report: DriftReport) -> list[str]:
    """Keep raw PDF excerpts outside the comparison table in literal blocks."""
    lines = ["", "## Evidence Section", ""]
    lines.append(
        "An item missing from a user-confirmed adjustment set does not establish "
        "that it was absent from, or not disclosed in, the original report."
    )
    if not report["evidence"]:
        lines.extend(["", "Both confirmed adjustment sets are empty."])
    for item in report["evidence"]:
        lines.extend([
            "", f"### {_markdown_text(item['canonical_name'])}", "",
            f"- **Canonical Name:** {_markdown_text(item['canonical_name'])}",
            f"- **Status:** {item['status']}",
        ])
        for side, period, entries in (
            ("Old", report["old_period"], item["old_evidence"]),
            ("New", report["new_period"], item["new_evidence"]),
        ):
            lines.extend(["", f"#### {side} Period Evidence ({period})", ""])
            if not entries:
                lines.append(
                    "Not in the user-confirmed adjustment set; no evidence attached for this side."
                )
            for entry in entries:
                lines.extend([
                    f"- **PDF Page Number:** {entry['page_number']}",
                    f"- **Report SHA256:** {entry['report_sha256']}",
                    "", "**Matched Text:**", "", _source_block(entry["matched_text"]),
                    "", "**Original Excerpt:**", "", _source_block(entry["evidence_excerpt"]), "",
                ])
    return lines


def render_markdown(report: DriftReport) -> str:
    """Render sorted categories and rows, followed by document-level sources."""
    lines = [
        "# Metric Mirage — Cross-Period Drift Report",
        "",
        f"- **Company:** {_markdown_text(report['company'])}",
        f"- **Metric:** {_markdown_text(report['metric_name'])}",
        f"- **Old Period:** {report['old_period']}",
        f"- **New Period:** {report['new_period']}",
        f"- **has_definition_drift:** {str(report['has_definition_drift']).lower()}",
        "",
        "## Adjustment Sets",
        "",
    ]
    rows: dict[str, tuple[str, str, str]] = {}
    for label, names, old_present, new_present, status in (
        ("Added Adjustments", report["added_adjustments"], "No", "Yes", "ADDED"),
        ("Removed Adjustments", report["removed_adjustments"], "Yes", "No", "REMOVED"),
        ("Unchanged Adjustments", report["unchanged_adjustments"], "Yes", "Yes", "UNCHANGED"),
    ):
        display = ", ".join(_markdown_text(name) for name in names) or "(none)"
        lines.append(f"- **{label}:** {display}")
        rows.update({name: (old_present, new_present, status) for name in names})
    if not report["has_definition_drift"]:
        lines.extend(["", "No changes in the user-confirmed adjustment set."])
    lines.extend([
        "",
        "## Comparison Table",
        "",
        "| Adjustment | Old Period | New Period | Status |",
        "| --- | --- | --- | --- |",
    ])
    for name, (old_present, new_present, status) in sorted(rows.items()):
        lines.append(f"| {_markdown_text(name)} | {old_present} | {new_present} | {status} |")
    if not rows:
        lines.extend(["", "Both adjustment sets are empty."])
    if "evidence" in report:
        lines.extend(_evidence_section(report))
    lines.extend([
        "",
        "## Old Source",
        "",
        _source_block(report["old_source"]),
        "",
        "## New Source",
        "",
        _source_block(report["new_source"]),
        "",
        "## Methodology Note",
        "",
        "This report compares user-confirmed adjustment sets. Set differences cannot "
        "independently establish that the company's complete financial metric formula "
        "changed. It provides no financial fraud or investment risk judgment.",
    ])
    return "\n".join(lines)
