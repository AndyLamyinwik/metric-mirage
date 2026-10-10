"""Trace only confirmed items to invented, temporary PDF evidence."""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from metric_mirage.candidates import extract_adjustment_candidates
from metric_mirage.confirmation import confirm_candidates
from metric_mirage.core import MetricDefinition
from metric_mirage.definition_io import load_definition_snapshot
from metric_mirage.drift_report import build_drift_report, render_json, render_markdown
from metric_mirage.evidence import create_evidence_snapshot, load_evidence_snapshot
from metric_mirage.reports import register_report
from tests.test_pdf_search import synthetic_unicode_pdf


class _EvidenceFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage evidence ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.pdf = self.directory / "SYNTHETIC FY2024.pdf"
        self.registry = self.directory / "registry" / "reports.json"
        self.definition_path = self.directory / "definition.json"
        self.output = self.directory / "evidence.json"
        self.pages = [
            "SYNTHETIC 經調整淨利潤不包括股份支付開支、無形資產攤銷及上市開支。",
            "SYNTHETIC Adjusted Net Profit excludes Share-Based Payments.",
        ]
        self.pdf.write_bytes(synthetic_unicode_pdf(self.pages))
        self.record = register_report(
            self.pdf, ticker="0700", company="Synthetic HK Company",
            report_type="annual", fiscal_period="FY2024",
            source_name="SYNTHETIC: 發行人來源 | arbitrary delimiter",
            registry_path=self.registry,
        )
        self.confirmation = confirm_candidates(
            "00700", fiscal_period="FY2024",
            selected_adjustments=["share_based_payments", "amortisation_of_intangible_assets"],
            output_path=self.definition_path, registry_path=self.registry,
        )

    def create(self, **changes: object) -> dict:
        arguments = {
            "fiscal_period": "FY2024", "definition_path": self.definition_path,
            "output_path": self.output, "registry_path": self.registry,
        }
        arguments.update(changes)
        return create_evidence_snapshot("00700", **arguments)

    def save_snapshot(self, data: dict) -> None:
        self.output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def load(self) -> dict:
        definition, digest = load_definition_snapshot(self.definition_path)
        return load_evidence_snapshot(self.output, definition=definition, definition_sha256=digest)

    def change_definition(self, **changes: object) -> None:
        data = asdict(self.confirmation.definition)
        data.update(changes)
        self.definition_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class EvidenceSnapshotTests(_EvidenceFixture):
    def test_snapshot_preserves_exact_pdf_evidence_on_multiple_pages(self) -> None:
        result = self.create()
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["company"], self.record.company)
        self.assertEqual(result["ticker"], "00700")
        self.assertEqual(result["fiscal_period"], "FY2024")
        self.assertEqual(result["metric_name"], "Adjusted Net Profit")
        self.assertEqual(result["definition_sha256"], hashlib.sha256(self.definition_path.read_bytes()).hexdigest())
        self.assertEqual(result["report_sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())
        self.assertEqual(result["source_name"], self.record.source_name)
        self.assertEqual(result["report_type"], "annual")
        self.assertEqual([item["canonical_name"] for item in result["items"]],
                         ["amortisation_of_intangible_assets", "share_based_payments"])
        candidates = extract_adjustment_candidates("00700", fiscal_period="FY2024", registry_path=self.registry)
        expected = {
            name: [{"page_number": item.page_number, "matched_text": item.matched_text,
                    "evidence_excerpt": item.evidence_excerpt}
                   for item in candidates.candidates if item.canonical_name == name]
            for name in self.confirmation.definition.adjustments
        }
        self.assertEqual({item["canonical_name"]: item["evidence"] for item in result["items"]}, expected)
        payments = next(item for item in result["items"] if item["canonical_name"] == "share_based_payments")
        self.assertEqual([item["page_number"] for item in payments["evidence"]], [1, 2])
        self.assertEqual([item["matched_text"] for item in payments["evidence"]],
                         ["股份支付開支", "Share-Based Payments"])
        for item in result["items"]:
            for evidence in item["evidence"]:
                self.assertIn(evidence["evidence_excerpt"], self.pages[evidence["page_number"] - 1])
                self.assertIn(evidence["matched_text"], evidence["evidence_excerpt"])
        self.assertEqual(self.load(), result)

    def test_output_is_deterministic_utf8_and_does_not_modify_inputs(self) -> None:
        inputs = {path: path.read_bytes() for path in (self.pdf, self.registry, self.definition_path)}
        first = self.create()
        second_path = self.directory / "second.json"
        self.assertEqual(self.create(output_path=second_path), first)
        self.assertEqual(self.output.read_bytes(), second_path.read_bytes())
        self.assertIn("股份支付開支".encode("utf-8"), self.output.read_bytes())
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8")), first)
        for path, original in inputs.items():
            self.assertEqual(path.read_bytes(), original)

    def test_changed_pdf_or_missing_pdf_cannot_produce_snapshot(self) -> None:
        original = self.pdf.read_bytes()
        for content in (original + b"\nchanged", None):
            with self.subTest(missing=content is None):
                if content is None:
                    self.pdf.unlink()
                else:
                    self.pdf.write_bytes(content)
                with self.assertRaises((OSError, ValueError)):
                    self.create()
                self.assertFalse(self.output.exists())

    def test_definition_metadata_and_unsupported_source_are_rejected(self) -> None:
        cases = [
            {"company": "Other Synthetic Company"},
            {"fiscal_period": "FY2025"},
            {"metric_name": "Adjusted EBITDA"},
            {"source": "SYNTHETIC: unverified arbitrary source"},
            {"source": self.confirmation.definition.source.replace(self.record.file_sha256, "a" * 64)},
        ]
        for change in cases:
            with self.subTest(change=change):
                self.change_definition(**change)
                with self.assertRaises((TypeError, ValueError)):
                    self.create()
                self.assertFalse(self.output.exists())

    def test_confirmed_item_without_traceable_candidate_is_rejected(self) -> None:
        self.change_definition(adjustments=["share_based_payments", "impairment_losses"])
        with self.assertRaisesRegex(ValueError, "impairment_losses|evidence|candidate"):
            self.create()
        self.assertFalse(self.output.exists())

    def test_report_ambiguity_requires_existing_type_filter(self) -> None:
        other = self.directory / "SYNTHETIC results.pdf"
        other.write_bytes(synthetic_unicode_pdf(["SYNTHETIC other disclosure."]))
        register_report(other, ticker="00700", company=self.record.company,
                        report_type="annual_results", fiscal_period="FY2024",
                        source_name="SYNTHETIC results", registry_path=self.registry)
        with self.assertRaisesRegex(ValueError, "(?i)multiple|ambiguous|more than"):
            self.create()
        self.assertEqual(self.create(report_type="annual")["report_type"], "annual")

    def test_existing_output_is_preserved(self) -> None:
        original = b"valuable user content"
        self.output.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "exists|overwrit"):
            self.create()
        self.assertEqual(self.output.read_bytes(), original)

    def test_write_and_publication_failures_leave_no_partial_output(self) -> None:
        baseline = {path.name for path in self.directory.iterdir()}
        for operation in ("os.fsync", "os.link"):
            with self.subTest(operation=operation):
                with patch(f"metric_mirage.confirmation.{operation}", side_effect=OSError("synthetic write failure")):
                    with self.assertRaises(OSError):
                        self.create()
                self.assertFalse(self.output.exists())
                self.assertEqual({path.name for path in self.directory.iterdir()}, baseline)

    def test_loader_binds_snapshot_to_original_definition_file_bytes(self) -> None:
        original = self.definition_path.read_bytes()
        self.definition_path.write_bytes(b"\xef\xbb\xbf" + original)
        snapshot = self.create()
        definition, digest = load_definition_snapshot(self.definition_path)
        self.assertEqual(definition, self.confirmation.definition)
        self.assertEqual(digest, snapshot["definition_sha256"])
        self.assertNotEqual(digest, hashlib.sha256(original).hexdigest())
        self.assertEqual(self.load(), snapshot)
        self.definition_path.write_bytes(self.definition_path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "SHA256|sha256|hash"):
            self.load()

    def test_loader_rejects_wrong_metadata_source_or_report_digest(self) -> None:
        baseline = self.create()
        mutations = [
            ("company", "Other Synthetic Company"), ("fiscal_period", "FY2025"),
            ("metric_name", "Adjusted EBITDA"), ("ticker", "0700"),
            ("source_name", "SYNTHETIC wrong issuer"), ("report_type", "quarterly"),
            ("report_sha256", "a" * 64), ("definition_sha256", "b" * 64),
        ]
        for field, value in mutations:
            with self.subTest(field=field):
                changed = deepcopy(baseline)
                changed[field] = value
                self.save_snapshot(changed)
                with self.assertRaises((TypeError, ValueError)):
                    self.load()

    def test_loader_rejects_missing_unknown_or_wrongly_typed_schema(self) -> None:
        baseline = self.create()
        malformed = []
        missing = deepcopy(baseline)
        missing.pop("report_sha256")
        malformed.append(missing)
        extra = deepcopy(baseline)
        extra["invented_confidence"] = 1
        malformed.append(extra)
        for field, value in (("schema_version", True), ("schema_version", 2), ("items", {}),
                             ("report_sha256", "invalid"), ("items", [])):
            changed = deepcopy(baseline)
            changed[field] = value
            malformed.append(changed)
        for index, data in enumerate(malformed):
            with self.subTest(case=index):
                self.save_snapshot(data)
                with self.assertRaises((TypeError, ValueError)):
                    self.load()

    def test_loader_rejects_unconfirmed_duplicate_or_incomplete_items(self) -> None:
        baseline = self.create()
        mutations = []
        unknown = deepcopy(baseline)
        unknown["items"][0]["canonical_name"] = "listing_expenses"
        mutations.append(unknown)
        missing = deepcopy(baseline)
        missing["items"].pop()
        mutations.append(missing)
        duplicate = deepcopy(baseline)
        duplicate["items"].append(deepcopy(duplicate["items"][0]))
        mutations.append(duplicate)
        empty = deepcopy(baseline)
        empty["items"][0]["evidence"] = []
        mutations.append(empty)
        for index, data in enumerate(mutations):
            with self.subTest(case=index):
                self.save_snapshot(data)
                with self.assertRaises((TypeError, ValueError)):
                    self.load()

    def test_loader_rejects_invalid_or_conflicting_page_evidence(self) -> None:
        baseline = self.create()
        mutations = []
        for field, value in (("page_number", 0), ("page_number", True), ("matched_text", ""),
                             ("matched_text", "股份支付開支"),
                             ("evidence_excerpt", "a different passage without the matched text")):
            changed = deepcopy(baseline)
            changed["items"][0]["evidence"][0][field] = value
            mutations.append(changed)
        for conflicting in (False, True):
            changed = deepcopy(baseline)
            duplicate = deepcopy(changed["items"][0]["evidence"][0])
            if conflicting:
                duplicate["evidence_excerpt"] += " conflicting page quotation"
            changed["items"][0]["evidence"].append(duplicate)
            mutations.append(changed)
        for index, data in enumerate(mutations):
            with self.subTest(case=index):
                self.save_snapshot(data)
                with self.assertRaises((TypeError, ValueError)):
                    self.load()

    def test_loader_rejects_invalid_json_duplicate_keys_and_missing_file(self) -> None:
        for raw in ("{broken", '{"schema_version": 1, "schema_version": 1}'):
            with self.subTest(raw=raw):
                self.output.write_text(raw, encoding="utf-8")
                with self.assertRaises((TypeError, ValueError)):
                    self.load()
        self.output.unlink()
        with self.assertRaises((OSError, ValueError)):
            self.load()

    def test_definition_reader_rejects_duplicate_keys_before_hash_binding(self) -> None:
        raw = self.definition_path.read_text(encoding="utf-8")
        self.definition_path.write_text(raw.replace('{', '{"company": "Injected",', 1), encoding="utf-8")
        with self.assertRaises((TypeError, ValueError)):
            load_definition_snapshot(self.definition_path)


class DriftEvidenceTests(_EvidenceFixture):
    def setUp(self) -> None:
        super().setUp()
        self.old_snapshot = self.create()
        self.old = self.confirmation.definition
        pdf = self.directory / "SYNTHETIC FY2025.pdf"
        pdf.write_bytes(synthetic_unicode_pdf([
            "SYNTHETIC Adjusted Net Profit excludes share-based payments, listing expenses."
        ]))
        register_report(pdf, ticker="00700", company=self.record.company,
                        report_type="annual", fiscal_period="FY2025",
                        source_name="SYNTHETIC new-year report", registry_path=self.registry)
        path = self.directory / "definition_2025.json"
        self.new = confirm_candidates(
            "00700", fiscal_period="FY2025", selected_adjustments=["share_based_payments", "listing_expenses"],
            output_path=path, registry_path=self.registry,
        ).definition
        self.new_snapshot = create_evidence_snapshot(
            "00700", fiscal_period="FY2025", definition_path=path,
            output_path=self.directory / "evidence_2025.json", registry_path=self.registry,
        )

    def report(self) -> dict:
        return build_drift_report(self.old, self.new, old_evidence=self.old_snapshot, new_evidence=self.new_snapshot)

    def test_json_evidence_preserves_status_period_sides_and_all_pages(self) -> None:
        report = json.loads(render_json(self.report()))
        rows = {item["canonical_name"]: item for item in report["evidence"]}
        self.assertEqual(list(rows), ["amortisation_of_intangible_assets", "listing_expenses", "share_based_payments"])
        self.assertEqual(rows["amortisation_of_intangible_assets"]["status"], "REMOVED")
        self.assertEqual(rows["amortisation_of_intangible_assets"]["new_evidence"], [])
        self.assertEqual(rows["listing_expenses"]["status"], "ADDED")
        self.assertEqual(rows["listing_expenses"]["old_evidence"], [])
        self.assertEqual(rows["share_based_payments"]["status"], "UNCHANGED")
        self.assertEqual([item["page_number"] for item in rows["share_based_payments"]["old_evidence"]], [1, 2])
        for row in rows.values():
            for side, snapshot in (("old_evidence", self.old_snapshot), ("new_evidence", self.new_snapshot)):
                for item in row[side]:
                    self.assertEqual(item["report_sha256"], snapshot["report_sha256"])
                    original = next(candidate for candidate in snapshot["items"] if candidate["canonical_name"] == row["canonical_name"])
                    self.assertIn({key: item[key] for key in ("page_number", "matched_text", "evidence_excerpt")}, original["evidence"])
        base = build_drift_report(self.old, self.new)
        self.assertEqual({key: value for key, value in report.items() if key != "evidence"}, base)
        self.assertNotIn("evidence", base)

    def test_markdown_evidence_uses_literal_blocks_and_accurate_absent_side_language(self) -> None:
        old = deepcopy(self.old_snapshot)
        raw = "原文 | special **text**\n``` fenced text\n股份支付開支 [link](example) <tag>"
        item = next(item for item in old["items"] if item["canonical_name"] == "share_based_payments")
        item["evidence"][0]["evidence_excerpt"] = raw
        report = build_drift_report(self.old, self.new, old_evidence=old, new_evidence=self.new_snapshot)
        markdown = render_markdown(report)
        self.assertIn("Evidence", markdown)
        self.assertIn(raw, markdown)
        self.assertIn("````text\n" + raw + "\n````", markdown)
        self.assertIn(self.old_snapshot["report_sha256"], markdown)
        self.assertIn(self.new_snapshot["report_sha256"], markdown)
        self.assertIn("FY2024", markdown)
        self.assertIn("FY2025", markdown)
        self.assertIn("Not in the user-confirmed adjustment set", markdown)
        self.assertIn("does not establish", markdown.lower())
        self.assertNotIn("verified as absent", markdown.lower())

    def test_both_snapshots_are_required_and_wrong_year_is_rejected(self) -> None:
        for arguments in ({"old_evidence": self.old_snapshot}, {"new_evidence": self.new_snapshot},
                          {"old_evidence": self.new_snapshot, "new_evidence": self.old_snapshot}):
            with self.subTest(arguments=list(arguments)), self.assertRaises((TypeError, ValueError)):
                build_drift_report(self.old, self.new, **arguments)

    def test_direct_report_api_rejects_invalid_snapshot_and_different_tickers(self) -> None:
        for change in ({"company": "Other Synthetic Company"}, {"items": []}, {"ticker": "09988"}):
            with self.subTest(change=change):
                altered = {**deepcopy(self.old_snapshot), **change}
                with self.assertRaises((TypeError, ValueError)):
                    build_drift_report(self.old, self.new, old_evidence=altered,
                                       new_evidence=self.new_snapshot)


if __name__ == "__main__":
    unittest.main()
