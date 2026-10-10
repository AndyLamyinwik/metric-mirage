"""Exercise custom JSON comparisons, drift detection, and CLI input errors."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage import MetricDefinition, MetricDiff, compare_metrics
from metric_mirage.__main__ import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def record(
    adjustments: list[str], *, fiscal_period: str = "FY 2024"
) -> dict[str, object]:
    """Create a single definition using the existing JSON schema."""
    return {
        "company": "Synthetic Test Company",
        "metric_name": "Adjusted EBITDA",
        "fiscal_period": fiscal_period,
        "adjustments": adjustments,
        "source": f"SYNTHETIC: invented {fiscal_period} test fixture.",
    }


def definition(adjustments: list[str]) -> MetricDefinition:
    return MetricDefinition(**record(adjustments))  # type: ignore[arg-type]


class DefinitionDriftTests(unittest.TestCase):
    def test_three_positional_argument_constructor_remains_compatible(self) -> None:
        for added, removed, unchanged, expected in (
            ((), (), (), False),
            ((), (), ("shared costs",), False),
            (("added costs",), (), (), True),
            ((), ("removed costs",), (), True),
        ):
            with self.subTest(added=added, removed=removed):
                diff = MetricDiff(added, removed, unchanged)
                self.assertEqual(diff.has_definition_drift, expected)
                self.assertIsInstance(diff.has_definition_drift, bool)

    def test_added_adjustment_causes_drift(self) -> None:
        diff = compare_metrics(definition(["Shared Costs"]), definition(["Shared Costs", "Added Costs"]))

        self.assertTrue(diff.has_definition_drift)

    def test_removed_adjustment_causes_drift(self) -> None:
        diff = compare_metrics(definition(["Shared Costs", "Removed Costs"]), definition(["Shared Costs"]))

        self.assertTrue(diff.has_definition_drift)

    def test_normalized_identical_sets_have_no_drift(self) -> None:
        diff = compare_metrics(
            definition([" SHARED\tCOSTS ", "Other Costs", "shared costs"]),
            definition(["other   costs", "Shared Costs"]),
        )

        self.assertFalse(diff.has_definition_drift)

    def test_empty_sets_have_no_drift(self) -> None:
        self.assertFalse(compare_metrics(definition([]), definition([])).has_definition_drift)


class CustomComparisonCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage tests ")
        self.addCleanup(temporary.cleanup)
        self.old_path = Path(temporary.name) / "old.json"
        self.new_path = Path(temporary.name) / "new.json"
        self.old_record = record(["Shared Costs", "Removed Costs"])
        self.new_record = record(["Shared Costs", "Added Costs"], fiscal_period="FY 2025")
        self.write_records()

    def write_records(self) -> None:
        for path, data in ((self.old_path, self.old_record), (self.new_path, self.new_record)):
            path.write_text(json.dumps(data), encoding="utf-8")

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

    def compare(self, *options: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("compare", str(self.old_path), str(self.new_path), *options)

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.startswith("Error: "), completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_two_valid_json_files_produce_default_text_report(self) -> None:
        completed = self.compare()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            "Metric Mirage - comparison\n"
            "Company: Synthetic Test Company\n"
            "Metric: Adjusted EBITDA\n"
            "Periods: FY 2024 -> FY 2025\n"
            "Source (old): SYNTHETIC: invented FY 2024 test fixture.\n"
            "Source (new): SYNTHETIC: invented FY 2025 test fixture.\n"
            "\n"
            "added:\n"
            "  - added costs\n"
            "removed:\n"
            "  - removed costs\n"
            "unchanged:\n"
            "  - shared costs\n"
            "has_definition_drift: true\n",
        )

    def test_json_output_is_a_pure_parseable_report(self) -> None:
        completed = self.compare("--format", "json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "company": "Synthetic Test Company",
                "metric_name": "Adjusted EBITDA",
                "old_period": "FY 2024",
                "new_period": "FY 2025",
                "added_adjustments": ["added costs"],
                "removed_adjustments": ["removed costs"],
                "unchanged_adjustments": ["shared costs"],
                "has_definition_drift": True,
            },
        )

    def test_json_report_is_deterministic_and_sorted_regardless_of_input_order(self) -> None:
        self.old_record["adjustments"] = ["Zeta Removed", "Beta Removed", "Zeta Shared", "Alpha Shared"]
        self.new_record["adjustments"] = ["Zeta Added", "Alpha Shared", "Alpha Added", "Zeta Shared"]
        self.write_records()
        first = self.compare("--format", "json")
        repeated = self.compare("--format", "json")
        self.old_record["adjustments"] = ["Alpha Shared", "Zeta Shared", "Beta Removed", "Zeta Removed"]
        self.new_record["adjustments"] = ["Zeta Shared", "Alpha Added", "Alpha Shared", "Zeta Added"]
        self.write_records()
        reordered = self.compare("--format", "json")

        for completed in (first, repeated, reordered):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
        self.assertEqual(first.stdout, repeated.stdout)
        self.assertEqual(first.stdout, reordered.stdout)
        report = json.loads(first.stdout)
        self.assertEqual(report["added_adjustments"], ["alpha added", "zeta added"])
        self.assertEqual(report["removed_adjustments"], ["beta removed", "zeta removed"])
        self.assertEqual(report["unchanged_adjustments"], ["alpha shared", "zeta shared"])

    def test_json_report_marks_normalized_identical_and_empty_sets_without_drift(self) -> None:
        for old_adjustments, new_adjustments, expected_unchanged in (
            ([" Shared\tCosts ", "SHARED COSTS"], ["shared costs"], ["shared costs"]),
            ([], [], []),
        ):
            with self.subTest(old=old_adjustments, new=new_adjustments):
                self.old_record["adjustments"] = old_adjustments
                self.new_record["adjustments"] = new_adjustments
                self.write_records()
                completed = self.compare("--format", "json")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                report = json.loads(completed.stdout)
                self.assertIs(report["has_definition_drift"], False)
                self.assertEqual(report["added_adjustments"], [])
                self.assertEqual(report["removed_adjustments"], [])
                self.assertEqual(report["unchanged_adjustments"], expected_unchanged)

    def test_explicit_text_format_matches_default(self) -> None:
        default = self.compare()
        explicit = self.compare("--format", "text")

        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(default.stdout, explicit.stdout)

    def test_utf8_bom_input_is_supported(self) -> None:
        self.old_path.write_text(json.dumps(self.old_record), encoding="utf-8-sig")

        completed = self.compare("--format", "json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(json.loads(completed.stdout)["has_definition_drift"])

    def test_missing_old_and_new_files_report_paths_without_traceback(self) -> None:
        for path in (self.old_path, self.new_path):
            with self.subTest(path=path):
                path.unlink()
                completed = self.compare("--format", "json")
                self.assert_input_error(completed)
                self.assertIn(str(path), completed.stderr)
                self.write_records()

    def test_unreadable_file_reports_helpful_error_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("metric_mirage.__main__.Path.read_text", side_effect=PermissionError("Access denied")),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main(["compare", str(self.old_path), str(self.new_path), "--format", "json"])

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn(str(self.old_path), stderr.getvalue())
        self.assertIn("Access denied", stderr.getvalue())
        self.assertTrue(stderr.getvalue().startswith("Error: "))
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_malformed_json_reports_file_and_location(self) -> None:
        self.old_path.write_text('{\n  "company":\n}', encoding="utf-8")

        completed = self.compare("--format", "json")

        self.assert_input_error(completed)
        self.assertIn(str(self.old_path), completed.stderr)
        self.assertRegex(completed.stderr, r"(?i)line\s+3")
        self.assertRegex(completed.stderr, r"(?i)column\s+1")

    def test_non_object_json_roots_are_rejected(self) -> None:
        for invalid in ([], None, "not a definition", 42, True):
            with self.subTest(invalid=invalid):
                self.old_path.write_text(json.dumps(invalid), encoding="utf-8")
                completed = self.compare("--format", "json")
                self.assert_input_error(completed)
                self.assertIn(str(self.old_path), completed.stderr)
                self.assertIn("object", completed.stderr.lower())

    def test_missing_model_fields_are_rejected(self) -> None:
        for field in self.old_record:
            with self.subTest(field=field):
                invalid = dict(self.old_record)
                del invalid[field]
                self.old_path.write_text(json.dumps(invalid), encoding="utf-8")
                completed = self.compare("--format", "json")
                self.assert_input_error(completed)
                self.assertIn(str(self.old_path), completed.stderr)
                self.assertIn(field, completed.stderr)

    def test_extra_model_fields_are_rejected(self) -> None:
        invalid = dict(self.old_record, unexpected_field="unused")
        self.old_path.write_text(json.dumps(invalid), encoding="utf-8")

        completed = self.compare("--format", "json")

        self.assert_input_error(completed)
        self.assertIn(str(self.old_path), completed.stderr)
        self.assertIn("unexpected_field", completed.stderr)

    def test_invalid_field_values_use_existing_model_validation(self) -> None:
        for field, invalid_value in (
            ("company", 12),
            ("metric_name", " "),
            ("fiscal_period", None),
            ("source", "\n\t"),
            ("adjustments", "Shared Costs"),
            ("adjustments", [" "]),
            ("adjustments", [4]),
        ):
            with self.subTest(field=field, value=invalid_value):
                invalid = dict(self.old_record)
                invalid[field] = invalid_value
                self.old_path.write_text(json.dumps(invalid), encoding="utf-8")
                completed = self.compare("--format", "json")
                self.assert_input_error(completed)
                self.assertIn(str(self.old_path), completed.stderr)
                self.assertIn(field, completed.stderr)

    def test_different_companies_are_rejected_without_traceback(self) -> None:
        self.new_record["company"] = "Other Synthetic Company"
        self.write_records()

        completed = self.compare("--format", "json")

        self.assert_input_error(completed)
        self.assertIn("companies", completed.stderr)
        self.assertIn("Other Synthetic Company", completed.stderr)

    def test_different_metrics_are_rejected_without_traceback(self) -> None:
        self.new_record["metric_name"] = "Adjusted Net Income"
        self.write_records()

        completed = self.compare("--format", "json")

        self.assert_input_error(completed)
        self.assertIn("metrics", completed.stderr)
        self.assertIn("Adjusted Net Income", completed.stderr)

    def test_non_utf8_input_reports_error_without_traceback(self) -> None:
        self.new_path.write_bytes(b"\xff\xfeinvalid")

        completed = self.compare("--format", "json")

        self.assert_input_error(completed)
        self.assertIn(str(self.new_path), completed.stderr)

    def test_invalid_format_is_an_argument_error(self) -> None:
        completed = self.compare("--format", "xml")

        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertIn("--format", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_demo_retains_its_existing_output(self) -> None:
        completed = self.run_cli("demo")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(
            completed.stdout,
            "Metric Mirage - SYNTHETIC demonstration\n"
            "Company: Synthetic Example Company\n"
            "Metric: Adjusted EBITDA\n"
            "Periods: FY 2024 -> FY 2025\n"
            "Source (old): SYNTHETIC: invented FY 2024 example; not a real financial report.\n"
            "Source (new): SYNTHETIC: invented FY 2025 example; not a real financial report.\n"
            "\n"
            "added:\n"
            "  - restructuring costs\n"
            "removed:\n"
            "  (none)\n"
            "unchanged:\n"
            "  - depreciation & amortization\n"
            "  - stock-based compensation\n",
        )


if __name__ == "__main__":
    unittest.main()
