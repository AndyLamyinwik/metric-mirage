"""Check candidate extraction and compatibility through real offline CLI calls."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from metric_mirage.reports import ReportRecord, register_report
from tests.test_pdf_search import encrypted_synthetic_pdf, synthetic_unicode_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AdjustmentCandidateCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage candidates CLI ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC candidate report.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.metadata = {
            "ticker": "0700", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented candidate CLI fixture",
        }

    def register(self, pages: list[str], **changes: str) -> ReportRecord:
        self.pdf.write_bytes(synthetic_unicode_pdf(pages))
        return register_report(self.pdf, **dict(self.metadata, **changes),
                               registry_path=self.registry)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"), check=False, timeout=15,
        )

    def candidates(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli(
            "reports", "candidates", "00700", "--period", "FY2024",
            "--registry", str(self.registry), *extra,
        )

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)

    def test_text_output_shows_source_page_raw_evidence_and_candidate_status(self) -> None:
        self.register(["SYNTHETIC cover", "經調整淨利潤不包括股份支付開支。"])
        completed = self.candidates()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        for expected in ("00700", "Synthetic HK Company", "FY2024", "Adjusted Net Profit",
                         "share_based_payments", "股份支付開支", "經調整淨利潤不包括股份支付開支",
                         self.metadata["source_name"]):
            self.assertIn(expected, completed.stdout)
        self.assertRegex(completed.stdout, r"(?i)PDF\s+page\s*:?\s+2")
        self.assertIn("not a confirmed", completed.stdout)

    def test_json_output_is_pure_parseable_and_contains_exact_evidence_fields(self) -> None:
        record = self.register(["SYNTHETIC cover",
                                "Adjusted Net Profit excludes listing expenses."])
        completed = self.candidates("--format", "json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        document = json.loads(completed.stdout)
        self.assertEqual(set(document), {
            "ticker", "company", "fiscal_period", "metric_name", "source_name",
            "file_sha256", "candidates",
        })
        self.assertEqual(document["ticker"], "00700")
        self.assertEqual(document["company"], record.company)
        self.assertEqual(document["fiscal_period"], "FY2024")
        self.assertEqual(document["metric_name"], "Adjusted Net Profit")
        self.assertEqual(document["source_name"], record.source_name)
        self.assertEqual(document["file_sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.assertEqual(len(document["candidates"]), 1)
        candidate = document["candidates"][0]
        self.assertEqual(set(candidate), {
            "canonical_name", "matched_text", "page_number", "evidence_excerpt",
        })
        self.assertEqual(candidate["canonical_name"], "listing_expenses")
        self.assertEqual(candidate["matched_text"], "listing expenses")
        self.assertEqual(candidate["page_number"], 2)
        self.assertIn("Adjusted Net Profit excludes listing expenses", candidate["evidence_excerpt"])
        self.assertNotIn("has_definition_drift", document)

    def test_traditional_simplified_and_english_match_real_pdf_pages(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤不包括股份支付開支。",
                       "SYNTHETIC 经调整净利润不包括减值亏损。",
                       "SYNTHETIC Non-HKFRS Adjusted Net Profit excludes listing expenses."])
        completed = self.candidates("--format", "json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        candidates = json.loads(completed.stdout)["candidates"]
        self.assertEqual([(item["page_number"], item["canonical_name"], item["matched_text"])
                          for item in candidates],
                         [(1, "share_based_payments", "股份支付開支"),
                          (2, "impairment_losses", "减值亏损"),
                          (3, "listing_expenses", "listing expenses")])

    def test_repeated_json_output_is_deterministic_and_registry_is_unchanged(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses and impairment losses."])
        before = self.registry.read_bytes()
        first = self.candidates("--format", "json")
        second = self.candidates("--format", "json")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(before, self.registry.read_bytes())

    def test_default_and_explicit_text_output_match(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        default = self.candidates()
        explicit = self.candidates("--format", "text")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        self.assertEqual(default.stdout, explicit.stdout)

    def test_empty_candidates_are_successful_json_without_registry_changes(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤有所增加。",
                       "普通損益表中的股份支付開支計入行政費用。"])
        before = self.registry.read_bytes()
        completed = self.candidates("--format", "json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout)["candidates"], [])
        self.assertEqual(before, self.registry.read_bytes())

    def test_empty_candidates_are_successful_text_result(self) -> None:
        self.register(["SYNTHETIC ordinary annual report without supported adjustment evidence."])
        completed = self.candidates()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertIn("Adjusted Net Profit", completed.stdout)
        self.assertIn("Candidates: 0", completed.stdout)

    def test_missing_registered_report_is_clear_and_creates_no_registry(self) -> None:
        completed = self.candidates("--format", "json")
        self.assert_input_error(completed)
        self.assertIn("00700", completed.stderr)
        self.assertIn("FY2024", completed.stderr)
        self.assertFalse(self.registry.exists())

    def test_ambiguous_reports_require_type_filter(self) -> None:
        self.register(["SYNTHETIC annual Adjusted Net Profit excludes listing expenses."])
        interim = self.directory / "SYNTHETIC interim.pdf"
        interim.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC interim Adjusted Net Profit excludes impairment losses."
        ]))
        register_report(interim, **dict(self.metadata, report_type="interim"),
                        registry_path=self.registry)
        ambiguous = self.candidates("--format", "json")
        self.assert_input_error(ambiguous)
        self.assertIn("--type", ambiguous.stderr)
        self.assertIn("reports list", ambiguous.stderr)
        filtered = self.candidates("--type", "annual", "--format", "json")
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        self.assertEqual([item["canonical_name"] for item in json.loads(filtered.stdout)["candidates"]],
                         ["listing_expenses"])

    def test_missing_original_pdf_is_clear(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        self.pdf.unlink()
        completed = self.candidates("--format", "json")
        self.assert_input_error(completed)
        self.assertIn(str(self.pdf), completed.stderr)

    def test_changed_pdf_is_not_read_as_registered_evidence(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        saved = self.registry.read_bytes()
        self.pdf.write_bytes(synthetic_unicode_pdf(["SYNTHETIC replacement"]))
        completed = self.candidates("--format", "json")
        self.assert_input_error(completed)
        self.assertIn("changed", completed.stderr.lower())
        self.assertEqual(saved, self.registry.read_bytes())

    def test_corrupt_pdf_error_has_no_traceback_or_json_output(self) -> None:
        self.pdf.write_bytes(b"%PDF-1.4\nSYNTHETIC invalid objects\n%%EOF\n")
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        completed = self.candidates("--format", "json")
        self.assert_input_error(completed)
        self.assertIn("PDF", completed.stderr)

    def test_no_text_pdf_error_is_distinct_from_empty_candidates(self) -> None:
        self.register(["", ""])
        completed = self.candidates("--format", "json")
        self.assert_input_error(completed)
        self.assertRegex(completed.stderr.lower(), r"extractable text|text layer|no text")

    def test_password_encrypted_pdf_has_clear_error(self) -> None:
        self.pdf.write_bytes(encrypted_synthetic_pdf("SYNTHETIC secret"))
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        completed = self.candidates("--format", "json")
        self.assert_input_error(completed)
        self.assertIn("encrypted", completed.stderr.lower())

    def test_invalid_ticker_is_rejected(self) -> None:
        completed = self.run_cli("reports", "candidates", "invalid", "--period", "FY2024",
                                 "--registry", str(self.registry))
        self.assert_input_error(completed)

    def test_missing_period_or_invalid_type_and_format_are_usage_errors(self) -> None:
        for arguments in (
            ["reports", "candidates", "00700"],
            ["reports", "candidates", "00700", "--period", "FY2024", "--type", "quarterly"],
            ["reports", "candidates", "00700", "--period", "FY2024", "--format", "xml"],
        ):
            with self.subTest(arguments=arguments):
                completed = self.run_cli(*arguments)
                self.assert_input_error(completed)
                self.assertEqual(completed.returncode, 2)

    def test_demo_and_compare_commands_keep_existing_behavior(self) -> None:
        demo = self.run_cli("demo")
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertEqual(demo.stderr, "")
        self.assertIn("SYNTHETIC demonstration", demo.stdout)
        old = str(PROJECT_ROOT / "examples" / "old.json")
        new = str(PROJECT_ROOT / "examples" / "new.json")
        comparison = self.run_cli("compare", old, new, "--format", "json")
        self.assertEqual(comparison.returncode, 0, comparison.stderr)
        self.assertEqual(comparison.stderr, "")
        report = json.loads(comparison.stdout)
        self.assertEqual(report["added_adjustments"], ["restructuring costs"])
        self.assertTrue(report["has_definition_drift"])

    def test_reports_add_list_and_search_keep_existing_behavior(self) -> None:
        text = "SYNTHETIC Adjusted Net Profit excludes listing expenses."
        self.pdf.write_bytes(synthetic_unicode_pdf([text]))
        added = self.run_cli(
            "reports", "add", str(self.pdf), "--ticker", "0700",
            "--company", self.metadata["company"], "--type", "annual",
            "--period", "FY2024", "--source", self.metadata["source_name"],
            "--registry", str(self.registry),
        )
        self.assertEqual(added.returncode, 0, added.stderr)
        self.assertEqual(added.stderr, "")
        listed = self.run_cli("reports", "list", "00700", "--format", "json",
                              "--registry", str(self.registry))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(len(json.loads(listed.stdout)), 1)
        searched = self.run_cli("reports", "search", "00700", "--period", "FY2024",
                                "--keyword", "Adjusted Net Profit", "--format", "json",
                                "--registry", str(self.registry))
        self.assertEqual(searched.returncode, 0, searched.stderr)
        search_report = json.loads(searched.stdout)
        self.assertEqual(search_report["total_matches"], 1)
        self.assertEqual(search_report["matches"][0]["page_number"], 1)
        self.assertIn(text, search_report["matches"][0]["excerpt"])
        candidate_report = self.candidates("--format", "json")
        self.assertEqual(candidate_report.returncode, 0, candidate_report.stderr)
        self.assertEqual(len(json.loads(candidate_report.stdout)["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
