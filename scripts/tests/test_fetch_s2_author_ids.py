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
