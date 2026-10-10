"""Conservative adjustment candidates tied to explicit Adjusted Net Profit text."""

from dataclasses import dataclass
from pathlib import Path
import re

from .reports import DEFAULT_REGISTRY, ReportRecord
from .search import (
    _extract_pages,
    _require_extractable_text,
    _search_view,
    _select_report,
)


_METRIC_NAME = "Adjusted Net Profit"
_CONTEXT_LIMIT = 600
_METRIC_ALIASES = (
    "經調整淨利潤",
    "经调整净利润",
    "Adjusted Net Profit",
    "Non-HKFRS Adjusted Net Profit",
)
_ADJUSTMENT_ALIASES: dict[str, tuple[str, ...]] = {
    "share_based_payments": (
        "股份支付開支", "股份支付費用", "股份支付开支", "股份支付费用",
        "share-based payments", "share-based payment expenses",
        "share-based compensation", "share based payments",
    ),
    "fair_value_changes": (
        "公允價值變動", "公允價值變化", "公允价值变动", "公允价值变化",
        "fair value changes", "changes in fair value",
    ),
    "impairment_losses": (
        "減值虧損", "減值損失", "减值亏损", "减值损失", "impairment losses",
    ),
    "listing_expenses": (
        "上市開支", "上市費用", "上市开支", "上市费用", "listing expenses", "listing costs",
    ),
    "acquisition_related_expenses": (
        "收購相關開支", "收購相關費用", "收购相关开支", "收购相关费用",
        "acquisition-related expenses", "acquisition related expenses",
    ),
    "amortisation_of_intangible_assets": (
        "無形資產攤銷", "无形资产摊销",
        "amortisation of intangible assets", "amortization of intangible assets",
    ),
}


def _alias_pattern(alias: str) -> str:
    """Use literal aliases, preventing English matches inside larger words."""
    normalized = " ".join(alias.split()).casefold()
    escaped = re.escape(normalized)
    if normalized[0].isascii() and normalized[0].isalnum():
        escaped = r"(?<![a-z0-9_-])" + escaped
    if normalized[-1].isascii() and normalized[-1].isalnum():
        escaped += r"(?![a-z0-9_])"
    return escaped


_METRIC_PATTERN = re.compile("|".join(
    _alias_pattern(alias) for alias in sorted(_METRIC_ALIASES, key=len, reverse=True)
))
_ITEM_PATTERN = re.compile("|".join(
    f"(?P<{name}>" + "|".join(
        _alias_pattern(alias) for alias in sorted(aliases, key=len, reverse=True)
    ) + ")"
    for name, aliases in _ADJUSTMENT_ALIASES.items()
))
_PARAGRAPH_BREAK = re.compile(r"\n[^\S\n]*\n")
_PREFIX = r"\s*[\"'”’）)]?\s*(?:[:：]\s*)?"
_ENGLISH_RELATION = (
    r"(?:excludes|excluding)(?![a-z0-9_])"
    r"|(?:is )?(?:calculated|computed|defined) by excluding(?![a-z0-9_])"
    r"|(?:is )?(?:calculated|computed|defined) as (?:net )?profit"
    r"(?: for the (?:year|period))? excluding(?![a-z0-9_])"
    r"|(?:'s )?adjustments? (?:include|comprise|are)(?![a-z0-9_])"
    r"|(?:reconciliation|adjustments?)\s*[:：]"
)
_CHINESE_RELATION = (
    r"(?:剔除|排除|扣除|不包括|不包含)"
    r"|(?:的)?(?:調整項目|调整项目)(?:包括|包含|為|为|如下|[:：])"
    r"|(?:的)?(?:調節表|调节表|調節項目|调节项目)\s*[:：]"
)
_RELATION_PATTERN = re.compile(
    _PREFIX + "(?:" + _ENGLISH_RELATION + "|" + _CHINESE_RELATION + ")"
)
_LIST_PREFIX = re.compile(
    r"\s*(?:(?:the )?(?:effects|impact) of\s+)?"
    r"(?:(?:the following(?: adjustments| items)?|以下(?:項目|项目)?|下列(?:項目|项目)?)\s*)?"
    r"[:：]?\s*(?:[-•*]\s*)?"
)
_CONJUNCTION = re.compile(r"(?:and|or)(?![a-z0-9_])\s*|(?:及|和|與|与)")
_BULLET = re.compile(r"[-•*]\s+")
_HEADING_PREFIX = re.compile(r"reconciliation (?:of (?:net )?profit )?to ")
_UNCERTAIN_PREFIX = re.compile(
    r"(?<![a-z0-9_])(?:not|never|incorrect|untrue|false|wrong|cannot|can't|"
    r"whether|if|may|might|could|would)(?![a-z0-9_])"
    r"|(?:並非|并非|並不|并不|不是|不應|不应|不能|不會|不会|假如|如果|"
    r"錯誤|错误|不正確|不正确|是否)"
)
_RELATIVE_CONTINUATION = re.compile(
    r"(?:which|that|these|those|they|it|but|however)(?![a-z0-9_])"
    r"|(?:該項|该项|其實|其实|惟該|惟该|但該|但该)"
)
_SENTENCE_END = re.compile(r"[.。!?！？]")


