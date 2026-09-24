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


class TestApplyCounts(unittest.TestCase):
    def test_sets_changed_counts(self):
        papers = [{"title": "Target"}]
        graph = {"generated_at": "2026-09-01", "edges": {"a": ["target"], "b": ["target"]}}
        self.assertEqual(acs.apply_counts(papers, graph), (1, 0))
        self.assertEqual(papers[0]["citations_by_source"]["in_corpus"],
                         {"count": 2, "updated": "2026-09-01"})
        # Same graph again: nothing to change.
        self.assertEqual(acs.apply_counts(papers, graph), (0, 0))

    def test_count_with_no_edge_left_is_cleared(self):
        papers = [{"title": "Gone", "citations_by_source": {"in_corpus": {"count": 7, "updated": "2026-01-01"}}},
                  {"title": "Other", "citations_by_source": {"in_corpus": {"count": 3, "updated": "2026-01-01"},
                                                             "scholar": {"count": 9}}}]
        self.assertEqual(acs.apply_counts(papers, {"edges": {}}), (0, 2))
        self.assertNotIn("citations_by_source", papers[0])
        self.assertEqual(papers[1]["citations_by_source"], {"scholar": {"count": 9}})

    def test_missing_graph_keeps_existing_counts(self):
        papers = [{"title": "Kept", "citations_by_source": {"in_corpus": {"count": 7, "updated": "2026-01-01"}}}]
        self.assertEqual(acs.apply_counts(papers, {"edges": {}}, clear_missing=False), (0, 0))
        self.assertEqual(papers[0]["citations_by_source"]["in_corpus"]["count"], 7)


if __name__ == "__main__":
    unittest.main()
