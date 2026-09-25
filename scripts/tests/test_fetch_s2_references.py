#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for fetch_s2_references.py, with every Semantic Scholar call mocked.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_s2_references as fsr


def http_error(code):
    return urllib.error.HTTPError("url", code, "error", {}, None)


def paper(title, venue="CVPR", year=2022, **extra):
    return {"title": title, "venue": venue, "year": year, **extra}


class FakeS2:
    """Stands in for fsr.s2_request: answers /paper/batch from a dict of
    external id -> record, bulk search from a list of pages, and
    search/match from a dict of query -> record."""

    def __init__(self, batch=None, bulk_pages=None, match=None):
        self.batch = batch or {}
        self.bulk_pages = list(bulk_pages or [])
        self.match = match or {}
        self.calls = []

    def __call__(self, path, params, body=None):
        self.calls.append((path, dict(params), body))
        if path == "/paper/batch":
            return [self.batch.get(i) for i in body["ids"]]
        if path == "/paper/search/bulk":
            return self.bulk_pages.pop(0)
        if path == "/paper/search/match":
            rec = self.match.get(params["query"])
            if rec is None:
                raise http_error(404)
            return {"data": [rec]}
        raise AssertionError(f"unexpected call {path}")


class TestHelpers(unittest.TestCase):
    def test_arxiv_id_strips_version_and_pdf(self):
        self.assertEqual(fsr.arxiv_id("https://arxiv.org/abs/2203.17270v2"), "2203.17270")
        self.assertEqual(fsr.arxiv_id("https://arxiv.org/pdf/1812.05784.pdf"), "1812.05784")
        self.assertEqual(fsr.arxiv_id("http://arxiv.org/abs/cs/0112017"), "cs/0112017")
        self.assertIsNone(fsr.arxiv_id("https://doi.org/10.1109/icra.2022.1"))
        self.assertIsNone(fsr.arxiv_id(None))

    def test_doi_of_ignores_arxiv_urls(self):
        self.assertEqual(fsr.doi_of({"doi": "https://doi.org/10.1109/ICRA.2022.123"}), "10.1109/ICRA.2022.123")
        self.assertIsNone(fsr.doi_of({"doi": "https://arxiv.org/abs/2203.17270"}))
        self.assertIsNone(fsr.doi_of({"doi": None}))

    def test_external_ids_fall_back_to_the_s2_citing_file(self):
        p = paper("BEVFormer")
        self.assertEqual(fsr.external_ids(p, {"bevformer": "2203.17270"}), [("arxiv", "ARXIV:2203.17270")])

    def test_pick_by_year_allows_one_year_and_prefers_the_closest(self):
        recs = [{"year": 2020, "corpusId": 1}, {"year": 2023, "corpusId": 2}, {"year": 2022, "corpusId": 3}]
        self.assertEqual(fsr.pick_by_year(recs, 2022)["corpusId"], 3)
        self.assertEqual(fsr.pick_by_year(recs[:2], 2022)["corpusId"], 2)
        self.assertIsNone(fsr.pick_by_year([{"year": 2019, "corpusId": 1}], 2022))

    def test_venue_filter_adds_the_per_year_ivs_names(self):
        self.assertEqual(fsr.s2_venue_filter("CVPR", [2022]), "CVPR")
        itsc = fsr.s2_venue_filter("ITSC", [2016, 2023])
        self.assertIn("2016 IEEE 19th International Conference on Intelligent Transportation Systems (ITSC)", itsc)
        self.assertIn("2023 IEEE 26th International Conference", itsc)
        self.assertIn("2021 IEEE Intelligent Vehicles Symposium (IV)", fsr.s2_venue_filter("IV", [2021]))
        # "IV" alone is the Information Visualisation conference on S2.
        self.assertNotIn("IV", fsr.S2_VENUES["IV"])


