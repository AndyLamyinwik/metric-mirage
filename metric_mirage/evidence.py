"""Trace confirmed adjustment sets to local PDF evidence snapshots."""

import json
from pathlib import Path
import re
from typing import TypedDict

from .candidates import _ADJUSTMENT_ALIASES, extract_adjustment_candidates
from .confirmation import _confirmation_source, _output_path, _write_json
from .core import MetricDefinition, _normalize_name
from .definition_io import _unique_object, load_definition_snapshot
from .reports import DEFAULT_REGISTRY, REPORT_TYPES, _path
from .tickers import normalize_hk_ticker


class EvidenceExcerpt(TypedDict):
    page_number: int
    matched_text: str
    evidence_excerpt: str


class EvidenceItem(TypedDict):
    canonical_name: str
    evidence: list[EvidenceExcerpt]


class EvidenceSnapshot(TypedDict):
    schema_version: int
    company: str
    ticker: str
    fiscal_period: str
    metric_name: str
    definition_sha256: str
    report_sha256: str
    source_name: str
    report_type: str
    items: list[EvidenceItem]


_SNAPSHOT_FIELDS = {
    "schema_version", "company", "ticker", "fiscal_period", "metric_name",
    "definition_sha256", "report_sha256", "source_name", "report_type", "items",
}


def _object(value: object, fields: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"Invalid evidence snapshot: {label} must contain exactly {', '.join(sorted(fields))}.")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid evidence snapshot: {label} must be a nonempty string.")
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-fA-F]{64}", value) is None:
        raise ValueError(f"Invalid evidence snapshot: {label} must contain exactly 64 hexadecimal characters.")
    return value.lower()


def _confirmed_names(definition: MetricDefinition) -> list[str]:
    if not isinstance(definition, MetricDefinition):
        raise TypeError("definition must be a MetricDefinition.")
    if _normalize_name(definition.metric_name) != _normalize_name("Adjusted Net Profit"):
        raise ValueError("Evidence snapshots support only Adjusted Net Profit definitions.")
    names = sorted(set(definition.adjustments))
    unknown = [name for name in names if name not in _ADJUSTMENT_ALIASES]
    if unknown:
        raise ValueError(
            f"Definition adjustments must be confirmed canonical names: {', '.join(unknown)}."
        )
    return names


