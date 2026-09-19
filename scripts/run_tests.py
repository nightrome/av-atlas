#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runs the full av-atlas test suite: Python unit tests (stdlib unittest,
no install needed) plus the pure-logic JS tests (plain Node, no npm install
needed). Exits non-zero if anything fails, so it's CI-friendly.

Usage: python av-atlas/scripts/run_tests.py
"""
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE = SCRIPTS_DIR.parent

# The smoke, regression and detail-page tests run the real page scripts
# against the real data/stats.json. That file is gitignored (it's derived and
# large -- see .gitignore), so a fresh clone doesn't have one until a full
# pipeline run has happened. Those three are skipped when it's missing rather
# than failing, so this one command works both locally and on a clean CI
# checkout. Skips are printed, never silent: a run that couldn't exercise the
# pages must not look identical to one that did.
STATS_FILE = BASE / "data" / "stats.json"


def main():
    failed = False
    skipped = []
    have_stats = STATS_FILE.exists()

    print("=== Python tests (scripts/tests) ===")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=SCRIPTS_DIR,
    )
    failed = failed or result.returncode != 0

    print("\n=== JS tests (tests/filters.test.js) ===")
    js_test = BASE / "tests" / "filters.test.js"
    result = subprocess.run(["node", str(js_test)], cwd=BASE)
    failed = failed or result.returncode != 0

    print("\n=== JS tests (tests/sortable.test.js) ===")
    sortable_test = BASE / "tests" / "sortable.test.js"
    result = subprocess.run(["node", str(sortable_test)], cwd=BASE)
    failed = failed or result.returncode != 0

    for label, script in (("QA smoke test", "qa_smoke_test.js"),
                          ("UI regression test", "ui_regression_test.js"),
                          ("Detail page test", "detail_page_test.js")):
        print(f"\n=== {label} (tests/{script}, capped at 1 minute) ===")
        if not have_stats:
            print(f"SKIPPED: needs {STATS_FILE}, which is gitignored and rebuilt by the pipeline.")
            skipped.append(label)
            continue
        try:
            result = subprocess.run(["node", str(BASE / "tests" / script)], cwd=BASE, timeout=75)
            failed = failed or result.returncode != 0
        except subprocess.TimeoutExpired:
            print(f"{label} exceeded its time budget and was killed.")
            failed = True

    if failed:
        print("\nSome tests failed.")
        sys.exit(1)
    if skipped:
        print(f"\nAll tests passed, but {len(skipped)} needed built data and were skipped: "
              f"{', '.join(skipped)}. Run scripts/build_public_site.py for a full run.")
    else:
        print("\nAll tests passed.")


if __name__ == "__main__":
    main()
