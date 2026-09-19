#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for apply_citation_sources.py's in-corpus citation counting.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply_citation_sources as acs


class TestInCorpusCounts(unittest.TestCase):
    def test_counts_every_edge_including_self_citations(self):
        # Self-citations count toward the total -- a separate per-author
        # Self-citation % metric (aggregate.py's self_citations field)
        # covers the "how much of this is self-citation" question now, so
        # the total no longer silently excludes them (user-requested).
        graph = {"edges": {"citerkey": ["citedkey"]}}
        counts = acs.in_corpus_counts(graph)
        self.assertEqual(counts.get("citedkey", 0), 1)

    def test_counts_a_citation_with_no_shared_author(self):
        graph = {"edges": {"citerkey": ["citedkey"]}}
        counts = acs.in_corpus_counts(graph)
        self.assertEqual(counts.get("citedkey", 0), 1)

    def test_multiple_citers_of_the_same_target_all_count(self):
        graph = {"edges": {"citer-a": ["target"], "citer-b": ["target"]}}
        counts = acs.in_corpus_counts(graph)
        self.assertEqual(counts.get("target", 0), 2)


if __name__ == "__main__":
    unittest.main()
