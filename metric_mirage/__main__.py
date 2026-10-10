"""Compare metric definitions and inspect local Hong Kong financial reports."""

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from importlib.resources import files
from pathlib import Path

from .candidates import extract_adjustment_candidates
from .confirmation import confirm_candidates
from .core import MetricDefinition, MetricDiff, compare_metrics
from .definition_io import load_definition as _load_definition, load_definition_snapshot
from .drift_report import build_drift_report, render_json, render_markdown
from .evidence import create_evidence_snapshot, load_evidence_snapshot
from .reports import DEFAULT_REGISTRY, REPORT_TYPES, list_reports, register_report
from .search import search_report
from .tickers import normalize_hk_ticker


def _load_demo() -> tuple[MetricDefinition, MetricDefinition]:
    resource = files("metric_mirage").joinpath("data", "synthetic_adjusted_ebitda.json")
    data: object = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("data_type") != "SYNTHETIC":
        raise ValueError("Demo data must be explicitly labeled SYNTHETIC.")
    definitions: list[MetricDefinition] = []
    for label in ("old", "new"):
        record = data.get(label)
        if not isinstance(record, dict):
            raise ValueError(f"Demo data must contain an object named {label!r}.")
        try:
            definition = MetricDefinition(**record)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid {label} demo definition: {error}") from error
        if not definition.source.startswith("SYNTHETIC:"):
            raise ValueError(f"The {label} demo source must be labeled SYNTHETIC.")
        definitions.append(definition)
    return definitions[0], definitions[1]


def _comparison_report(
    old: MetricDefinition, new: MetricDefinition, diff: MetricDiff
) -> dict[str, str | list[str] | bool]:
    """Serialize descriptive metadata and normalized set differences."""
    return {
        "company": new.company,
        "metric_name": new.metric_name,
        "old_period": old.fiscal_period,
        "new_period": new.fiscal_period,
        "added_adjustments": list(diff.added_adjustments),
        "removed_adjustments": list(diff.removed_adjustments),
        "unchanged_adjustments": list(diff.unchanged_adjustments),
        "has_definition_drift": diff.has_definition_drift,
    }


def _print_adjustments(label: str, names: tuple[str, ...]) -> None:
    print(f"{label}:")
    if names:
        for name in names:
            print(f"  - {name}")
    else:
        print("  (none)")


def _print_comparison(
    old: MetricDefinition,
    new: MetricDefinition,
    diff: MetricDiff,
    *,
    synthetic_demo: bool = False,
) -> None:
    title = "SYNTHETIC demonstration" if synthetic_demo else "comparison"
    print(f"Metric Mirage - {title}")
    print(f"Company: {new.company}")
    print(f"Metric: {new.metric_name}")
    print(f"Periods: {old.fiscal_period} -> {new.fiscal_period}")
    print(f"Source (old): {old.source}")
    print(f"Source (new): {new.source}")
    print()
    _print_adjustments("added", diff.added_adjustments)
    _print_adjustments("removed", diff.removed_adjustments)
    _print_adjustments("unchanged", diff.unchanged_adjustments)
    if not synthetic_demo:
        print(f"has_definition_drift: {str(diff.has_definition_drift).lower()}")


