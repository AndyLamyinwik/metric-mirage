"""Register local PDF report metadata without copying or parsing reports."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .core import _require_text
from .tickers import normalize_hk_ticker


DEFAULT_REGISTRY = Path(".metric_mirage/reports.json")
REPORT_TYPES = ("annual", "interim", "annual_results", "interim_results")


@dataclass(frozen=True, slots=True)
class ReportRecord:
    """Metadata identifying a local report and its SHA256 file contents.

    The absolute file path remains traceable even if the file later moves or
    becomes unavailable. Creating a record does not parse or reopen the PDF.
    """

    ticker: str
    company: str
    report_type: str
    fiscal_period: str
    source_name: str
    local_file: str
    file_sha256: str
    publication_date: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "ticker", normalize_hk_ticker(_require_text(self.ticker, "ticker"))
        )
        for field_name in ("company", "report_type", "fiscal_period", "source_name"):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )
        if self.report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of: {', '.join(REPORT_TYPES)}.")

        if not isinstance(self.local_file, str):
            raise TypeError("local_file must be an absolute path string.")
        if not self.local_file.strip() or not Path(self.local_file).is_absolute():
            raise ValueError("local_file must be an absolute path.")

        digest = _require_text(self.file_sha256, "file_sha256")
        if re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise ValueError("file_sha256 must contain exactly 64 hexadecimal characters.")
        object.__setattr__(self, "file_sha256", digest.lower())

        if self.publication_date is not None:
            if not isinstance(self.publication_date, str):
                raise TypeError("publication_date must be a YYYY-MM-DD string or None.")
            if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", self.publication_date) is None:
                raise ValueError("publication_date must be a valid YYYY-MM-DD date.")
            try:
                date.fromisoformat(self.publication_date)
            except ValueError as exc:
                raise ValueError("publication_date must be a valid YYYY-MM-DD date.") from exc


def _path(value: str | Path, field_name: str) -> Path:
    if not isinstance(value, (str, Path)):
        raise TypeError(f"{field_name} must be a string or Path.")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{field_name} must not be empty or whitespace-only.")
    try:
        return Path(value).expanduser()
    except RuntimeError as exc:
        raise ValueError(f"Cannot expand {field_name} path {value!r}: {exc}") from exc


def _pdf_digest(local_file: Path) -> str:
    """Validate basic PDF boundaries and hash bytes in one streaming pass."""
    try:
        if not local_file.is_file():
            raise ValueError(f"Report file is not a regular file: {local_file}")
        digest = hashlib.sha256()
        prefix = b""
        tail = b""
        with local_file.open("rb") as report:
            while chunk := report.read(65536):
                digest.update(chunk)
                if len(prefix) < 32:
                    prefix = (prefix + chunk)[:32]
                tail = (tail + chunk)[-1024:]
    except OSError as exc:
        raise ValueError(f"Cannot read report file {local_file}: {exc}") from exc

    if re.match(rb"%PDF-[0-9]+\.[0-9]+(?:\r\n|\n|\r)", prefix) is None or not tail.rstrip(
        b" \t\r\n\f\v"
    ).endswith(b"%%EOF"):
        raise ValueError(
            f"Report file does not have a valid PDF header and final %%EOF marker: {local_file}"
        )
    return digest.hexdigest()


def load_registry(registry_path: str | Path = DEFAULT_REGISTRY) -> list[ReportRecord]:
    """Read validated records; absent registries are empty, invalid ones fail."""
    path = _path(registry_path, "registry_path").resolve()
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Cannot read report registry {path}: {exc}") from exc

    try:
        data = json.loads(contents)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in report registry {path}: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"version", "reports"}:
        raise ValueError(f"Invalid report registry {path}: expected version and reports fields.")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError(f"Invalid report registry {path}: unsupported registry version.")
    if not isinstance(data["reports"], list):
        raise ValueError(f"Invalid report registry {path}: reports must be an array.")

    records: list[ReportRecord] = []
    hashes: set[str] = set()
    for index, raw_record in enumerate(data["reports"]):
        if not isinstance(raw_record, dict):
            raise ValueError(f"Invalid report registry {path}: reports[{index}] must be an object.")
        try:
            record = ReportRecord(**raw_record)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid report registry {path}, reports[{index}]: {exc}") from exc
        if record.file_sha256 in hashes:
            raise ValueError(f"Invalid report registry {path}: duplicate file_sha256 records.")
        hashes.add(record.file_sha256)
        records.append(record)
    return records


@contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    """Protect one registry update with an exclusively created sibling lock."""
    lock_path = path.with_name(f"{path.name}.lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"Cannot create report registry directory {path.parent}: {exc}") from exc
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            f"Report registry is locked: {lock_path}. Another registration may be running; "
            "remove a stale lock only after confirming no writer is active."
        ) from exc
    except OSError as exc:
        raise OSError(f"Cannot lock report registry {path}: {exc}") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            # Preserve any primary read/write error rather than masking it.
            pass


def _write_registry(path: Path, records: list[ReportRecord]) -> None:
    """Replace the registry only after a complete, flushed sibling write."""
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(
                {"version": 1, "reports": [asdict(record) for record in records]},
                temporary,
                indent=2,
                ensure_ascii=False,
            )
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    except (OSError, UnicodeError) as exc:
        raise OSError(f"Cannot write report registry {path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def register_report(
    local_file: str | Path,
    *,
    ticker: str | int,
    company: str,
    report_type: str,
    fiscal_period: str,
    source_name: str,
    publication_date: str | None = None,
    registry_path: str | Path = DEFAULT_REGISTRY,
) -> ReportRecord:
    """Register a local PDF, retaining its absolute path and content identity.

    Re-registering the same bytes with the same metadata returns the original
    record and leaves the registry unchanged. Conflicting metadata for the same
    bytes fails instead of replacing an existing record.
    """
    requested_path = _path(local_file, "local_file")
    try:
        absolute_path = requested_path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"Report file does not exist: {requested_path.absolute()}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot access report file {requested_path.absolute()}: {exc}") from exc

    record = ReportRecord(
        ticker=normalize_hk_ticker(ticker),
        company=company,
        report_type=report_type,
        fiscal_period=fiscal_period,
        source_name=source_name,
        local_file=str(absolute_path),
        file_sha256=_pdf_digest(absolute_path),
        publication_date=publication_date,
    )
    path = _path(registry_path, "registry_path").resolve()
    if path == absolute_path:
        raise ValueError("registry_path must be different from the original PDF file path.")
    with _registry_lock(path):
        records = load_registry(path)
        for existing in records:
            if existing.file_sha256 != record.file_sha256:
                continue
            previous_metadata = asdict(existing)
            new_metadata = asdict(record)
            previous_metadata.pop("local_file")
            new_metadata.pop("local_file")
            if previous_metadata != new_metadata:
                raise ValueError(
                    f"Report file SHA256 is already registered with different metadata in {path}; "
                    "the existing record has been preserved."
                )
            return existing
        records.append(record)
        _write_registry(path, records)
    return record


def list_reports(
    ticker: str | int, registry_path: str | Path = DEFAULT_REGISTRY
) -> list[ReportRecord]:
    """List one normalized Hong Kong ticker's reports in a stable order."""
    normalized_ticker = normalize_hk_ticker(ticker)
    return sorted(
        (record for record in load_registry(registry_path) if record.ticker == normalized_ticker),
        key=lambda record: (
            record.fiscal_period,
            record.report_type,
            record.publication_date or "",
            record.file_sha256,
        ),
    )
