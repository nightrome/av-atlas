#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for fetch_arxiv_monthly.py against recorded OAI-PMH responses.

fixtures/oai_arxiv_page1.xml and page2.xml are records cut from a real
ListRecords response (set=cs, 2026-09-14). Only the resumption token was
added by hand, so the two pages chain like a real multi-page harvest.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import email.message
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_arxiv_monthly as fam
import merge_corpus as mc

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PAGE1 = (FIXTURES / "oai_arxiv_page1.xml").read_text(encoding="utf-8")
PAGE2 = (FIXTURES / "oai_arxiv_page2.xml").read_text(encoding="utf-8")
NO_RECORDS = ('<?xml version="1.0" encoding="UTF-8"?>'
              '<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">'
              '<error code="noRecordsMatch">No records</error></OAI-PMH>')


def http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://oaipmh.arxiv.org/oai", code, "x", headers, None)


class FakeOai:
    """Serves page1 then page2 for the "cs" set, nothing for any other set."""

    def __init__(self, errors=()):
        self.urls = []
        self.errors = list(errors)

    def __call__(self, url):
        self.urls.append(url)
        if self.errors:
            raise self.errors.pop(0)
        if "resumptionToken=fixture-token-1" in url:
            return PAGE2
        if "set=cs&" in url:
            return PAGE1
        return NO_RECORDS


class TestParse(unittest.TestCase):
    def test_page_with_token(self):
        records, token = fam.parse_list_records(PAGE1)
        self.assertEqual(token, "fixture-token-1")
        self.assertEqual(len(records), 6)
        read = records[0]
        self.assertEqual(read["id"], "2609.12371")
        self.assertEqual(read["categories"], ["cs.RO"])
        self.assertEqual(read["authors"][:2], ["Zhiyuan Liu", "Yuanxin Tian"])
        self.assertTrue(read["title"].startswith("READ: Learning Risk-Informed Fields"))
        self.assertNotIn("\n", read["abstract"])

    def test_last_page_has_empty_token(self):
        records, token = fam.parse_list_records(PAGE2)
        self.assertIsNone(token)
        self.assertEqual(records[0]["doi"], "10.1109/SDF67080.2025.11331266")

    def test_no_records_match_is_empty_not_an_error(self):
        self.assertEqual(fam.parse_list_records(NO_RECORDS), ([], None))

    def test_other_oai_errors_raise(self):
        bad = NO_RECORDS.replace("noRecordsMatch", "badResumptionToken")
        with self.assertRaises(fam.HarvestError):
            fam.parse_list_records(bad)


class TestIds(unittest.TestCase):
    def test_arxiv_id_from_urls_and_bare_ids(self):
        self.assertEqual(fam.arxiv_id_from("https://arxiv.org/abs/2608.17420"), "2608.17420")
        self.assertEqual(fam.arxiv_id_from("http://arxiv.org/abs/2301.00001v3"), "2301.00001")
        self.assertEqual(fam.arxiv_id_from("2301.00001v2"), "2301.00001")
        self.assertEqual(fam.arxiv_id_from("cs/0112017"), "cs/0112017")
        self.assertIsNone(fam.arxiv_id_from("10.1109/CVPR.2020.001"))
        self.assertIsNone(fam.arxiv_id_from(None))

    def test_new_submission_uses_the_id_month(self):
        floor = "2026-08-15"
        # A 2022 paper whose OAI "created" field says September 2026 (seen
        # in the real response) is not new.
        self.assertFalse(fam.is_new_submission({"created": "2026-09-11"}, "2204.07865", floor))
        self.assertTrue(fam.is_new_submission({"created": "2026-07-24"}, "2609.11947", floor))
        self.assertTrue(fam.is_new_submission({"created": "2026-08-20"}, "2608.30001", floor))
        self.assertFalse(fam.is_new_submission({"created": "2026-08-02"}, "2608.00101", floor))
        self.assertFalse(fam.is_new_submission({"created": "2026-09-01"}, "cs/0112017", floor))


class TestSelect(unittest.TestCase):
    def setUp(self):
        self.records = fam.parse_list_records(PAGE1)[0] + fam.parse_list_records(PAGE2)[0]

    def ids(self, accepted):
        return [r["id"] for r in accepted]

    def test_keeps_new_av_papers_only(self):
        accepted, counts = fam.select(self.records, set(), set(), set())
        # READ (cs.RO), When2Talk (cs.HC primary, cross-listed to cs.CV) and
        # the multi-vehicle dataset (cs.RO). Left out: the 2022 paper, a
        # cs.CL paper, a withdrawn one, and the Vienna drive-test dataset,
        # which is outside cs.CV/cs.RO and has no AV phrase.
        self.assertEqual(self.ids(accepted), ["2609.12371", "2609.12503", "2609.12871"])
        self.assertEqual(counts["withdrawn"], 1)
        self.assertEqual(counts["accepted"], 3)

    def test_outside_focus_needs_an_av_phrase(self):
        base = {"id": "2609.99999", "created": "2026-09-10", "categories": ["cs.LG"],
                "abstract": "We study sample efficiency."}
        driver_only = dict(base, title="Driving Down Costs of Device Driver Verification")
        phrase = dict(base, title="Sample-Efficient Planning for Autonomous Driving")
        self.assertFalse(fam.is_av(driver_only))
        self.assertTrue(fam.is_av(phrase))
        self.assertTrue(fam.is_av(dict(driver_only, categories=["cs.LG", "cs.RO"])))

    def test_dedupes_against_corpus_ledger_and_itself(self):
        read_title = mc.normalize_title(self.records[0]["title"])
        accepted, counts = fam.select(self.records, {"2609.12503"}, {read_title}, {"2609.12871"})
        self.assertEqual(accepted, [])
        self.assertEqual(counts["already_in_corpus"], 2)

    def test_same_paper_in_two_sets_is_taken_once(self):
        accepted, _ = fam.select(self.records + self.records, set(), set(), set())
        self.assertEqual(self.ids(accepted), ["2609.12371", "2609.12503", "2609.12871"])


