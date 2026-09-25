#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for apply_citation_sources.py's in-corpus citation counting.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    def test_count_dropped_from_the_graph_is_removed(self):
        # A rebuilt graph can lose edges (the stricter matcher took
        # "Welcome" from 29 to 0); the old count must not linger.
        papers = [{"title": "Welcome", "citations_by_source": {"in_corpus": {"count": 29, "updated": "2026-09-08"}}}]
        self.assertEqual(acs.apply_counts(papers, {"generated_at": "2026-09-24", "edges": {}}), 1)
        self.assertNotIn("citations_by_source", papers[0])

    def test_unchanged_count_keeps_its_date(self):
        papers = [{"title": "Target", "citations_by_source": {"in_corpus": {"count": 1, "updated": "2026-09-08"}}}]
        graph = {"generated_at": "2026-09-24", "edges": {"citer": ["target"]}}
        self.assertEqual(acs.apply_counts(papers, graph), 0)
        self.assertEqual(papers[0]["citations_by_source"]["in_corpus"]["updated"], "2026-09-08")

    def test_changed_count_is_restamped(self):
        papers = [{"title": "Target"}]
        graph = {"generated_at": "2026-09-24", "edges": {"a": ["target"], "b": ["target"]}}
        acs.apply_counts(papers, graph)
        self.assertEqual(papers[0]["citations_by_source"]["in_corpus"], {"count": 2, "updated": "2026-09-24"})

    def test_missing_graph_file_leaves_papers_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            papers_file = Path(tmp) / "papers_full.json"
            raw = json.dumps([{"title": "Kept", "citations_by_source": {"in_corpus": {"count": 7}}}])
            papers_file.write_text(raw, encoding="utf-8")
            graph_file = Path(tmp) / "citation_graph.json"
            with mock.patch.object(acs, "PAPERS_FILE", papers_file), mock.patch.object(acs, "GRAPH_FILE", graph_file):
                acs.main()
            self.assertEqual(papers_file.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
