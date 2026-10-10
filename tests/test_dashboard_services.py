"""Offline dashboard integration using the existing synthetic PDF fixture."""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage.core import compare_metrics
from metric_mirage.dashboard.services import Workspace, demo_analysis
from metric_mirage.drift_report import render_json, render_markdown
from tests.test_pdf_search import synthetic_unicode_pdf


SELECTIONS = ["share_based_payments", "amortisation_of_intangible_assets"]


def report_bytes(period: str = "FY2024") -> bytes:
    """Invented disclosures with an actual extractable Unicode text layer."""
    additional = "、上市開支" if period == "FY2025" else ""
    return synthetic_unicode_pdf([
        f"SYNTHETIC {period} financial report. No real financial information.",
        f"SYNTHETIC 經調整淨利潤不包括股份支付開支、無形資產攤銷{additional}。",
        "SYNTHETIC Adjusted Net Profit excludes Share-Based Payments.",
    ])


class DashboardServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage dashboard services ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.workspace = Workspace(self.directory / "private")

    def register(self, period: str = "FY2024", **changes):
        metadata = {
            "filename": "SYNTHETIC.pdf", "ticker": "0700",
            "company": "SYNTHETIC 港股公司", "report_type": "annual",
            "fiscal_period": period, "source_name": "SYNTHETIC Issuer IR",
        }
        metadata.update(changes)
        return self.workspace.register_pdf(report_bytes(period), **metadata)

    def definition(self, period: str = "FY2024", **changes):
        report = self.register(period, **changes)
        names = SELECTIONS + (["listing_expenses"] if period == "FY2025" else [])
        return report, self.workspace.confirm(report, names)

    def files(self) -> set[Path]:
        return {path for path in self.workspace.root.rglob("*") if path.is_file()}

    def test_registration_persists_safe_path_and_original_pdf_digest(self) -> None:
        pdf = report_bytes()
        report = self.register(filename="../../outside.pdf")
        self.assertEqual(report.ticker, "00700")
        self.assertEqual(report.file_sha256, hashlib.sha256(pdf).hexdigest())
        self.assertEqual(Path(report.local_file).read_bytes(), pdf)
        self.assertTrue(Path(report.local_file).is_relative_to(self.workspace.root))
        self.assertFalse((self.directory / "outside.pdf").exists())
        self.assertEqual(Workspace(self.workspace.root).reports(), [report])

    def test_duplicate_registration_does_not_duplicate_or_overwrite(self) -> None:
        first = self.register()
        original = Path(first.local_file).read_bytes()
        second = self.register(filename="same disclosure renamed.pdf")
        self.assertEqual(first, second)
        self.assertEqual(self.workspace.reports(), [first])
        self.assertEqual(Path(first.local_file).read_bytes(), original)
        with self.assertRaises((TypeError, ValueError)):
            self.register(company="Different SYNTHETIC company")
        self.assertEqual(self.workspace.reports(), [first])

    def test_invalid_uploads_do_not_publish_reports(self) -> None:
        metadata = {
            "filename": "bad.pdf", "ticker": "700", "company": "SYNTHETIC",
            "report_type": "annual", "fiscal_period": "FY2024", "source_name": "SYNTHETIC",
        }
        for pdf, changes in (
            (b"This is not a PDF", {}),
            (report_bytes(), {"ticker": "../../700"}),
            (report_bytes(), {"report_type": "quarterly"}),
            (b"%PDF-1.7\n" + b"x" * (25 * 1024 * 1024) + b"\n%%EOF", {}),
        ):
            with self.subTest(changes=changes, size=len(pdf)):
                before = self.files()
                with self.assertRaises((TypeError, ValueError, OSError)):
                    self.workspace.register_pdf(pdf, **{**metadata, **changes})
                self.assertEqual(self.workspace.reports(), [])
                self.assertEqual(self.files(), before)

    def test_search_preserves_chinese_english_pages_and_limit(self) -> None:
        report = self.register()
        chinese = self.workspace.search(report, "經調整淨利潤")
        self.assertEqual(chinese.total_matches, 1)
        self.assertEqual(chinese.matches[0].page_number, 2)
        self.assertIn("股份支付開支", chinese.matches[0].excerpt)
        english = self.workspace.search(report, "adjusted NET profit", max_results=1)
        self.assertEqual(english.total_matches, 1)
        self.assertEqual(english.matches[0].page_number, 3)
        empty = self.workspace.search(report, "definitely absent synthetic keyword")
        self.assertEqual(empty.total_matches, 0)
        self.assertEqual(empty.matches, ())

    def test_candidates_require_explicit_present_selection(self) -> None:
        report = self.register()
        candidates = self.workspace.candidates(report)
        self.assertEqual({item.canonical_name for item in candidates.candidates}, set(SELECTIONS))
        self.assertEqual([item.page_number for item in candidates.candidates], [2, 2, 3])
        for names in ([], ["not_an_adjustment"], ["listing_expenses"]):
            with self.subTest(names=names), self.assertRaises((TypeError, ValueError)):
                self.workspace.confirm(report, names)
        self.assertEqual(self.workspace.definitions(), [])
        loaded = self.workspace.confirm(report, [SELECTIONS[0], SELECTIONS[0]])
        self.assertEqual(loaded.definition.adjustments, (SELECTIONS[0],))
        data = json.loads(self.workspace.read_artifact(loaded.path))
        self.assertEqual(set(data), {"company", "metric_name", "fiscal_period", "adjustments", "source"})
        self.assertEqual(data["metric_name"], "Adjusted Net Profit")
        self.assertEqual(loaded.sha256, hashlib.sha256(loaded.path.read_bytes()).hexdigest())
        self.assertEqual(loaded.ticker, "00700")

    def test_pdf_change_and_missing_source_are_rejected(self) -> None:
        report = self.register()
        source = Path(report.local_file)
        original = source.read_bytes()
        source.write_bytes(report_bytes("FY2025"))
        for action in (lambda: self.workspace.candidates(report), lambda: self.workspace.confirm(report, SELECTIONS)):
            with self.assertRaisesRegex(ValueError, "changed since registration"):
                action()
        source.write_bytes(original)
        source.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.workspace.search(report, "Adjusted Net Profit")
        self.assertEqual(self.workspace.definitions(), [])

    def test_definition_upload_keeps_exact_bytes_and_is_compatible(self) -> None:
        report, confirmed = self.definition()
        raw = self.workspace.read_artifact(confirmed.path)
        imported = self.workspace.import_definition(raw)
        self.assertEqual(imported.definition, confirmed.definition)
        self.assertEqual(imported.sha256, confirmed.sha256)
        self.assertEqual(self.workspace.read_artifact(imported.path), raw)
        self.assertEqual(self.workspace.load_definition(imported.path), imported)
        self.assertFalse(compare_metrics(confirmed.definition, imported.definition).has_definition_drift)
        self.assertEqual(Path(report.local_file).read_bytes(), report_bytes())

    def test_definition_upload_errors_leave_existing_files_unchanged(self) -> None:
        self.definition()
        for raw in (b"not JSON", b"[]", b"{}", b"{\"company\":\"one\",\"company\":\"two\"}", b"x" * (2 * 1024 * 1024 + 1)):
            with self.subTest(size=len(raw)):
                before = self.files()
                with self.assertRaises((TypeError, ValueError, OSError)):
                    self.workspace.import_definition(raw)
                self.assertEqual(self.files(), before)

    def test_arbitrary_files_cannot_be_opened_as_dashboard_artifacts(self) -> None:
        outside = self.directory / "secret.json"
        outside.write_text("private", encoding="utf-8")
        self.workspace.root.mkdir(parents=True)
        inside = self.workspace.root / "arbitrary.json"
        inside.write_text("private", encoding="utf-8")
        for path in (outside, inside, self.directory / "../secret.json"):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                self.workspace.read_artifact(path)
        with self.assertRaises((OSError, ValueError)):
            self.workspace.load_definition(outside)

    def test_complete_two_year_confirmation_evidence_and_report_exports(self) -> None:
        old_report, old = self.definition("FY2024")
        new_report, new = self.definition("FY2025")
        original_registry = self.workspace.registry_path.read_bytes()
        old_snapshot, old_path = self.workspace.create_evidence(old, old_report)
        new_snapshot, new_path = self.workspace.create_evidence(new, new_report)
        result = self.workspace.analyse(old, new, old_evidence=old_snapshot, new_evidence=new_snapshot)
        self.assertEqual(result["added_adjustments"], ["listing_expenses"])
        self.assertEqual(result["removed_adjustments"], [])
        self.assertEqual(result["unchanged_adjustments"], sorted(SELECTIONS))
        self.assertTrue(result["has_definition_drift"])
        self.assertEqual(json.loads(render_json(result)), result)
        markdown = render_markdown(result)
        self.assertIn("ADDED", markdown)
        self.assertIn("PDF Page Number", markdown)
        self.assertIn(old_report.file_sha256, markdown)
        sbc = next(item for item in result["evidence"] if item["canonical_name"] == "share_based_payments")
        self.assertEqual([entry["page_number"] for entry in sbc["old_evidence"]], [2, 3])
        self.assertEqual(json.loads(self.workspace.read_artifact(old_path)), old_snapshot)
        self.assertEqual(json.loads(self.workspace.read_artifact(new_path)), new_snapshot)
        self.assertEqual(self.workspace.registry_path.read_bytes(), original_registry)
        self.assertEqual(Path(old_report.local_file).read_bytes(), report_bytes("FY2024"))
        self.assertEqual(Path(new_report.local_file).read_bytes(), report_bytes("FY2025"))

    def test_evidence_upload_validates_definition_and_rejects_tampering(self) -> None:
        report, definition = self.definition()
        snapshot, path = self.workspace.create_evidence(definition, report)
        imported, imported_path = self.workspace.import_evidence(self.workspace.read_artifact(path), definition)
        self.assertEqual(imported, snapshot)
        self.assertEqual(json.loads(self.workspace.read_artifact(imported_path)), snapshot)
        for changes in ({"definition_sha256": "0" * 64}, {"company": "Another SYNTHETIC company"}, {"items": []}):
            with self.subTest(changes=changes):
                before = self.files()
                altered = json.dumps({**snapshot, **changes}, ensure_ascii=False).encode("utf-8")
                with self.assertRaises(ValueError):
                    self.workspace.import_evidence(altered, definition)
                self.assertEqual(self.files(), before)

    def test_comparison_errors_do_not_mix_company_period_or_partial_evidence(self) -> None:
        old_report, old = self.definition("FY2024")
        _, new = self.definition("FY2025")
        old_evidence, _ = self.workspace.create_evidence(old, old_report)
        for pair, kwargs in (((new, old), {}), ((old, old), {}), ((old, new), {"old_evidence": old_evidence})):
            with self.subTest(pair=[item.definition.fiscal_period for item in pair]), self.assertRaises(ValueError):
                self.workspace.analyse(*pair, **kwargs)
        altered = asdict(new.definition)
        altered["company"] = "Unrelated SYNTHETIC company"
        unrelated = self.workspace.import_definition(json.dumps(altered).encode("utf-8"))
        with self.assertRaisesRegex(ValueError, "company"):
            self.workspace.analyse(old, unrelated)
        plain = self.workspace.analyse(old, new)
        self.assertNotIn("evidence", plain)

    def test_demo_calls_core_engine_and_has_no_invented_evidence(self) -> None:
        with patch("metric_mirage.drift_report.compare_metrics", wraps=compare_metrics) as compare:
            result = demo_analysis()
        compare.assert_called_once()
        self.assertEqual(result["added_adjustments"], ["listing_expenses"])
        self.assertEqual(result["removed_adjustments"], [])
        self.assertEqual(result["unchanged_adjustments"], sorted(SELECTIONS))
        self.assertIn("SYNTHETIC", result["company"].upper())
        self.assertIn("SYNTHETIC", result["old_source"])
        self.assertIn("SYNTHETIC", result["new_source"])
        self.assertNotIn("evidence", result)

    def test_failed_atomic_publication_leaves_no_incomplete_artifact(self) -> None:
        _, confirmed = self.definition()
        raw = self.workspace.read_artifact(confirmed.path)
        before = self.files()
        with patch("metric_mirage.dashboard.services.os.link", side_effect=OSError("SYNTHETIC disk error")):
            with self.assertRaisesRegex(OSError, "Cannot save uploaded file"):
                self.workspace.import_definition(raw)
        self.assertEqual(self.files(), before)
        self.assertEqual(self.workspace.read_artifact(confirmed.path), raw)

    def test_definition_changes_invalidate_analysis_and_evidence_binding(self) -> None:
        report, old = self.definition("FY2024")
        _, new = self.definition("FY2025")
        raw = old.path.read_bytes()
        old.path.write_bytes(raw + b"\n")
        for action in (
            lambda: self.workspace.analyse(old, new),
            lambda: self.workspace.create_evidence(old, report),
            lambda: self.workspace.import_evidence(b"{}", old),
        ):
            with self.assertRaisesRegex(ValueError, "Definition SHA256 changed"):
                action()


if __name__ == "__main__":
    unittest.main()
