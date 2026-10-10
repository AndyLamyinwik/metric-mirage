"""Run report registration and listing commands with synthetic temporary files."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage.__main__ import main
from tests.test_reports import synthetic_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReportsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage report CLI ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC report.pdf"
        self.pdf.write_bytes(synthetic_pdf())
        self.registry = self.directory / "registry" / "reports.json"

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments],
            cwd=PROJECT_ROOT, capture_output=True, text=True, check=False, timeout=15,
        )

    def add_arguments(self, *extra: str) -> list[str]:
        return [
            "reports", "add", str(self.pdf), "--ticker", "0700",
            "--company", "Synthetic HK Company", "--type", "annual",
            "--period", "FY2024", "--source", "SYNTHETIC: invented test report",
            "--registry", str(self.registry), *extra,
        ]

    def add(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli(*self.add_arguments(*extra))

    def listing(self, *extra: str, ticker: str = "00700") -> subprocess.CompletedProcess[str]:
        return self.run_cli("reports", "list", ticker, "--registry", str(self.registry), *extra)

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)

    def test_add_and_text_listing_display_traceable_metadata(self) -> None:
        added = self.add("--publication-date", "2025-03-20")
        listed = self.listing()

        for completed in (added, listed):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
            for expected in (
                "00700", "Synthetic HK Company", "annual", "FY2024",
                "2025-03-20", "SYNTHETIC: invented test report", str(self.pdf.resolve()),
                hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
            ):
                with self.subTest(output=completed.stdout, expected=expected):
                    self.assertIn(expected, completed.stdout)

    def test_json_listing_is_a_pure_parseable_array(self) -> None:
        added = self.add()
        self.assertEqual(added.returncode, 0, added.stderr)

        completed = self.listing("--format", "json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout), [{
            "ticker": "00700", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "publication_date": None, "source_name": "SYNTHETIC: invented test report",
            "local_file": str(self.pdf.resolve()),
            "file_sha256": hashlib.sha256(self.pdf.read_bytes()).hexdigest(),
        }])

    def test_listing_accepts_equivalent_ticker_formats(self) -> None:
        self.assertEqual(self.add().returncode, 0)
        canonical = self.listing("--format", "json")
        for ticker in ("700", "0700", "0700.HK", "00700.hk"):
            with self.subTest(ticker=ticker):
                completed = self.listing("--format", "json", ticker=ticker)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, canonical.stdout)

    def test_json_listing_is_deterministic(self) -> None:
        self.assertEqual(self.add().returncode, 0)
        self.pdf.write_bytes(synthetic_pdf("SYNTHETIC SECOND PERIOD"))
        self.assertEqual(self.add("--period", "FY2025").returncode, 0)
        first = self.listing("--format", "json")
        repeated = self.listing("--format", "json")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(first.stdout, repeated.stdout)
        self.assertEqual(len(json.loads(first.stdout)), 2)

    def test_default_and_explicit_text_listing_match(self) -> None:
        self.assertEqual(self.add().returncode, 0)
        default = self.listing()
        explicit = self.listing("--format", "text")

        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(default.stdout, explicit.stdout)

    def test_duplicate_add_does_not_create_another_record(self) -> None:
        first = self.add()
        self.assertEqual(first.returncode, 0, first.stderr)
        saved = self.registry.read_bytes()
        duplicate = self.add("--ticker", "00700.HK")

        self.assertEqual(duplicate.returncode, 0, duplicate.stderr)
        self.assertEqual(duplicate.stderr, "")
        self.assertIn("00700", duplicate.stdout)
        self.assertEqual(self.registry.read_bytes(), saved)
        self.assertEqual(len(json.loads(self.listing("--format", "json").stdout)), 1)

    def test_empty_registry_lists_without_creating_a_registry(self) -> None:
        text = self.listing()
        structured = self.listing("--format", "json")

        self.assertEqual(text.returncode, 0, text.stderr)
        self.assertIn("00700", text.stdout)
        self.assertEqual(structured.returncode, 0, structured.stderr)
        self.assertEqual(structured.stderr, "")
        self.assertEqual(json.loads(structured.stdout), [])
        self.assertFalse(self.registry.exists())

    def test_other_ticker_lists_an_empty_array(self) -> None:
        self.assertEqual(self.add().returncode, 0)
        completed = self.listing("--format", "json", ticker="1810")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout), [])

    def test_missing_pdf_reports_file_without_traceback(self) -> None:
        self.pdf.unlink()
        completed = self.add()

        self.assert_input_error(completed)
        self.assertIn(str(self.pdf), completed.stderr)
        self.assertFalse(self.registry.exists())

    def test_invalid_pdf_reports_error_without_creating_registry(self) -> None:
        self.pdf.write_text("SYNTHETIC non-PDF fixture", encoding="utf-8")
        completed = self.add()

        self.assert_input_error(completed)
        self.assertIn("PDF", completed.stderr)
        self.assertFalse(self.registry.exists())

    def test_invalid_ticker_is_reported_for_add_and_list(self) -> None:
        for completed in (self.add("--ticker", "0"), self.listing(ticker="700.US")):
            with self.subTest(completed=completed):
                self.assert_input_error(completed)
        self.assertFalse(self.registry.exists())

    def test_invalid_publication_date_does_not_create_registry(self) -> None:
        completed = self.add("--publication-date", "2025-02-30")

        self.assert_input_error(completed)
        self.assertIn("publication_date", completed.stderr)
        self.assertFalse(self.registry.exists())

    def test_conflicting_duplicate_metadata_preserves_registry(self) -> None:
        self.assertEqual(self.add().returncode, 0)
        saved = self.registry.read_bytes()
        completed = self.add("--company", "Other Synthetic HK Company")

        self.assert_input_error(completed)
        self.assertEqual(self.registry.read_bytes(), saved)

    def test_corrupt_registry_reports_error_and_preserves_user_data(self) -> None:
        self.registry.parent.mkdir()
        saved = b"{ invalid synthetic registry"
        self.registry.write_bytes(saved)
        for completed in (self.add(), self.listing("--format", "json")):
            with self.subTest(completed=completed):
                self.assert_input_error(completed)
                self.assertIn(str(self.registry), completed.stderr)
                self.assertEqual(self.registry.read_bytes(), saved)

    def test_registry_write_error_is_caught_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("metric_mirage.reports.os.replace", side_effect=PermissionError("Write denied")),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            exit_code = main(self.add_arguments())

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Write denied", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertFalse(self.registry.exists())

    def test_unreadable_pdf_error_is_caught_without_traceback(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("metric_mirage.reports.Path.open", side_effect=PermissionError("Read denied")),
            redirect_stdout(stdout), redirect_stderr(stderr),
        ):
            exit_code = main(self.add_arguments())

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Read denied", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())
        self.assertFalse(self.registry.exists())

    def test_invalid_report_type_is_a_clean_argument_error(self) -> None:
        completed = self.add("--type", "quarterly")

        self.assertEqual(completed.returncode, 2)
        self.assert_input_error(completed)
        self.assertIn("--type", completed.stderr)
        self.assertFalse(self.registry.exists())

    def test_invalid_output_format_is_a_clean_argument_error(self) -> None:
        completed = self.listing("--format", "xml")

        self.assertEqual(completed.returncode, 2)
        self.assert_input_error(completed)
        self.assertIn("--format", completed.stderr)


if __name__ == "__main__":
    unittest.main()
