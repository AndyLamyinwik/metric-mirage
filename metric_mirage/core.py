"""Validated metric definitions and deterministic adjustment-set comparison."""

from collections.abc import Sequence
from dataclasses import dataclass


def _require_text(value: object, field_name: str) -> str:
    """Validate a required text field without guessing its meaning."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string.")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty or whitespace-only.")
    return stripped


def _normalize_name(name: str) -> str:
    """Use case-insensitive names with consistent whitespace as identities."""
    return " ".join(name.split()).casefold()


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    """One reported metric definition, with adjustment names as literal labels.

    Adjustment sequences are copied into a tuple so later edits to an input
    list cannot change a definition. Normalized names become identities during
    comparison; punctuation and wording retain their literal meaning.
    """

    company: str
    metric_name: str
    fiscal_period: str
    adjustments: Sequence[str]
    source: str

    def __post_init__(self) -> None:
        for field_name in ("company", "metric_name", "fiscal_period", "source"):
            object.__setattr__(
                self, field_name, _require_text(getattr(self, field_name), field_name)
            )

        if isinstance(self.adjustments, (str, bytes)) or not isinstance(
            self.adjustments, Sequence
        ):
            raise TypeError("adjustments must be a sequence of names, not a string or scalar.")

        names = tuple(
            _require_text(name, f"adjustments[{index}]")
            for index, name in enumerate(self.adjustments)
        )
        object.__setattr__(self, "adjustments", names)


@dataclass(frozen=True, slots=True)
class MetricDiff:
    """Sorted tuples of normalized adjustment identities in each category."""

    added_adjustments: tuple[str, ...]
    removed_adjustments: tuple[str, ...]
    unchanged_adjustments: tuple[str, ...]

    @property
    def has_definition_drift(self) -> bool:
        """Whether normalized adjustment sets differ, without financial inference."""
        return bool(self.added_adjustments or self.removed_adjustments)


def compare_metrics(old: MetricDefinition, new: MetricDefinition) -> MetricDiff:
    """Compare adjustment sets for the same company and metric.

    Names are casefolded, leading/trailing whitespace is removed, and internal
    whitespace is collapsed. Duplicate normalized names count as one item.
    Period labels and sources are descriptive metadata; no financial values,
    chronological ordering, or semantic equivalence are inferred.

    Raises:
        TypeError: Either argument is not a MetricDefinition.
        ValueError: The company or metric names are incompatible.
    """
    if not isinstance(old, MetricDefinition):
        raise TypeError("old must be a MetricDefinition.")
    if not isinstance(new, MetricDefinition):
        raise TypeError("new must be a MetricDefinition.")

    if _normalize_name(old.company) != _normalize_name(new.company):
        raise ValueError(
            f"Cannot compare different companies: {old.company!r} and {new.company!r}."
        )
    if _normalize_name(old.metric_name) != _normalize_name(new.metric_name):
        raise ValueError(
            f"Cannot compare different metrics: {old.metric_name!r} and {new.metric_name!r}."
        )

    old_names = {_normalize_name(name) for name in old.adjustments}
    new_names = {_normalize_name(name) for name in new.adjustments}
    return MetricDiff(
        added_adjustments=tuple(sorted(new_names - old_names)),
        removed_adjustments=tuple(sorted(old_names - new_names)),
        unchanged_adjustments=tuple(sorted(old_names & new_names)),
    )
