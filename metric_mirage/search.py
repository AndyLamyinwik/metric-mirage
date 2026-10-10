"""Search registered PDF text with literal keywords and original-text excerpts."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
from threading import RLock
import warnings

from pypdf import PdfReader

from .core import _require_text
from .reports import DEFAULT_REGISTRY, REPORT_TYPES, ReportRecord, list_reports
from .tickers import normalize_hk_ticker


_PDF_DIAGNOSTIC_LOCK = RLock()


@dataclass(frozen=True, slots=True)
class SearchMatch:
    """One occurrence, located by a one-based PDF page and raw text excerpt."""

    page_number: int
    keyword: str
    excerpt: str


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Bounded search excerpts with the total count and registered provenance."""

    report: ReportRecord
    keyword: str
    total_matches: int
    matches: tuple[SearchMatch, ...]


class _WarningCapture(logging.Handler):
    """Collect pypdf diagnostics without leaking library logs into CLI output."""

    def __init__(self, messages: list[str]) -> None:
        super().__init__(logging.WARNING)
        self.messages = messages

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def _pdf_diagnostics() -> Iterator[tuple[list[str], list[warnings.WarningMessage]]]:
    """Keep parser warnings available so uncertain extraction can fail clearly."""
    messages: list[str] = []
    handler = _WarningCapture(messages)
    logger = logging.getLogger("pypdf")
    with _PDF_DIAGNOSTIC_LOCK:
        previous_handlers = logger.handlers[:]
        previous_level = logger.level
        previous_propagation = logger.propagate
        previous_disabled = logger.disabled
        logger.handlers = [handler]
        logger.setLevel(logging.WARNING)
        logger.propagate = False
        logger.disabled = False
        try:
            with warnings.catch_warnings(record=True) as python_warnings:
                warnings.simplefilter("always")
                yield messages, python_warnings
        finally:
            logger.handlers = previous_handlers
            logger.setLevel(previous_level)
            logger.propagate = previous_propagation
            logger.disabled = previous_disabled
            handler.close()


def _check_diagnostics(
    diagnostics: tuple[list[str], list[warnings.WarningMessage]], context: str
) -> None:
    messages, python_warnings = diagnostics
    if messages or python_warnings:
        detail = messages[0] if messages else str(python_warnings[0].message)
        raise ValueError(
            f"Cannot reliably extract text from {context}: PDF parser warning: {detail}. "
            "Check the PDF's text layer or font encoding; no search results were returned."
        )


def _extract_pages(report: ReportRecord) -> Iterator[str]:
    """Verify the registered bytes and extract every page without losing positions."""
    path = Path(report.local_file)
    try:
        stream = path.open("rb")
    except FileNotFoundError as exc:
        raise ValueError(f"Registered PDF file does not exist: {path}") from exc
    except OSError as exc:
        raise OSError(f"Cannot read registered PDF file {path}: {exc}") from exc

    with stream:
        try:
            digest = hashlib.sha256()
            while chunk := stream.read(65536):
                digest.update(chunk)
            stream.seek(0)
        except OSError as exc:
            raise OSError(f"Cannot read registered PDF file {path}: {exc}") from exc
        if digest.hexdigest() != report.file_sha256:
            raise ValueError(
                f"Registered PDF file has changed since registration: {path}. "
                "Restore the original file or review and re-register its metadata; "
                "use reports list to review existing records."
            )

        with _pdf_diagnostics() as diagnostics:
            try:
                reader = PdfReader(stream, strict=True)
            except Exception as exc:
                # Malformed PDFs can raise non-pypdf exceptions inside the parser.
                raise ValueError(f"Cannot read PDF {path}: damaged or invalid PDF: {exc}") from exc
            _check_diagnostics(diagnostics, f"PDF {path}")

            try:
                encrypted = reader.is_encrypted
                if encrypted and not reader.decrypt(""):
                    raise ValueError("an empty password did not unlock the document")
            except Exception as exc:
                raise ValueError(
                    f"Cannot read encrypted PDF {path}: {exc}. "
                    "Use an authorized, readable PDF; password entry is not supported."
                ) from exc
            _check_diagnostics(diagnostics, f"PDF {path}")

            try:
                page_count = len(reader.pages)
            except Exception as exc:
                raise ValueError(f"Cannot read pages in PDF {path}: {exc}") from exc
            _check_diagnostics(diagnostics, f"PDF {path}")

            for index in range(page_count):
                context = f"PDF page {index + 1} in {path}"
                try:
                    extracted = reader.pages[index].extract_text()
                except Exception as exc:
                    raise ValueError(f"Cannot extract text from {context}: {exc}") from exc
                _check_diagnostics(diagnostics, context)
                if extracted is None:
                    yield ""
                    continue
                if not isinstance(extracted, str):
                    raise ValueError(f"Cannot extract usable text from {context}.")
                if "\ufffd" in extracted:
                    raise ValueError(
                        f"Cannot reliably extract text from {context}: replacement characters "
                        "indicate a text or font encoding problem; no search results were returned."
                    )
                yield extracted


