"""Offline PDF-to-report acceptance and useful evidence CLI input errors."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from metric_mirage.confirmation import confirm_candidates
from metric_mirage.evidence import create_evidence_snapshot
from metric_mirage.reports import register_report
from tests.test_pdf_search import synthetic_unicode_pdf


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class EvidenceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="Metric Mirage evidence CLI ")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.registry = self.directory / "registry" / "reports.json"
        self.company = "Synthetic HK Company"
        self.paths: dict[str, dict[str, Path]] = {}
        self.pages = {
            "FY2024": [
                "SYNTHETIC FY2024 report. Invented financial disclosures.",
                "SYNTHETIC 經調整淨利潤不包括股份支付開支、無形資產攤銷。",
                "SYNTHETIC Adjusted Net Profit excludes Share-Based Payments.",
            ],
            "FY2025": [
                "SYNTHETIC FY2025 report. Invented financial disclosures.",
                "SYNTHETIC 经调整净利润不包括股份支付开支、无形资产摊销、上市开支。",
            ],
        }
        for period, pages in self.pages.items():
            year = period[2:]
            self.paths[period] = {
                "pdf": self.directory / f"SYNTHETIC {period}.pdf",
                "definition": self.directory / f"definition_{year}.json",
                "evidence": self.directory / f"evidence_{year}.json",
            }
            self.paths[period]["pdf"].write_bytes(synthetic_unicode_pdf(pages))

    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "metric_mirage", *arguments], cwd=PROJECT_ROOT,
            capture_output=True, text=True, encoding="utf-8", timeout=20, check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def successful(self, *arguments: str) -> str:
        completed = self.run_cli(*arguments)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        return completed.stdout

    def assert_input_error(self, completed: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertTrue(completed.stderr.strip())
        self.assertNotIn("Traceback", completed.stderr)

    def selections(self, period: str) -> list[str]:
        names = ["share_based_payments", "amortisation_of_intangible_assets"]
        return names + (["listing_expenses"] if period == "FY2025" else [])

    def prepare(self) -> None:
        for period, paths in self.paths.items():
            register_report(paths["pdf"], ticker="00700", company=self.company,
                            report_type="annual", fiscal_period=period,
                            source_name=f"SYNTHETIC: invented {period} issuer IR",
                            registry_path=self.registry)
            confirm_candidates("00700", fiscal_period=period, selected_adjustments=self.selections(period),
                               output_path=paths["definition"], registry_path=self.registry)
            create_evidence_snapshot("00700", fiscal_period=period,
                                     definition_path=paths["definition"], output_path=paths["evidence"],
                                     registry_path=self.registry)

    def drift(self, *options: str) -> subprocess.CompletedProcess[str]:
        return self.run_cli("drift", str(self.paths["FY2024"]["definition"]),
                            str(self.paths["FY2025"]["definition"]), *options)

    def evidence_options(self) -> list[str]:
        return ["--old-evidence", str(self.paths["FY2024"]["evidence"]),
                "--new-evidence", str(self.paths["FY2025"]["evidence"])]

    def test_complete_synthetic_cli_workflow_preserves_every_item_source_and_page(self) -> None:
        for period, paths in self.paths.items():
            added = self.successful(
                "reports", "add", str(paths["pdf"]), "--ticker", "0700", "--company", self.company,
                "--type", "annual", "--period", period, "--source", f"SYNTHETIC: invented {period} issuer IR",
                "--registry", str(self.registry),
            )
            self.assertIn(period, added)
        listed = json.loads(self.successful("reports", "list", "00700", "--format", "json", "--registry", str(self.registry)))
        self.assertEqual({item["fiscal_period"] for item in listed}, {"FY2024", "FY2025"})
        self.assertIn(self.company, self.successful("reports", "list", "00700", "--registry", str(self.registry)))
        for period, keyword, page in (("FY2024", "經調整淨利潤", 2), ("FY2024", "adjusted net profit", 3),
                                      ("FY2025", "经调整净利润", 2)):
            searched = json.loads(self.successful(
                "reports", "search", "00700", "--period", period, "--keyword", keyword,
                "--format", "json", "--registry", str(self.registry),
            ))
            self.assertEqual(searched["total_matches"], 1)
            self.assertEqual(searched["matches"][0]["page_number"], page)
        for period, paths in self.paths.items():
            candidates = json.loads(self.successful(
                "reports", "candidates", "00700", "--period", period, "--format", "json", "--registry", str(self.registry),
            ))
            self.assertEqual({item["canonical_name"] for item in candidates["candidates"]}, set(self.selections(period)))
            selectors = [option for name in self.selections(period) for option in ("--select", name)]
            confirmed = self.successful(
                "reports", "confirm", "00700", "--period", period, *selectors,
                "--output", str(paths["definition"]), "--registry", str(self.registry),
            )
            self.assertIn(str(paths["definition"]), confirmed)
            exported = self.successful(
                "reports", "evidence", "00700", "--period", period, "--type", "annual",
                "--definition", str(paths["definition"]), "--output", str(paths["evidence"]),
                "--registry", str(self.registry),
            )
            self.assertIn(str(paths["evidence"]), exported)
            snapshot = json.loads(paths["evidence"].read_text(encoding="utf-8"))
            self.assertEqual(snapshot["definition_sha256"], hashlib.sha256(paths["definition"].read_bytes()).hexdigest())
            self.assertEqual(snapshot["report_sha256"], hashlib.sha256(paths["pdf"].read_bytes()).hexdigest())
            for item in snapshot["items"]:
                for evidence in item["evidence"]:
                    self.assertIn(evidence["evidence_excerpt"], self.pages[period][evidence["page_number"] - 1])
                    self.assertIn(evidence["matched_text"], evidence["evidence_excerpt"])
        options = self.evidence_options()
        old, new = self.paths["FY2024"], self.paths["FY2025"]
        base = json.loads(self.successful("drift", str(old["definition"]), str(new["definition"]), "--format", "json"))
        self.assertNotIn("evidence", base)
        self.assertIn("Comparison Table", self.successful("drift", str(old["definition"]), str(new["definition"])))
        markdown = self.successful("drift", str(old["definition"]), str(new["definition"]), *options)
        self.assertIn("Evidence", markdown)
        self.assertIn("股份支付開支", markdown)
        self.assertIn("上市开支", markdown)
        report = json.loads(self.successful("drift", str(old["definition"]), str(new["definition"]), *options, "--format", "json"))
        self.assertEqual(report["added_adjustments"], ["listing_expenses"])
        self.assertEqual(report["removed_adjustments"], [])
        self.assertEqual(report["unchanged_adjustments"], ["amortisation_of_intangible_assets", "share_based_payments"])
        rows = {item["canonical_name"]: item for item in report["evidence"]}
        self.assertEqual(rows["listing_expenses"]["old_evidence"], [])
        self.assertEqual(rows["listing_expenses"]["new_evidence"][0]["page_number"], 2)
        self.assertEqual([item["page_number"] for item in rows["share_based_payments"]["old_evidence"]], [2, 3])
        for period, side in (("FY2024", "old_evidence"), ("FY2025", "new_evidence")):
            digest = hashlib.sha256(self.paths[period]["pdf"].read_bytes()).hexdigest()
            self.assertIn(digest, markdown)
            for row in rows.values():
                for item in row[side]:
                    self.assertEqual(item["report_sha256"], digest)
                    self.assertIn(item["evidence_excerpt"], self.pages[period][item["page_number"] - 1])
        comparison = json.loads(self.successful("compare", str(old["definition"]), str(new["definition"]), "--format", "json"))
        self.assertEqual(comparison["added_adjustments"], ["listing_expenses"])
        self.assertIn("SYNTHETIC", self.successful("demo"))

    def test_evidence_requires_explicit_definition_and_output(self) -> None:
        for tail in ([], ["--definition", str(self.paths["FY2024"]["definition"])],
                     ["--output", str(self.paths["FY2024"]["evidence"])]):
            with self.subTest(options=tail):
                self.assert_input_error(self.run_cli("reports", "evidence", "00700", "--period", "FY2024", *tail))

    def test_only_one_or_swapped_year_snapshot_fails_cleanly(self) -> None:
        self.prepare()
        for options in (
            ["--old-evidence", str(self.paths["FY2024"]["evidence"])],
            ["--new-evidence", str(self.paths["FY2025"]["evidence"])],
            ["--old-evidence", str(self.paths["FY2025"]["evidence"]), "--new-evidence", str(self.paths["FY2024"]["evidence"])],
        ):
            with self.subTest(options=options):
                self.assert_input_error(self.drift(*options, "--format", "json"))

    def test_changed_definition_file_hash_is_rejected_instead_of_partial_json(self) -> None:
        self.prepare()
        definition = self.paths["FY2024"]["definition"]
        definition.write_bytes(definition.read_bytes() + b"\n")
        failed = self.drift(*self.evidence_options(), "--format", "json")
        self.assert_input_error(failed)
        self.assertIn("sha256", failed.stderr.lower())

    def test_malformed_or_missing_snapshot_fails_without_traceback(self) -> None:
        self.prepare()
        snapshot = self.paths["FY2024"]["evidence"]
        for raw in ("{invalid", None):
            with self.subTest(missing=raw is None):
                if raw is None:
                    snapshot.unlink()
                else:
                    snapshot.write_text(raw, encoding="utf-8")
                self.assert_input_error(self.drift(*self.evidence_options(), "--format", "json"))

    def test_existing_evidence_output_is_rejected_and_preserved(self) -> None:
        self.prepare()
        paths = self.paths["FY2024"]
        original = paths["evidence"].read_bytes()
        completed = self.run_cli(
            "reports", "evidence", "00700", "--period", "FY2024", "--definition", str(paths["definition"]),
            "--output", str(paths["evidence"]), "--registry", str(self.registry),
        )
        self.assert_input_error(completed)
        self.assertEqual(paths["evidence"].read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
