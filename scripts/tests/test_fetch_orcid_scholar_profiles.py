#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_orcid_scholar_profiles as f


def url_item(value):
    return {"url-name": "x", "url": {"value": value}}


class TestScholarProfileFromUrls(unittest.TestCase):
    def test_finds_a_profile_and_drops_extra_parameters(self):
        urls = [url_item("https://example.org/~me"),
                url_item("https://scholar.google.com/citations?user=4PRFXzwAAAAJ&hl=en&oi=ao")]
        self.assertEqual(f.scholar_profile_from_urls(urls),
                         "https://scholar.google.com/citations?user=4PRFXzwAAAAJ")

    def test_other_country_domain_and_user_not_first(self):
        urls = [url_item("https://scholar.google.co.uk/citations?hl=en&user=-CA8QgwAAAAJ")]
        self.assertEqual(f.scholar_profile_from_urls(urls),
                         "https://scholar.google.com/citations?user=-CA8QgwAAAAJ")

    def test_a_scholar_search_or_other_site_is_not_a_profile(self):
        urls = [url_item("https://scholar.google.com/scholar?q=me"), url_item("https://github.com/me")]
        self.assertIsNone(f.scholar_profile_from_urls(urls))
        self.assertIsNone(f.scholar_profile_from_urls([]))
        self.assertIsNone(f.scholar_profile_from_urls(None))


class TestOrcidsFromPapers(unittest.TestCase):
    def test_only_av_papers_and_first_name_wins(self):
        papers = [
            {"av_relevance": "AV", "authors_detail": [{"name": "Holger Caesar", "orcid": "0000-0001"}]},
            {"av_relevance": "non-AV", "authors_detail": [{"name": "Other Person", "orcid": "0000-0002"}]},
            {"av_relevance": "AV", "authors_detail": [{"name": "H. Caesar", "orcid": "0000-0001"}, {"name": "No Orcid"}]},
        ]
        self.assertEqual(f.orcids_from_papers(papers), {"0000-0001": "Holger Caesar"})


class TestRun(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = f.CANDIDATES_FILE
        f.CANDIDATES_FILE = Path(self._tmp.name) / "c.json"
        self.addCleanup(lambda: setattr(f, "CANDIDATES_FILE", self._orig))

    def test_records_profiles_and_remembers_checked(self):
        answers = {"A": [url_item("https://scholar.google.com/citations?user=aaaaaaaaaaaa")], "B": []}
        state = {"profiles": {}, "checked": {}}
        f.run({"A": "Ann Alpha", "B": "Bob Beta"}, state, fetch=answers.__getitem__, sleep=lambda s: None)
        self.assertEqual(state["profiles"],
                         {"Ann Alpha": {"scholar_url": "https://scholar.google.com/citations?user=aaaaaaaaaaaa", "orcid": "A"}})
        self.assertEqual(set(state["checked"]), {"A", "B"})

    def test_checked_orcids_are_not_asked_again(self):
        state = {"profiles": {}, "checked": {"A": "2026-01-01"}}
        calls = []
        f.run({"A": "Ann Alpha"}, state, fetch=lambda o: calls.append(o) or [], sleep=lambda s: None)
        self.assertEqual(calls, [])

    def test_a_429_stops_without_marking_that_orcid_checked(self):
        def blocked(orcid):
            raise f.Blocked("HTTP 429")
        state = {"profiles": {}, "checked": {}}
        f.run({"A": "Ann Alpha"}, state, fetch=blocked, sleep=lambda s: None)
        self.assertEqual(state["checked"], {})

    def test_one_failing_record_is_skipped_not_fatal(self):
        def flaky(orcid):
            if orcid == "A":
                raise ValueError("bad json")
            return []
        state = {"profiles": {}, "checked": {}}
        f.run({"A": "Ann Alpha", "B": "Bob Beta"}, state, fetch=flaky, sleep=lambda s: None)
        self.assertEqual(set(state["checked"]), {"B"})


if __name__ == "__main__":
    unittest.main()