def _search_view(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Map collapsed whitespace and casefolded characters to raw source spans."""
    characters: list[str] = []
    source_spans: list[tuple[int, int]] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            start = index
            while index < len(text) and text[index].isspace():
                index += 1
            if characters and index < len(text):
                characters.append(" ")
                source_spans.append((start, index))
        else:
            folded = text[index].casefold()
            characters.extend(folded)
            source_spans.extend([(index, index + 1)] * len(folded))
            index += 1
    return "".join(characters), source_spans


def _excerpt(text: str, start: int, end: int) -> str:
    """Return up to 250 original characters centered around the source hit."""
    limit = 250
    if end - start >= limit:
        return text[start : start + limit]
    excerpt_start = max(0, start - (limit - (end - start)) // 2)
    excerpt_end = min(len(text), excerpt_start + limit)
    excerpt_start = max(0, excerpt_end - limit)
    return text[excerpt_start:excerpt_end]


def _select_report(
    ticker: str | int,
    fiscal_period: str,
    report_type: str | None,
    registry_path: str | Path,
) -> ReportRecord:
    """Select one registered report using the same validation for PDF tools."""
    normalized_ticker = normalize_hk_ticker(ticker)
    period = _require_text(fiscal_period, "fiscal_period")
    if report_type is not None:
        report_type = _require_text(report_type, "report_type")
        if report_type not in REPORT_TYPES:
            raise ValueError(f"report_type must be one of: {', '.join(REPORT_TYPES)}.")

    candidates = [
        record
        for record in list_reports(normalized_ticker, registry_path)
        if record.fiscal_period == period
        and (report_type is None or record.report_type == report_type)
    ]
    selection = f"ticker {normalized_ticker}, fiscal period {period!r}"
    if report_type is not None:
        selection += f", report type {report_type!r}"
    if not candidates:
        raise ValueError(f"No registered report found for {selection}; use reports list to review records.")
    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguous report selection: {len(candidates)} registered reports match {selection}. "
            "Use --type to narrow the selection and reports list to review records."
        )
    return candidates[0]


def _require_extractable_text(report: ReportRecord, has_text: bool) -> None:
    """Distinguish an unreadable text layer from a successful empty result."""
    if not has_text:
        raise ValueError(
            f"No extractable text in registered PDF {report.local_file}. "
            "It may be scanned, have no text layer, or use unsupported text/font encoding. "
            "This is not a normal zero-match result; OCR is not supported."
        )


def search_report(
    ticker: str | int,
    *,
    fiscal_period: str,
    keyword: str,
    report_type: str | None = None,
    max_results: int = 10,
    registry_path: str | Path = DEFAULT_REGISTRY,
) -> SearchResult:
    """Search one uniquely selected registered report, without financial inference.

    Matching is a literal, non-overlapping substring search within each page,
    with Unicode casefolding and collapsed whitespace. The excerpts retain the
    extracted text's spelling and spacing. Every page is searched to calculate
    total_matches, even when max_results limits the returned excerpts.

    Raises:
        TypeError: An input has an inappropriate type.
        ValueError: Selection is missing/ambiguous, limits are invalid, registered
            bytes changed, the PDF is unreadable, or no text can be extracted.
        OSError: The local PDF cannot be opened or read.
    """
    normalized_ticker = normalize_hk_ticker(ticker)
    period = _require_text(fiscal_period, "fiscal_period")
    search_keyword = _require_text(keyword, "keyword")
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise TypeError("max_results must be a positive integer between 1 and 1000.")
    if not 1 <= max_results <= 1000:
        raise ValueError("max_results must be a positive integer between 1 and 1000.")
    report = _select_report(normalized_ticker, period, report_type, registry_path)

    normalized_keyword = " ".join(search_keyword.split()).casefold()
    matches: list[SearchMatch] = []
    total_matches = 0
    has_text = False
    for page_number, raw_text in enumerate(_extract_pages(report), start=1):
        if not any(character.isprintable() and not character.isspace() for character in raw_text):
            continue
        has_text = True
        searchable, spans = _search_view(raw_text)
        position = 0
        while (position := searchable.find(normalized_keyword, position)) != -1:
            total_matches += 1
            if len(matches) < max_results:
                start = spans[position][0]
                end = spans[position + len(normalized_keyword) - 1][1]
                matches.append(SearchMatch(page_number, search_keyword, _excerpt(raw_text, start, end)))
            position += len(normalized_keyword)

    _require_extractable_text(report, has_text)
    return SearchResult(report, search_keyword, total_matches, tuple(matches))
