"""Verify that the public demonstration command runs end to end."""

import json
from pathlib import Path
import subprocess
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DemoCliTests(unittest.TestCase):
    def test_demo_displays_synthetic_comparison(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "metric_mirage", "demo"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        output = completed.stdout.casefold()
        for expected in (
            "synthetic",
            "company: synthetic example company",
            "adjusted ebitda",
            "fy 2024",
            "fy 2025",
            "added",
            "removed",
            "unchanged",
            "restructuring costs",
            "depreciation & amortization",
            "stock-based compensation",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, output)

    def test_example_and_bundled_data_match_and_are_labeled_synthetic(self) -> None:
        example_path = PROJECT_ROOT / "examples" / "synthetic_adjusted_ebitda.json"
        bundled_path = PROJECT_ROOT / "metric_mirage" / "data" / "synthetic_adjusted_ebitda.json"
        example = json.loads(example_path.read_text(encoding="utf-8"))
        bundled = json.loads(bundled_path.read_text(encoding="utf-8"))

        self.assertEqual(example, bundled)
        for data in (example, bundled):
            with self.subTest(data=data):
                self.assertEqual(data["data_type"], "SYNTHETIC")
                for period in ("old", "new"):
                    self.assertTrue(data[period]["source"].startswith("SYNTHETIC:"))


if __name__ == "__main__":
    unittest.main()
