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


def main():
    failed = False

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

    print("\n=== QA smoke test (tests/qa_smoke_test.js, capped at 1 minute) ===")
    qa_test = BASE / "tests" / "qa_smoke_test.js"
    try:
        result = subprocess.run(["node", str(qa_test)], cwd=BASE, timeout=75)
        failed = failed or result.returncode != 0
    except subprocess.TimeoutExpired:
        print("QA smoke test exceeded its time budget and was killed.")
        failed = True

    print("\n=== UI regression test (tests/ui_regression_test.js, capped at 1 minute) ===")
    ui_test = BASE / "tests" / "ui_regression_test.js"
    try:
        result = subprocess.run(["node", str(ui_test)], cwd=BASE, timeout=75)
        failed = failed or result.returncode != 0
    except subprocess.TimeoutExpired:
        print("UI regression test exceeded its time budget and was killed.")
        failed = True

    print("\n=== Detail page test (tests/detail_page_test.js, capped at 1 minute) ===")
    detail_test = BASE / "tests" / "detail_page_test.js"
    try:
        result = subprocess.run(["node", str(detail_test)], cwd=BASE, timeout=75)
        failed = failed or result.returncode != 0
    except subprocess.TimeoutExpired:
        print("Detail page test exceeded its time budget and was killed.")
        failed = True

    if failed:
        print("\nSome tests failed.")
        sys.exit(1)
    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
