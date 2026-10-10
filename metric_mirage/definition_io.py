"""Read the existing definition schema, optionally binding it to file bytes."""

import hashlib
import json
from pathlib import Path

from .core import MetricDefinition
from .reports import _path


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}.")
        result[key] = value
    return result


def _parse_definition(
    text: str, path: Path, *, reject_duplicate_keys: bool = False,
) -> MetricDefinition:
    try:
        data: object = json.loads(
            text, object_pairs_hook=_unique_object if reject_duplicate_keys else None,
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid JSON in '{path}': {error.msg} "
            f"(line {error.lineno}, column {error.colno})."
        ) from error
    except ValueError as error:
        raise ValueError(f"Invalid JSON in '{path}': {error}") from error
    if not isinstance(data, dict):
        raise ValueError(
            f"Invalid metric definition in '{path}': "
            "expected a single MetricDefinition JSON object."
        )
    try:
        return MetricDefinition(**data)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid metric definition in '{path}': {error}") from error


def load_definition(path: Path) -> MetricDefinition:
    """Preserve the existing CLI loader's text reads and user-facing errors."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as error:
        raise ValueError(f"JSON file not found: '{path}'.") from error
    except OSError as error:
        raise ValueError(f"Cannot read JSON file '{path}': {error}.") from error
    except UnicodeError as error:
        raise ValueError(f"Cannot read JSON file '{path}': expected UTF-8 text.") from error
    return _parse_definition(text, path)


def load_definition_snapshot(path: str | Path) -> tuple[MetricDefinition, str]:
    """Hash and parse the same single read, including UTF-8 BOM/file whitespace."""
    target = _path(path, "definition_path")
    try:
        raw = target.read_bytes()
        text = raw.decode("utf-8-sig")
    except FileNotFoundError as error:
        raise ValueError(f"JSON file not found: '{target}'.") from error
    except OSError as error:
        raise ValueError(f"Cannot read JSON file '{target}': {error}.") from error
    except UnicodeError as error:
        raise ValueError(f"Cannot read JSON file '{target}': expected UTF-8 text.") from error
    return _parse_definition(text, target, reject_duplicate_keys=True), hashlib.sha256(raw).hexdigest()
