"""Confirm only explicit selections from invented, temporary registered PDFs."""

from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage.__main__ import _load_definition
from metric_mirage.candidates import extract_adjustment_candidates
from metric_mirage.confirmation import ConfirmationResult, confirm_candidates
from metric_mirage.core import MetricDefinition
from metric_mirage.reports import ReportRecord, register_report
from tests.test_pdf_search import synthetic_unicode_pdf


class CandidateConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage confirmation ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC report.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.output = self.directory / "definition.json"
        self.metadata = {
            "ticker": "0700", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented confirmation fixture",
        }

    def register(self, pages: list[str] | None = None, **changes: str) -> ReportRecord:
        if pages is None:
            pages = ["SYNTHETIC Adjusted Net Profit excludes share-based payments, "
                     "listing expenses and amortisation of intangible assets."]
        self.pdf.write_bytes(synthetic_unicode_pdf(pages))
        return register_report(self.pdf, **dict(self.metadata, **changes),
                               registry_path=self.registry)

    def confirm(self, selected: object = ("share_based_payments",),
                **changes: object) -> ConfirmationResult:
        arguments: dict[str, object] = {
            "fiscal_period": "FY2024", "selected_adjustments": selected,
            "output_path": self.output, "registry_path": self.registry,
        }
        arguments.update(changes)
        return confirm_candidates("00700", **arguments)  # type: ignore[arg-type]

    def assert_no_output_or_temporary_files(self) -> None:
        self.assertFalse(self.output.exists())
        self.assertEqual({path.name for path in self.directory.iterdir()},
                         {self.pdf.name, self.registry.parent.name})

    def test_single_selection_exports_only_that_candidate(self) -> None:
        record = self.register()
        result = self.confirm()
        self.assertIsInstance(result, ConfirmationResult)
        self.assertIsInstance(result.definition, MetricDefinition)
        self.assertEqual(result.report, record)
        self.assertEqual(result.definition.adjustments, ("share_based_payments",))
        self.assertEqual(result.output_path, self.output.resolve())
        self.assertEqual({candidate.canonical_name for candidate in result.selected_candidates},
                         {"share_based_payments"})

    def test_multiple_selections_have_fixed_canonical_sorting(self) -> None:
        self.register()
        result = self.confirm(("share_based_payments", "amortisation_of_intangible_assets"))
        self.assertEqual(result.definition.adjustments,
                         ("amortisation_of_intangible_assets", "share_based_payments"))
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["adjustments"],
                         ["amortisation_of_intangible_assets", "share_based_payments"])

    def test_repeated_selection_deduplicates_without_selecting_other_candidates(self) -> None:
        self.register()
        result = self.confirm(["share_based_payments", "share_based_payments"])
        self.assertEqual(result.definition.adjustments, ("share_based_payments",))
        self.assertEqual(len(result.selected_candidates), 1)

    def test_input_order_does_not_change_exported_definition(self) -> None:
        self.register()
        first = self.confirm(("share_based_payments", "listing_expenses"))
        second = self.confirm(("listing_expenses", "share_based_payments"),
                              output_path=self.directory / "second.json")
        self.assertEqual(first.definition, second.definition)
        self.assertEqual(first.selected_candidates, second.selected_candidates)
        self.assertEqual(self.output.read_bytes(), second.output_path.read_bytes())

    def test_unknown_canonical_name_is_rejected(self) -> None:
        self.register()
        with self.assertRaises(ValueError) as raised:
            self.confirm(("invented_expenses",))
        self.assertIn("invented_expenses", str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_known_name_absent_from_this_report_is_rejected(self) -> None:
        self.register()
        with self.assertRaises(ValueError) as raised:
            self.confirm(("impairment_losses",))
        self.assertIn("impairment_losses", str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_mixed_valid_and_invalid_selection_never_exports_partial_definition(self) -> None:
        self.register()
        with self.assertRaises(ValueError):
            self.confirm(("share_based_payments", "impairment_losses"))
        self.assertFalse(self.output.exists())

    def test_no_selection_is_rejected_in_python_api(self) -> None:
        self.register()
        with self.assertRaises(ValueError):
            self.confirm(())
        self.assertFalse(self.output.exists())

    def test_invalid_selection_sequence_or_members_are_rejected(self) -> None:
        self.register()
        for selection in (None, "share_based_payments", b"share_based_payments", 12,
                          [None], [12], [""], ["   "]):
            with self.subTest(selection=selection):
                with self.assertRaises((TypeError, ValueError)):
                    self.confirm(selection)
                self.assertFalse(self.output.exists())

    def test_canonical_name_does_not_accept_case_guessing_or_aliases(self) -> None:
        self.register()
        for selection in ("SHARE_BASED_PAYMENTS", "share-based payments", "股份支付開支"):
            with self.subTest(selection=selection):
                with self.assertRaises(ValueError):
                    self.confirm((selection,))
                self.assertFalse(self.output.exists())

    def test_empty_candidates_do_not_export_a_fabricated_definition(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤增加。\n\n股份支付開支計入行政費用。"])
        self.assertEqual(extract_adjustment_candidates(
            "00700", fiscal_period="FY2024", registry_path=self.registry).candidates, ())
        with self.assertRaises(ValueError):
            self.confirm()
        self.assertFalse(self.output.exists())

    def test_export_is_exact_existing_schema_loadable_by_compare_loader(self) -> None:
        record = self.register()
        result = self.confirm()
        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(set(data),
                         {"company", "metric_name", "fiscal_period", "adjustments", "source"})
        self.assertEqual(data["company"], record.company)
        self.assertEqual(data["fiscal_period"], record.fiscal_period)
        self.assertEqual(data["metric_name"], "Adjusted Net Profit")
        self.assertEqual(MetricDefinition(**data), result.definition)
        self.assertEqual(_load_definition(self.output), result.definition)

    def test_utf8_export_preserves_chinese_characters(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤不包括股份支付開支。"],
                      company="合成香港公司", source_name="SYNTHETIC: 自行建立的合成報告")
        self.confirm()
        raw = self.output.read_bytes()
        self.assertIn("合成香港公司".encode("utf-8"), raw)
        self.assertIn("自行建立的合成報告".encode("utf-8"), raw)
        self.assertNotIn(b"\\u5408", raw)
        json.loads(raw.decode("utf-8"))

    def test_source_records_registered_source_hash_and_user_selection_status(self) -> None:
        record = self.register()
        result = self.confirm()
        self.assertIn(record.source_name, result.definition.source)
        self.assertIn(record.file_sha256, result.definition.source)
        self.assertIn("user", result.definition.source.lower())
        self.assertNotIn("automatically verified", result.definition.source.lower())

    def test_selected_evidence_keeps_each_actual_pdf_page_and_raw_text(self) -> None:
        self.register(["SYNTHETIC cover", "經調整淨利潤不包括股份支付開支。",
                       "SYNTHETIC Adjusted Net Profit excludes share-based payments and listing expenses."])
        before = extract_adjustment_candidates("00700", fiscal_period="FY2024",
                                               registry_path=self.registry)
        result = self.confirm()
        expected = tuple(item for item in before.candidates
                         if item.canonical_name == "share_based_payments")
        self.assertEqual(result.selected_candidates, expected)
        self.assertEqual([item.page_number for item in result.selected_candidates], [2, 3])
        self.assertEqual([item.matched_text for item in result.selected_candidates],
                         ["股份支付開支", "share-based payments"])

    def test_confirmation_does_not_change_pdf_registry_or_infer_drift(self) -> None:
        self.register()
        pdf_before, registry_before = self.pdf.read_bytes(), self.registry.read_bytes()
        with patch("metric_mirage.core.compare_metrics") as comparison:
            result = self.confirm()
        comparison.assert_not_called()
        self.assertFalse(hasattr(result, "has_definition_drift"))
        self.assertEqual(self.pdf.read_bytes(), pdf_before)
        self.assertEqual(self.registry.read_bytes(), registry_before)

    def test_confirmation_result_is_immutable(self) -> None:
        self.register()
        result = self.confirm()
        with self.assertRaises(FrozenInstanceError):
            result.output_path = self.directory / "replacement.json"  # type: ignore[misc]

    def test_missing_registered_report_has_clear_error(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self.confirm()
        self.assertIn("00700", str(raised.exception))
        self.assertIn("FY2024", str(raised.exception))
        self.assertFalse(self.output.exists())
        self.assertFalse(self.registry.exists())

    def test_missing_original_pdf_prevents_export(self) -> None:
        self.register()
        self.pdf.unlink()
        with self.assertRaises(ValueError) as raised:
            self.confirm()
        self.assertIn(str(self.pdf), str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_sha256_mismatch_stops_before_pdf_parse_and_export(self) -> None:
        self.register()
        self.pdf.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC replacement Adjusted Net Profit excludes share-based payments."
        ]))
        with patch("metric_mirage.search.PdfReader") as reader:
            with self.assertRaises(ValueError) as raised:
                self.confirm()
        reader.assert_not_called()
        self.assertIn("changed", str(raised.exception).lower())
        self.assertFalse(self.output.exists())

    def test_corrupt_registered_pdf_does_not_export(self) -> None:
        self.pdf.write_bytes(b"%PDF-1.4\nSYNTHETIC damaged PDF\n%%EOF\n")
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        with self.assertRaises(ValueError):
            self.confirm()
        self.assertFalse(self.output.exists())

    def register_second_report(self, *, report_type: str = "interim") -> ReportRecord:
        second = self.directory / "SYNTHETIC second.pdf"
        second.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC second Adjusted Net Profit excludes impairment losses."
        ]))
        return register_report(second, **dict(self.metadata, report_type=report_type),
                               registry_path=self.registry)

    def test_report_ambiguity_is_not_silently_resolved(self) -> None:
        self.register()
        self.register_second_report()
        with self.assertRaises(ValueError) as raised:
            self.confirm()
        self.assertIn("reports list", str(raised.exception))
        self.assertFalse(self.output.exists())

    def test_type_filter_selects_existing_registered_report(self) -> None:
        self.register()
        interim = self.register_second_report()
        result = self.confirm(("impairment_losses",), report_type="interim")
        self.assertEqual(result.report, interim)
        self.assertEqual(result.definition.adjustments, ("impairment_losses",))

    def test_same_type_ambiguity_remains_an_error(self) -> None:
        self.register()
        self.register_second_report(report_type="annual")
        with self.assertRaises(ValueError):
            self.confirm(report_type="annual")
        self.assertFalse(self.output.exists())

    def test_existing_output_file_is_never_overwritten(self) -> None:
        self.register()
        original = b"SYNTHETIC user's existing document"
        self.output.write_bytes(original)
        with self.assertRaises((ValueError, OSError)):
            self.confirm()
        self.assertEqual(self.output.read_bytes(), original)

    def test_existing_output_directory_is_rejected(self) -> None:
        self.register()
        self.output.mkdir()
        saved = self.output / "keep.txt"
        saved.write_text("SYNTHETIC keep", encoding="utf-8")
        with self.assertRaises((ValueError, OSError)):
            self.confirm()
        self.assertEqual(saved.read_text(encoding="utf-8"), "SYNTHETIC keep")

    def test_output_cannot_replace_original_pdf_or_registry(self) -> None:
        self.register()
        for path in (self.pdf, self.registry):
            saved = path.read_bytes()
            with self.subTest(path=path), self.assertRaises((ValueError, OSError)):
                self.confirm(output_path=path)
            self.assertEqual(path.read_bytes(), saved)

    def test_dangling_output_symlink_is_not_followed_or_overwritten(self) -> None:
        self.register()
        missing = self.directory / "must-not-be-created.json"
        try:
            self.output.symlink_to(missing)
        except OSError:
            # Windows may require a privilege to create real symlinks. Model
            # only the existing leaf entry; resolve of the parent stays real.
            original_lstat = Path.lstat

            def dangling_leaf(path, **kwargs):
                if path == self.output:
                    return os.stat_result((stat.S_IFLNK | 0o777,) + (0,) * 9)
                return original_lstat(path, **kwargs)

            with patch("metric_mirage.confirmation.Path.lstat", autospec=True,
                       side_effect=dangling_leaf) as lstat:
                with self.assertRaises((ValueError, OSError)):
                    self.confirm()
                self.assertTrue(any(call.args == (self.output,) for call in lstat.call_args_list))
            self.assertFalse(self.output.exists())
            self.assertFalse(missing.exists())
            return
        with self.assertRaises((ValueError, OSError)):
            self.confirm()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(missing.exists())

    def test_missing_output_parent_is_clear_error_without_creating_directories(self) -> None:
        self.register()
        missing = self.directory / "missing"
        with self.assertRaises((ValueError, OSError)):
            self.confirm(output_path=missing / "definition.json")
        self.assertFalse(missing.exists())

    def test_output_parent_symlink_loop_has_clear_error_and_preserves_inputs(self) -> None:
        self.register()
        pdf_before, registry_before = self.pdf.read_bytes(), self.registry.read_bytes()
        original_resolve = Path.resolve

        def resolve_parent(path, *args, **kwargs):
            if path == self.output.parent and kwargs.get("strict") is True:
                raise RuntimeError("SYNTHETIC symlink loop")
            return original_resolve(path, *args, **kwargs)

        with patch("metric_mirage.confirmation.Path.resolve", autospec=True,
                   side_effect=resolve_parent):
            with self.assertRaises(ValueError) as raised:
                self.confirm()
        self.assertIn(str(self.output.parent), str(raised.exception))
        self.assertIn("resolve", str(raised.exception).lower())
        self.assert_no_output_or_temporary_files()
        self.assertEqual(self.pdf.read_bytes(), pdf_before)
        self.assertEqual(self.registry.read_bytes(), registry_before)

    def test_serialization_failure_leaves_no_formal_output_or_temp_file(self) -> None:
        self.register()

        def interrupted_dump(data, stream, **kwargs):
            stream.write('{"company":')
            raise OSError("SYNTHETIC serialization interrupted")

        with patch("metric_mirage.confirmation.json.dump", side_effect=interrupted_dump):
            with self.assertRaises((ValueError, OSError)):
                self.confirm()
        self.assert_no_output_or_temporary_files()

    def test_fsync_failure_leaves_no_formal_output_or_temp_file(self) -> None:
        self.register()
        with patch("metric_mirage.confirmation.os.fsync",
                   side_effect=OSError("SYNTHETIC disk failure")):
            with self.assertRaises((ValueError, OSError)):
                self.confirm()
        self.assert_no_output_or_temporary_files()

    def test_publication_failure_leaves_no_formal_output_or_temp_file(self) -> None:
        self.register()
        with patch("metric_mirage.confirmation.os.link",
                   side_effect=OSError("SYNTHETIC unsupported publication")):
            with self.assertRaises((ValueError, OSError)):
                self.confirm()
        self.assert_no_output_or_temporary_files()

    def test_output_created_by_another_writer_before_publication_is_preserved(self) -> None:
        self.register()
        real_link = os.link
        original = b"SYNTHETIC concurrent writer's existing output"

        def concurrent_write(source, destination, **kwargs):
            Path(destination).write_bytes(original)
            return real_link(source, destination, **kwargs)

        with patch("metric_mirage.confirmation.os.link", side_effect=concurrent_write):
            with self.assertRaises((ValueError, OSError)):
                self.confirm()
        self.assertEqual(self.output.read_bytes(), original)
        self.assertEqual({path.name for path in self.directory.iterdir()},
                         {self.pdf.name, self.registry.parent.name, self.output.name})


if __name__ == "__main__":
    unittest.main()
