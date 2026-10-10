"""Compare the adjustment sets used to define a financial metric."""

from .candidates import AdjustmentCandidate, CandidateResult, extract_adjustment_candidates
from .confirmation import ConfirmationResult, confirm_candidates
from .core import MetricDefinition, MetricDiff, compare_metrics
from .evidence import EvidenceSnapshot, create_evidence_snapshot, load_evidence_snapshot
from .reports import ReportRecord, list_reports, load_registry, register_report
from .search import SearchMatch, SearchResult, search_report
from .tickers import normalize_hk_ticker

__all__ = [
    "MetricDefinition",
    "MetricDiff",
    "compare_metrics",
    "normalize_hk_ticker",
    "ReportRecord",
    "register_report",
    "list_reports",
    "load_registry",
    "SearchMatch",
    "SearchResult",
    "search_report",
    "AdjustmentCandidate",
    "CandidateResult",
    "extract_adjustment_candidates",
    "ConfirmationResult",
    "confirm_candidates",
    "EvidenceSnapshot",
    "create_evidence_snapshot",
    "load_evidence_snapshot",
]
