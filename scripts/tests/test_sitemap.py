#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for build_public_site.py's sitemap: which URLs it lists and the
lastmod date it gives them. Runs against a small stats.json fixture.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_public_site as bps

STATS = {
    "content_updated": "2026-08-01",
    "corpus_stats": {"by_venue": {"CVPR": 900, "IEEE Transactions on Medical Imaging": 500, "IV": 100}},
    "all_papers": [
        {"title": "Lift, Splat, Shoot (LSS)", "venue": "CVPR", "citations": 50,
         "authors": ["Jonah Philion"], "institutions": ["NVIDIA"], "countries": ["United States"]},
        {"title": "A Paper", "venue": "IV", "citations": 1,
         "authors": ["Jane Doe"], "institutions": ["TU Delft"], "countries": ["Netherlands"]},
    ],
}


class WriteSitemapTests(unittest.TestCase):
    def _sitemap(self, stats):
        with tempfile.TemporaryDirectory() as d:
            stats_path = Path(d) / "stats.json"
            stats_path.write_text(json.dumps(stats), encoding="utf-8")
            bps.write_sitemap(Path(d), stats_path)
            text = (Path(d) / "sitemap.xml").read_text(encoding="utf-8")
        return re.findall(r"<loc>([^<]*)</loc>", text), re.findall(r"<lastmod>([^<]*)</lastmod>", text)

    def test_bare_detail_templates_are_left_out(self):
        urls, _ = self._sitemap(STATS)
        for page in bps.DETAIL_TEMPLATES:
            self.assertNotIn(f"{bps.SITE_URL}/{page}", urls)
        self.assertIn(f"{bps.SITE_URL}/index.html", urls)
        self.assertIn(f"{bps.SITE_URL}/authors.html", urls)
        # The parameterized detail pages are still there.
        self.assertIn(f"{bps.SITE_URL}/author.html?name=Jane%20Doe", urls)
        self.assertIn(f"{bps.SITE_URL}/country.html?name=Netherlands", urls)

    def test_paper_url_matches_the_pages_own_canonical_escaping(self):
        # filters.js's detailCanonicalUrl escapes the same way (see
        # quoteParam there), so the sitemap entry and the canonical agree.
        urls, _ = self._sitemap(STATS)
        self.assertIn(f"{bps.SITE_URL}/paper.html?title=Lift%2C%20Splat%2C%20Shoot%20%28LSS%29", urls)

    def test_only_venues_with_av_papers_are_listed(self):
        urls, _ = self._sitemap(STATS)
        venues = [u for u in urls if "/venue.html?" in u]
        self.assertEqual(venues, [f"{bps.SITE_URL}/venue.html?name=CVPR", f"{bps.SITE_URL}/venue.html?name=IV"])

    def test_lastmod_is_the_content_date_not_the_build_date(self):
        _, lastmods = self._sitemap(STATS)
        self.assertEqual(set(lastmods), {"2026-08-01"})
        # A stats.json without the field (older builds) still gets a date.
        _, lastmods = self._sitemap({k: v for k, v in STATS.items() if k != "content_updated"})
        self.assertEqual(len(set(lastmods)), 1)
        self.assertRegex(lastmods[0], r"^\d{4}-\d{2}-\d{2}$")


if __name__ == "__main__":
    unittest.main()