class TestStepIds(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ids_path = Path(self.tmp.name) / "ids.json"
        sleep = patch.object(fsr.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.addCleanup(self.tmp.cleanup)
        citing = patch.object(fsr, "s2_citing_arxiv_ids", return_value={})
        citing.start()
        self.addCleanup(citing.stop)

    def run_ids(self, papers, fake, title_match=True):
        with patch.object(fsr, "s2_request", fake):
            return fsr.step_ids(papers, self.ids_path, title_match=title_match)

    def test_three_passes_in_order(self):
        papers = [
            paper("PointPillars: Fast Encoders", arxiv_url="https://arxiv.org/abs/1812.05784"),
            paper("A Venue Only Paper", year=2022),
            paper("Found By Title Search", venue="Some Workshop"),
            paper("Nowhere To Be Found", venue="Some Workshop"),
        ]
        fake = FakeS2(
            batch={"ARXIV:1812.05784": {"corpusId": 11}},
            bulk_pages=[
                {"data": [{"title": "A venue-only paper", "year": 2021, "externalIds": {"CorpusId": 22}}],
                 "token": "next"},
                {"data": [{"title": "Unrelated", "year": 2022, "externalIds": {"CorpusId": 99}}]},
            ],
            match={"Found By Title Search": {"title": "Found by title search.", "corpusId": 33}},
        )
        state = self.run_ids(papers, fake)
        self.assertEqual(state["ids"], {"pointpillarsfastencoders": 11, "avenueonlypaper": 22,
                                        "foundbytitlesearch": 33})
        self.assertEqual(state["via"]["avenueonlypaper"], "venue")
        self.assertIn("nowheretobefound", state["no_match"])
        self.assertEqual(state["venue_years_scanned"], ["CVPR|2022"])
        # The bulk scan followed the continuation token, and covered one
        # year either side of the corpus's own years.
        bulk = [c for c in fake.calls if c[0] == "/paper/search/bulk"]
        self.assertEqual(len(bulk), 2)
        self.assertEqual(bulk[0][1]["year"], "2021-2023")
        self.assertEqual(bulk[1][1]["token"], "next")
        # Saved to disk, and a rerun asks S2 nothing new.
        saved = json.loads(self.ids_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["ids"]["foundbytitlesearch"], 33)
        rerun = FakeS2()
        self.run_ids(papers, rerun)
        self.assertEqual([c[0] for c in rerun.calls], [])

    def test_title_match_needs_the_same_normalized_title(self):
        fake = FakeS2(match={"Objects as Points": {"title": "Tracking Objects as Points", "corpusId": 5}})
        state = self.run_ids([paper("Objects as Points", venue="Elsewhere")], fake)
        self.assertEqual(state["ids"], {})
        self.assertIn("objectsaspoints", state["no_match"])

    def test_venue_scan_rejects_a_same_title_paper_years_away(self):
        fake = FakeS2(bulk_pages=[{"data": [{"title": "Welcome", "year": 2016, "externalIds": {"CorpusId": 7}}]}])
        state = self.run_ids([paper("Welcome", venue="IROS", year=2022)], fake, title_match=False)
        self.assertEqual(state["ids"], {})

    def test_malformed_id_in_a_batch_only_loses_that_id(self):
        calls = []

        def fake(path, params, body=None):
            calls.append(body["ids"])
            if "DOI:bad" in body["ids"]:
                raise http_error(400)
            return [{"corpusId": 1} for _ in body["ids"]]

        with patch.object(fsr, "s2_request", fake):
            out = fsr.batch_lookup(["ARXIV:1", "DOI:bad", "ARXIV:2"])
        self.assertEqual(out, [{"corpusId": 1}, None, {"corpusId": 1}])


class TestStepRefs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.refs_path = Path(self.tmp.name) / "refs.json"
        sleep = patch.object(fsr.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_stores_all_reference_ids_and_records_empty_and_unknown(self):
        papers = [paper("Citing Paper"), paper("No Refs Paper"), paper("Unknown Paper"),
                  paper("IEEE Paper"), paper("Unmapped")]
        ids_state = {"ids": {"citingpaper": 1, "norefspaper": 2, "unknownpaper": 3, "ieeepaper": 4}}

        def fake(path, params, body=None):
            self.assertEqual(params["fields"], "referenceCount,references.corpusId")
            by_id = {
                "CorpusId:1": {"paperId": "a", "references": [
                    {"paperId": "x", "corpusId": "20"}, {"paperId": None, "corpusId": None},
                    {"paperId": "y", "corpusId": "10"}, {"paperId": "x", "corpusId": "20"}]},
                "CorpusId:2": {"paperId": "b", "referenceCount": 0, "references": []},
                # S2 knows there are 40 references but the publisher has
                # them elided from the API.
                "CorpusId:4": {"paperId": "d", "referenceCount": 40, "references": []},
            }
            return [by_id.get(i) for i in body["ids"]]

        with patch.object(fsr, "s2_request", fake):
            refs = fsr.step_refs(papers, ids_state, self.refs_path)
        self.assertEqual(refs["references"], {"citingpaper": [10, 20]})
        self.assertIn("norefspaper", refs["empty"])
        self.assertIn("unknownpaper", refs["not_found"])
        self.assertIn("ieeepaper", refs["elided"])
        self.assertNotIn("ieeepaper", refs["empty"])
        # Not retried until RETRY_EMPTY_AFTER_DAYS have passed.
        self.assertEqual(fsr.refs_pending(papers, ids_state, fsr.load_refs(self.refs_path)), [])

    def test_too_large_response_halves_the_batch(self):
        papers = [paper(f"Paper {i}") for i in range(4)]
        ids_state = {"ids": {f"paper{i}": i for i in range(4)}}
        sizes = []

        def fake(path, params, body=None):
            sizes.append(len(body["ids"]))
            if len(body["ids"]) > 2:
                raise http_error(400)
            return [{"references": [{"corpusId": 99}]} for _ in body["ids"]]

        with patch.object(fsr, "s2_request", fake), \
                patch.object(fsr, "REFS_BATCH_SIZE", 4), patch.object(fsr, "MIN_REFS_BATCH_SIZE", 1):
            refs = fsr.step_refs(papers, ids_state, self.refs_path)
        self.assertEqual(sizes, [4, 2, 2])
        self.assertEqual(len(refs["references"]), 4)

    def test_elided_step_asks_the_one_paper_endpoint_and_remembers_misses(self):
        papers = [paper("Preprint", venue="arXiv.org"), paper("IEEE Paper", venue="ICRA")]
        ids_state = {"ids": {"preprint": 1, "ieeepaper": 2}}
        fsr.save_json(self.refs_path, {"elided": {"preprint": "2026-01-01", "ieeepaper": "2026-01-01"}})
        calls = []

        def fake(path, params, body=None):
            calls.append((path, params["offset"]))
            if path == "/paper/CorpusId:2/references":
                return {"offset": 0, "data": []}
            # Two pages, and a reference S2 has no CorpusId for.
            if params["offset"] == 0:
                return {"offset": 0, "next": 2, "data": [
                    {"citedPaper": {"corpusId": "30"}}, {"citedPaper": {"corpusId": None}}]}
            return {"offset": 2, "data": [{"citedPaper": {"corpusId": 10}}]}

        with patch.object(fsr, "s2_request", fake):
            refs = fsr.step_elided(papers, ids_state, self.refs_path)
        self.assertEqual(refs["references"], {"preprint": [10, 30]})
        self.assertNotIn("preprint", refs["elided"])
        self.assertIn("ieeepaper", refs["elided_checked"])
        self.assertEqual(calls, [("/paper/CorpusId:1/references", 0), ("/paper/CorpusId:1/references", 2),
                                 ("/paper/CorpusId:2/references", 0)])
        # A second run doesn't ask again for the one that had nothing.
        calls.clear()
        with patch.object(fsr, "s2_request", fake):
            fsr.step_elided(papers, ids_state, self.refs_path)
        self.assertEqual(calls, [])


class TestRateLimit(unittest.TestCase):
    def test_repeated_429_raises_rate_limited(self):
        with patch.object(fsr, "load_api_key", return_value="k"), \
                patch.object(fsr.time, "sleep"), \
                patch.object(fsr.urllib.request, "urlopen", side_effect=http_error(429)) as urlopen:
            with self.assertRaises(fsr.RateLimited):
                fsr.s2_request("/paper/batch", {"fields": "corpusId"}, {"ids": ["ARXIV:1"]})
        self.assertEqual(urlopen.call_count, fsr.MAX_RATE_LIMIT_RETRIES)


if __name__ == "__main__":
    unittest.main()