@dataclass(frozen=True, slots=True)
class AdjustmentCandidate:
    """One dictionary label with an exact raw match and same-page evidence."""

    canonical_name: str
    matched_text: str
    page_number: int
    evidence_excerpt: str


@dataclass(frozen=True, slots=True)
class CandidateResult:
    """Unconfirmed candidates and their existing registered report provenance."""

    report: ReportRecord
    metric_name: str
    candidates: tuple[AdjustmentCandidate, ...]


def _relation_start(
    raw_text: str,
    searchable: str,
    spans: list[tuple[int, int]],
    metric: re.Match[str],
) -> tuple[int, int] | None:
    """Find a directly attached positive relation or an explicit list heading."""
    preceding = searchable[max(0, metric.start() - _CONTEXT_LIMIT):metric.start()]
    preceding = re.split(r"[.。!?！？;；]", preceding)[-1]
    if _UNCERTAIN_PREFIX.search(preceding):
        return None
    relation = _RELATION_PATTERN.match(searchable, metric.end())
    if relation is not None:
        return metric.start(), relation.end()

    # An English reconciliation heading can name the metric after "to".
    # Requiring the start of its raw line and a colon avoids prose proximity.
    metric_raw_start = spans[metric.start()][0]
    line_start = raw_text.rfind("\n", 0, metric_raw_start) + 1
    prefix = " ".join(raw_text[line_start:metric_raw_start].split()).casefold() + " "
    colon = re.match(r"\s*[\"'”’)]?\s*[:：]", searchable[metric.end():])
    if _HEADING_PREFIX.fullmatch(prefix) is None or colon is None:
        return None
    evidence_start = metric.start()
    while evidence_start and spans[evidence_start - 1][0] >= line_start:
        evidence_start -= 1
    return evidence_start, metric.end() + colon.end()


def _after_item(
    raw_text: str,
    searchable: str,
    spans: list[tuple[int, int]],
    end: int,
) -> tuple[bool, int | None]:
    """Accept only closed list items; trailing narrative cannot claim a label."""
    next_position = end
    while next_position < len(searchable) and searchable[next_position].isspace():
        next_position += 1
    if next_position == len(searchable) or searchable[next_position] in ".。!！":
        return True, None
    if searchable[next_position] in ",，、;；":
        next_position += 1
        while next_position < len(searchable) and searchable[next_position].isspace():
            next_position += 1
        # A comma-attached qualifier can deny the apparent exclusion. Omit
        # such an item rather than truncate its evidence before the denial.
        if _RELATIVE_CONTINUATION.match(searchable, next_position):
            return False, None
        conjunction = _CONJUNCTION.match(searchable, next_position)
        if conjunction:
            next_position = conjunction.end()
        if _RELATIVE_CONTINUATION.match(searchable, next_position):
            return False, None
        return True, next_position
    conjunction = _CONJUNCTION.match(searchable, next_position)
    if conjunction:
        if _RELATIVE_CONTINUATION.match(searchable, conjunction.end()):
            return False, None
        return True, conjunction.end()

    # Plain lines in an explicit list or bullet lines can separate entries.
    # The next alias must still be a complete item, not the start of prose.
    gap = raw_text[spans[end - 1][1]:spans[next_position][0]]
    if "\n" in gap:
        bullet = _BULLET.match(searchable, next_position)
        if bullet:
            return True, bullet.end()
        if _ITEM_PATTERN.match(searchable, next_position):
            return True, next_position
    if searchable[next_position] == "等":
        remainder = searchable[next_position + 1:].lstrip()
        if not remainder or remainder[0] in ".。!！":
            return True, None
    return False, None


