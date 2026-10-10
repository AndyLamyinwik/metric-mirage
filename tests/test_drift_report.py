"""Exercise cross-period reports with synthetic definitions and local JSON files."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage.core import MetricDefinition, compare_metrics
from metric_mirage.drift_report import build_drift_report, render_json, render_markdown


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def definition(
    period: str,
    adjustments: list[str],
    *,
    company: str = "Synthetic HK Company",
    metric_name: str = "Adjusted Net Profit",
    source: str = "SYNTHETIC: user-selected test adjustments",
) -> MetricDefinition:
    return MetricDefinition(company, metric_name, period, adjustments, source)


class DriftReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old = definition("FY2024", ["Share_Based_Payments", "Impairment_Losses"])
        self.new = definition("FY2025", ["Share_Based_Payments", "Listing_Expenses"])

    def test_added_removed_and_unchanged_adjustments(self) -> None:
        report = build_drift_report(self.old, self.new)
        self.assertEqual(report["added_adjustments"], ["listing_expenses"])
        self.assertEqual(report["removed_adjustments"], ["impairment_losses"])
        self.assertEqual(report["unchanged_adjustments"], ["share_based_payments"])
        self.assertIs(report["has_definition_drift"], True)

    def test_identical_sets_do_not_create_drift(self) -> None:
        report = build_drift_report(self.old, definition("FY2025", list(self.old.adjustments)))
        self.assertIs(report["has_definition_drift"], False)
        self.assertEqual(report["added_adjustments"], [])
        self.assertEqual(report["removed_adjustments"], [])
        markdown = render_markdown(report)
        self.assertIn("false", markdown)
        self.assertNotIn("| ADDED |", markdown)
        self.assertNotIn("| REMOVED |", markdown)

    def test_empty_sets_have_an_empty_comparison_table(self) -> None:
        report = build_drift_report(definition("FY2024", []), definition("FY2025", []))
        self.assertIs(report["has_definition_drift"], False)
        self.assertEqual(report["unchanged_adjustments"], [])
        markdown = render_markdown(report)
        self.assertIn("| Adjustment | Old Period | New Period | Status |", markdown)
        self.assertNotIn("| ADDED |", markdown)
        self.assertNotIn("| REMOVED |", markdown)
        self.assertNotIn("| UNCHANGED |", markdown)

    def test_normalization_deduplication_and_sorting_use_existing_rules(self) -> None:
        old = definition("FY2024", [" Zeta   Shared ", " Alpha Removed ", "ZETA SHARED"])
        new = definition("FY2025", ["Beta Added", " zeta\tshared ", "Alpha Added"])
        report = build_drift_report(old, new)
        reordered = build_drift_report(
            definition("FY2024", list(reversed(old.adjustments))),
            definition("FY2025", list(reversed(new.adjustments))),
        )
        self.assertEqual(report, reordered)
        self.assertEqual(report["added_adjustments"], ["alpha added", "beta added"])
        self.assertEqual(report["removed_adjustments"], ["alpha removed"])
        self.assertEqual(report["unchanged_adjustments"], ["zeta shared"])
        self.assertEqual(render_markdown(report), render_markdown(reordered))
        self.assertEqual(render_json(report), render_json(reordered))

    def test_comparison_delegates_to_existing_compare_metrics(self) -> None:
        with patch("metric_mirage.drift_report.compare_metrics", wraps=compare_metrics) as compare:
            build_drift_report(self.old, self.new)
        compare.assert_called_once_with(self.old, self.new)

    def test_reversed_years_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "later|order"):
            build_drift_report(self.new, self.old)

    def test_same_year_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "later|same|equal"):
            build_drift_report(self.old, definition("FY2024", ["Other"]))

    def test_unsupported_period_formats_are_not_guessed(self) -> None:
        for period in ("FY 2024", "fy2024", "FY24", "2024", "H12024", "FY２０２４", "FY2024\nextra"):
            with self.subTest(period=period), self.assertRaisesRegex(ValueError, "FY"):
                build_drift_report(definition(period, []), self.new)
        with self.assertRaisesRegex(ValueError, "FY"):
            build_drift_report(self.old, definition("FY 2025", []))

    def test_incompatible_company_or_metric_is_rejected(self) -> None:
        for change, message in (({"company": "Another Synthetic Company"}, "companies"),
                                ({"metric_name": "Adjusted EBITDA"}, "metrics")):
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                build_drift_report(self.old, definition("FY2025", [], **change))

    def test_markdown_table_has_sorted_rows_and_presence_status(self) -> None:
        markdown = render_markdown(build_drift_report(self.old, self.new))
        self.assertIn("| Adjustment | Old Period | New Period | Status |", markdown)
        rows = [
            "| impairment\\_losses | Yes | No | REMOVED |",
            "| listing\\_expenses | No | Yes | ADDED |",
            "| share\\_based\\_payments | Yes | Yes | UNCHANGED |",
        ]
        for row in rows:
            self.assertIn(row, markdown)
        self.assertEqual([markdown.index(row) for row in rows], sorted(markdown.index(row) for row in rows))

    def test_sources_are_preserved_without_interpreting_their_format(self) -> None:
        old_source = "SYNTHETIC: 發行人來源 | SHA256: " + "a" * 64 + "\nopaque ``` evidence"
        new_source = "SYNTHETIC: arbitrary delimiters [] | SHA256: " + "b" * 64
        report = build_drift_report(
            definition("FY2024", [], source=old_source),
            definition("FY2025", [], source=new_source),
        )
        decoded = json.loads(render_json(report))
        self.assertEqual(decoded["old_source"], old_source)
        self.assertEqual(decoded["new_source"], new_source)
        markdown = render_markdown(report)
        self.assertIn(old_source, markdown)
        self.assertIn(new_source, markdown)

    def test_json_report_contains_required_metadata_and_exact_categories(self) -> None:
        report = json.loads(render_json(build_drift_report(self.old, self.new)))
        self.assertEqual(report["company"], self.old.company)
        self.assertEqual(report["metric_name"], "Adjusted Net Profit")
        self.assertEqual(report["old_period"], "FY2024")
        self.assertEqual(report["new_period"], "FY2025")
        self.assertEqual(set(report), {
            "company", "metric_name", "old_period", "new_period", "added_adjustments",
            "removed_adjustments", "unchanged_adjustments", "has_definition_drift",
            "old_source", "new_source",
        })

    def test_methodology_note_describes_evidence_limits(self) -> None:
        markdown = render_markdown(build_drift_report(self.old, self.new))
        note = markdown[markdown.index("Methodology Note"):].casefold()
        self.assertIn("user-confirmed", note)
        self.assertIn("complete", note)
        self.assertIn("formula", note)
        self.assertIn("fraud", note)
        self.assertIn("investment", note)


class DriftReportCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage drift tests ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.old_path = self.directory / "definition_2024.json"
        self.new_path = self.directory / "definition_2025.json"
        self.old_data: dict[str, object] = {
            "company": "Synthetic HK Company", "metric_name": "Adjusted Net Profit",
            "fiscal_period": "FY2024", "adjustments": ["share_based_payments", "impairment_losses"],
            "source": "SYNTHETIC: issuer source 舊年度 | SHA256: " + "a" * 64,
        }
        self.new_data: dict[str, object] = {
            **self.old_data, "fiscal_period": "FY2025",
            "adjustments": ["listing_expenses", "share_based_payments"],
            "source": "SYNTHETIC: issuer source 新年度 | SHA256: " + "b" * 64,
        }
        self.write_records()

    def write_records(self) -> None:
        for path, data in ((self.old_path, self.old_data), (self.new_path, self.new_data)):
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8", check=False, timeout=15,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def drift(self, *options: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("drift", str(self.old_path), str(self.new_path), *options)

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)

    def test_default_markdown_works_end_to_end_with_two_synthetic_files(self) -> None:
        completed = self.drift()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertIn("| Adjustment | Old Period | New Period | Status |", completed.stdout)
        self.assertIn("FY2024", completed.stdout)
        self.assertIn("FY2025", completed.stdout)
        self.assertIn(str(self.old_data["source"]), completed.stdout)
        self.assertIn(str(self.new_data["source"]), completed.stdout)
        self.assertIn("Methodology Note", completed.stdout)

    def test_explicit_markdown_matches_the_default(self) -> None:
        default = self.drift()
        explicit = self.drift("--format", "markdown")
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(default.stdout, explicit.stdout)

    def test_json_output_is_pure_parseable_and_preserves_unicode_sources(self) -> None:
        completed = self.drift("--format", "json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        report = json.loads(completed.stdout)
        self.assertEqual(report["added_adjustments"], ["listing_expenses"])
        self.assertEqual(report["removed_adjustments"], ["impairment_losses"])
        self.assertEqual(report["unchanged_adjustments"], ["share_based_payments"])
        self.assertIs(report["has_definition_drift"], True)
        self.assertEqual(report["old_source"], self.old_data["source"])
        self.assertEqual(report["new_source"], self.new_data["source"])

    def test_reversed_same_and_unsupported_years_fail_cleanly(self) -> None:
        for old_period, new_period in (("FY2025", "FY2024"), ("FY2024", "FY2024"), ("FY 2024", "FY2025")):
            with self.subTest(old_period=old_period, new_period=new_period):
                self.old_data["fiscal_period"] = old_period
                self.new_data["fiscal_period"] = new_period
                self.write_records()
                self.assert_input_error(self.drift("--format", "json"))

    def test_incompatible_company_and_metric_fail_cleanly(self) -> None:
        for field, value in (("company", "Other Synthetic Company"), ("metric_name", "Adjusted EBITDA")):
            with self.subTest(field=field):
                original = self.new_data[field]
                self.new_data[field] = value
                self.write_records()
                self.assert_input_error(self.drift())
                self.new_data[field] = original

    def test_missing_or_malformed_json_uses_existing_input_errors(self) -> None:
        self.old_path.unlink()
        missing = self.drift()
        self.assert_input_error(missing)
        self.assertIn("not found", missing.stderr)
        self.old_path.write_text("{broken json", encoding="utf-8")
        malformed = self.drift()
        self.assert_input_error(malformed)
        self.assertIn("JSON", malformed.stderr)

    def test_unsupported_format_is_rejected(self) -> None:
        self.assert_input_error(self.drift("--format", "text"))

    def test_compare_retains_support_for_other_period_labels(self) -> None:
        self.old_data["fiscal_period"] = "Interim 2024"
        self.new_data["fiscal_period"] = "Interim 2025"
        self.write_records()
        completed = self.run_cli("compare", str(self.old_path), str(self.new_path), "--format", "json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["old_period"], "Interim 2024")
        self.assertTrue(report["has_definition_drift"])

    def test_demo_and_reports_listing_still_work(self) -> None:
        demo = self.run_cli("demo")
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertIn("SYNTHETIC", demo.stdout)
        listed = self.run_cli("reports", "list", "0700", "--format", "json", "--registry", str(self.directory / "missing-registry.json"))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout), [])


if __name__ == "__main__":
    unittest.main()
