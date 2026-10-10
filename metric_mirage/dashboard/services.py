"""Local dashboard operations that reuse the financial APIs without UI imports."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib.resources import files
import json
import os
from pathlib import Path
import tempfile
from typing import TypeVar
from uuid import uuid4

from ..candidates import CandidateResult, extract_adjustment_candidates
from ..confirmation import _confirmation_source, confirm_candidates
from ..core import MetricDefinition, _normalize_name
from ..definition_io import load_definition_snapshot
from ..drift_report import DriftReport, build_drift_report
from ..evidence import EvidenceSnapshot, create_evidence_snapshot, load_evidence_snapshot
from ..reports import ReportRecord, _pdf_digest, load_registry, register_report
from ..search import SearchResult, search_report


MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class LoadedDefinition:
    path: Path
    definition: MetricDefinition
    sha256: str
    ticker: str | None = None


def demo_analysis() -> DriftReport:
    """Compute the labeled demo using the existing comparison/report engine."""
    resource = files("metric_mirage").joinpath("data", "synthetic_adjusted_net_profit.json")
    data = json.loads(resource.read_text(encoding="utf-8"))
    if data.get("data_type") != "SYNTHETIC":
        raise ValueError("Dashboard demo must be labeled SYNTHETIC.")
    old, new = MetricDefinition(**data["old"]), MetricDefinition(**data["new"])
    if not all(value.source.startswith("SYNTHETIC:") for value in (old, new)):
        raise ValueError("Dashboard demo sources must be labeled SYNTHETIC.")
    return build_drift_report(old, new)


class Workspace:
    """Controlled uploads/exports and the existing local report registry.

    The root is trusted application configuration, never a dashboard text input.
    User filenames are ignored for storage; complete validated uploads get unique
    names. This supports a local single-user workflow, not tenant isolation.
    """

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or os.environ.get("METRIC_MIRAGE_DATA_DIR", ".metric_mirage")).absolute().resolve()
        self.registry_path = self.root / "reports.json"

    def _directory(self, name: str) -> Path:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise ValueError("Dashboard storage directories must remain inside the configured data directory.")
        return directory

    def _artifact_path(self, path: Path, *, directory: str) -> Path:
        target = Path(path).absolute()
        allowed = self.root / directory
        if target.is_symlink() or target.resolve().parent != allowed or allowed.is_symlink():
            raise ValueError("Select a dashboard artifact; arbitrary file paths are not accepted.")
        if target.suffix != ".json":
            raise ValueError("Dashboard artifacts must be JSON files.")
        return target

    def _upload(
        self, data: bytes, *, directory: str, suffix: str, limit: int,
        validate: Callable[[Path], T],
    ) -> tuple[Path, T]:
        if not isinstance(data, bytes):
            raise TypeError("Uploaded content must be bytes.")
        if not data:
            raise ValueError("Uploaded file is empty.")
        if len(data) > limit:
            raise ValueError(f"Uploaded file exceeds the {limit // (1024 * 1024)} MiB size limit.")
        parent = self._directory(directory)
        target = parent / f"{uuid4().hex}{suffix}"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=parent, prefix=".upload-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            result = validate(temporary)
            os.link(temporary, target)
            return target, result
        except UnicodeError as exc:
            raise ValueError(f"Uploaded JSON must use UTF-8: {exc}") from exc
        except OSError as exc:
            raise OSError(f"Cannot save uploaded file: {exc}") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def reports(self) -> list[ReportRecord]:
        return sorted(load_registry(self.registry_path), key=lambda report: (
            report.company.casefold(), report.ticker, report.fiscal_period,
            report.report_type, report.file_sha256,
        ))

    def _registered(self, report: ReportRecord) -> ReportRecord:
        if not isinstance(report, ReportRecord) or report not in self.reports():
            raise ValueError("Select a report from the current registry.")
        return report

    def register_pdf(
        self, data: bytes, *, filename: str, ticker: str | int, company: str,
        report_type: str, fiscal_period: str, source_name: str,
    ) -> ReportRecord:
        """Validate and store bytes safely; register without overwriting reports."""
        # filename is display-only. It never contributes to a filesystem path.
        target, _ = self._upload(
            data, directory="uploads", suffix=".pdf", limit=MAX_PDF_BYTES,
            validate=_pdf_digest,
        )
        try:
            report = register_report(
                target, ticker=ticker, company=company, report_type=report_type,
                fiscal_period=fiscal_period, source_name=source_name,
                registry_path=self.registry_path,
            )
        except (TypeError, ValueError, OSError):
            target.unlink(missing_ok=True)
            raise
        if Path(report.local_file) != target:
            target.unlink(missing_ok=True)  # Existing duplicate record retains its original file.
        return report

    def load_definition(self, path: Path) -> LoadedDefinition:
        target = self._artifact_path(path, directory="definitions")
        definition, digest = load_definition_snapshot(target)
        tickers = {
            report.ticker for report in self.reports()
            if _normalize_name(report.company) == _normalize_name(definition.company)
            and report.fiscal_period == definition.fiscal_period
            and definition.source == _confirmation_source(report.source_name, report.file_sha256)
        }
        ticker = next(iter(tickers)) if len(tickers) == 1 else None
        return LoadedDefinition(target, definition, digest, ticker)

    def definitions(self) -> list[LoadedDefinition]:
        directory = self.root / "definitions"
        if not directory.exists():
            return []
        return sorted((self.load_definition(path) for path in directory.glob("*.json")), key=lambda loaded: (
            loaded.definition.company.casefold(), loaded.definition.fiscal_period,
            loaded.definition.metric_name.casefold(), loaded.sha256, str(loaded.path),
        ))

    def import_definition(self, data: bytes) -> LoadedDefinition:
        target, _ = self._upload(
            data, directory="definitions", suffix=".json", limit=MAX_JSON_BYTES,
            validate=load_definition_snapshot,
        )
        return self.load_definition(target)

    def search(self, report: ReportRecord, keyword: str, max_results: int = 10) -> SearchResult:
        record = self._registered(report)
        return search_report(
            record.ticker, fiscal_period=record.fiscal_period, report_type=record.report_type,
            keyword=keyword, max_results=max_results, registry_path=self.registry_path,
        )

    def candidates(self, report: ReportRecord) -> CandidateResult:
        record = self._registered(report)
        return extract_adjustment_candidates(
            record.ticker, fiscal_period=record.fiscal_period, report_type=record.report_type,
            registry_path=self.registry_path,
        )

    def confirm(self, report: ReportRecord, selected: Sequence[str]) -> LoadedDefinition:
        record = self._registered(report)
        target = self._directory("definitions") / f"{uuid4().hex}.json"
        confirm_candidates(
            record.ticker, fiscal_period=record.fiscal_period,
            selected_adjustments=selected, output_path=target,
            report_type=record.report_type, registry_path=self.registry_path,
        )
        return self.load_definition(target)

    def create_evidence(
        self, definition: LoadedDefinition, report: ReportRecord,
    ) -> tuple[EvidenceSnapshot, Path]:
        current = self.load_definition(definition.path)
        if current.sha256 != definition.sha256:
            raise ValueError("Definition SHA256 changed; reload the definition before creating evidence.")
        record = self._registered(report)
        target = self._directory("evidence") / f"{uuid4().hex}.json"
        snapshot = create_evidence_snapshot(
            record.ticker, fiscal_period=record.fiscal_period,
            definition_path=current.path, output_path=target,
            report_type=record.report_type, registry_path=self.registry_path,
        )
        return snapshot, target

    def import_evidence(
        self, data: bytes, definition: LoadedDefinition,
    ) -> tuple[EvidenceSnapshot, Path]:
        current = self.load_definition(definition.path)
        if current.sha256 != definition.sha256:
            raise ValueError("Definition SHA256 changed; reload the definition before attaching evidence.")
        target, snapshot = self._upload(
            data, directory="evidence", suffix=".json", limit=MAX_JSON_BYTES,
            validate=lambda path: load_evidence_snapshot(
                path, definition=current.definition, definition_sha256=current.sha256,
            ),
        )
        return snapshot, target

    def analyse(
        self, old: LoadedDefinition, new: LoadedDefinition,
        old_evidence: EvidenceSnapshot | None = None, new_evidence: EvidenceSnapshot | None = None,
    ) -> DriftReport:
        old_current, new_current = self.load_definition(old.path), self.load_definition(new.path)
        if old_current.sha256 != old.sha256 or new_current.sha256 != new.sha256:
            raise ValueError("Definition SHA256 changed; reload the selected definitions.")
        # Validate associations against the actual file digests, not self-reported hashes.
        from ..evidence import validate_evidence_snapshot
        for snapshot, current in ((old_evidence, old_current), (new_evidence, new_current)):
            if snapshot is not None:
                validate_evidence_snapshot(snapshot, definition=current.definition, definition_sha256=current.sha256)
        return build_drift_report(
            old_current.definition, new_current.definition,
            old_evidence=old_evidence, new_evidence=new_evidence,
        )

    def read_artifact(self, path: Path) -> bytes:
        target = Path(path)
        if target.absolute().parent == self.root / "definitions":
            target = self._artifact_path(target, directory="definitions")
        else:
            target = self._artifact_path(target, directory="evidence")
        return target.read_bytes()
