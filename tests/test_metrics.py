"""Exercise literal adjustment-set comparisons and input validation."""

from collections import UserList
from dataclasses import FrozenInstanceError
from itertools import permutations
import unittest

from metric_mirage import MetricDefinition, MetricDiff, compare_metrics


def definition(
    adjustments: list[str] | tuple[str, ...],
    *,
    company: str = "Synthetic Example Company",
    metric_name: str = "Adjusted EBITDA",
    fiscal_period: str = "FY 2024",
) -> MetricDefinition:
    """Create a definition whose provenance is explicitly synthetic."""
    return MetricDefinition(
        company=company,
        metric_name=metric_name,
        fiscal_period=fiscal_period,
        adjustments=adjustments,
        source="SYNTHETIC test fixture; not a real financial report",
    )


class CompareMetricsTests(unittest.TestCase):
    def test_added_adjustment(self) -> None:
        old = definition(["Depreciation & Amortization", "Stock-Based Compensation"])
        new = definition(
            ["Depreciation & Amortization", "Stock-Based Compensation", "Restructuring Costs"],
            fiscal_period="FY 2025",
        )

        result = compare_metrics(old, new)

        self.assertIsInstance(result, MetricDiff)
        self.assertEqual(result.added_adjustments, ("restructuring costs",))
        self.assertEqual(result.removed_adjustments, ())
        self.assertEqual(
            result.unchanged_adjustments,
            ("depreciation & amortization", "stock-based compensation"),
        )

    def test_removed_adjustment(self) -> None:
        result = compare_metrics(
            definition(["Stock-Based Compensation", "Restructuring Costs"]),
            definition(["Stock-Based Compensation"], fiscal_period="FY 2025"),
        )

        self.assertEqual(result.added_adjustments, ())
        self.assertEqual(result.removed_adjustments, ("restructuring costs",))
        self.assertEqual(result.unchanged_adjustments, ("stock-based compensation",))

    def test_identical_definitions(self) -> None:
        item = definition(["Stock-Based Compensation", "Depreciation & Amortization"])

        result = compare_metrics(item, item)

        self.assertEqual(result.added_adjustments, ())
        self.assertEqual(result.removed_adjustments, ())
        self.assertEqual(
            result.unchanged_adjustments,
            ("depreciation & amortization", "stock-based compensation"),
        )

    def test_case_and_whitespace_differences(self) -> None:
        result = compare_metrics(
            definition(["  Stock-Based\tCompensation  ", "RESTRUCTURING   COSTS"]),
            definition(["stock-based compensation", "\nrestructuring costs\t"]),
        )

        self.assertEqual(result.added_adjustments, ())
        self.assertEqual(result.removed_adjustments, ())
        self.assertEqual(
            result.unchanged_adjustments,
            ("restructuring costs", "stock-based compensation"),
        )

    def test_unicode_casefold(self) -> None:
        result = compare_metrics(definition(["Straße Costs"]), definition(["STRASSE COSTS"]))

        self.assertEqual(result.unchanged_adjustments, ("strasse costs",))
        self.assertEqual(result.added_adjustments, ())
        self.assertEqual(result.removed_adjustments, ())

    def test_input_order_does_not_change_any_result_group(self) -> None:
        old_items = ["Zeta Costs", "Beta Costs", "Shared Costs"]
        new_items = ["Gamma Costs", "Alpha Costs", "Shared Costs"]
        for old_order in permutations(old_items):
            for new_order in permutations(new_items):
                with self.subTest(old=old_order, new=new_order):
                    result = compare_metrics(definition(old_order), definition(new_order))
                    self.assertEqual(result.added_adjustments, ("alpha costs", "gamma costs"))
                    self.assertEqual(result.removed_adjustments, ("beta costs", "zeta costs"))
                    self.assertEqual(result.unchanged_adjustments, ("shared costs",))

    def test_duplicates_collapse_after_normalization(self) -> None:
        result = compare_metrics(
            definition(["Shared Costs", " SHARED  COSTS ", "Removed Costs", "removed costs"]),
            definition(["shared costs", "Added Costs", " ADDED\tCOSTS "]),
        )

        self.assertEqual(result.added_adjustments, ("added costs",))
        self.assertEqual(result.removed_adjustments, ("removed costs",))
        self.assertEqual(result.unchanged_adjustments, ("shared costs",))

    def test_punctuation_and_wording_remain_distinct(self) -> None:
        result = compare_metrics(
            definition(["Research & Development", "Stock-Based Compensation"]),
            definition(["Research and Development", "Stock Based Compensation"]),
        )

        self.assertEqual(
            result.added_adjustments,
            ("research and development", "stock based compensation"),
        )
        self.assertEqual(
            result.removed_adjustments,
            ("research & development", "stock-based compensation"),
        )
        self.assertEqual(result.unchanged_adjustments, ())

    def test_company_and_metric_formatting_are_compatible(self) -> None:
        result = compare_metrics(
            definition([], company="  SYNTHETIC\tEXAMPLE Company ", metric_name="Adjusted\nEBITDA"),
            definition([], company="synthetic example company", metric_name=" adjusted  ebitda "),
        )

        self.assertEqual(result.added_adjustments, ())
        self.assertEqual(result.removed_adjustments, ())
        self.assertEqual(result.unchanged_adjustments, ())

    def test_different_companies_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "(?i)compan"):
            compare_metrics(definition([], company="Synthetic A"), definition([], company="Synthetic B"))

    def test_different_metrics_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "(?i)metric"):
            compare_metrics(definition([]), definition([], metric_name="Adjusted Net Income"))

    def test_empty_adjustment_lists(self) -> None:
        cases = [
            ([], [], (), (), ()),
            ([], ["Restructuring Costs"], ("restructuring costs",), (), ()),
            (["Restructuring Costs"], [], (), ("restructuring costs",), ()),
        ]
        for old_items, new_items, added, removed, unchanged in cases:
            with self.subTest(old=old_items, new=new_items):
                result = compare_metrics(definition(old_items), definition(new_items))
                self.assertEqual(result.added_adjustments, added)
                self.assertEqual(result.removed_adjustments, removed)
                self.assertEqual(result.unchanged_adjustments, unchanged)

    def test_result_groups_are_immutable_tuples(self) -> None:
        result = compare_metrics(definition(["Removed", "Shared"]), definition(["Added", "Shared"]))

        self.assertIsInstance(result.added_adjustments, tuple)
        self.assertIsInstance(result.removed_adjustments, tuple)
        self.assertIsInstance(result.unchanged_adjustments, tuple)

    def test_invalid_comparison_arguments_are_rejected(self) -> None:
        valid = definition([])
        for invalid in (None, {}, "Adjusted EBITDA", 42):
            for old, new in ((invalid, valid), (valid, invalid)):
                with self.subTest(old=old, new=new):
                    with self.assertRaisesRegex(TypeError, "MetricDefinition"):
                        compare_metrics(old, new)  # type: ignore[arg-type]


