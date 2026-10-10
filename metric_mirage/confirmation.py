"""Export explicit user selections in the existing MetricDefinition format."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile

from .candidates import (
    _ADJUSTMENT_ALIASES,
    AdjustmentCandidate,
    extract_adjustment_candidates,
)
from .core import MetricDefinition, _require_text
from .reports import DEFAULT_REGISTRY, ReportRecord, _path


@dataclass(frozen=True, slots=True)
class ConfirmationResult:
    """An exported user selection and the candidate evidence it retains."""

    definition: MetricDefinition
    report: ReportRecord
    selected_candidates: tuple[AdjustmentCandidate, ...]
    output_path: Path


def _output_path(value: str | Path) -> Path:
    """Resolve the parent while preserving existing or dangling leaf symlinks."""
    requested = _path(value, "output_path").absolute()
    try:
        parent = requested.parent.resolve(strict=True)
        if not parent.is_dir():
            raise ValueError(f"Output parent is not a directory: {parent}")
        target = parent / requested.name
        try:
            target.lstat()
        except FileNotFoundError:
            return target
    except FileNotFoundError as exc:
        raise ValueError(f"Output directory does not exist: {requested.parent}") from exc
    except RuntimeError as exc:
        raise ValueError(f"Cannot resolve output directory {requested.parent}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"Cannot access output path {requested}: {exc}") from exc
    raise ValueError(f"Output path already exists: {target}. Existing files will not be overwritten.")


def _write_json(target: Path, data: object, *, label: str = "definition") -> None:
    """Publish complete UTF-8 JSON atomically without replacing any entry.

    A same-directory hard link creates the final name exclusively after the
    temporary file has been flushed and closed. Unsupported filesystems fail
    without publishing a partial file or falling back to an overwrite.
    """
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target)
    except FileExistsError as exc:
        raise ValueError(
            f"Output path already exists: {target}. Existing files will not be overwritten."
        ) from exc
    except UnicodeError as exc:
        raise ValueError(f"Cannot encode {label} as UTF-8 for {target}: {exc}") from exc
    except OSError as exc:
        raise OSError(f"Cannot write {label} to {target}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Preserve the original write error; the final name is never partial.
                pass


def _write_definition(target: Path, definition: MetricDefinition) -> None:
    """Retain the original definition writer and its error behavior."""
    _write_json(target, asdict(definition))


def _confirmation_source(source_name: str, report_sha256: str) -> str:
    """Build the existing explicit confirmation source without parsing it."""
    return (
        f"{source_name} | SHA256: {report_sha256} | "
        "User-selected adjustment candidates; not an independently verified complete formula."
    )


def confirm_candidates(
    ticker: str | int,
    *,
    fiscal_period: str,
    selected_adjustments: Sequence[str],
    output_path: str | Path,
    report_type: str | None = None,
    registry_path: str | Path = DEFAULT_REGISTRY,
) -> ConfirmationResult:
    """Export only explicitly selected, currently extracted canonical labels.

    Confirmation records the user's choices, not independent verification of
    a complete financial formula. PDF selection, registered SHA256 checks and
    text extraction use the existing candidate function without changing its
    rules. All validation precedes output writing; PDF and registry are read
    only. Existing output entries are rejected, including dangling symlinks
    and files created by another writer during publication.
    """
    if isinstance(selected_adjustments, (str, bytes)) or not isinstance(
        selected_adjustments, Sequence
    ):
        raise TypeError("selected_adjustments must be a sequence of canonical names.")
    selected = tuple(sorted({
        _require_text(name, f"selected_adjustments[{index}]")
        for index, name in enumerate(selected_adjustments)
    }))
    if not selected:
        raise ValueError("Select at least one adjustment explicitly with --select.")
    unknown = [name for name in selected if name not in _ADJUSTMENT_ALIASES]
    if unknown:
        raise ValueError(
            f"Unknown adjustment canonical_name: {', '.join(unknown)}. "
            "Select names shown by reports candidates."
        )

    result = extract_adjustment_candidates(
        ticker, fiscal_period=fiscal_period, report_type=report_type,
        registry_path=registry_path,
    )
    if not result.candidates:
        raise ValueError("No adjustment candidates found in this report; no definition was exported.")
    available = {candidate.canonical_name for candidate in result.candidates}
    missing = [name for name in selected if name not in available]
    if missing:
        raise ValueError(
            f"Selected adjustments are not candidates in this report: {', '.join(missing)}. "
            f"Available candidates: {', '.join(sorted(available))}."
        )

    report = result.report
    definition = MetricDefinition(
        company=report.company,
        metric_name="Adjusted Net Profit",
        fiscal_period=report.fiscal_period,
        adjustments=selected,
        source=_confirmation_source(report.source_name, report.file_sha256),
    )
    target = _output_path(output_path)
    _write_definition(target, definition)
    evidence = tuple(
        candidate for candidate in result.candidates if candidate.canonical_name in selected
    )
    return ConfirmationResult(definition, report, evidence, target)