def _run_reports(args: argparse.Namespace) -> int:
    """Run a local report command; route input errors through main's handler."""
    if args.report_command == "add":
        record = register_report(
            args.local_file,
            ticker=args.ticker,
            company=args.company,
            report_type=args.report_type,
            fiscal_period=args.period,
            source_name=args.source,
            publication_date=args.publication_date,
            registry_path=args.registry,
        )
        print(f"Registered report: {record.ticker} | {record.company}")
        print(f"Type: {record.report_type}")
        print(f"Period: {record.fiscal_period}")
        print(f"Publication date: {record.publication_date or '(not provided)'}")
        print(f"Source: {record.source_name}")
        print(f"Local file: {record.local_file}")
        print(f"SHA256: {record.file_sha256}")
        print(f"Registry: {args.registry.expanduser().resolve()}")
        return 0

    if args.report_command == "search":
        result = search_report(
            args.ticker,
            fiscal_period=args.period,
            keyword=args.keyword,
            report_type=args.report_type,
            max_results=args.max_results,
            registry_path=args.registry,
        )
        record = result.report
        if args.format == "json":
            report = {
                "ticker": record.ticker,
                "company": record.company,
                "fiscal_period": record.fiscal_period,
                "source_name": record.source_name,
                "file_sha256": record.file_sha256,
                "keyword": result.keyword,
                "total_matches": result.total_matches,
                "matches": [asdict(match) for match in result.matches],
            }
            print(json.dumps(report, indent=2))
        else:
            print(f"Metric Mirage - PDF search for {record.ticker}")
            print(f"Company: {record.company}")
            print(f"Period: {record.fiscal_period}")
            print(f"Type: {record.report_type}")
            print(f"Source: {record.source_name}")
            print(f"Local file: {record.local_file}")
            print(f"SHA256: {record.file_sha256}")
            print(f"Keyword: {result.keyword}")
            print(f"Total matches: {result.total_matches}")
            print(f"Returned matches: {len(result.matches)}")
            if not result.matches:
                print("No matches found in the extracted text.")
            for match in result.matches:
                print(f"\nPDF page: {match.page_number}")
                print(f"Keyword: {match.keyword}")
                print(f"Excerpt: {match.excerpt}")
        return 0

    if args.report_command == "candidates":
        result = extract_adjustment_candidates(
            args.ticker,
            fiscal_period=args.period,
            report_type=args.report_type,
            registry_path=args.registry,
        )
        record = result.report
        if args.format == "json":
            report = {
                "ticker": record.ticker,
                "company": record.company,
                "fiscal_period": record.fiscal_period,
                "metric_name": result.metric_name,
                "source_name": record.source_name,
                "file_sha256": record.file_sha256,
                "candidates": [asdict(candidate) for candidate in result.candidates],
            }
            print(json.dumps(report, indent=2))
        else:
            print(f"Metric Mirage - adjustment candidates for {record.ticker}")
            print(f"Company: {record.company}")
            print(f"Period: {record.fiscal_period}")
            print(f"Type: {record.report_type}")
            print(f"Metric: {result.metric_name}")
            print(f"Source: {record.source_name}")
            print(f"Local file: {record.local_file}")
            print(f"SHA256: {record.file_sha256}")
            print(f"Candidates: {len(result.candidates)}")
            print("Candidates require review; they are not a confirmed metric definition.")
            if not result.candidates:
                print("No supported adjustment candidates with explicit metric evidence found.")
            for candidate in result.candidates:
                print(f"\nPDF page: {candidate.page_number}")
                print(f"Adjustment: {candidate.canonical_name}")
                print(f"Matched text: {candidate.matched_text}")
                print(f"Evidence: {candidate.evidence_excerpt}")
        return 0

    if args.report_command == "confirm":
        result = confirm_candidates(
            args.ticker,
            fiscal_period=args.period,
            selected_adjustments=args.select,
            output_path=args.output,
            report_type=args.report_type,
            registry_path=args.registry,
        )
        print("Metric Mirage - user-selected metric definition export")
        print(f"Company: {result.definition.company}")
        print(f"Period: {result.definition.fiscal_period}")
        print(f"Metric: {result.definition.metric_name}")
        print("Selected adjustments:")
        for name in result.definition.adjustments:
            pages = sorted({
                candidate.page_number for candidate in result.selected_candidates
                if candidate.canonical_name == name
            })
            print(f"  - {name} | PDF pages: {', '.join(str(page) for page in pages)}")
        print(f"Output: {result.output_path}")
        print("User selection does not independently verify a complete financial formula.")
        return 0

    if args.report_command == "evidence":
        snapshot = create_evidence_snapshot(
            args.ticker, fiscal_period=args.period,
            definition_path=args.definition, output_path=args.output,
            report_type=args.report_type, registry_path=args.registry,
        )
        print(f"Metric Mirage - evidence snapshot for {snapshot['ticker']}")
        print(f"Company: {snapshot['company']}")
        print(f"Period: {snapshot['fiscal_period']}")
        print(f"Metric: {snapshot['metric_name']}")
        print(f"Definition SHA256: {snapshot['definition_sha256']}")
        print(f"Report SHA256: {snapshot['report_sha256']}")
        for item in snapshot["items"]:
            pages = ", ".join(str(entry["page_number"]) for entry in item["evidence"])
            print(f"  - {item['canonical_name']} | PDF pages: {pages}")
        print(f"Output: {args.output.absolute()}")
        return 0

    ticker = normalize_hk_ticker(args.ticker)
    records = list_reports(ticker, registry_path=args.registry)
    if args.format == "json":
        print(json.dumps([asdict(record) for record in records], indent=2))
    else:
        print(f"Metric Mirage - local reports for {ticker}")
        if not records:
            print("  (none)")
        for record in records:
            print(f"\nCompany: {record.company}")
            print(f"Type: {record.report_type}")
            print(f"Period: {record.fiscal_period}")
            print(f"Publication date: {record.publication_date or '(not provided)'}")
            print(f"Source: {record.source_name}")
            print(f"Local file: {record.local_file}")
            print(f"SHA256: {record.file_sha256}")
    return 0


