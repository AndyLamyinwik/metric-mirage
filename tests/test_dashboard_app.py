"""Exercise real Streamlit widgets, uploaded files and an offline two-year flow."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from streamlit.delta_generator import DeltaGenerator
import streamlit as st
from streamlit.testing.v1 import AppTest

from metric_mirage.core import compare_metrics
from metric_mirage.dashboard.services import Workspace
from metric_mirage.drift_report import render_json, render_markdown
from tests.test_dashboard_services import SELECTIONS, report_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DashboardAppTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage AppTest ")
        self.addCleanup(temporary.cleanup)
        self.workspace = Workspace(Path(temporary.name) / "private")
        environment = patch.dict(os.environ, {"METRIC_MIRAGE_DATA_DIR": str(self.workspace.root)})
        environment.start()
        self.addCleanup(environment.stop)
        self.app = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=20)
        self.app.run()
        self.assert_healthy()

    def assert_healthy(self) -> None:
        self.assertEqual(len(self.app.exception), 0, [error.message for error in self.app.exception])

    def navigate(self, page: str) -> None:
        self.app.sidebar.radio(key="page").set_value(page).run()
        self.assert_healthy()

    def select_period(self, key: str, period: str) -> None:
        selector = self.app.selectbox(key=key)
        index = next(index for index, label in enumerate(selector.options) if period in label)
        selector.select_index(index).run()
        self.assert_healthy()

    def register_ui(self, period: str = "FY2024", *, ticker: str = "0700", company: str = "SYNTHETIC 港股公司") -> None:
        self.navigate("Report Manager")
        self.app.file_uploader(key="pdf_upload").set_value(("../../SYNTHETIC.pdf", report_bytes(period), "application/pdf"))
        self.app.text_input(key="ticker").set_value(ticker)
        self.app.text_input(key="company").set_value(company)
        self.app.text_input(key="fiscal_period").set_value(period)
        self.app.text_input(key="source_name").set_value("SYNTHETIC Issuer IR")
        self.app.button(key="register_pdf").click().run()
        self.assert_healthy()

    def prepared_definitions(self):
        pairs = []
        for period in ("FY2024", "FY2025"):
            report = self.workspace.register_pdf(
                report_bytes(period), filename="SYNTHETIC.pdf", ticker="700",
                company="SYNTHETIC 港股公司", report_type="annual",
                fiscal_period=period, source_name="SYNTHETIC Issuer IR",
            )
            names = SELECTIONS + (["listing_expenses"] if period == "FY2025" else [])
            pairs.append((report, self.workspace.confirm(report, names)))
        return pairs

    def test_all_pages_start_and_empty_states_are_clear(self) -> None:
        self.assertEqual(self.app.title[0].value, "Metric Mirage")
        self.assertTrue(any("Financial Metric Forensics for Hong Kong Equities" in element.value for element in self.app.caption))
        for page in ("Report Manager", "Financial Metric Explorer", "Drift Analysis", "Evidence Explorer"):
            with self.subTest(page=page):
                self.navigate(page)
                self.assertEqual(len(self.app.error), 0)
        self.assertTrue(any(element.value == "No Evidence Attached" for element in self.app.info))
        self.assertFalse(self.workspace.registry_path.exists())

    def test_demo_is_computed_labeled_and_persists_without_fake_evidence(self) -> None:
        with patch("metric_mirage.drift_report.compare_metrics", wraps=compare_metrics) as compare:
            self.app.button(key="explore_demo").click().run()
        self.assert_healthy()
        compare.assert_called_once()
        metrics = {element.label: element.value for element in self.app.metric}
        self.assertEqual(metrics, {"Added Adjustments": "1", "Removed Adjustments": "0", "Unchanged Adjustments": "2"})
        self.assertTrue(any("SYNTHETIC DEMO" in element.value for element in self.app.warning))
        self.assertTrue(any(element.value == "Definition Set Changed" for element in self.app.success))
        self.assertTrue(any(element.value == "No Evidence Attached" for element in self.app.info))
        self.assertNotIn("evidence", self.app.session_state["analysis"])
        chart = json.loads(self.app.get("plotly_chart")[0].proto.spec)
        self.assertEqual(chart["data"][0]["y"], [1, 0, 2])
        self.app.run()
        self.assert_healthy()
        self.assertEqual(self.app.session_state["analysis"]["added_adjustments"], ["listing_expenses"])
        self.navigate("Drift Analysis")
        self.assertEqual(len(self.app.download_button), 2)
        self.assertEqual(self.app.session_state["analysis"]["added_adjustments"], ["listing_expenses"])
        self.assertNotIn("evidence", self.app.session_state["analysis"])
        self.assertTrue(any("SYNTHETIC DEMO" in element.value for element in self.app.warning))

    def test_invalid_ticker_upload_and_empty_confirmation_show_input_errors(self) -> None:
        self.register_ui(ticker="700.INVALID")
        self.assertTrue(self.app.error)
        self.assertEqual(self.workspace.reports(), [])
        self.app.file_uploader(key="pdf_upload").set_value(("invalid.pdf", b"not a PDF", "application/pdf"))
        self.app.text_input(key="ticker").set_value("700")
        self.app.button(key="register_pdf").click().run()
        self.assert_healthy()
        self.assertTrue(self.app.error)
        self.assertEqual(self.workspace.reports(), [])
        self.register_ui()
        self.navigate("Financial Metric Explorer")
        self.app.button(key="extract_candidates").click().run()
        self.assertEqual(self.app.multiselect(key="selected_adjustments").value, [])
        self.app.button(key="confirm_selection").click().run()
        self.assert_healthy()
        self.assertTrue(self.app.error)
        self.assertEqual(self.workspace.definitions(), [])

    def test_complete_pdf_upload_confirmation_evidence_and_download_workflow(self) -> None:
        captured: dict[str, bytes] = {}
        original_download = DeltaGenerator.download_button
        original_root_download = st.download_button

        def download(widget, label, data, *args, **kwargs):
            captured[label] = data
            return original_download(widget, label, data, *args, **kwargs)

        def root_download(label, data, *args, **kwargs):
            captured[label] = data
            return original_root_download(label, data, *args, **kwargs)

        with patch.object(DeltaGenerator, "download_button", download), patch.object(st, "download_button", root_download):
            for period in ("FY2024", "FY2025"):
                self.register_ui(period)
                self.assertTrue(any("Report registered" in message.value for message in self.app.success))
                self.assertTrue(any("PDF SHA256" in frame.value.columns for frame in self.app.dataframe))
                self.navigate("Financial Metric Explorer")
                self.app.selectbox(key="explorer_period").set_value(period).run()
                self.app.text_input(key="keyword").set_value("經調整淨利潤").run()
                self.app.button(key="search").click().run()
                result = self.app.session_state["search_result"]
                self.assertEqual(result.total_matches, 1)
                self.assertEqual(result.matches[0].page_number, 2)
                self.assertIn("股份支付開支", result.matches[0].excerpt)
                self.app.text_input(key="keyword").set_value("adjusted NET profit").run()
                self.app.button(key="search").click().run()
                self.assertEqual(self.app.session_state["search_result"].matches[0].page_number, 3)
                self.app.button(key="extract_candidates").click().run()
                self.assertEqual(self.app.multiselect(key="selected_adjustments").value, [])
                names = SELECTIONS + (["listing_expenses"] if period == "FY2025" else [])
                self.app.multiselect(key="selected_adjustments").set_value(names).run()
                self.app.button(key="confirm_selection").click().run()
                self.assert_healthy()
                confirmed = self.app.session_state["confirmed_definition"]
                exported = json.loads(captured["Download MetricDefinition JSON"])
                self.assertEqual(exported["adjustments"], sorted(names))
                self.assertEqual(exported["fiscal_period"], period)
                self.assertEqual(captured["Download MetricDefinition JSON"], confirmed.path.read_bytes())
            self.assertEqual(len(self.workspace.reports()), 2)
            self.assertEqual(len(self.workspace.definitions()), 2)
            self.navigate("Evidence Explorer")
            for period in ("FY2024", "FY2025"):
                self.select_period("evidence_definition", period)
                self.app.button(key="create_evidence").click().run()
                self.assert_healthy()
                snapshot = self.app.session_state["evidence_snapshot"]
                self.assertEqual(snapshot["fiscal_period"], period)
                self.assertEqual(json.loads(captured["Download Evidence Snapshot JSON"]), snapshot)
                self.app.selectbox(key="evidence_adjustment").set_value("share_based_payments").run()
                self.assertTrue(any("股份支付開支" in code.value for code in self.app.code))
            self.navigate("Drift Analysis")
            self.select_period("old_definition", "FY2024")
            self.select_period("new_definition", "FY2025")
            self.app.button(key="run_analysis").click().run()
            self.assert_healthy()
            report = self.app.session_state["analysis"]
            self.assertEqual(report["added_adjustments"], ["listing_expenses"])
            self.assertEqual(report["removed_adjustments"], [])
            self.assertEqual(report["unchanged_adjustments"], sorted(SELECTIONS))
            self.assertEqual(captured["Download Markdown"], render_markdown(report).encode("utf-8"))
            self.assertEqual(captured["Download JSON"], render_json(report).encode("utf-8"))
            self.assertEqual(json.loads(captured["Download JSON"]), report)
            self.assertTrue(any(element.value == "Evidence Available" for element in self.app.success))
            self.app.selectbox(key="drift_adjustment").set_value("share_based_payments").run()
            self.assertTrue(any("PDF Page 3" in element.value for element in self.app.caption))
            self.navigate("Overview")
            self.assertEqual(self.app.session_state["analysis"], report)
            self.assertTrue(any("00700" in element.value for element in self.app.caption))

    def test_definition_uploads_zero_difference_and_period_errors(self) -> None:
        (_, old), (_, new) = self.prepared_definitions()
        self.navigate("Drift Analysis")
        self.app.file_uploader(key="old_definition_upload").set_value(("old.json", old.path.read_bytes(), "application/json"))
        new_data = json.loads(new.path.read_bytes())
        new_data["adjustments"] = list(old.definition.adjustments)
        self.app.file_uploader(key="new_definition_upload").set_value(("new.json", json.dumps(new_data).encode("utf-8"), "application/json"))
        self.app.run()
        self.app.button(key="run_analysis").click().run()
        self.assert_healthy()
        self.assertFalse(self.app.session_state["analysis"]["has_definition_drift"])
        self.assertTrue(any(element.value == "No Set Changes Detected" for element in self.app.info))
        self.assertTrue(any(element.value == "No Evidence Attached" for element in self.app.info))
        self.app.file_uploader(key="old_definition_upload").set_value(("reversed.json", new.path.read_bytes(), "application/json")).run()
        self.assertNotIn("analysis", self.app.session_state)
        self.app.button(key="run_analysis").click().run()
        self.assert_healthy()
        self.assertTrue(any("must be later" in element.value for element in self.app.error))
        self.app.file_uploader(key="new_definition_upload").set_value(("broken.json", b"not JSON", "application/json")).run()
        self.assert_healthy()
        self.assertTrue(self.app.error)
        self.assertNotIn("analysis", self.app.session_state)

    def test_evidence_upload_validation_and_definition_switch_clear_stale_data(self) -> None:
        (old_report, old), (_, _) = self.prepared_definitions()
        snapshot, path = self.workspace.create_evidence(old, old_report)
        self.navigate("Evidence Explorer")
        self.select_period("evidence_definition", "FY2024")
        wrong = {**snapshot, "definition_sha256": "0" * 64}
        self.app.file_uploader(key="evidence_upload").set_value(("bad.json", json.dumps(wrong).encode("utf-8"), "application/json")).run()
        self.assert_healthy()
        self.assertTrue(any("Evidence Validation Failed" in element.value for element in self.app.error))
        self.assertNotIn("evidence_snapshot", self.app.session_state)
        self.app.file_uploader(key="evidence_upload").set_value(("good.json", path.read_bytes(), "application/json")).run()
        self.assert_healthy()
        self.assertEqual(self.app.session_state["evidence_snapshot"], snapshot)
        self.assertTrue(any(element.value == "Evidence Available" for element in self.app.success))
        self.navigate("Drift Analysis")
        self.select_period("old_definition", "FY2024")
        self.select_period("new_definition", "FY2025")
        self.app.button(key="run_analysis").click().run()
        self.assert_healthy()
        self.assertTrue(any("Attach matching evidence for both years" in error.value for error in self.app.error))
        self.assertNotIn("analysis", self.app.session_state)
        self.app.checkbox(key="include_evidence").set_value(False).run()
        self.app.button(key="run_analysis").click().run()
        self.assert_healthy()
        self.assertNotIn("evidence", self.app.session_state["analysis"])
        self.assertEqual(len(self.app.download_button), 2)
        self.navigate("Evidence Explorer")
        self.select_period("evidence_definition", "FY2025")
        self.assertTrue(any("Evidence Validation Failed" in element.value for element in self.app.error))
        self.assertNotIn("evidence_snapshot", self.app.session_state)
        self.app.button(key="clear_evidence_upload").click().run()
        self.assert_healthy()
        self.assertTrue(any(element.value == "No Evidence Attached" for element in self.app.info))

    def test_company_switch_and_search_change_do_not_reuse_old_results(self) -> None:
        self.register_ui(company="A SYNTHETIC Company")
        self.register_ui("FY2025", ticker="9988", company="B SYNTHETIC Company")
        self.navigate("Financial Metric Explorer")
        self.app.selectbox(key="explorer_company").select_index(0).run()
        self.app.button(key="search").click().run()
        self.app.button(key="extract_candidates").click().run()
        self.app.multiselect(key="selected_adjustments").set_value(["share_based_payments"]).run()
        self.app.button(key="confirm_selection").click().run()
        self.assertEqual(self.app.session_state["confirmed_definition"].definition.company, "A SYNTHETIC Company")
        self.app.run()
        self.assertIn("candidate_result", self.app.session_state)
        self.app.selectbox(key="explorer_company").select_index(1).run()
        self.assert_healthy()
        for key in ("search_result", "candidate_result", "confirmed_definition"):
            self.assertNotIn(key, self.app.session_state)
        self.app.button(key="extract_candidates").click().run()
        self.assertEqual(self.app.multiselect(key="selected_adjustments").value, [])
        self.assertEqual(self.app.session_state["candidate_result"].report.company, "B SYNTHETIC Company")
        self.app.text_input(key="keyword").set_value("does not exist").run()
        self.app.button(key="search").click().run()
        self.assert_healthy()
        self.assertTrue(any("No keyword matches found" in element.value for element in self.app.info))

    def test_invalid_evidence_cannot_be_silently_used_for_analysis(self) -> None:
        (report, old), (_, new) = self.prepared_definitions()
        snapshot, _ = self.workspace.create_evidence(old, report)
        self.navigate("Drift Analysis")
        self.select_period("old_definition", "FY2024")
        self.select_period("new_definition", "FY2025")
        self.app.button(key="run_analysis").click().run()
        self.assertIn("analysis", self.app.session_state)
        wrong = json.dumps({**snapshot, "company": "Unrelated SYNTHETIC"}).encode("utf-8")
        self.app.file_uploader(key="old_evidence_upload").set_value(("wrong.json", wrong, "application/json")).run()
        self.assert_healthy()
        self.assertNotIn("analysis", self.app.session_state)
        self.app.button(key="run_analysis").click().run()
        self.assertTrue(any("Evidence Validation Failed" in element.value for element in self.app.error))
        self.assertNotIn("analysis", self.app.session_state)
        self.assertEqual(len(self.app.download_button), 0)

    def test_uploaded_definitions_persist_across_pages_and_replacement_invalidates_evidence(self) -> None:
        (old_report, old), (new_report, new) = self.prepared_definitions()
        _, old_path = self.workspace.create_evidence(old, old_report)
        _, new_path = self.workspace.create_evidence(new, new_report)
        self.navigate("Drift Analysis")
        for side, definition, evidence_path in (("old", old, old_path), ("new", new, new_path)):
            self.app.file_uploader(key=f"{side}_definition_upload").set_value((f"{side}.json", definition.path.read_bytes(), "application/json"))
            self.app.file_uploader(key=f"{side}_evidence_upload").set_value((f"{side}_evidence.json", evidence_path.read_bytes(), "application/json"))
        self.app.run()
        self.app.button(key="run_analysis").click().run()
        self.assert_healthy()
        analysis = self.app.session_state["analysis"]
        self.assertIn("evidence", analysis)
        self.navigate("Overview")
        self.navigate("Drift Analysis")
        self.assertEqual(self.app.session_state["analysis"], analysis)
        self.assertEqual(len(self.app.download_button), 2)
        self.app.file_uploader(key="old_definition_upload").set_value(("changed.json", old.path.read_bytes() + b"\n", "application/json")).run()
        self.assert_healthy()
        self.assertNotIn("analysis", self.app.session_state)
        self.assertTrue(any("Evidence Validation Failed" in error.value for error in self.app.error))
        self.assertEqual(len(self.app.download_button), 0)

    def test_replaced_pdf_with_same_period_is_ambiguous_and_clears_old_results(self) -> None:
        self.register_ui()
        self.navigate("Financial Metric Explorer")
        self.app.button(key="search").click().run()
        self.app.button(key="extract_candidates").click().run()
        self.app.multiselect(key="selected_adjustments").set_value(["share_based_payments"]).run()
        self.app.button(key="confirm_selection").click().run()
        self.assertIn("confirmed_definition", self.app.session_state)
        self.navigate("Report Manager")
        self.app.file_uploader(key="pdf_upload").set_value(("replacement.pdf", report_bytes("FY2025"), "application/pdf"))
        self.app.text_input(key="ticker").set_value("700")
        self.app.text_input(key="company").set_value("SYNTHETIC 港股公司")
        self.app.text_input(key="fiscal_period").set_value("FY2024")
        self.app.text_input(key="source_name").set_value("SYNTHETIC Issuer IR")
        self.app.button(key="register_pdf").click().run()
        self.assert_healthy()
        self.assertEqual(len(self.workspace.reports()), 2)
        self.navigate("Financial Metric Explorer")
        self.assertTrue(any("Multiple registered reports" in error.value for error in self.app.error))
        for key in ("search_result", "candidate_result", "confirmed_definition"):
            self.assertNotIn(key, self.app.session_state)


if __name__ == "__main__":
    unittest.main()
