"""Verify PDF search output and expected failures through the actual CLI."""

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage import register_report
from metric_mirage.__main__ import main
from tests.test_pdf_search import encrypted_synthetic_pdf, synthetic_unicode_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PdfSearchCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage search CLI ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC searchable report.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.metadata = {
            "ticker": "0700", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented search CLI fixture",
        }

    def register(self, pages: list[str], **changes: str):
        self.pdf.write_bytes(synthetic_unicode_pdf(pages))
        return register_report(self.pdf, **dict(self.metadata, **changes),
                               registry_path=self.registry)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"), check=False, timeout=15,
        )

    def search_arguments(self, keyword: str = "Adjusted EBITDA", *extra: str) -> list[str]:
        return [
            "reports", "search", "00700", "--period", "FY2024", "--keyword", keyword,
            "--registry", str(self.registry), *extra,
        ]

    def search(self, keyword: str = "Adjusted EBITDA", *extra: str):
        return self.run_cli(*self.search_arguments(keyword, *extra))

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)

    def test_text_search_displays_report_keyword_excerpt_and_pdf_page(self) -> None:
        self.register(["SYNTHETIC cover", "SYNTHETIC Adjusted EBITDA disclosure."])
        completed = self.search()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        for expected in ("00700", "Synthetic HK Company", "FY2024", "Adjusted EBITDA",
                         "SYNTHETIC Adjusted EBITDA disclosure."):
            self.assertIn(expected, completed.stdout)
        self.assertRegex(completed.stdout, r"(?i)PDF\s+page\s*:?\s+2")

    def test_json_search_is_pure_parseable_metadata_and_matches(self) -> None:
        record = self.register(["SYNTHETIC cover", "SYNTHETIC Adjusted EBITDA disclosure."])
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        document = json.loads(completed.stdout)
        self.assertEqual(set(document), {
            "ticker", "company", "fiscal_period", "source_name", "file_sha256",
            "keyword", "total_matches", "matches",
        })
        self.assertEqual(document["ticker"], "00700")
        self.assertEqual(document["company"], record.company)
        self.assertEqual(document["fiscal_period"], "FY2024")
        self.assertEqual(document["source_name"], record.source_name)
        self.assertEqual(document["file_sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.assertEqual(document["keyword"], "Adjusted EBITDA")
        self.assertEqual(document["total_matches"], 1)
        self.assertEqual(len(document["matches"]), 1)
        self.assertEqual(set(document["matches"][0]), {"page_number", "keyword", "excerpt"})
        self.assertEqual(document["matches"][0]["page_number"], 2)
        self.assertIn("Adjusted EBITDA", document["matches"][0]["excerpt"])

    def test_real_pdf_traditional_and_simplified_search_json(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤", "SYNTHETIC 经调整净利润"])
        for keyword, page in (("經調整淨利潤", 1), ("经调整净利润", 2)):
            with self.subTest(keyword=keyword):
                completed = self.search(keyword, "--format", "json")
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                document = json.loads(completed.stdout)
                self.assertEqual(document["total_matches"], 1)
                self.assertEqual(document["matches"][0]["page_number"], page)
                self.assertIn(keyword, document["matches"][0]["excerpt"])

    def test_max_results_preserves_total_matches_in_json(self) -> None:
        self.register(["Adjusted EBITDA; adjusted EBITDA", "ADJUSTED EBITDA", "Adjusted EBITDA"])
        completed = self.search("Adjusted EBITDA", "--format", "json", "--max-results", "2")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(completed.stdout)
        self.assertEqual(document["total_matches"], 4)
        self.assertEqual(len(document["matches"]), 2)
        self.assertEqual([match["page_number"] for match in document["matches"]], [1, 1])

    def test_default_and_explicit_text_output_match(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        default = self.search()
        explicit = self.search("Adjusted EBITDA", "--format", "text")

        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(default.stdout, explicit.stdout)

    def test_repeated_json_search_is_deterministic(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        first = self.search("Adjusted EBITDA", "--format", "json")
        repeated = self.search("Adjusted EBITDA", "--format", "json")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertEqual(first.stdout, repeated.stdout)

    def test_no_hits_is_successful_empty_json(self) -> None:
        self.register(["SYNTHETIC ordinary report"])
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        document = json.loads(completed.stdout)
        self.assertEqual(document["total_matches"], 0)
        self.assertEqual(document["matches"], [])

    def test_no_hits_is_successful_text_result(self) -> None:
        self.register(["SYNTHETIC ordinary report"])
        completed = self.search()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertIn("Adjusted EBITDA", completed.stdout)
        self.assertIn("0", completed.stdout)

    def test_no_registered_report_is_clear_without_creating_registry(self) -> None:
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assert_input_error(completed)
        self.assertIn("00700", completed.stderr)
        self.assertIn("FY2024", completed.stderr)
        self.assertFalse(self.registry.exists())

    def test_missing_original_pdf_is_clear(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        self.pdf.unlink()
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assert_input_error(completed)
        self.assertIn(str(self.pdf), completed.stderr)

    def test_changed_original_pdf_is_not_searched_as_registered_data(self) -> None:
        self.register(["SYNTHETIC original Adjusted EBITDA"])
        saved = self.registry.read_bytes()
        self.pdf.write_bytes(synthetic_unicode_pdf(["SYNTHETIC changed Adjusted EBITDA"]))
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assert_input_error(completed)
        self.assertRegex(completed.stderr.lower(), r"changed|sha256|checksum")
        self.assertEqual(self.registry.read_bytes(), saved)

    def test_corrupt_pdf_is_clear_without_traceback(self) -> None:
        self.pdf.write_bytes(b"%PDF-1.4\nSYNTHETIC corrupt objects\n%%EOF\n")
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assert_input_error(completed)
        self.assertIn("PDF", completed.stderr)

    def test_no_text_is_distinct_from_zero_matches(self) -> None:
        self.register(["", ""])
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assert_input_error(completed)
        self.assertRegex(completed.stderr.lower(), r"extractable text|text layer|no text")

    def test_password_encrypted_pdf_is_clear(self) -> None:
        self.pdf.write_bytes(encrypted_synthetic_pdf("SYNTHETIC secret"))
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        completed = self.search("Adjusted EBITDA", "--format", "json")

        self.assert_input_error(completed)
        self.assertIn("encrypt", completed.stderr.lower())

    def test_ambiguity_error_and_type_filter(self) -> None:
        self.register(["SYNTHETIC annual Adjusted EBITDA"])
        other = self.directory / "SYNTHETIC interim.pdf"
        other.write_bytes(synthetic_unicode_pdf(["SYNTHETIC interim Adjusted EBITDA"]))
        register_report(other, **dict(self.metadata, report_type="interim"),
                        registry_path=self.registry)
        ambiguous = self.search()
        filtered = self.search("Adjusted EBITDA", "--type", "annual", "--format", "json")

        self.assert_input_error(ambiguous)
        self.assertIn("reports list", ambiguous.stderr)
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        self.assertEqual(json.loads(filtered.stdout)["total_matches"], 1)

    def test_type_filter_still_rejects_multiple_reports(self) -> None:
        self.register(["SYNTHETIC original annual Adjusted EBITDA"])
        other = self.directory / "SYNTHETIC revised annual.pdf"
        other.write_bytes(synthetic_unicode_pdf(["SYNTHETIC revised annual Adjusted EBITDA"]))
        register_report(other, **self.metadata, registry_path=self.registry)
        completed = self.search("Adjusted EBITDA", "--type", "annual", "--format", "json")

        self.assert_input_error(completed)
        self.assertIn("reports list", completed.stderr)

    def test_invalid_max_results_is_clean_argument_error(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        for value in ("0", "-1", "1001", "ten", "1.5"):
            with self.subTest(value=value):
                completed = self.search("Adjusted EBITDA", "--max-results", value)
                self.assert_input_error(completed)
                self.assertIn("--max-results", completed.stderr)

    def test_invalid_keyword_is_clean_error(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        for keyword in ("", " \t"):
            with self.subTest(keyword=keyword):
                completed = self.search(keyword)
                self.assert_input_error(completed)
                self.assertIn("keyword", completed.stderr)

    def test_invalid_type_and_format_are_clean_argument_errors(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        for extra in (("--type", "quarterly"), ("--format", "xml")):
            with self.subTest(extra=extra):
                completed = self.search("Adjusted EBITDA", *extra)
                self.assert_input_error(completed)

    def test_unreadable_pdf_error_is_caught_without_traceback(self) -> None:
        self.register(["SYNTHETIC Adjusted EBITDA"])
        real_open = Path.open

        def fail_pdf_open(path: Path, *args: object, **kwargs: object):
            if path == self.pdf:
                raise PermissionError("SYNTHETIC read denied")
            return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("pathlib.Path.open", new=fail_pdf_open), redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = main(self.search_arguments())

        self.assertNotEqual(exit_code, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("read denied", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_existing_demo_compare_add_and_list_still_work(self) -> None:
        self.pdf.write_bytes(synthetic_unicode_pdf(["SYNTHETIC Adjusted EBITDA"]))
        added = self.run_cli(
            "reports", "add", str(self.pdf), "--ticker", "0700", "--company",
            "Synthetic HK Company", "--type", "annual", "--period", "FY2024",
            "--source", "SYNTHETIC test", "--registry", str(self.registry),
        )
        listed = self.run_cli("reports", "list", "00700", "--format", "json",
                              "--registry", str(self.registry))
        demo = self.run_cli("demo")
        compared = self.run_cli("compare", "examples/old.json", "examples/new.json",
                                "--format", "json")

        for completed in (added, listed, demo, compared):
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stderr, "")
        self.assertEqual(len(json.loads(listed.stdout)), 1)
        self.assertTrue(json.loads(compared.stdout)["has_definition_drift"])
        self.assertIn("SYNTHETIC", demo.stdout)


if __name__ == "__main__":
    unittest.main()
