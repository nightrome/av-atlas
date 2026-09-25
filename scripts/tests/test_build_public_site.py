#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for the slimmed stats.json and the about.json that build_public_site.py
publishes: nothing a page reads may be dropped.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_public_site as bps

SITE = bps.SITE_DIR
STATS_FILE = bps.BASE / "data" / "stats.json"


def site_sources():
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(SITE.glob("*.html")) + sorted(SITE.glob("*.js"))}


# filters.js keeps top_papers in step with all_papers when it is there, and
# rebuilds it after swapping the paper list. Neither needs it in the file.
TOP_PAPERS_WRITES = ("(stats.top_papers || []).forEach(mutate)", "stats.top_papers = ")


def code_lines(text):
    return [line for line in text.splitlines()
            if not line.lstrip().startswith("//")
            and not any(w in line for w in TOP_PAPERS_WRITES)]


def reads(text, name):
    """Lines of `text` that read property `name` (obj.name or obj['name'])."""
    pattern = re.compile(r"\.%s\b|\[['\"]%s['\"]\]" % (name, name))
    return [line for line in code_lines(text) if pattern.search(line)]


def stats_keys_read(text):
    """Top-level keys read as stats.X (not the file name stats.json)."""
    pattern = re.compile(r"\b(?:stats|currentStats)\.(?!json\b)([A-Za-z_]\w*)")
    return {k for line in code_lines(text) for k in pattern.findall(line)}


class SlimStatsTest(unittest.TestCase):
    STATS = {
        "generated_at": "2026-09-24", "generated_from": 10, "content_updated": "2026-09-20",
        "content_hash": "abc", "version": "1.2", "site_version": "1.2.3",
        "av_relevant": 2, "corpus_stats": {"total_researchers": 3}, "verification": {},
        "top_countries": [["Germany", 1]], "some_new_key": [1],
        "best_by_venue": {}, "best_by_year": {}, "top_papers": [], "top_authors": [],
        "top_institutions": [], "venue_images": {},
        "all_papers": [
            {"title": "A", "venue": "CVPR", "source": "venue_listing", "doi": None,
             "citations_updated": None, "has_code_link": True, "cd_n_citers": 4,
             "venue_status": "x", "cd_index": 0.5},
            {"title": "B", "venue": "arXiv", "source": "arxiv_s2_citing_discovery"},
            {"title": "C", "venue": "IEEE Access", "source": "arxiv_s2_citing_discovery"},
        ],
    }

    def test_drops_unused_keys_and_fields(self):
        out = bps.slim_stats(self.STATS)
        for key in bps.UNUSED_STATS_KEYS:
            self.assertNotIn(key, out)
        self.assertEqual(out["all_papers"][0],
                         {"title": "A", "venue": "CVPR", "source": "venue_listing", "cd_index": 0.5})
        # The input is left alone: data/stats.json keeps everything.
        self.assertIn("has_code_link", self.STATS["all_papers"][0])

    def test_keeps_everything_else(self):
        out = bps.slim_stats(self.STATS)
        for key in ("generated_at", "generated_from", "content_updated", "content_hash",
                    "version", "site_version", "some_new_key", "corpus_stats"):
            self.assertEqual(out[key], self.STATS[key])

    def test_about_payload(self):
        out = bps.about_payload(self.STATS)
        self.assertEqual(set(out), {"generated_at", "generated_from", "content_updated",
                                    "content_hash", "version", "site_version", "av_relevant",
                                    "corpus_stats", "verification", "top_countries",
                                    "paper_sources"})
        self.assertEqual(out["paper_sources"],
                         {"total": 3, "venue_listing": 1, "citation_found": 2, "arxiv_only": 1})


class SiteReadsTest(unittest.TestCase):
    """Checks the drop lists against what site/ actually reads."""

    def test_no_page_reads_a_dropped_key_or_field(self):
        for name, text in site_sources().items():
            for key in bps.UNUSED_STATS_KEYS + bps.UNUSED_PAPER_FIELDS:
                self.assertEqual(reads(text, key), [], f"{name} reads {key}")

    def test_about_json_has_what_about_html_reads(self):
        text = (SITE / "about.html").read_text(encoding="utf-8")
        self.assertIn("fetch('about.json')", text)
        self.assertNotIn("fetch('stats.json')", text)
        provided = set(bps.ABOUT_KEYS) | {"paper_sources"}
        missing = {k for k in stats_keys_read(text) if k not in provided and not bps.is_version_key(k)}
        self.assertEqual(missing, set())

    def test_pages_preload_what_they_fetch(self):
        for name, text in site_sources().items():
            if not name.endswith(".html"):
                continue
            for payload in ("stats.json", "about.json"):
                if f"fetch('{payload}')" in text or (payload == "stats.json" and "fetchStatsWithRelevance(" in text):
                    self.assertIn(f'<link rel="preload" href="{payload}" as="fetch" crossorigin>', text, name)


@unittest.skipUnless(STATS_FILE.exists(), "needs data/stats.json (gitignored, built by the pipeline)")
class RealDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        cls.slim = bps.slim_stats(cls.stats)

    def test_keeps_every_top_level_key_a_page_reads(self):
        read = set()
        for text in site_sources().values():
            read |= stats_keys_read(text)
        lost = {k for k in read if k in self.stats and k not in self.slim}
        self.assertEqual(lost, set())

    def test_keeps_every_paper_field_but_the_unused_ones(self):
        before = {k for p in self.stats["all_papers"] for k, v in p.items() if v is not None}
        after = {k for p in self.slim["all_papers"] for k in p}
        self.assertEqual(after, before - set(bps.UNUSED_PAPER_FIELDS))

    def test_about_payload_is_small(self):
        about = json.dumps(bps.about_payload(self.stats))
        self.assertLess(len(about), 1_000_000)


if __name__ == "__main__":
    unittest.main()