def _max_results(value: str) -> int:
    """Keep CLI result limits within a positive, bounded integer range."""
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("max-results must be an integer between 1 and 1000.") from error
    if not 1 <= number <= 1000:
        raise argparse.ArgumentTypeError("max-results must be an integer between 1 and 1000.")
    return number


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m metric_mirage",
        description="Financial Metric Forensics for the Hong Kong stock market.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run the synthetic comparison")
    comparison = commands.add_parser("compare", help="compare two metric definition JSON files")
    comparison.add_argument("old_file", type=Path, help="JSON definition for the old period")
    comparison.add_argument("new_file", type=Path, help="JSON definition for the new period")
    comparison.add_argument(
        "--format", choices=("text", "json"), default="text", help="output format (default: text)"
    )
    drift = commands.add_parser("drift", help="report changes between confirmed FY definitions")
    drift.add_argument("old_file", type=Path, help="JSON definition for the old fiscal year")
    drift.add_argument("new_file", type=Path, help="JSON definition for the new fiscal year")
    drift.add_argument(
        "--format", choices=("markdown", "json"), default="markdown",
        help="output format (default: markdown)",
    )
    drift.add_argument("--old-evidence", type=Path, help="snapshot for the old definition")
    drift.add_argument("--new-evidence", type=Path, help="snapshot for the new definition")
    reports = commands.add_parser("reports", help="register or inspect local financial reports")
    report_commands = reports.add_subparsers(dest="report_command", required=True)
    registration = report_commands.add_parser("add", help="register an existing local PDF")
    registration.add_argument("local_file", type=Path, help="local PDF to register in place")
    registration.add_argument("--ticker", required=True, help="Hong Kong stock code")
    registration.add_argument("--company", required=True, help="company name")
    registration.add_argument("--type", dest="report_type", choices=REPORT_TYPES, required=True)
    registration.add_argument("--period", required=True, help="fiscal period label")
    registration.add_argument("--source", required=True, help="report source name")
    registration.add_argument("--publication-date", help="optional publication date (YYYY-MM-DD)")
    registration.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY, help="local JSON registry path"
    )
    listing = report_commands.add_parser("list", help="list reports for a Hong Kong stock code")
    listing.add_argument("ticker", help="Hong Kong stock code")
    listing.add_argument("--format", choices=("text", "json"), default="text")
    listing.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY, help="local JSON registry path"
    )
    search = report_commands.add_parser("search", help="search extracted PDF text for a keyword")
    search.add_argument("ticker", help="Hong Kong stock code")
    search.add_argument("--period", required=True, help="exact registered fiscal period label")
    search.add_argument("--keyword", required=True, help="literal keyword or phrase")
    search.add_argument("--type", dest="report_type", choices=REPORT_TYPES, help="optional report type filter")
    search.add_argument("--max-results", type=_max_results, default=10, help="matches to return (1-1000; default: 10)")
    search.add_argument("--format", choices=("text", "json"), default="text")
    search.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY, help="local JSON registry path"
    )
    candidates = report_commands.add_parser(
        "candidates", help="extract evidence for Adjusted Net Profit adjustment candidates"
    )
    candidates.add_argument("ticker", help="Hong Kong stock code")
    candidates.add_argument("--period", required=True, help="exact registered fiscal period label")
    candidates.add_argument(
        "--type", dest="report_type", choices=REPORT_TYPES, help="optional report type filter"
    )
    candidates.add_argument("--format", choices=("text", "json"), default="text")
    candidates.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY, help="local JSON registry path"
    )
    confirmation = report_commands.add_parser(
        "confirm", help="export explicitly selected candidates as a metric definition"
    )
    confirmation.add_argument("ticker", help="Hong Kong stock code")
    confirmation.add_argument("--period", required=True, help="exact registered fiscal period label")
    confirmation.add_argument(
        "--select", action="append", required=True, metavar="CANONICAL_NAME",
        help="candidate to retain; repeat to select several items",
    )
    confirmation.add_argument("--output", type=Path, required=True, help="new UTF-8 JSON output file")
    confirmation.add_argument(
        "--type", dest="report_type", choices=REPORT_TYPES, help="optional report type filter"
    )
    confirmation.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY, help="local JSON registry path"
    )
    evidence = report_commands.add_parser(
        "evidence", help="save PDF evidence for confirmed adjustment items"
    )
    evidence.add_argument("ticker", help="Hong Kong stock code")
    evidence.add_argument("--period", required=True, help="exact registered fiscal period label")
    evidence.add_argument("--definition", type=Path, required=True, help="confirmed definition JSON")
    evidence.add_argument("--output", type=Path, required=True, help="new UTF-8 evidence JSON file")
    evidence.add_argument(
        "--type", dest="report_type", choices=REPORT_TYPES, help="optional report type filter"
    )
    evidence.add_argument(
        "--registry", type=Path, default=DEFAULT_REGISTRY, help="local JSON registry path"
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "reports":
            return _run_reports(args)
        old_evidence = new_evidence = None
        if args.command == "drift" and (
            (args.old_evidence is None) != (args.new_evidence is None)
        ):
            raise ValueError("Provide both --old-evidence and --new-evidence, or neither.")
        if args.command == "demo":
            old, new = _load_demo()
        elif args.command == "drift" and args.old_evidence is not None:
            old, old_digest = load_definition_snapshot(args.old_file)
            new, new_digest = load_definition_snapshot(args.new_file)
            old_evidence = load_evidence_snapshot(
                args.old_evidence, definition=old, definition_sha256=old_digest,
            )
            new_evidence = load_evidence_snapshot(
                args.new_evidence, definition=new, definition_sha256=new_digest,
            )
        else:
            old = _load_definition(args.old_file)
            new = _load_definition(args.new_file)
        if args.command == "drift":
            report = build_drift_report(
                old, new, old_evidence=old_evidence, new_evidence=new_evidence,
            )
            print(render_json(report) if args.format == "json" else render_markdown(report))
            return 0
        diff = compare_metrics(old, new)
    except (OSError, TypeError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.command == "compare" and args.format == "json":
        print(json.dumps(_comparison_report(old, new, diff), indent=2))
    else:
        _print_comparison(old, new, diff, synthetic_demo=args.command == "demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