def validate_evidence_snapshot(
    data: object, *, definition: MetricDefinition, definition_sha256: str,
) -> EvidenceSnapshot:
    """Validate a complete snapshot without reopening the original PDF.

    The supplied digest binds validation to the caller's definition file bytes.
    This checks structure and recorded association; it does not authenticate a
    manually altered snapshot or independently verify a financial formula.
    """
    names = _confirmed_names(definition)
    expected_digest = _sha256(definition_sha256, "expected definition_sha256")
    snapshot = _object(data, _SNAPSHOT_FIELDS, "snapshot")
    if type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1:
        raise ValueError("Invalid evidence snapshot: unsupported schema_version; expected integer 1.")
    company = _text(snapshot["company"], "company")
    ticker = _text(snapshot["ticker"], "ticker")
    period = _text(snapshot["fiscal_period"], "fiscal_period")
    metric = _text(snapshot["metric_name"], "metric_name")
    source_name = _text(snapshot["source_name"], "source_name")
    report_type = _text(snapshot["report_type"], "report_type")
    if normalize_hk_ticker(ticker) != ticker:
        raise ValueError("Invalid evidence snapshot: ticker must be a normalized five-digit Hong Kong code.")
    if report_type not in REPORT_TYPES:
        raise ValueError(f"Invalid evidence snapshot: unsupported report_type {report_type!r}.")
    digest = _sha256(snapshot["definition_sha256"], "definition_sha256")
    report_digest = _sha256(snapshot["report_sha256"], "report_sha256")
    if digest != expected_digest:
        raise ValueError("Evidence definition SHA256 does not match the definition JSON file bytes.")
    if _normalize_name(company) != _normalize_name(definition.company):
        raise ValueError("Evidence company does not match the metric definition.")
    if period != definition.fiscal_period:
        raise ValueError("Evidence fiscal_period does not match the metric definition.")
    if _normalize_name(metric) != _normalize_name(definition.metric_name):
        raise ValueError("Evidence metric_name does not match the metric definition.")
    if definition.source != _confirmation_source(source_name, report_digest):
        raise ValueError(
            "Definition source does not match this report's supported confirmation source format and SHA256."
        )
    if not isinstance(snapshot["items"], list):
        raise ValueError("Invalid evidence snapshot: items must be an array.")
    items: list[EvidenceItem] = []
    seen_names: set[str] = set()
    for index, value in enumerate(snapshot["items"]):
        item = _object(value, {"canonical_name", "evidence"}, f"items[{index}]")
        name = _text(item["canonical_name"], f"items[{index}].canonical_name")
        if name not in names:
            raise ValueError(f"Evidence contains an unconfirmed adjustment: {name}.")
        if name in seen_names:
            raise ValueError(f"Evidence contains duplicate adjustment items: {name}.")
        seen_names.add(name)
        if not isinstance(item["evidence"], list) or not item["evidence"]:
            raise ValueError(f"Missing evidence for confirmed adjustment: {name}.")
        excerpts: list[EvidenceExcerpt] = []
        pages: set[int] = set()
        for entry_index, value in enumerate(item["evidence"]):
            label = f"items[{index}].evidence[{entry_index}]"
            entry = _object(value, {"page_number", "matched_text", "evidence_excerpt"}, label)
            page = entry["page_number"]
            if type(page) is not int or page < 1:
                raise ValueError(f"Invalid evidence snapshot: {label}.page_number must be a positive 1-based integer.")
            if page in pages:
                raise ValueError(f"Duplicate or conflicting evidence for {name} on PDF page {page}.")
            pages.add(page)
            matched = _text(entry["matched_text"], f"{label}.matched_text")
            excerpt = _text(entry["evidence_excerpt"], f"{label}.evidence_excerpt")
            if matched not in excerpt:
                raise ValueError(f"Invalid evidence snapshot: matched_text is absent from its evidence_excerpt for {name}.")
            if _normalize_name(matched) not in {
                _normalize_name(alias) for alias in _ADJUSTMENT_ALIASES[name]
            }:
                raise ValueError(f"Invalid evidence snapshot: matched_text is not a supported alias for {name}.")
            excerpts.append({"page_number": page, "matched_text": matched, "evidence_excerpt": excerpt})
        items.append({"canonical_name": name, "evidence": sorted(excerpts, key=lambda entry: entry["page_number"])})
    missing = sorted(set(names) - seen_names)
    if missing:
        raise ValueError(f"Missing evidence for confirmed adjustments: {', '.join(missing)}.")
    return {
        "schema_version": 1, "company": company, "ticker": ticker,
        "fiscal_period": period, "metric_name": metric,
        "definition_sha256": digest, "report_sha256": report_digest,
        "source_name": source_name, "report_type": report_type,
        "items": sorted(items, key=lambda item: item["canonical_name"]),
    }


def load_evidence_snapshot(
    path: str | Path, *, definition: MetricDefinition, definition_sha256: str,
) -> EvidenceSnapshot:
    """Read strict UTF-8 JSON and bind the complete snapshot to a definition."""
    target = _path(path, "evidence_path")
    try:
        text = target.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ValueError(f"Evidence snapshot file not found: {target}.") from exc
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read evidence snapshot {target}: {exc}") from exc
    try:
        data: object = json.loads(text, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"Invalid evidence JSON in {target}: {exc}") from exc
    try:
        return validate_evidence_snapshot(
            data, definition=definition, definition_sha256=definition_sha256,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid evidence snapshot {target}: {exc}") from exc


def create_evidence_snapshot(
    ticker: str | int, *, fiscal_period: str, definition_path: str | Path,
    output_path: str | Path, report_type: str | None = None,
    registry_path: str | Path = DEFAULT_REGISTRY,
) -> EvidenceSnapshot:
    """Re-extract all confirmed items and exclusively publish their evidence.

    Selection, current PDF SHA256 checks and raw extraction reuse the existing
    candidate implementation. Registry, PDF and definition are read only.
    """
    definition, definition_digest = load_definition_snapshot(definition_path)
    names = _confirmed_names(definition)
    result = extract_adjustment_candidates(
        ticker, fiscal_period=fiscal_period, report_type=report_type,
        registry_path=registry_path,
    )
    report = result.report
    items: list[EvidenceItem] = [
        {"canonical_name": name, "evidence": [
            {"page_number": candidate.page_number,
             "matched_text": candidate.matched_text,
             "evidence_excerpt": candidate.evidence_excerpt}
            for candidate in result.candidates if candidate.canonical_name == name
        ]}
        for name in names
    ]
    snapshot = validate_evidence_snapshot(
        {
            "schema_version": 1, "company": report.company, "ticker": report.ticker,
            "fiscal_period": report.fiscal_period, "metric_name": result.metric_name,
            "definition_sha256": definition_digest, "report_sha256": report.file_sha256,
            "source_name": report.source_name, "report_type": report.report_type, "items": items,
        },
        definition=definition, definition_sha256=definition_digest,
    )
    _write_json(_output_path(output_path), snapshot, label="evidence snapshot")
    return snapshot
