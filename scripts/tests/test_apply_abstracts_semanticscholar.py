#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for apply_abstracts_semanticscholar.py's single-writer merge.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply_abstracts_semanticscholar as aas


class TestApplyAbstractsSemanticScholar(unittest.TestCase):
    def _run(self, papers, abstracts):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        papers_file = Path(tmpdir.name) / "papers_full.json"
        abstracts_file = Path(tmpdir.name) / "abstracts_semanticscholar.json"
        papers_file.write_text(json.dumps(papers), encoding="utf-8")
        abstracts_file.write_text(json.dumps({"abstracts": abstracts}), encoding="utf-8")

        orig_papers, orig_abstracts = aas.PAPERS_FILE, aas.ABSTRACTS_FILE
        aas.PAPERS_FILE, aas.ABSTRACTS_FILE = papers_file, abstracts_file
        try:
            aas.main()
        finally:
            aas.PAPERS_FILE, aas.ABSTRACTS_FILE = orig_papers, orig_abstracts
        return json.loads(papers_file.read_text(encoding="utf-8"))

    def test_fills_a_missing_abstract(self):
        papers = [{"title": "Some Paper", "abstract": None}]
        abstracts = {"somepaper": "We propose a method for X."}
        result = self._run(papers, abstracts)
        self.assertEqual(result[0]["abstract"], "We propose a method for X.")
        self.assertEqual(result[0]["abstract_source"], "semanticscholar")

    def test_never_overwrites_an_existing_abstract(self):
        # A real, better-sourced abstract (CVF/NeurIPS/arXiv/etc.) must never
        # be replaced by this fallback source.
        papers = [{"title": "Some Paper", "abstract": "The real, original abstract."}]
        abstracts = {"somepaper": "A different, Semantic-Scholar-sourced abstract."}
        result = self._run(papers, abstracts)
        self.assertEqual(result[0]["abstract"], "The real, original abstract.")
        self.assertNotIn("abstract_source", result[0])

    def test_paper_with_no_match_in_the_side_file_is_left_untouched(self):
        papers = [{"title": "Unmatched Paper", "abstract": None}]
        result = self._run(papers, abstracts={})
        self.assertIsNone(result[0]["abstract"])
        self.assertNotIn("abstract_source", result[0])


if __name__ == "__main__":
    unittest.main()