class TestHarvest(unittest.TestCase):
    def test_follows_resumption_token_with_only_the_token(self):
        fake = FakeOai()
        records = fam.harvest("cs", "2026-08-15", fake, sleep_fn=lambda s: None)
        self.assertEqual(len(records), 7)
        self.assertIn("from=2026-08-15", fake.urls[0])
        self.assertTrue(fake.urls[0].startswith("https://oaipmh.arxiv.org/oai?"))
        self.assertIn("resumptionToken=fixture-token-1", fake.urls[1])
        self.assertNotIn("set=", fake.urls[1])

    def test_waits_out_flow_control(self):
        sleeps = []
        fake = FakeOai(errors=[http_error(503, retry_after=20)])
        fam.harvest("cs", "2026-08-15", fake, sleep_fn=sleeps.append)
        self.assertEqual(sleeps[0], 20)

    def test_stops_on_403_and_429(self):
        for code in (403, 429):
            with self.assertRaises(fam.HarvestError):
                fam.harvest("cs", "2026-08-15", FakeOai(errors=[http_error(code)]), sleep_fn=lambda s: None)


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.venues = root / "venues"
        self.venues.mkdir()
        (self.venues / "cvpr2026.json").write_text(json.dumps([{"title": "Some CVPR Paper"}]), encoding="utf-8")
        self.state = root / "arxiv_monthly_state.json"
        self.kw = dict(sleep_fn=lambda s: None, venues_dir=self.venues, state_file=self.state,
                       papers_file=root / "missing.json", arxiv_ids_file=root / "missing2.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_writes_venue_file_and_ledger(self):
        fake = FakeOai()
        summary = fam.run(fetch_fn=fake, today="2026-09-24", **self.kw)
        self.assertIn("from=2026-08-15", fake.urls[0])
        self.assertEqual(summary["accepted"], 3)
        out = json.loads((self.venues / "arxiv_monthly_2026-09.json").read_text(encoding="utf-8"))
        rec = next(p for p in out if p["arxiv_id"] == "2609.12871")
        self.assertEqual(rec["arxiv_url"], "https://arxiv.org/abs/2609.12871")
        self.assertEqual(rec["doi"], "10.1109/SDF67080.2025.11331266")
        self.assertEqual(rec["conference"], "arXiv preprint")
        self.assertEqual(rec["year"], 2026)
        self.assertEqual(rec["primary_category"], "cs.RO")
        self.assertEqual(rec["first_seen"], "2026-09-24")
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["window_end"], "2026-09-24")
        self.assertEqual(sorted(state["seen"]), ["2609.12371", "2609.12503", "2609.12871"])

    def test_second_run_overlaps_and_adds_nothing_twice(self):
        fam.run(fetch_fn=FakeOai(), today="2026-09-24", **self.kw)
        fake = FakeOai()
        summary = fam.run(fetch_fn=fake, today="2026-10-24", **self.kw)
        self.assertIn(f"from=2026-09-{24 - fam.OVERLAP_DAYS}", fake.urls[0])
        self.assertEqual(summary["accepted"], 0)
        self.assertFalse((self.venues / "arxiv_monthly_2026-10.json").exists())
        state = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(state["window_end"], "2026-10-24")
        self.assertEqual(len(state["runs"]), 2)

    def test_dry_run_and_failed_run_write_nothing(self):
        fam.run(fetch_fn=FakeOai(), today="2026-09-24", dry_run=True, **self.kw)
        self.assertFalse(self.state.exists())
        with self.assertRaises(fam.HarvestError):
            fam.run(fetch_fn=FakeOai(errors=[http_error(429)]), today="2026-09-24", **self.kw)
        self.assertFalse(self.state.exists())
        self.assertEqual([f.name for f in self.venues.iterdir()], ["cvpr2026.json"])


class TestMergeReadsMonthlyFiles(unittest.TestCase):
    def test_discovery_source_label(self):
        self.assertEqual(mc.discovery_source("arxiv_monthly_2026-09.json"), "arxiv_monthly_intake")
        self.assertEqual(mc.discovery_source("arxiv_s2_citing.json"), "arxiv_s2_citing_discovery")


if __name__ == "__main__":
    unittest.main()
