#!/usr/bin/env python3
"""Check Swift line coverage from `xcrun xccov view --report --json`.

Counts only application code: test bundles (targets named *.xctest or ending
in Tests) are ignored, files can be excluded with glob patterns, and a file
that appears in several targets is counted once. Fails when the line coverage
is below --min.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path
from typing import Any


def is_test_target(name: str) -> bool:
    base = name.removesuffix(".xctest")
    return name.endswith(".xctest") or base.endswith(("Tests", "UITests"))


def collect(report: dict[str, Any], exclude: list[str]) -> dict[str, tuple[int, int]]:
    """Map file path -> (covered lines, executable lines) for application code."""
    files: dict[str, tuple[int, int]] = {}
    for target in report.get("targets", []):
        if is_test_target(target.get("name", "")):
            continue
        for entry in target.get("files", []):
            path = entry.get("path", entry.get("name", ""))
            if any(fnmatch.fnmatch(path, pattern) for pattern in exclude):
                continue
            covered = int(entry.get("coveredLines", 0))
            executable = int(entry.get("executableLines", 0))
            previous = files.get(path)
            if previous is None or covered > previous[0]:
                files[path] = (covered, executable)
    return files


def percent(covered: int, executable: int) -> float:
    return 100.0 * covered / executable if executable else 100.0


def summary(files: dict[str, tuple[int, int]], total: float, minimum: float) -> str:
    lines = [
        "### Swift coverage",
        "",
        f"Line coverage: **{total:.1f}%** (minimum {minimum:g}%)",
        "",
        "| File | Covered | Lines | % |",
        "|---|---|---|---|",
    ]
    worst = sorted(files.items(), key=lambda kv: percent(*kv[1]))[:10]
    for path, (covered, executable) in worst:
        pct = percent(covered, executable)
        lines.append(f"| `{Path(path).name}` | {covered} | {executable} | {pct:.1f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--min", type=float, default=0.0)
    parser.add_argument("--exclude", action="append", default=[], help="glob on the file path")
    parser.add_argument("--github-summary", type=Path)
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    files = collect(report, args.exclude)
    if not files:
        print("::error::no application code found in the coverage report", file=sys.stderr)
        return 1

    covered = sum(c for c, _ in files.values())
    executable = sum(e for _, e in files.values())
    total = percent(covered, executable)
    print(f"Swift line coverage: {total:.2f}% ({covered}/{executable} lines, {len(files)} files)")

    if args.github_summary:
        with args.github_summary.open("a", encoding="utf-8") as fh:
            fh.write(summary(files, total, args.min))

    if total < args.min:
        print(f"::error::coverage {total:.2f}% is below the minimum {args.min:g}%", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
