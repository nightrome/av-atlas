#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_virtual_site as vs

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_dump():
    return json.loads((FIXTURES / "virtual_site_dump.json").read_text(encoding="utf-8"))


class TestSelectPapers(unittest.TestCase):
    def test_icml_keeps_main_and_position_tracks_only(self):
        titles = [p["title"] for p in vs.select_papers(load_dump(), "ICML", 2026)]
        self.assertEqual(titles, ["Driving World Models & Planning",
                                  "Position: Benchmarks Should Report Variance"])

    def test_other_venues_keep_main_track_only(self):
        dump = load_dump()
        for ev in dump["results"]:
            if ev["sourceurl"]:
                ev["sourceurl"] = ev["sourceurl"].replace("ICML.cc", "ICLR.cc")
        titles = [p["title"] for p in vs.select_papers(dump, "ICLR", 2026)]
        self.assertEqual(titles, ["Driving World Models & Planning"])

    def test_oral_duplicate_is_merged_into_the_poster_record(self):
        papers = vs.select_papers(load_dump(), "ICML", 2026)
        first = papers[0]
        self.assertEqual(first["authors"], "Ada Lovelace, Alan Turing")
        self.assertEqual(first["abstract"], "We plan with a learned world model.")
        self.assertEqual(first["page"], "/virtual/2026/poster/101")


class TestPosterAbstract(unittest.TestCase):
    def test_parses_the_abstract_block(self):
        page = (FIXTURES / "virtual_site_poster_excerpt.html").read_text(encoding="utf-8")
        self.assertEqual(
            vs.parse_poster_abstract(page),
            "Reinforcement learning from human feedback (RLHF) with proximal policy optimization (PPO) "
            "is widely used but often yields less diverse outputs than supervised fine‑tuning.")

    def test_missing_block_is_none(self):
        self.assertIsNone(vs.parse_poster_abstract("<html></html>"))


class TestFillAbstracts(unittest.TestCase):
    def test_stops_on_429(self):
        papers = [{"title": str(i), "abstract": None, "page": f"/virtual/2026/poster/{i}"} for i in range(4)]
        err = urllib.error.HTTPError("u", 429, "Too Many Requests", {}, None)
        with mock.patch.object(vs, "fetch", side_effect=err) as f:
            requests, failures = vs.fill_abstracts(papers, "icml.cc", save=lambda: None, delay=0)
        self.assertEqual((requests, failures), (1, 1))
        self.assertEqual(f.call_count, 1)

    def test_skips_papers_that_already_have_one(self):
        papers = [{"title": "a", "abstract": "have it", "page": "/p/1"},
                  {"title": "b", "abstract": None, "page": "/p/2"}]
        with mock.patch.object(vs, "fetch", return_value='<div class="abstract-text-inner">New</div>'):
            requests, _ = vs.fill_abstracts(papers, "iclr.cc", save=lambda: None, delay=0)
        self.assertEqual(requests, 1)
        self.assertEqual(papers[1]["abstract"], "New")


class TestMain(unittest.TestCase):
    def test_writes_file_and_reuses_existing_abstracts(self):
        dump = load_dump()
        # Pad with enough main-track papers to pass the "too few" guard.
        for i in range(120):
            dump["results"].append({"name": f"Paper {i}", "authors": [{"fullname": "X"}],
                                    "virtualsite_url": f"/virtual/2026/poster/{1000 + i}",
                                    "sourceurl": "https://openreview.net/group?id=ICML.cc/2026/Conference"})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "icml2026.json"
            out.write_text(json.dumps([{"title": "Paper 7", "authors": "X", "abstract": "Kept"}]),
                           encoding="utf-8")
            with mock.patch.object(vs, "OUT_DIR", Path(tmp)), \
                    mock.patch.object(vs, "fetch", return_value=json.dumps(dump)):
                self.assertEqual(vs.main(["ICML", "2026", "--force"]), 0)
            rows = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 122)
        self.assertEqual(set(rows[0]), {"title", "authors", "abstract"})
        self.assertEqual({r["title"]: r["abstract"] for r in rows}["Paper 7"], "Kept")

    def test_empty_dump_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(vs, "OUT_DIR", Path(tmp)), \
                    mock.patch.object(vs, "fetch", return_value='{"count": 0, "results": []}'):
                self.assertEqual(vs.main(["NeurIPS", "2026"]), 1)
            self.assertFalse((Path(tmp) / "neurips2026.json").exists())


if __name__ == "__main__":
    unittest.main()
