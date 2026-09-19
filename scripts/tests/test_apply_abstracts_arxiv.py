#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for apply_abstracts_arxiv.py -- folds mine_abstracts.py's
side file (data/abstracts_arxiv.json) into papers_full.json, only filling
gaps, same convention as apply_affiliations_arxiv.py.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply_abstracts_arxiv as aaa
import mine_abstracts as ma


class TestApplyAbstractsArxiv(unittest.TestCase):
    def _run(self, papers, cache):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        papers_file = Path(tmpdir.name) / "papers_full.json"
        cache_file = Path(tmpdir.name) / "abstracts_arxiv.json"
        papers_file.write_text(json.dumps(papers), encoding="utf-8")
        cache_file.write_text(json.dumps(cache), encoding="utf-8")

        orig = (aaa.PAPERS_FILE, ma.ABSTRACTS_CACHE_FILE)
        aaa.PAPERS_FILE = papers_file
        ma.ABSTRACTS_CACHE_FILE = cache_file
        try:
            aaa.main()
        finally:
            aaa.PAPERS_FILE, ma.ABSTRACTS_CACHE_FILE = orig
        return json.loads(papers_file.read_text(encoding="utf-8"))

    def test_fills_a_missing_abstract_from_the_cache(self):
        papers = [{"title": "Real Paper"}]
        cache = {"realpaper": {"abstract": "This paper studies..."}}
        result = self._run(papers, cache)
        self.assertEqual(result[0]["abstract"], "This paper studies...")

    def test_applies_exhausted_marker_when_no_abstract_was_found(self):
        papers = [{"title": "Unfindable Paper"}]
        cache = {"unfindablepaper": {"exhausted": True}}
        result = self._run(papers, cache)
        self.assertTrue(result[0].get("abstract_search_exhausted"))
        self.assertNotIn("abstract", result[0])

    def test_existing_abstract_is_never_overwritten(self):
        papers = [{"title": "Real Paper", "abstract": "The original, correct abstract."}]
        cache = {"realpaper": {"abstract": "A different, wrong abstract."}}
        result = self._run(papers, cache)
        self.assertEqual(result[0]["abstract"], "The original, correct abstract.")

    def test_existing_exhausted_marker_is_never_overwritten_by_a_new_search(self):
        # If a paper was already marked exhausted, a later cache entry
        # (e.g. from a re-run with a different search strategy) shouldn't
        # silently flip it back to unknown.
        papers = [{"title": "Paper", "abstract_search_exhausted": True}]
        cache = {"paper": {"abstract": "Found it after all"}}
        result = self._run(papers, cache)
        self.assertNotIn("abstract", result[0])
        self.assertTrue(result[0]["abstract_search_exhausted"])

    def test_paper_with_no_cache_entry_is_left_alone(self):
        papers = [{"title": "Never Searched"}]
        result = self._run(papers, {})
        self.assertNotIn("abstract", result[0])
        self.assertNotIn("abstract_search_exhausted", result[0])


class TestAlreadyKnown(unittest.TestCase):
    def test_true_when_papers_full_json_already_has_an_abstract(self):
        self.assertTrue(ma.already_known({"title": "P", "abstract": "..."}, {}))

    def test_true_when_papers_full_json_already_marked_exhausted(self):
        self.assertTrue(ma.already_known({"title": "P", "abstract_search_exhausted": True}, {}))

    def test_true_when_only_the_cache_has_it_not_yet_applied(self):
        cache = {"p": {"abstract": "..."}}
        self.assertTrue(ma.already_known({"title": "P"}, cache))

    def test_true_when_only_the_cache_marks_it_exhausted(self):
        cache = {"p": {"exhausted": True}}
        self.assertTrue(ma.already_known({"title": "P"}, cache))

    def test_false_when_neither_source_knows_about_it(self):
        self.assertFalse(ma.already_known({"title": "Brand New Paper"}, {}))


if __name__ == "__main__":
    unittest.main()
