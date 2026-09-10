#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for merge_corpus.py -- title normalization/dedup, and the
authors_detail carry-over safety net (a real incident: re-running this script
used to silently discard OpenAlex author enrichment that a separate script
had patched directly onto papers_full.json, because this script rebuilds
that file from venues/*.json alone).

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_merge_corpus.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import merge_corpus as mc


class TestNormalizeTitle(unittest.TestCase):
    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(mc.normalize_title("Depth Anything!"), mc.normalize_title("depth anything"))

    def test_different_titles_do_not_collide(self):
        self.assertNotEqual(mc.normalize_title("CARLA Simulator"), mc.normalize_title("CARLA 2.0 Simulator"))

    def test_none_title_does_not_crash(self):
        self.assertEqual(mc.normalize_title(None), "")


class TestConferenceAndYearForFile(unittest.TestCase):
    def test_derives_conference_and_year_from_a_per_venue_year_filename(self):
        self.assertEqual(mc.conference_and_year_for_file("cvpr2024.json"), ("CVPR", 2024))
        self.assertEqual(mc.conference_and_year_for_file("neurips2025.json"), ("NeurIPS", 2025))

    def test_strips_a_github_suffix(self):
        self.assertEqual(mc.conference_and_year_for_file("icra2019_github.json"), ("ICRA", 2019))

    def test_a_continuous_journal_file_gets_conference_but_no_year(self):
        # "_all" files (e.g. ijcv_all.json) span many years -- the real year
        # has to come from each entry, not the filename.
        self.assertEqual(mc.conference_and_year_for_file("ijcv_all.json"), ("IJCV", None))

    def test_unrecognized_prefix_returns_no_conference(self):
        conference, year = mc.conference_and_year_for_file("arxiv_s2_citing.json")
        self.assertIsNone(conference)


class TestMergeCorpusEndToEnd(unittest.TestCase):
    def _run(self, venue_papers, prior_papers_full=None, venue_filename="cvpr2024.json"):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        base = Path(tmpdir.name)
        venues_dir = base / "venues"
        venues_dir.mkdir()
        (venues_dir / venue_filename).write_text(json.dumps(venue_papers), encoding="utf-8")

        categories_file = base / "categories.json"
        categories_file.write_text(json.dumps({"categories": []}), encoding="utf-8")

        out_file = base / "papers_full.json"
        if prior_papers_full is not None:
            out_file.write_text(json.dumps(prior_papers_full), encoding="utf-8")

        orig = (mc.VENUES_DIR, mc.OUT_FILE, mc.CATEGORIES_FILE)
        mc.VENUES_DIR, mc.OUT_FILE, mc.CATEGORIES_FILE = (venues_dir, out_file, categories_file)
        try:
            mc.main()
        finally:
            mc.VENUES_DIR, mc.OUT_FILE, mc.CATEGORIES_FILE = orig

        return json.loads(out_file.read_text(encoding="utf-8"))

    def test_carries_over_authors_detail_from_prior_run(self):
        prior = [{
            "title": "Planning-Oriented Autonomous Driving",
            "authors_detail": [{"name": "Chonghao Sima", "affiliations": ["HKU"], "countries": ["HK"]}],
        }]
        fresh_venue_papers = [{
            "title": "Planning-Oriented Autonomous Driving",
            "authors": "C Sima", "abstract": "Autonomous driving.", "conference": "CVPR", "year": 2023,
        }]
        papers = self._run(fresh_venue_papers, prior_papers_full=prior)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["authors_detail"][0]["name"], "Chonghao Sima")

    def test_carries_over_citations_by_source_from_prior_run(self):
        prior = [{
            "title": "Some Paper",
            "citations_by_source": {"openalex": {"count": 40, "updated": "2026-08-01"}},
        }]
        venue_papers = [{"title": "Some Paper", "authors": "S Name", "conference": "CVPR", "year": 2023}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertEqual(papers[0]["citations_by_source"]["openalex"]["count"], 40)

    def test_carries_over_authors_detail_source_from_prior_run(self):
        # A real bug, not hypothetical: authors_detail_source was missing
        # from CARRY_OVER_FIELDS, so it got silently dropped on every rebuild
        # even though authors_detail itself survived -- confirmed on real
        # data, every paper with authors_detail showed
        # authors_detail_source=None.
        prior = [{
            "title": "Some Paper",
            "authors_detail": [{"name": "Carried Name"}],
            "authors_detail_source": "openalex",
        }]
        venue_papers = [{"title": "Some Paper", "authors": "S Name", "conference": "CVPR", "year": 2023}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertEqual(papers[0]["authors_detail_source"], "openalex")

    def test_carries_over_a_confirmed_false_has_code_link(self):
        # has_code_link is a real 3-state field (True/False/never-checked) --
        # a bare truthy carry-over check would silently drop every confirmed
        # False (checked, no code link found), making it indistinguishable
        # from "never checked" on the very next rebuild. Regression test for
        # exactly that bug, caught before it shipped.
        prior = [{"title": "Checked, No Code", "has_code_link": False}]
        venue_papers = [{"title": "Checked, No Code", "authors": "A B", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertIn("has_code_link", papers[0])
        self.assertFalse(papers[0]["has_code_link"])

    def test_carries_over_abstract_search_exhausted_from_prior_run(self):
        # mine_abstracts.py sets this when a clean arXiv search confirms no
        # match exists, so a rerun of this script (which rebuilds the file
        # from venues/*.json alone) must not silently discard that -- the
        # same class of bug the authors_detail carry-over above already
        # guards against.
        prior = [{"title": "No Match Paper", "abstract_search_exhausted": True}]
        venue_papers = [{"title": "No Match Paper", "authors": "A B", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertTrue(papers[0]["abstract_search_exhausted"])

    def test_carried_over_detail_is_kept_when_no_fresher_source_exists(self):
        prior = [{"title": "Some Paper", "authors_detail": [{"name": "Carried Name"}]}]
        venue_papers = [{"title": "Some Paper", "authors": "S Name", "conference": "CVPR", "year": 2023}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertEqual(papers[0]["authors_detail"][0]["name"], "Carried Name")

    def test_no_prior_file_does_not_crash(self):
        venue_papers = [{"title": "New Paper", "authors": "A B", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers, prior_papers_full=None)
        self.assertEqual(len(papers), 1)
        self.assertNotIn("authors_detail", papers[0])

    def test_venue_and_year_are_derived_from_filename_when_absent_from_entries(self):
        # The whole point of stripping "conference"/"year" from venues/*.json
        # (repo-size cleanup) -- a real per-venue-year file with neither
        # field on any entry must still produce the right venue/year.
        venue_papers = [{"title": "Stripped Fields Paper", "authors": "A B"}]
        papers = self._run(venue_papers, venue_filename="wacv2023.json")
        self.assertEqual(papers[0]["venue"], "WACV")
        self.assertEqual(papers[0]["year"], 2023)

    def test_explicit_per_entry_conference_and_year_still_win(self):
        # An entry that DOES carry its own conference/year (an "_all"
        # journal file's year, or a not-yet-migrated file) must not be
        # overridden by a filename-derived guess.
        venue_papers = [{"title": "Explicit Fields Paper", "authors": "A B",
                          "conference": "IJCV", "year": 2019}]
        papers = self._run(venue_papers, venue_filename="ijcv_all.json")
        self.assertEqual(papers[0]["venue"], "IJCV")
        self.assertEqual(papers[0]["year"], 2019)

    def test_every_paper_gets_classified(self):
        venue_papers = [{"title": "Autonomous Driving Survey", "authors": "A B",
                          "abstract": "A survey of autonomous driving.", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers)
        self.assertIn("category", papers[0])
        self.assertIn("av_relevance", papers[0])
        self.assertEqual(papers[0]["av_relevance"], "AV")

    def test_icra_and_iros_papers_are_included(self):
        venue_papers = [
            {"title": "An ICRA Paper", "authors": "A", "conference": "ICRA", "year": 2022},
            {"title": "An IROS Paper", "authors": "B", "conference": "IROS", "year": 2022},
            {"title": "A CVPR Paper", "authors": "C", "conference": "CVPR", "year": 2024},
        ]
        papers = self._run(venue_papers)
        self.assertEqual(len(papers), 3)

    def test_dedupes_by_normalized_title(self):
        venue_papers = [
            {"title": "Same Paper", "authors": "A", "conference": "CVPR", "year": 2024},
            {"title": "same paper", "authors": "B", "conference": "CVPR", "year": 2024},
        ]
        papers = self._run(venue_papers)
        self.assertEqual(len(papers), 1)


if __name__ == "__main__":
    unittest.main()
