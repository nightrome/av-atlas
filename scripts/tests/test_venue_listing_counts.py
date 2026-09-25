#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pinned paper counts for the CVF-hosted venue files (CVPR, ICCV, WACV, ACCV),
so a fetch that stops partway through fails the test suite instead of
quietly shipping. That happened once: cvpr2022.json held 774 of 2,074
papers for months, and every CVPR trend on the site showed a fake 2022 dip.

For years that come from CVF Open Access, the number is the size of the CVF
listing (openaccess.thecvf.com/<CONF><YEAR>?day=all) minus the few entries
whose paper page is a dead link (404). For the older years that came from
DBLP (accv2012-2018, cvpr2012, wacv2012-2019) it's simply what the file held
when this test was written. A file may hold more than its pinned number
(CVPR 2026 has 26 papers that have since dropped off the listing), never
fewer.

When a new edition is fetched, add its count here -- the last test fails
until you do.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import re
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_cvf_history as fch

VENUES_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "venues"

EXPECTED_MIN_PAPERS = {
    # DBLP
    "accv2012": 225, "accv2014": 226, "accv2016": 143, "accv2018": 269,
    "cvpr2012": 465,
    "wacv2012": 59, "wacv2013": 73, "wacv2014": 152, "wacv2015": 155,
    "wacv2016": 189, "wacv2017": 140, "wacv2018": 221, "wacv2019": 230,
    # CVF Open Access listings
    "accv2020": 254, "accv2022": 277, "accv2024": 269,
    "cvpr2013": 471, "cvpr2014": 540, "cvpr2015": 602, "cvpr2016": 643,
    "cvpr2017": 783, "cvpr2018": 979, "cvpr2019": 1294, "cvpr2020": 1466,
    "cvpr2021": 1660,
    "cvpr2022": 2071,  # 2,074 listed, 3 dead links
    "cvpr2023": 2353,
    "cvpr2024": 2713,  # 2,715 listed, 2 dead links
    "cvpr2025": 2871, "cvpr2026": 4042,
    "iccv2013": 454, "iccv2015": 526,
    "iccv2017": 620,  # 621 listed, 1 dead link
    "iccv2019": 1075, "iccv2021": 1612, "iccv2023": 2156, "iccv2025": 2701,
    "wacv2020": 378, "wacv2021": 406, "wacv2022": 406, "wacv2023": 639,
    "wacv2024": 846, "wacv2025": 929, "wacv2026": 831,
}

CVF_FILE = re.compile(r"(cvpr|iccv|wacv|accv)\d{4}")


def _load(name):
    return json.loads((VENUES_DIR / f"{name}.json").read_text(encoding="utf-8"))


class TestVenueListingCounts(unittest.TestCase):
    def test_no_file_is_smaller_than_its_listing(self):
        short = []
        for name, expected in sorted(EXPECTED_MIN_PAPERS.items()):
            path = VENUES_DIR / f"{name}.json"
            if not path.exists():
                short.append(f"{name}: file missing (expected {expected})")
                continue
            n = len(_load(name))
            if n < expected:
                short.append(f"{name}: {n} papers, expected at least {expected}")
        self.assertEqual(short, [], "venue files look truncated:\n" + "\n".join(short))

    def test_cvf_fetched_files_have_abstracts(self):
        # Files fetched from CVF carry a "path" per entry and get the
        # abstract from the paper page. A file where many are missing means
        # the page parsing broke, which is a truncation of a different kind.
        for name in sorted(EXPECTED_MIN_PAPERS):
            papers = _load(name)
            if not papers or "path" not in papers[0]:
                continue
            with_abstract = sum(1 for p in papers if p.get("abstract"))
            self.assertGreaterEqual(with_abstract / len(papers), 0.99, name)

    def test_every_cvf_venue_file_has_a_pinned_count(self):
        names = sorted(f.stem for f in VENUES_DIR.glob("*.json") if CVF_FILE.fullmatch(f.stem))
        missing = [n for n in names if n not in EXPECTED_MIN_PAPERS]
        self.assertEqual(missing, [], "add these to EXPECTED_MIN_PAPERS")


class TestFetchCvfHistoryGuards(unittest.TestCase):
    def test_should_write_only_complete_fetches(self):
        self.assertTrue(fch.should_write(2074, 2074, 0, 774))
        self.assertTrue(fch.should_write(2071, 2074, 3, 774))
        self.assertFalse(fch.should_write(774, 2074, 0, 0))
        self.assertFalse(fch.should_write(2070, 2074, 3, 774))

    def test_should_not_shrink_an_existing_file(self):
        # CVPR 2026's listing lost 26 papers after the first fetch; a
        # refetch must not silently drop them from the corpus.
        self.assertFalse(fch.should_write(4042, 4042, 0, 4068))

    def test_retries_transient_errors(self):
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) < 3:
                raise ConnectionError("dropped")
            return {"title": "T", "abstract": "A", "authors": "X"}

        with mock.patch.object(fch.time, "sleep"):
            detail = fch.fetch_with_retries("p", attempts=3, fetch_paper=flaky)
        self.assertEqual(detail["title"], "T")
        self.assertEqual(len(calls), 3)

    def test_does_not_retry_a_dead_link(self):
        calls = []

        def gone(path):
            calls.append(path)
            raise urllib.error.HTTPError(path, 404, "Not Found", {}, None)

        with mock.patch.object(fch.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                fch.fetch_with_retries("p", attempts=3, fetch_paper=gone)
        self.assertEqual(len(calls), 1)

    def test_gives_up_after_the_last_attempt(self):
        def broken(path):
            raise urllib.error.HTTPError(path, 503, "Unavailable", {}, None)

        with mock.patch.object(fch.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                fch.fetch_with_retries("p", attempts=2, fetch_paper=broken)


if __name__ == "__main__":
    unittest.main()
