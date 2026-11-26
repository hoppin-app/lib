"""Tests for tools/xcode_coverage.py."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import xcode_coverage  # noqa: E402


def entry(path: str, covered: int, executable: int) -> dict:
    return {"path": path, "coveredLines": covered, "executableLines": executable}


REPORT = {
    "targets": [
        {
            "name": "App.app",
            "files": [entry("/src/App/A.swift", 80, 100), entry("/src/App/Views/V.swift", 0, 100)],
        },
        {"name": "AppTests.xctest", "files": [entry("/src/AppTests/T.swift", 50, 50)]},
        {"name": "AppUITests.xctest", "files": [entry("/src/AppUITests/U.swift", 50, 50)]},
    ]
}


def run(report: dict, *extra: str) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cov.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        summary = Path(tmp) / "summary.md"
        args = ["--report", str(path), "--github-summary", str(summary), *extra]
        code = xcode_coverage.main(args)
        return code, summary.read_text(encoding="utf-8") if summary.exists() else ""


class CoverageTests(unittest.TestCase):
    def test_test_targets_are_ignored(self):
        files = xcode_coverage.collect(REPORT, [])
        self.assertEqual(sorted(files), ["/src/App/A.swift", "/src/App/Views/V.swift"])

    def test_percentage_over_application_code(self):
        code, summary = run(REPORT, "--min", "40")
        self.assertEqual(code, 0)
        self.assertIn("40.0%", summary)

    def test_fails_below_minimum(self):
        code, _ = run(REPORT, "--min", "41")
        self.assertEqual(code, 1)

    def test_exclude_globs_change_the_total(self):
        code, summary = run(REPORT, "--min", "80", "--exclude", "*/Views/*")
        self.assertEqual(code, 0)
        self.assertIn("80.0%", summary)

    def test_same_file_in_two_targets_counts_once(self):
        report = {
            "targets": [
                {"name": "App.app", "files": [entry("/src/A.swift", 10, 20)]},
                {"name": "Kit.framework", "files": [entry("/src/A.swift", 15, 20)]},
            ]
        }
        files = xcode_coverage.collect(report, [])
        self.assertEqual(files, {"/src/A.swift": (15, 20)})

    def test_report_without_application_code_is_an_error(self):
        code, _ = run({"targets": [{"name": "AppTests.xctest", "files": []}]})
        self.assertEqual(code, 1)

    def test_file_without_executable_lines_counts_as_covered(self):
        self.assertEqual(xcode_coverage.percent(0, 0), 100.0)


if __name__ == "__main__":
    unittest.main()
