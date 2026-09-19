#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for apply_affiliations_arxiv.py's has_code_link handling --
it must apply unconditionally (not gated by authors_detail richness the way
the affiliations block below it is), and only overwrite the unset (None)
case, never a value a prior run already applied.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply_affiliations_arxiv as afa


class TestApplyAffiliationsArxiv(unittest.TestCase):
    def _run(self, papers, arxiv_affs, country_map=None):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        papers_file = Path(tmpdir.name) / "papers_full.json"
        affs_file = Path(tmpdir.name) / "affiliations_arxiv.json"
        country_file = Path(tmpdir.name) / "institution_countries.json"
        papers_file.write_text(json.dumps(papers), encoding="utf-8")
        affs_file.write_text(json.dumps(arxiv_affs), encoding="utf-8")
        country_file.write_text(json.dumps(country_map or {}), encoding="utf-8")

        orig = (afa.PAPERS_FILE, afa.ARXIV_AFFS_FILE, afa.COUNTRY_MAP_FILE)
        afa.PAPERS_FILE, afa.ARXIV_AFFS_FILE, afa.COUNTRY_MAP_FILE = papers_file, affs_file, country_file
        try:
            afa.main()
        finally:
            afa.PAPERS_FILE, afa.ARXIV_AFFS_FILE, afa.COUNTRY_MAP_FILE = orig
        return json.loads(papers_file.read_text(encoding="utf-8"))

    def test_has_code_link_applied_even_when_paper_already_has_richer_authors_detail(self):
        # Unlike authors_detail itself, has_code_link isn't an "affiliation
        # richness" question -- a paper OpenAlex already enriched should
        # still pick it up.
        papers = [{"title": "Paper", "authors_detail": [{"name": "Someone", "affiliations": ["MIT"]}]}]
        arxiv_affs = {"paper": {"authors": [], "arxiv_id": "2401.00001", "has_code_link": True}}
        result = self._run(papers, arxiv_affs)
        self.assertTrue(result[0]["has_code_link"])
        # And the richer authors_detail must not have been overwritten.
        self.assertEqual(result[0]["authors_detail"][0]["name"], "Someone")

    def test_has_code_link_false_applied_when_checked_and_not_found(self):
        papers = [{"title": "Paper"}]
        arxiv_affs = {"paper": {"authors": [], "arxiv_id": "2401.00001", "has_code_link": False}}
        result = self._run(papers, arxiv_affs)
        self.assertIn("has_code_link", result[0])
        self.assertFalse(result[0]["has_code_link"])

    def test_has_code_link_omitted_when_no_arxiv_id_ever_resolved(self):
        # has_code_link is None (not False) when there was no ar5iv page to
        # check at all -- must not be recorded as a confirmed "no code".
        papers = [{"title": "Paper"}]
        arxiv_affs = {"paper": {"authors": [], "arxiv_id": None, "has_code_link": None}}
        result = self._run(papers, arxiv_affs)
        self.assertNotIn("has_code_link", result[0])

    def test_existing_has_code_link_not_overwritten(self):
        papers = [{"title": "Paper", "has_code_link": True}]
        arxiv_affs = {"paper": {"authors": [], "arxiv_id": "2401.00001", "has_code_link": False}}
        result = self._run(papers, arxiv_affs)
        self.assertTrue(result[0]["has_code_link"])


if __name__ == "__main__":
    unittest.main()
