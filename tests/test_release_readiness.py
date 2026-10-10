"""Offline release contracts that exercise the installed package in isolation."""

from dataclasses import asdict
from importlib import metadata, resources
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from metric_mirage.core import MetricDefinition
from metric_mirage.dashboard.services import demo_analysis
from metric_mirage.drift_report import build_drift_report, render_json, render_markdown


class ReleaseReadinessTests(unittest.TestCase):
    def test_dashboard_defaults_use_loopback_and_disable_usage_telemetry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        configuration = tomllib.loads((root / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
        self.assertEqual(configuration["server"]["address"], "127.0.0.1")
        self.assertIs(configuration["browser"]["gatherUsageStats"], False)
        self.assertEqual(configuration["server"]["maxUploadSize"], 25)

    def test_installed_distribution_declares_version_and_runtime_dependencies(self) -> None:
        distribution = metadata.metadata("metric-mirage")
        self.assertEqual(distribution["Version"], "0.1.0")
        self.assertEqual(distribution["Requires-Python"], ">=3.11")
        requirements = distribution.get_all("Requires-Dist") or []
        for dependency in ("pypdf", "streamlit", "plotly"):
            with self.subTest(dependency=dependency):
                self.assertTrue(any(requirement.startswith(dependency) for requirement in requirements))

    def test_bundled_demo_resources_are_valid_and_explicitly_synthetic(self) -> None:
        for name in ("synthetic_adjusted_ebitda.json", "synthetic_adjusted_net_profit.json"):
            with self.subTest(resource=name):
                data = json.loads(resources.files("metric_mirage").joinpath("data", name).read_text(encoding="utf-8"))
                self.assertEqual(data["data_type"], "SYNTHETIC")
                for side in ("old", "new"):
                    definition = MetricDefinition(**data[side])
                    self.assertTrue(definition.source.startswith("SYNTHETIC:"))

    def test_public_api_and_dashboard_import_from_an_unrelated_directory(self) -> None:
        script = """
import json
import metric_mirage
import metric_mirage.dashboard.ui
from metric_mirage.dashboard.services import demo_analysis

assert all(hasattr(metric_mirage, name) for name in metric_mirage.__all__)
assert metric_mirage.normalize_hk_ticker('0700.HK') == '00700'
print(json.dumps(demo_analysis(), ensure_ascii=False))
"""
        with tempfile.TemporaryDirectory(prefix="Metric Mirage installed imports ") as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-c", script], cwd=temporary,
                capture_output=True, text=True, encoding="utf-8", check=False, timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["metric_name"], "Adjusted Net Profit")
        self.assertEqual(report["added_adjustments"], ["listing_expenses"])
        self.assertNotIn("evidence", report)

    def test_installed_cli_demo_runs_without_project_working_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="Metric Mirage 外部目錄 ") as temporary:
            completed = subprocess.run(
                [sys.executable, "-I", "-m", "metric_mirage", "demo"], cwd=temporary,
                capture_output=True, text=True, encoding="utf-8", check=False, timeout=15,
            )
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertIn("SYNTHETIC demonstration", completed.stdout)
        self.assertIn("restructuring costs", completed.stdout)

    def test_dashboard_demo_results_do_not_retain_user_changes(self) -> None:
        original = demo_analysis()
        changed = demo_analysis()
        changed["company"] = "SYNTHETIC private workspace selection"
        changed["added_adjustments"].append("unconfirmed_item")
        changed["old_source"] = "SYNTHETIC unrelated source"
        self.assertEqual(demo_analysis(), original)
        self.assertNotIn("evidence", original)
        self.assertFalse(any("page_number" in key for key in original))

    def test_isolated_cli_exports_preserve_chinese_and_match_public_renderers(self) -> None:
        old = MetricDefinition(
            "SYNTHETIC 繁體與简体公司", "Adjusted Net Profit", "FY2024",
            ["share_based_payments"], "SYNTHETIC: 原始來源 | SHA256: " + "a" * 64,
        )
        new = MetricDefinition(
            old.company, old.metric_name, "FY2025",
            ["listing_expenses", "share_based_payments"],
            "SYNTHETIC: 新年度来源\nUTF-8 原文 `內容` | SHA256: " + "b" * 64,
        )
        report = build_drift_report(old, new)
        with tempfile.TemporaryDirectory(prefix="Metric Mirage UTF-8 release ") as temporary:
            directory = Path(temporary)
            for label, definition in (("舊年度", old), ("新年度", new)):
                (directory / f"{label}.json").write_text(json.dumps(asdict(definition), ensure_ascii=False), encoding="utf-8")
            for format_name, expected in (("json", render_json(report)), ("markdown", render_markdown(report))):
                with self.subTest(format=format_name):
                    command = [
                        sys.executable, "-I", "-X", "utf8", "-m", "metric_mirage", "drift",
                        "舊年度.json", "新年度.json", "--format", format_name,
                    ]
                    first = subprocess.run(command, cwd=directory, capture_output=True, check=False, timeout=15)
                    second = subprocess.run(command, cwd=directory, capture_output=True, check=False, timeout=15)
                    self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
                    self.assertEqual(first.stderr, b"")
                    self.assertEqual(first.stdout, second.stdout)
                    output = first.stdout.decode("utf-8").replace("\r\n", "\n")
                    self.assertEqual(output, expected + "\n")
                    self.assertIn("繁體與简体公司", output)
                    self.assertIn("原始來源", output)
                    self.assertIn("新年度来源", output)
                    if format_name == "json":
                        self.assertEqual(json.loads(output), report)
                        self.assertNotIn("evidence", json.loads(output))
                    else:
                        self.assertNotIn("## Evidence Section", output)


if __name__ == "__main__":
    unittest.main()