class MetricDefinitionTests(unittest.TestCase):
    def test_required_text_fields_reject_non_strings(self) -> None:
        fields = ("company", "metric_name", "fiscal_period", "source")
        for field in fields:
            for invalid in (None, 2024, [], True):
                with self.subTest(field=field, value=invalid):
                    kwargs = self.valid_kwargs()
                    kwargs[field] = invalid
                    with self.assertRaisesRegex(TypeError, field.split("_")[0]):
                        MetricDefinition(**kwargs)  # type: ignore[arg-type]

    def test_required_text_fields_reject_blank_strings(self) -> None:
        for field in ("company", "metric_name", "fiscal_period", "source"):
            for invalid in ("", " ", "\t\n"):
                with self.subTest(field=field, value=invalid):
                    kwargs = self.valid_kwargs()
                    kwargs[field] = invalid
                    with self.assertRaisesRegex(ValueError, field.split("_")[0]):
                        MetricDefinition(**kwargs)  # type: ignore[arg-type]

    def test_adjustments_reject_bare_strings_and_non_sequences(self) -> None:
        invalid_values = ("Restructuring Costs", b"Costs", None, 12, {}, {"Costs"}, iter(["Costs"]))
        for invalid in invalid_values:
            with self.subTest(value=invalid):
                kwargs = self.valid_kwargs()
                kwargs["adjustments"] = invalid
                with self.assertRaisesRegex(TypeError, "adjustment"):
                    MetricDefinition(**kwargs)  # type: ignore[arg-type]

    def test_adjustments_reject_non_string_items(self) -> None:
        for invalid in (None, 1, [], True):
            with self.subTest(item=invalid):
                kwargs = self.valid_kwargs()
                kwargs["adjustments"] = ["Valid Costs", invalid]
                with self.assertRaisesRegex(TypeError, "adjustment"):
                    MetricDefinition(**kwargs)  # type: ignore[arg-type]

    def test_adjustments_reject_blank_items(self) -> None:
        for invalid in ("", " ", "\n\t"):
            with self.subTest(item=invalid):
                with self.assertRaisesRegex(ValueError, "adjustment"):
                    definition(["Valid Costs", invalid])

    def test_adjustments_snapshot_mutable_input(self) -> None:
        items = ["Shared Costs"]
        item = definition(items)
        items.append("Later Costs")
        items[0] = "Changed Costs"

        self.assertIsInstance(item.adjustments, tuple)
        self.assertEqual(
            compare_metrics(item, definition(["Shared Costs"])).unchanged_adjustments,
            ("shared costs",),
        )
        self.assertEqual(compare_metrics(item, definition(["Shared Costs"])).added_adjustments, ())
        self.assertEqual(compare_metrics(item, definition(["Shared Costs"])).removed_adjustments, ())

    def test_general_sequence_input_is_snapshotted(self) -> None:
        kwargs = self.valid_kwargs()
        original = UserList(["Shared Costs"])
        kwargs["adjustments"] = original
        item = MetricDefinition(**kwargs)  # type: ignore[arg-type]
        original.append("Later Costs")

        self.assertIsInstance(item.adjustments, tuple)
        self.assertEqual(compare_metrics(item, definition(["Shared Costs"])).unchanged_adjustments, ("shared costs",))
        self.assertEqual(compare_metrics(item, definition([])).removed_adjustments, ("shared costs",))

    def test_definition_is_frozen(self) -> None:
        item = definition([])

        with self.assertRaises(FrozenInstanceError):
            item.company = "Changed Company"  # type: ignore[misc]

    def test_fiscal_period_is_an_opaque_nonempty_label(self) -> None:
        result = compare_metrics(
            definition([], fiscal_period="Prior Report"),
            definition([], fiscal_period="Current Report"),
        )

        self.assertEqual(result.added_adjustments, ())
        self.assertEqual(result.removed_adjustments, ())
        self.assertEqual(result.unchanged_adjustments, ())

    @staticmethod
    def valid_kwargs() -> dict[str, object]:
        return {
            "company": "Synthetic Example Company",
            "metric_name": "Adjusted EBITDA",
            "fiscal_period": "FY 2024",
            "adjustments": [],
            "source": "SYNTHETIC test fixture",
        }


if __name__ == "__main__":
    unittest.main()
