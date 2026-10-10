"""Check explicit candidate selection and existing comparison through offline CLI."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from metric_mirage.core import MetricDefinition
from metric_mirage.reports import ReportRecord, register_report
from tests.test_pdf_search import synthetic_unicode_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CandidateConfirmationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage confirmation CLI ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC report.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.output = self.directory / "definition_2024.json"
        self.metadata = {
            "ticker": "0700", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented confirmation CLI fixture",
        }

    def register(self, pages: list[str] | None = None, **changes: str) -> ReportRecord:
        if pages is None:
            pages = ["SYNTHETIC cover", "SYNTHETIC Adjusted Net Profit excludes "
                     "share-based payments and amortisation of intangible assets."]
        self.pdf.write_bytes(synthetic_unicode_pdf(pages))
        return register_report(self.pdf, **dict(self.metadata, **changes),
                               registry_path=self.registry)

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
            env=dict(os.environ, PYTHONIOENCODING="utf-8"), check=False, timeout=15,
        )

    def confirm(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("reports", "confirm", "00700", "--period", "FY2024",
                            "--registry", str(self.registry), *extra)

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)

    def read_export(self, path: Path | None = None) -> MetricDefinition:
        target = self.output if path is None else path
        data = json.loads(target.read_text(encoding="utf-8"))
        return MetricDefinition(**data)

    def test_single_selection_cli_displays_company_period_evidence_page_and_output(self) -> None:
        self.register()
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        for expected in ("Synthetic HK Company", "FY2024", "share_based_payments", str(self.output)):
            self.assertIn(expected, completed.stdout)
        self.assertRegex(completed.stdout, r"(?i)(?:PDF\s+pages?|pages?)\s*:?[ \t]*2")
        self.assertNotIn("amortisation_of_intangible_assets", completed.stdout)
        self.assertEqual(self.read_export().adjustments, ("share_based_payments",))

    def test_multiple_repeatable_select_options_export_sorted_names(self) -> None:
        self.register()
        completed = self.confirm("--select", "share_based_payments", "--select",
                                 "amortisation_of_intangible_assets", "--output", str(self.output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.read_export().adjustments,
                         ("amortisation_of_intangible_assets", "share_based_payments"))

    def test_duplicate_select_options_export_one_identity(self) -> None:
        self.register()
        completed = self.confirm("--select", "share_based_payments", "--select",
                                 "share_based_payments", "--output", str(self.output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.read_export().adjustments, ("share_based_payments",))

    def test_unknown_canonical_name_errors_without_export(self) -> None:
        self.register()
        completed = self.confirm("--select", "invented_expenses", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("invented_expenses", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_known_but_absent_item_errors_without_export(self) -> None:
        self.register()
        completed = self.confirm("--select", "impairment_losses", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("impairment_losses", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_mixed_selection_failure_does_not_write_valid_subset(self) -> None:
        self.register()
        completed = self.confirm("--select", "share_based_payments", "--select",
                                 "listing_expenses", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertFalse(self.output.exists())

    def test_select_is_required_without_automatically_confirming_all(self) -> None:
        self.register()
        completed = self.confirm("--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("--select", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_output_option_is_required(self) -> None:
        self.register()
        completed = self.confirm("--select", "share_based_payments")
        self.assert_input_error(completed)
        self.assertIn("--output", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_period_option_is_required(self) -> None:
        self.register()
        completed = self.run_cli("reports", "confirm", "00700", "--select",
                                 "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("--period", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_empty_candidates_do_not_export_ordinary_financial_expenses(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤增加。", "股份支付開支計入行政費用。"])
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertFalse(self.output.exists())

    def test_chinese_export_is_raw_utf8_and_valid_current_schema(self) -> None:
        record = self.register(["SYNTHETIC 經調整淨利潤不包括股份支付開支。"],
                               company="合成香港公司", source_name="SYNTHETIC: 合成財報来源")
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        raw = self.output.read_text(encoding="utf-8")
        self.assertIn("合成香港公司", raw)
        self.assertIn("合成財報来源", raw)
        data = json.loads(raw)
        self.assertEqual(set(data), {"company", "metric_name", "fiscal_period", "adjustments", "source"})
        self.assertEqual(data["metric_name"], "Adjusted Net Profit")
        self.assertEqual(data["company"], record.company)
        self.assertIn(record.file_sha256, data["source"])
        self.assertEqual(self.read_export().adjustments, ("share_based_payments",))

    def test_confirmed_exports_are_readable_by_existing_compare_text_and_json(self) -> None:
        self.register(["SYNTHETIC FY2024 Adjusted Net Profit excludes share-based payments."])
        old = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assertEqual(old.returncode, 0, old.stderr)
        new_pdf = self.directory / "SYNTHETIC FY2025.pdf"
        new_pdf.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC FY2025 经调整净利润不包括股份支付开支、无形资产摊销。"
        ]))
        register_report(new_pdf, **dict(self.metadata, fiscal_period="FY2025"),
                        registry_path=self.registry)
        new_output = self.directory / "definition_2025.json"
        new = self.run_cli("reports", "confirm", "00700", "--period", "FY2025",
                          "--select", "share_based_payments", "--select",
                          "amortisation_of_intangible_assets", "--output", str(new_output),
                          "--registry", str(self.registry))
        self.assertEqual(new.returncode, 0, new.stderr)
        for format_args in ((), ("--format", "json")):
            with self.subTest(format_args=format_args):
                compared = self.run_cli("compare", str(self.output), str(new_output), *format_args)
                self.assertEqual(compared.returncode, 0, compared.stderr)
                self.assertEqual(compared.stderr, "")
                if format_args:
                    report = json.loads(compared.stdout)
                    self.assertEqual(report["metric_name"], "Adjusted Net Profit")
                    self.assertEqual(report["old_period"], "FY2024")
                    self.assertEqual(report["new_period"], "FY2025")
                    self.assertEqual(report["added_adjustments"], ["amortisation_of_intangible_assets"])
                    self.assertEqual(report["removed_adjustments"], [])
                    self.assertEqual(report["unchanged_adjustments"], ["share_based_payments"])
                    self.assertTrue(report["has_definition_drift"])
                else:
                    self.assertIn("Adjusted Net Profit", compared.stdout)
                    self.assertIn("amortisation_of_intangible_assets", compared.stdout)
                    self.assertIn("has_definition_drift: true", compared.stdout)
                    self.assertIn("user", compared.stdout.lower())

    def test_missing_original_pdf_is_clear_error_without_traceback(self) -> None:
        self.register()
        self.pdf.unlink()
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn(str(self.pdf), completed.stderr)
        self.assertFalse(self.output.exists())

    def test_original_hash_mismatch_prevents_export(self) -> None:
        self.register()
        self.pdf.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC changed Adjusted Net Profit excludes share-based payments."
        ]))
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("changed", completed.stderr.lower())
        self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved_on_error(self) -> None:
        self.register()
        original = "SYNTHETIC already exists 離線資料".encode("utf-8")
        self.output.write_bytes(original)
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn(str(self.output), completed.stderr)
        self.assertEqual(self.output.read_bytes(), original)

    def test_missing_output_parent_errors_without_partial_file(self) -> None:
        self.register()
        missing = self.directory / "missing" / "definition.json"
        completed = self.confirm("--select", "share_based_payments", "--output", str(missing))
        self.assert_input_error(completed)
        self.assertFalse(missing.exists())

    def add_second_report(self, report_type: str = "interim") -> ReportRecord:
        second = self.directory / "SYNTHETIC second.pdf"
        second.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC second Adjusted Net Profit excludes impairment losses."
        ]))
        return register_report(second, **dict(self.metadata, report_type=report_type),
                               registry_path=self.registry)

    def test_ambiguous_report_errors_and_suggests_reports_list(self) -> None:
        self.register()
        self.add_second_report()
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("reports list", completed.stderr)
        self.assertIn("--type", completed.stderr)
        self.assertFalse(self.output.exists())

    def test_type_option_resolves_annual_interim_ambiguity(self) -> None:
        self.register()
        self.add_second_report()
        completed = self.confirm("--type", "interim", "--select", "impairment_losses",
                                 "--output", str(self.output))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.read_export().adjustments, ("impairment_losses",))

    def test_same_type_ambiguity_is_not_silently_resolved(self) -> None:
        self.register()
        self.add_second_report("annual")
        completed = self.confirm("--type", "annual", "--select", "share_based_payments",
                                 "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertFalse(self.output.exists())

    def test_no_registered_report_errors_without_creating_registry(self) -> None:
        completed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        self.assert_input_error(completed)
        self.assertIn("00700", completed.stderr)
        self.assertIn("FY2024", completed.stderr)
        self.assertFalse(self.registry.exists())
        self.assertFalse(self.output.exists())

    def test_pdf_registry_and_candidate_results_remain_unchanged_after_confirmation(self) -> None:
        self.register()
        pdf_before, registry_before = self.pdf.read_bytes(), self.registry.read_bytes()
        candidate_args = ("reports", "candidates", "00700", "--period", "FY2024",
                          "--format", "json", "--registry", str(self.registry))
        before = self.run_cli(*candidate_args)
        confirmed = self.confirm("--select", "share_based_payments", "--output", str(self.output))
        after = self.run_cli(*candidate_args)
        self.assertEqual(confirmed.returncode, 0, confirmed.stderr)
        self.assertEqual(before.returncode, 0, before.stderr)
        self.assertEqual(after.returncode, 0, after.stderr)
        self.assertEqual(json.loads(before.stdout), json.loads(after.stdout))
        self.assertEqual(self.pdf.read_bytes(), pdf_before)
        self.assertEqual(self.registry.read_bytes(), registry_before)

    def test_existing_demo_compare_and_report_commands_still_work(self) -> None:
        self.pdf.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC Adjusted Net Profit excludes share-based payments."
        ]))
        added = self.run_cli("reports", "add", str(self.pdf), "--ticker", "0700",
                             "--company", self.metadata["company"], "--type", "annual",
                             "--period", "FY2024", "--source", self.metadata["source_name"],
                             "--registry", str(self.registry))
        self.assertEqual(added.returncode, 0, added.stderr)
        demo = self.run_cli("demo")
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertIn("SYNTHETIC", demo.stdout)
        compared = self.run_cli("compare", str(PROJECT_ROOT / "examples" / "old.json"),
                                str(PROJECT_ROOT / "examples" / "new.json"), "--format", "json")
        self.assertEqual(compared.returncode, 0, compared.stderr)
        json.loads(compared.stdout)
        listed = self.run_cli("reports", "list", "00700", "--format", "json",
                              "--registry", str(self.registry))
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(len(json.loads(listed.stdout)), 1)
        searched = self.run_cli("reports", "search", "00700", "--period", "FY2024",
                                "--keyword", "Adjusted Net Profit", "--format", "json",
                                "--registry", str(self.registry))
        self.assertEqual(searched.returncode, 0, searched.stderr)
        self.assertEqual(json.loads(searched.stdout)["total_matches"], 1)
        candidates = self.run_cli("reports", "candidates", "00700", "--period", "FY2024",
                                  "--format", "json", "--registry", str(self.registry))
        self.assertEqual(candidates.returncode, 0, candidates.stderr)
        self.assertEqual(json.loads(candidates.stdout)["candidates"][0]["canonical_name"],
                         "share_based_payments")


if __name__ == "__main__":
    unittest.main()
