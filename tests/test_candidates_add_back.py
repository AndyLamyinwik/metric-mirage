"""Offline regression coverage for explicit add-back definitions and Roman lists."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from metric_mirage.candidates import extract_adjustment_candidates
from metric_mirage.confirmation import confirm_candidates
from metric_mirage.evidence import create_evidence_snapshot, load_evidence_snapshot
from tests.test_candidates import ENGLISH_ITEMS
from tests.test_pdf_search import synthetic_unicode_pdf
from metric_mirage.reports import register_report


SUPPORTED_ITEMS = (
    ("share_based_payments", "share-based compensation"),
    ("fair_value_changes", "net fair value changes on investments"),
    ("amortisation_of_intangible_assets",
     "amortization of intangible assets resulting from acquisitions"),
)
DEFINITION = (
    "We define Adjusted Net Profit as profit for the {period}, as adjusted by adding back "
    "(i) share-based compensation, (ii) net fair value changes on investments, "
    "(iii) amortization of intangible assets resulting from acquisitions, "
    "(iv) changes of value of financial liabilities to fund investors, and "
    "(v) income tax effects of non-IFRS adjustments."
)


class AddBackCandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage add-back ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.registry = self.directory / "reports.json"
        self.pdf = self.directory / "SYNTHETIC add-back.pdf"
        self.pdf.write_bytes(synthetic_unicode_pdf(["SYNTHETIC placeholder"]))
        register_report(
            self.pdf, ticker="1810", company="Synthetic Add-back Company",
            report_type="annual", fiscal_period="FY2024",
            source_name="SYNTHETIC explicit add-back fixture", registry_path=self.registry,
        )

    def extract_pages(self, *pages: str):
        with patch("metric_mirage.search.PdfReader", return_value=SimpleNamespace(
            is_encrypted=False,
            pages=[SimpleNamespace(extract_text=Mock(return_value=text)) for text in pages],
        )):
            return extract_adjustment_candidates(
                "1810", fiscal_period="FY2024", registry_path=self.registry,
            )

    def assert_items(self, raw: str, expected=SUPPORTED_ITEMS):
        result = self.extract_pages(raw)
        self.assertEqual(
            [(item.canonical_name, item.matched_text) for item in result.candidates],
            list(expected),
        )
        for item in result.candidates:
            self.assertIn(item.evidence_excerpt, raw)
            self.assertIn(item.matched_text, item.evidence_excerpt)
        return result

    def test_year_and_period_definitions_stop_before_unknown_adjustments(self) -> None:
        for period in ("year", "period"):
            raw = DEFINITION.format(period=period)
            with self.subTest(period=period):
                result = self.assert_items(raw)
                for item in result.candidates:
                    self.assertTrue(item.evidence_excerpt.startswith("We define Adjusted Net Profit"))
                    self.assertNotIn("financial liabilities", item.evidence_excerpt)
                    self.assertNotIn("income tax", item.evidence_excerpt)

    def test_synthetic_pdf_definition_can_confirm_and_export_evidence(self) -> None:
        # These PDF bytes are invented fixtures, not a downloaded company report.
        self.pdf.write_bytes(synthetic_unicode_pdf([DEFINITION.format(period="year")]))
        self.registry = self.directory / "add_back_reports.json"
        register_report(
            self.pdf, ticker="1810", company="Synthetic Add-back Company",
            report_type="annual", fiscal_period="FY2024",
            source_name="SYNTHETIC explicit add-back fixture", registry_path=self.registry,
        )
        definition_path = self.directory / "definition.json"
        confirmation = confirm_candidates(
            "1810", fiscal_period="FY2024", selected_adjustments=[name for name, _ in SUPPORTED_ITEMS],
            output_path=definition_path, registry_path=self.registry,
        )
        snapshot_path = self.directory / "evidence.json"
        snapshot = create_evidence_snapshot(
            "1810", fiscal_period="FY2024", definition_path=definition_path,
            output_path=snapshot_path, registry_path=self.registry,
        )
        loaded = load_evidence_snapshot(
            snapshot_path, definition=confirmation.definition,
            definition_sha256=snapshot["definition_sha256"],
        )
        self.assertEqual(loaded, snapshot)
        self.assertEqual(
            {item["canonical_name"]: item["evidence"][0]["matched_text"] for item in snapshot["items"]},
            dict(SUPPORTED_ITEMS),
        )

    def test_passive_definition_and_calculation_are_explicit_relations(self) -> None:
        for verb in ("defined", "calculated", "computed"):
            raw = (
                f"Adjusted Net Profit is {verb} as net profit for the period, "
                "as adjusted by adding back listing expenses."
            )
            with self.subTest(verb=verb):
                self.assert_items(raw, (("listing_expenses", "listing expenses"),))

    def test_quoted_active_definition_preserves_definition_verb(self) -> None:
        for definition in ("We define", "The group defines", "The company defines"):
            raw = (
                f'{definition} "Adjusted Net Profit" as profit for the year, '
                "as adjusted by adding back (i) listing expenses."
            )
            with self.subTest(definition=definition):
                result = self.assert_items(raw, (("listing_expenses", "listing expenses"),))
                self.assertTrue(result.candidates[0].evidence_excerpt.startswith(definition))

    def test_numbered_list_supports_all_existing_canonicals_in_order(self) -> None:
        numerals = ("i", "ii", "iii", "iv", "v", "vi")
        for separator in (", ", "\n", ";\n"):
            raw = "Adjusted Net Profit reconciliation:\n" + separator.join(
                f"({number}) {label}" for number, (_, label) in zip(numerals, ENGLISH_ITEMS)
            ) + "."
            with self.subTest(separator=separator):
                self.assert_items(raw, ENGLISH_ITEMS)

    def test_raw_line_wraps_and_case_are_preserved_in_full_aliases(self) -> None:
        raw = (
            "We define Adjusted\n Net Profit as profit for the year, as adjusted by adding back\n"
            "(i) Share-Based\n Compensation, (ii) Net Fair Value Changes\n on Investments, "
            "(iii) Amortization of Intangible Assets\n resulting from acquisitions."
        )
        result = self.assert_items(raw, (
            ("share_based_payments", "Share-Based\n Compensation"),
            ("fair_value_changes", "Net Fair Value Changes\n on Investments"),
            ("amortisation_of_intangible_assets",
             "Amortization of Intangible Assets\n resulting from acquisitions"),
        ))
        self.assertTrue(all(item.page_number == 1 for item in result.candidates))

    def test_british_spelling_is_a_complete_alias(self) -> None:
        raw = ("Adjusted Net Profit excludes "
               "amortisation of intangible assets resulting from acquisitions.")
        self.assert_items(raw, (("amortisation_of_intangible_assets",
                                "amortisation of intangible assets resulting from acquisitions"),))

    def test_unsupported_item_stops_list_without_scanning_later_known_labels(self) -> None:
        for items, expected in (
            ("(i) income tax effects, (ii) listing expenses.", ()),
            ("(i) listing expenses, (ii) income tax effects, (iii) impairment losses.",
             (("listing_expenses", "listing expenses"),)),
            ("(i) listing expenses, (ii) changes of value of financial liabilities to investors, "
             "(iii) share-based compensation.", (("listing_expenses", "listing expenses"),)),
        ):
            with self.subTest(items=items):
                self.assert_items("Adjusted Net Profit excludes " + items, expected)

    def test_unknown_qualifiers_and_ordinary_expense_prose_are_not_truncated(self) -> None:
        for item in (
            "net fair value changes on derivatives",
            "amortization of intangible assets resulting from restructuring",
            "net fair value changes on investments are reported in operating costs",
            "amortization of intangible assets resulting from acquisitions are ordinary expenses",
            "share-based compensation, which is not added back",
        ):
            with self.subTest(item=item):
                self.assert_items(
                    "We define Adjusted Net Profit as profit for the year, "
                    "as adjusted by adding back (i) " + item + ".", (),
                )

    def test_numbering_requires_first_marker_and_contiguous_roman_sequence(self) -> None:
        for items, expected in (
            ("(ii) listing expenses.", ()),
            ("i) listing expenses.", ()),
            ("(1) listing expenses.", ()),
            ("(i) listing expenses, (iii) impairment losses.",
             (("listing_expenses", "listing expenses"),)),
            ("(i) listing expenses, (i) impairment losses.",
             (("listing_expenses", "listing expenses"),)),
            ("(i) listing expenses, impairment losses.",
             (("listing_expenses", "listing expenses"),)),
        ):
            with self.subTest(items=items):
                self.assert_items("Adjusted Net Profit excludes " + items, expected)

    def test_negated_uncertain_and_questioned_definitions_are_rejected(self) -> None:
        raw = (
            "We define Adjusted Net Profit as profit for the year, "
            "as adjusted by adding back (i) listing expenses."
        )
        for text in (
            raw.replace("We define", "We do not define"),
            raw.replace("We define", "We may define"),
            "It is incorrect to say that " + raw,
            "Whether " + raw,
            raw.replace("as adjusted by", "as not adjusted by"),
            raw.replace(".", "?"),
            "Adjusted Net Profit is not defined as profit for the year, "
            "as adjusted by adding back (i) listing expenses.",
        ):
            with self.subTest(text=text):
                self.assert_items(text, ())

    def test_bare_as_relation_and_other_metrics_do_not_claim_labels(self) -> None:
        for text in (
            "Adjusted Net Profit as profit for the year, as adjusted by adding back listing expenses.",
            "We describe Adjusted Net Profit as profit for the year, "
            "as adjusted by adding back listing expenses.",
            "We define Adjusted EBITDA as profit for the year, as adjusted by adding back listing expenses.",
            "We define Unadjusted Net Profit as profit for the year, "
            "as adjusted by adding back listing expenses.",
            "We define Adjusted Net Profit as revenue for the year, "
            "as adjusted by adding back listing expenses.",
        ):
            with self.subTest(text=text):
                self.assert_items(text, ())

    def test_new_relation_keeps_paragraph_page_sentence_and_raw_context_limits(self) -> None:
        heading = (
            "We define Adjusted Net Profit as profit for the year, as adjusted by adding back "
        )
        self.assert_items(heading + "\n\n(i) listing expenses.", ())
        self.assert_items(heading + ". (i) listing expenses.", ())
        self.assert_items(heading + " " * 650 + "(i) listing expenses.", ())
        self.assertEqual(self.extract_pages(heading, "(i) listing expenses.").candidates, ())
        result = self.extract_pages("", heading + "(i) listing expenses.")
        self.assertEqual([(item.page_number, item.canonical_name) for item in result.candidates],
                         [(2, "listing_expenses")])


if __name__ == "__main__":
    unittest.main()
