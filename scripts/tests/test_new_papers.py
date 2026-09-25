#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for new_papers.py (which papers count as new, and the Atom feed) and
for build_public_site.py publishing both files.

Usage: python -m unittest discover -s scripts/tests
"""
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_public_site as bps
import new_papers as np_
from merge_corpus import FIRST_SEEN_BASELINE

ATOM = "{http://www.w3.org/2005/Atom}"


def pair(title, first_seen, **paper):
    entry = {"title": title}
    if first_seen is not None:
        entry["first_seen"] = first_seen
    return entry, {"title": title, "venue": "CVPR", "year": 2026, "authors": ["Ada Lovelace"], **paper}


class TestRecentPapers(unittest.TestCase):
    TODAY = "2026-12-01"

    def titles(self, pairs):
        return [p["title"] for p in np_.recent_papers(pairs, self.TODAY)["papers"]]

    def test_window_baseline_and_missing_dates(self):
        pairs = [
            pair("Inside the window", "2026-11-20"),
            pair("Too old", "2026-08-01"),
            pair("Baseline", FIRST_SEEN_BASELINE),
            pair("No date", None),
        ]
        self.assertEqual(self.titles(pairs), ["Inside the window"])

    def test_newest_first_then_venue_then_title(self):
        pairs = [
            pair("B paper", "2026-10-01"),
            pair("Z paper", "2026-11-01", venue="CVPR"),
            pair("A paper", "2026-11-01", venue="ICRA"),
            pair("Y paper", "2026-11-01", venue="CVPR"),
        ]
        self.assertEqual(self.titles(pairs), ["Y paper", "Z paper", "A paper", "B paper"])

    def test_only_http_arxiv_links_are_kept(self):
        pairs = [pair("Good", "2026-11-01", arxiv_url="https://arxiv.org/abs/2611.00001"),
                 pair("Bad", "2026-11-01", arxiv_url="javascript:alert(1)")]
        got = {p["title"]: p["arxiv_url"] for p in np_.recent_papers(pairs, self.TODAY)["papers"]}
        self.assertEqual(got, {"Good": "https://arxiv.org/abs/2611.00001", "Bad": None})

    def test_long_author_lists_are_cut_but_counted(self):
        names = [f"Author {i}" for i in range(25)]
        p = np_.recent_papers([pair("Big", "2026-11-01", authors=names)], self.TODAY)["papers"][0]
        self.assertEqual(len(p["authors"]), np_.MAX_AUTHORS)
        self.assertEqual(p["n_authors"], 25)


class TestAtomFeed(unittest.TestCase):
    def payload(self):
        return np_.recent_papers([
            pair("Planning & <Prediction> for \"Cars\"", "2026-11-02",
                 arxiv_url="https://arxiv.org/abs/2611.00002",
                 authors=["Zoë Ünal", "Ada Lovelace", "Alan Turing", "Grace Hopper"]),
            pair("Control\x0bcharacter title", "2026-10-15"),
        ], "2026-12-01")

    def test_feed_is_well_formed_atom(self):
        root = ET.fromstring(np_.render_atom(self.payload()).encode("utf-8"))
        self.assertEqual(root.tag, ATOM + "feed")
        for field in ("id", "title", "updated"):
            self.assertIsNotNone(root.find(ATOM + field), field)
        self.assertEqual(root.find(ATOM + "updated").text, "2026-11-02T00:00:00Z")
        entries = root.findall(ATOM + "entry")
        self.assertEqual(len(entries), 2)
        first = entries[0]
        self.assertEqual(first.find(ATOM + "title").text, "Planning & <Prediction> for \"Cars\"")
        self.assertEqual(first.find(ATOM + "id").text,
                         np_.paper_url("Planning & <Prediction> for \"Cars\""))
        related = [l.get("href") for l in first.findall(ATOM + "link") if l.get("rel") == "related"]
        self.assertEqual(related, ["https://arxiv.org/abs/2611.00002"])
        self.assertIn("et al.", first.find(ATOM + "summary").text)
        self.assertEqual(entries[1].find(ATOM + "title").text, "Controlcharacter title")
        for entry in entries:
            for field in ("id", "title", "updated"):
                self.assertIsNotNone(entry.find(ATOM + field), field)

    def test_empty_feed_is_still_valid(self):
        root = ET.fromstring(np_.render_atom({"generated_at": "2026-12-01", "papers": []}).encode("utf-8"))
        self.assertEqual(root.findall(ATOM + "entry"), [])
        self.assertEqual(root.find(ATOM + "updated").text, "2026-12-01T00:00:00Z")

    def test_feed_is_capped(self):
        payload = {"papers": [{"title": f"P{i}", "first_seen": "2026-11-01"} for i in range(150)]}
        root = ET.fromstring(np_.render_atom(payload).encode("utf-8"))
        self.assertEqual(len(root.findall(ATOM + "entry")), np_.FEED_LIMIT)

    def test_same_data_gives_the_same_feed(self):
        self.assertEqual(np_.render_atom(self.payload()), np_.render_atom(self.payload()))


class TestPublishNewPapers(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        (self.base / "data").mkdir()
        self.page_dir = self.base / "public"
        self.page_dir.mkdir()
        orig = bps.BASE
        bps.BASE = self.base
        self.addCleanup(setattr, bps, "BASE", orig)

    def test_copies_the_list_and_writes_the_feed(self):
        payload = np_.recent_papers([pair("A New Paper", "2026-11-02")], "2026-12-01")
        (self.base / "data" / "new_papers.json").write_text(json.dumps(payload), encoding="utf-8")
        bps.write_new_papers(self.page_dir)
        published = json.loads((self.page_dir / "new_papers.json").read_text(encoding="utf-8"))
        self.assertEqual([p["title"] for p in published["papers"]], ["A New Paper"])
        root = ET.parse(self.page_dir / "feed.xml").getroot()
        self.assertEqual(len(root.findall(ATOM + "entry")), 1)

    def test_missing_list_publishes_empty_files(self):
        bps.write_new_papers(self.page_dir)
        published = json.loads((self.page_dir / "new_papers.json").read_text(encoding="utf-8"))
        self.assertEqual(published["papers"], [])
        ET.parse(self.page_dir / "feed.xml")

    def test_new_page_is_published(self):
        self.assertIn("new.html", bps.PUBLISHED_HTML)


if __name__ == "__main__":
    unittest.main()
