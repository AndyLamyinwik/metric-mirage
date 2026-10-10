"""Extract conservative candidates only from invented, temporary PDF reports."""

from dataclasses import FrozenInstanceError
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pypdf import PdfReader

from metric_mirage.candidates import (
    AdjustmentCandidate,
    CandidateResult,
    extract_adjustment_candidates,
)
from metric_mirage.core import MetricDefinition
from metric_mirage.reports import ReportRecord, register_report
from tests.test_pdf_search import encrypted_synthetic_pdf, synthetic_unicode_pdf


ENGLISH_ITEMS = (
    ("share_based_payments", "share-based payments"),
    ("fair_value_changes", "fair value changes"),
    ("impairment_losses", "impairment losses"),
    ("listing_expenses", "listing expenses"),
    ("acquisition_related_expenses", "acquisition-related expenses"),
    ("amortisation_of_intangible_assets", "amortisation of intangible assets"),
)
TRADITIONAL_ITEMS = (
    ("share_based_payments", "股份支付開支"),
    ("fair_value_changes", "公允價值變動"),
    ("impairment_losses", "減值虧損"),
    ("listing_expenses", "上市開支"),
    ("acquisition_related_expenses", "收購相關開支"),
    ("amortisation_of_intangible_assets", "無形資產攤銷"),
)
SIMPLIFIED_ITEMS = (
    ("share_based_payments", "股份支付开支"),
    ("fair_value_changes", "公允价值变动"),
    ("impairment_losses", "减值亏损"),
    ("listing_expenses", "上市开支"),
    ("acquisition_related_expenses", "收购相关开支"),
    ("amortisation_of_intangible_assets", "无形资产摊销"),
)


class AdjustmentCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage candidates ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC candidate report.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.metadata = {
            "ticker": "0700.HK", "company": "Synthetic HK Company",
            "report_type": "annual", "fiscal_period": "FY2024",
            "source_name": "SYNTHETIC: invented candidate fixture",
        }

    def register(self, pages: list[str], **changes: str) -> ReportRecord:
        self.pdf.write_bytes(synthetic_unicode_pdf(pages))
        return register_report(self.pdf, **dict(self.metadata, **changes),
                               registry_path=self.registry)

    def extract(self, **changes: object) -> CandidateResult:
        arguments: dict[str, object] = {
            "fiscal_period": "FY2024", "registry_path": self.registry,
        }
        arguments.update(changes)
        return extract_adjustment_candidates("00700", **arguments)  # type: ignore[arg-type]

    def mock_pages(self, *texts: str | None):
        return patch("metric_mirage.search.PdfReader", return_value=SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=Mock(return_value=text)) for text in texts],
        ))

    def assert_candidates(self, result: CandidateResult,
                          expected: tuple[tuple[str, str], ...], page: int = 1) -> None:
        self.assertIsInstance(result, CandidateResult)
        self.assertEqual(result.metric_name, "Adjusted Net Profit")
        self.assertEqual([(item.canonical_name, item.matched_text) for item in result.candidates],
                         list(expected))
        self.assertTrue(all(isinstance(item, AdjustmentCandidate) for item in result.candidates))
        self.assertEqual([item.page_number for item in result.candidates], [page] * len(expected))

    def test_traditional_chinese_metric_and_all_six_items(self) -> None:
        text = "SYNTHETIC 經調整淨利潤不包括" + "、".join(
            label for _, label in TRADITIONAL_ITEMS
        ) + "。"
        self.register([text])
        self.assert_candidates(self.extract(), TRADITIONAL_ITEMS)

    def test_simplified_chinese_metric_and_all_six_items(self) -> None:
        text = "SYNTHETIC 经调整净利润不包括" + "、".join(
            label for _, label in SIMPLIFIED_ITEMS
        ) + "。"
        self.register([text])
        self.assert_candidates(self.extract(), SIMPLIFIED_ITEMS)

    def test_english_metric_and_all_six_items(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes " + ", ".join(
            label for _, label in ENGLISH_ITEMS
        ) + "."])
        self.assert_candidates(self.extract(), ENGLISH_ITEMS)

    def test_non_hkfrs_metric_and_casefold_preserve_matched_text(self) -> None:
        original = "Share-Based Payments"
        self.register(["SYNTHETIC non-hkfrs ADJUSTED NET PROFIT excludes " + original + "."])
        self.assert_candidates(self.extract(), (("share_based_payments", original),))

    def test_english_alternate_dictionary_aliases(self) -> None:
        aliases = (
            ("share_based_payments", "share-based payment expenses"),
            ("fair_value_changes", "changes in fair value"),
            ("listing_expenses", "listing costs"),
            ("acquisition_related_expenses", "acquisition related expenses"),
            ("amortisation_of_intangible_assets", "amortization of intangible assets"),
        )
        self.register(["SYNTHETIC Adjusted Net Profit excludes " + ", ".join(
            label for _, label in aliases
        ) + "."])
        self.assert_candidates(self.extract(), aliases)

    def test_explicit_reconciliation_bullet_list(self) -> None:
        text = ("SYNTHETIC Adjusted Net Profit reconciliation:\n"
                "- listing expenses\n- impairment losses")
        self.register([text])
        self.assert_candidates(self.extract(), (
            ("listing_expenses", "listing expenses"),
            ("impairment_losses", "impairment losses"),
        ))

    def test_explicit_adjustment_headings_in_chinese(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤的調整項目包括上市開支。",
                       "SYNTHETIC 经调整净利润调整项目：上市开支。"])
        result = self.extract()
        self.assertEqual([(item.page_number, item.canonical_name, item.matched_text)
                          for item in result.candidates],
                         [(1, "listing_expenses", "上市開支"),
                          (2, "listing_expenses", "上市开支")])

    def test_collapsed_whitespace_retains_raw_matched_text_and_evidence(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        raw = "SYNTHETIC Adjusted\n Net Profit excludes share-based\n  payments."
        with self.mock_pages(raw):
            result = self.extract()
        self.assert_candidates(result, (("share_based_payments", "share-based\n  payments"),))
        self.assertIn(result.candidates[0].evidence_excerpt, raw)
        self.assertIn("Adjusted\n Net Profit", result.candidates[0].evidence_excerpt)
        self.assertIn("share-based\n  payments", result.candidates[0].evidence_excerpt)

    def test_casefold_expansion_before_metric_preserves_original_source_spans(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        raw = "SYNTHETIC " + "ß" * 30 + " Adjusted Net Profit excludes Share-Based\n Payments."
        with self.mock_pages(raw):
            result = self.extract()
        self.assert_candidates(result, (("share_based_payments", "Share-Based\n Payments"),))
        self.assertIn(result.candidates[0].evidence_excerpt, raw)
        self.assertIn("Adjusted Net Profit", result.candidates[0].evidence_excerpt)
        self.assertIn("Share-Based\n Payments", result.candidates[0].evidence_excerpt)

    def test_candidate_page_numbers_preserve_blank_pages(self) -> None:
        self.register(["", "SYNTHETIC cover", "", "Adjusted Net Profit excludes listing expenses."])
        self.assert_candidates(self.extract(), (("listing_expenses", "listing expenses"),), page=4)

    def test_duplicate_canonical_on_one_page_keeps_first_raw_occurrence(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes Share-Based Payments, "
                       "share based payments and 股份支付開支."])
        self.assert_candidates(self.extract(), (("share_based_payments", "Share-Based Payments"),))

    def test_repeated_metric_sections_deduplicate_on_same_page(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses.\n"
                       "Adjusted Net Profit excludes listing expenses."])
        self.assert_candidates(self.extract(), (("listing_expenses", "listing expenses"),))

    def test_same_canonical_on_different_pages_retains_each_source_page(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses.",
                       "SYNTHETIC 經調整淨利潤不包括上市開支。"])
        result = self.extract()
        self.assertEqual([(item.page_number, item.canonical_name, item.matched_text)
                          for item in result.candidates],
                         [(1, "listing_expenses", "listing expenses"),
                          (2, "listing_expenses", "上市開支")])

    def test_source_order_and_repeated_extraction_are_stable(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes impairment losses and listing expenses.",
                       "Adjusted Net Profit excludes fair value changes and share-based payments."])
        result = self.extract()
        self.assertEqual([(item.page_number, item.canonical_name) for item in result.candidates],
                         [(1, "impairment_losses"), (1, "listing_expenses"),
                          (2, "fair_value_changes"), (2, "share_based_payments")])
        self.assertEqual(result, self.extract())

    def test_evidence_is_exact_pdf_extracted_text_with_metric_and_item(self) -> None:
        text = "SYNTHETIC note:\nAdjusted Net Profit excludes listing expenses and impairment losses."
        record = self.register([text])
        raw = PdfReader(io.BytesIO(self.pdf.read_bytes()), strict=True).pages[0].extract_text()
        result = self.extract()
        self.assertEqual(result.report, record)
        self.assertEqual(len(result.candidates), 2)
        for candidate in result.candidates:
            self.assertIn(candidate.matched_text, raw)
            self.assertIn(candidate.evidence_excerpt, raw)
            self.assertIn(candidate.matched_text, candidate.evidence_excerpt)
            self.assertIn("Adjusted Net Profit", candidate.evidence_excerpt)

    def test_candidates_are_separate_from_metric_definition_and_registry(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        before = self.registry.read_bytes()
        with patch("metric_mirage.core.compare_metrics") as compare:
            result = self.extract()
        compare.assert_not_called()
        self.assertNotIsInstance(result, MetricDefinition)
        self.assertFalse(hasattr(result, "has_definition_drift"))
        self.assertEqual(before, self.registry.read_bytes())

    def test_adjustment_candidate_is_immutable(self) -> None:
        candidate = AdjustmentCandidate("listing_expenses", "上市開支", 1,
                                        "經調整淨利潤不包括上市開支。")
        with self.assertRaises(FrozenInstanceError):
            candidate.matched_text = "invented"  # type: ignore[misc]

    def test_no_supported_item_is_normal_empty_result(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes ordinary operating expenses."])
        saved = self.registry.read_bytes()
        self.assertEqual(self.extract().candidates, ())
        self.assertEqual(saved, self.registry.read_bytes())

    def test_missing_metric_does_not_promote_expense_labels(self) -> None:
        self.register(["SYNTHETIC income statement: 股份支付開支、上市開支及減值虧損。"])
        self.assertEqual(self.extract().candidates, ())

    def test_misleading_share_based_expense_on_other_page_is_not_a_candidate(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤有所增加。",
                       "SYNTHETIC 普通損益表：股份支付開支計入行政費用。"])
        self.assertEqual(self.extract().candidates, ())

    def test_nearby_expense_without_explicit_association_is_not_a_candidate(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤有所增加，股份支付開支計入行政費用。"])
        self.assertEqual(self.extract().candidates, ())

    def test_unrelated_same_page_paragraph_is_not_a_candidate(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤不包括上市開支。\n\n"
                       "普通損益表：股份支付開支計入行政費用。"])
        self.assert_candidates(self.extract(), (("listing_expenses", "上市開支"),))

    def test_list_stops_at_unrelated_prose_in_same_sentence(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses, "
                       "while share-based payments are ordinary operating expenses."])
        self.assert_candidates(self.extract(), (("listing_expenses", "listing expenses"),))

    def test_conjoined_ordinary_expense_is_not_promoted_to_adjustment(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes share-based payments "
                       "and listing expenses are reported in operating costs."])
        self.assert_candidates(self.extract(), (("share_based_payments", "share-based payments"),))

    def test_other_metric_reconciliation_is_not_reused(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit increased.\n"
                       "Adjusted EBITDA excludes share-based payments."])
        self.assertEqual(self.extract().candidates, ())

    def test_negated_exclusion_does_not_create_candidate(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit does not exclude share-based payments."])
        self.assertEqual(self.extract().candidates, ())

    def test_chinese_negated_exclusion_does_not_create_candidate(self) -> None:
        self.register(["SYNTHETIC 經調整淨利潤不剔除股份支付開支。"])
        self.assertEqual(self.extract().candidates, ())

    def test_negated_or_questioned_relation_does_not_create_candidate(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        for raw in (
            "It is incorrect to say that Adjusted Net Profit excludes share-based payments.",
            "Adjusted Net Profit excludes share-based payments, which are not excluded in this report.",
            "Adjusted Net Profit excludes share-based payments? No, it does not.",
            "Adjusted Net Profit excludes share-based payments and listing expenses?",
            "並非經調整淨利潤剔除股份支付開支。",
        ):
            with self.subTest(raw=raw), self.mock_pages(raw):
                self.assertEqual(self.extract().candidates, ())

    def test_unadjusted_word_is_not_adjusted_metric(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        for raw in ("SYNTHETIC Unadjusted Net Profit excludes share-based payments.",
                    "SYNTHETIC Un-Adjusted Net Profit excludes share-based payments."):
            with self.subTest(raw=raw), self.mock_pages(raw):
                self.assertEqual(self.extract().candidates, ())

    def test_metric_relation_does_not_cross_pages(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes", "listing expenses."])
        self.assertEqual(self.extract().candidates, ())

    def test_metric_relation_does_not_cross_blank_paragraph(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes:\n\n"
                       "Income statement: share-based payments."])
        self.assertEqual(self.extract().candidates, ())

    def test_distant_item_is_not_associated_with_metric(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit reconciliation: " + "x" * 900
                       + " share-based payments."])
        self.assertEqual(self.extract().candidates, ())

    def test_normalization_does_not_bypass_finite_raw_context_bound(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        raw = "Adjusted Net Profit excludes " + " " * 650 + "share-based payments."
        with self.mock_pages(raw):
            self.assertEqual(self.extract().candidates, ())

    def test_missing_registry_has_clear_error_and_creates_no_data(self) -> None:
        with self.assertRaises(ValueError) as raised:
            self.extract()
        self.assertIn("00700", str(raised.exception))
        self.assertIn("FY2024", str(raised.exception))
        self.assertFalse(self.registry.exists())

    def test_wrong_period_or_ticker_does_not_select_report(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        with self.assertRaises(ValueError):
            self.extract(fiscal_period="FY2025")
        with self.assertRaises(ValueError):
            extract_adjustment_candidates("1810", fiscal_period="FY2024",
                                          registry_path=self.registry)

    def test_invalid_period_type_and_ticker_are_rejected(self) -> None:
        for changes in ({"fiscal_period": " "}, {"fiscal_period": None},
                        {"report_type": "quarterly"}):
            with self.subTest(changes=changes):
                with self.assertRaises((TypeError, ValueError)):
                    self.extract(**changes)
        with self.assertRaises((TypeError, ValueError)):
            extract_adjustment_candidates("invalid", fiscal_period="FY2024",
                                          registry_path=self.registry)

    def test_ambiguous_selection_can_be_narrowed_by_report_type(self) -> None:
        annual = self.register(["SYNTHETIC annual Adjusted Net Profit excludes listing expenses."])
        interim = self.directory / "SYNTHETIC interim.pdf"
        interim.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC interim Adjusted Net Profit excludes impairment losses."
        ]))
        register_report(interim, **dict(self.metadata, report_type="interim"),
                        registry_path=self.registry)
        with self.assertRaises(ValueError) as raised:
            self.extract()
        self.assertIn("reports list", str(raised.exception))
        self.assertIn("--type", str(raised.exception))
        self.assertEqual(self.extract(report_type="annual").report, annual)

    def test_type_filter_does_not_silently_resolve_same_type_ambiguity(self) -> None:
        self.register(["SYNTHETIC first Adjusted Net Profit excludes listing expenses."])
        revised = self.directory / "SYNTHETIC revised annual.pdf"
        revised.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC revised Adjusted Net Profit excludes listing expenses."
        ]))
        register_report(revised, **self.metadata, registry_path=self.registry)
        with self.assertRaises(ValueError) as raised:
            self.extract(report_type="annual")
        self.assertIn("reports list", str(raised.exception))

    def test_missing_original_pdf_is_a_clear_error(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        self.pdf.unlink()
        with self.assertRaises(ValueError) as raised:
            self.extract()
        self.assertIn(str(self.pdf), str(raised.exception))

    def test_registered_hash_mismatch_stops_before_pdf_parser(self) -> None:
        self.register(["SYNTHETIC Adjusted Net Profit excludes listing expenses."])
        self.pdf.write_bytes(synthetic_unicode_pdf(["SYNTHETIC replacement"]))
        with patch("metric_mirage.search.PdfReader") as reader:
            with self.assertRaises(ValueError) as raised:
                self.extract()
        reader.assert_not_called()
        self.assertIn("changed", str(raised.exception).lower())

    def test_corrupt_registered_pdf_is_rejected(self) -> None:
        self.pdf.write_bytes(b"%PDF-1.4\nSYNTHETIC damaged objects\n%%EOF\n")
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        with self.assertRaises(ValueError) as raised:
            self.extract()
        self.assertIn("PDF", str(raised.exception))

    def test_no_text_pdf_is_distinct_from_empty_candidates(self) -> None:
        self.register(["", ""])
        with self.assertRaises(ValueError) as raised:
            self.extract()
        self.assertRegex(str(raised.exception).lower(), r"extractable text|text layer|no text")

    def test_page_failure_discards_partial_candidate_results(self) -> None:
        self.register(["SYNTHETIC placeholder"])
        reader = SimpleNamespace(is_encrypted=False, pages=[
            SimpleNamespace(extract_text=Mock(return_value="Adjusted Net Profit excludes listing expenses.")),
            SimpleNamespace(extract_text=Mock(side_effect=RuntimeError("SYNTHETIC font failure"))),
        ])
        with patch("metric_mirage.search.PdfReader", return_value=reader):
            with self.assertRaises(ValueError) as raised:
                self.extract()
        self.assertRegex(str(raised.exception).lower(), r"page\s+2")

    def test_encrypted_unreadable_pdf_retains_existing_error_behavior(self) -> None:
        self.pdf.write_bytes(encrypted_synthetic_pdf("SYNTHETIC secret"))
        register_report(self.pdf, **self.metadata, registry_path=self.registry)
        with self.assertRaises(ValueError) as raised:
            self.extract()
        self.assertIn("encrypted", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