def _paragraph_candidates(
    raw_text: str, page_number: int, offset: int
) -> list[tuple[int, AdjustmentCandidate]]:
    """Parse explicit lists without scanning arbitrary nearby financial prose."""
    searchable, spans = _search_view(raw_text)
    candidates: list[tuple[int, AdjustmentCandidate]] = []
    for metric in _METRIC_PATTERN.finditer(searchable):
        relation = _relation_start(raw_text, searchable, spans, metric)
        if relation is None:
            continue
        evidence_start, list_start = relation
        sentence_end = _SENTENCE_END.search(
            searchable, list_start, min(len(searchable), metric.start() + _CONTEXT_LIMIT),
        )
        if sentence_end is not None and sentence_end.group() in "?？":
            continue
        prefix = _LIST_PREFIX.match(searchable, list_start)
        assert prefix is not None  # The optional prefix always matches.
        position = prefix.end()
        while position < len(searchable):
            item = _ITEM_PATTERN.match(searchable, position)
            if item is None:
                break
            raw_start = spans[item.start()][0]
            raw_end = spans[item.end() - 1][1]
            evidence_raw_start = spans[evidence_start][0]
            if raw_end - evidence_raw_start > _CONTEXT_LIMIT:
                break
            closed, next_position = _after_item(raw_text, searchable, spans, item.end())
            if not closed:
                break
            assert item.lastgroup is not None
            candidates.append((offset + raw_start, AdjustmentCandidate(
                canonical_name=item.lastgroup,
                matched_text=raw_text[raw_start:raw_end],
                page_number=page_number,
                evidence_excerpt=raw_text[evidence_raw_start:raw_end],
            )))
            if next_position is None:
                break
            position = next_position
            while position < len(searchable) and searchable[position].isspace():
                position += 1
    return candidates


def _page_candidates(raw_text: str, page_number: int) -> list[AdjustmentCandidate]:
    """Preserve first reliable occurrence of each canonical label per page."""
    located: list[tuple[int, AdjustmentCandidate]] = []
    paragraph_start = 0
    for boundary in _PARAGRAPH_BREAK.finditer(raw_text):
        located.extend(_paragraph_candidates(
            raw_text[paragraph_start:boundary.start()], page_number, paragraph_start,
        ))
        paragraph_start = boundary.end()
    located.extend(_paragraph_candidates(raw_text[paragraph_start:], page_number, paragraph_start))
    located.sort(key=lambda match: (match[0], match[1].canonical_name))
    seen: set[str] = set()
    result: list[AdjustmentCandidate] = []
    for _, candidate in located:
        if candidate.canonical_name not in seen:
            result.append(candidate)
            seen.add(candidate.canonical_name)
    return result


def extract_adjustment_candidates(
    ticker: str | int,
    *,
    fiscal_period: str,
    report_type: str | None = None,
    registry_path: str | Path = DEFAULT_REGISTRY,
) -> CandidateResult:
    """Return unconfirmed dictionary candidates from explicit same-page lists.

    Only Adjusted Net Profit and its four documented names are recognized.
    English names are casefolded and whitespace is collapsed for matching.
    Original aliases and evidence remain raw PDF text. Evidence must include
    the metric, an explicit exclusion/adjustment/reconciliation relation, and
    the complete item within 600 raw characters, without crossing a blank
    paragraph or sentence. Proximity alone and unsupported list syntax yield
    no candidate. The function does not write definitions or infer drift.

    Report selection, registered-byte checks, PDF extraction and unreadable or
    absent text errors are shared with search_report. An otherwise readable
    document with no supported candidates returns an empty tuple.
    """
    report = _select_report(ticker, fiscal_period, report_type, registry_path)
    candidates: list[AdjustmentCandidate] = []
    has_text = False
    for page_number, raw_text in enumerate(_extract_pages(report), start=1):
        if not any(character.isprintable() and not character.isspace() for character in raw_text):
            continue
        has_text = True
        candidates.extend(_page_candidates(raw_text, page_number))
    _require_extractable_text(report, has_text)
    return CandidateResult(report, _METRIC_NAME, tuple(candidates))
