#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_s2_author_ids as fsi


class TestResolveAuthorIds(unittest.TestCase):
    def test_404_is_a_clean_no_match_not_an_error(self):
        # Confirmed live against the real API: search/match responds 404
        # with {"error": "Title match not found"} when nothing matches --
        # a real, already-shipped bug had this raise up to the caller's
        # generic except-Exception, which counted it as a failure and never
        # marked the title checked, so it got requeried forever.
        with patch.object(fsi, "s2_get", side_effect=urllib.error.HTTPError(
                "url", 404, "Not Found", {}, None)):
            result = fsi.resolve_author_ids("Some Unmatched Title", ["A. Person"])
        self.assertEqual(result, {})

    def test_non_404_http_error_still_raises(self):
        with patch.object(fsi, "s2_get", side_effect=urllib.error.HTTPError(
                "url", 500, "Server Error", {}, None)):
            with self.assertRaises(urllib.error.HTTPError):
                fsi.resolve_author_ids("Some Title", ["A. Person"])

    def test_matching_authors_resolve_by_position(self):
        with patch.object(fsi, "s2_get", return_value={"data": [
            {"title": "Real Paper Title", "authors": [
                {"name": "Jane Doe", "authorId": "111"},
                {"name": "John Smith", "authorId": "222"},
            ]},
        ]}):
            result = fsi.resolve_author_ids("Real Paper Title", ["Jane Doe", "John Smith"])
        self.assertEqual(result, {"janedoe": "111", "johnsmith": "222"})

    def test_mismatched_author_count_resolves_nothing(self):
        # No safe way to know which API author lines up with which of ours.
        with patch.object(fsi, "s2_get", return_value={"data": [
            {"title": "Real Paper Title", "authors": [{"name": "Jane Doe", "authorId": "111"}]},
        ]}):
            result = fsi.resolve_author_ids("Real Paper Title", ["Jane Doe", "John Smith"])
        self.assertEqual(result, {})

    def test_title_mismatch_resolves_nothing(self):
        with patch.object(fsi, "s2_get", return_value={"data": [
            {"title": "A Completely Different Paper", "authors": [{"name": "Jane Doe", "authorId": "111"}]},
        ]}):
            result = fsi.resolve_author_ids("Real Paper Title", ["Jane Doe"])
        self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()


class BatchTests(unittest.TestCase):
    def test_external_id_prefers_arxiv_then_doi(self):
        self.assertEqual(fsi.external_id({"arxiv_url": "https://arxiv.org/abs/2401.00001v2"}), "ARXIV:2401.00001")
        self.assertEqual(fsi.external_id({"doi": "https://doi.org/10.1109/X.1"}), "DOI:10.1109/X.1")
        self.assertIsNone(fsi.external_id({"title": "t"}))

    def test_batch_resolves_matching_authors_and_marks_unknown_papers_checked(self):
        papers = [{"title": "A", "authors": ["Ann Lee", "Bo Chen"], "arxiv_url": "https://arxiv.org/abs/2401.00001"},
                  {"title": "B", "authors": ["Cy Dun"], "arxiv_url": "https://arxiv.org/abs/2401.00002"},
                  {"title": "C", "authors": ["Di Eve"]}]
        answer = [{"authors": [{"name": "Ann Lee", "authorId": "1"}, {"name": "Bo Chen", "authorId": "2"}]}, None]
        ids, checked = {}, set()
        rest = fsi.resolve_by_id(papers, ids, checked, lambda: None, post=lambda e: answer)
        self.assertEqual(ids, {"annlee": "1", "bochen": "2"})
        self.assertEqual(checked, {"a", "b"})
        self.assertEqual([p["title"] for p in rest], ["C"])


class ProfileCountTests(unittest.TestCase):
    def test_counts_record_every_profile_a_name_appears_under(self):
        papers = [{"title": "A", "authors": ["Ann Lee"], "arxiv_url": "https://arxiv.org/abs/2401.00001"},
                  {"title": "B", "authors": ["Ann Lee"], "arxiv_url": "https://arxiv.org/abs/2401.00002"},
                  {"title": "C", "authors": ["Ann Lee"], "arxiv_url": "https://arxiv.org/abs/2401.00003"}]
        answer = [{"authors": [{"name": "Ann Lee", "authorId": "1"}]},
                  {"authors": [{"name": "Ann Lee", "authorId": "9"}]},
                  {"authors": [{"name": "Ann Lee", "authorId": "9"}]}]
        counts = {}
        fsi.resolve_by_id(papers, {}, set(), lambda: None, post=lambda e: answer, counts=counts)
        self.assertEqual(counts, {"annlee": {"1": 1, "9": 2}})


class StringAuthorsTests(unittest.TestCase):
    def test_authors_come_as_one_comma_separated_string(self):
        self.assertEqual(fsi.author_list({"authors": "Ann Lee, Bo Chen"}), ["Ann Lee", "Bo Chen"])
        papers = [{"title": "A", "authors": "Alex H. Lang, Yu Pan", "arxiv_url": "https://arxiv.org/abs/2401.00001"}]
        answer = [{"authors": [{"name": "A. Lang", "authorId": "1"}, {"name": "Yu-Xin Pan", "authorId": "2"}]}]
        ids = {}
        fsi.resolve_by_id(papers, ids, set(), lambda: None, post=lambda e: answer)
        self.assertEqual(ids, {"alexhlang": "1", "yupan": "2"})
        self.assertFalse(fsi.same_person_name("Wei Zhang", "Wei Li"))
