#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_common as fc


class TestByCitations(unittest.TestCase):
    def test_sorts_descending_by_in_corpus_citations(self):
        papers = [
            {"title": "Low", "citations_by_source": {"in_corpus": {"count": 5}}},
            {"title": "High", "citations_by_source": {"in_corpus": {"count": 500}}},
            {"title": "Mid", "citations_by_source": {"in_corpus": {"count": 50}}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual([p["title"] for p in result], ["High", "Mid", "Low"])

    def test_missing_or_zero_citations_sorts_last(self):
        papers = [
            {"title": "NoCitationsField"},
            {"title": "Cited", "citations_by_source": {"in_corpus": {"count": 10}}},
            {"title": "EmptySource", "citations_by_source": {}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual(result[0]["title"], "Cited")

    def test_does_not_use_the_dead_top_level_citations_field(self):
        # Confirmed on real data: papers_full.json's own "citations" field
        # is always None (0 of 25,639 AV papers have it set, including
        # nuScenes) -- only citations_by_source.in_corpus.count is real.
        papers = [
            {"title": "FakeHighCitations", "citations": 99999},
            {"title": "RealHighCitations", "citations_by_source": {"in_corpus": {"count": 100}}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual(result[0]["title"], "RealHighCitations")

    def test_ties_broken_by_title_for_determinism(self):
        papers = [
            {"title": "Zebra", "citations_by_source": {"in_corpus": {"count": 10}}},
            {"title": "Apple", "citations_by_source": {"in_corpus": {"count": 10}}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual([p["title"] for p in result], ["Apple", "Zebra"])

    def test_accepts_a_generator(self):
        gen = ({"title": str(i), "citations_by_source": {"in_corpus": {"count": i}}} for i in range(3))
        result = fc.by_citations(gen)
        self.assertEqual([p["title"] for p in result], ["2", "1", "0"])


if __name__ == "__main__":
    unittest.main()
