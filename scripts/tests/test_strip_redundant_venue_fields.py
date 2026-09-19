#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import merge_corpus as mc
import strip_redundant_venue_fields as strip


class TestSameMappingAsMergeCorpus(unittest.TestCase):
    def test_prefix_table_matches_merge_corpus_exactly(self):
        # The two tables are kept as separate copies (see this script's own
        # docstring for why) -- this is the guard against them drifting
        # apart, which would silently break the filename-derived fallback
        # for whatever venue got added to one table and not the other.
        self.assertEqual(strip.VENUE_PREFIX_TO_CONFERENCE, mc.VENUE_PREFIX_TO_CONFERENCE)


class TestStripEntries(unittest.TestCase):
    def test_strips_conference_and_year_from_a_per_venue_year_file(self):
        papers = [{"title": "A", "conference": "CVPR", "year": 2024}]
        out, n_conf, n_year = strip.strip_entries(papers, strip_year=True)
        self.assertEqual(out, [{"title": "A"}])
        self.assertEqual((n_conf, n_year), (1, 1))

    def test_keeps_year_but_strips_conference_for_an_all_file(self):
        papers = [{"title": "A", "conference": "IJCV", "year": 2019}]
        out, n_conf, n_year = strip.strip_entries(papers, strip_year=False)
        self.assertEqual(out, [{"title": "A", "year": 2019}])
        self.assertEqual((n_conf, n_year), (1, 0))

    def test_no_op_when_fields_already_absent(self):
        papers = [{"title": "A"}]
        out, n_conf, n_year = strip.strip_entries(papers, strip_year=True)
        self.assertEqual(out, [{"title": "A"}])
        self.assertEqual((n_conf, n_year), (0, 0))

    def test_leaves_other_fields_untouched(self):
        papers = [{"title": "A", "authors": "B C", "conference": "CVPR", "year": 2024, "abstract": "..."}]
        out, _, _ = strip.strip_entries(papers, strip_year=True)
        self.assertEqual(out, [{"title": "A", "authors": "B C", "abstract": "..."}])

    def test_does_not_mutate_the_input_list(self):
        papers = [{"title": "A", "conference": "CVPR", "year": 2024}]
        strip.strip_entries(papers, strip_year=True)
        self.assertIn("conference", papers[0])
        self.assertIn("year", papers[0])


if __name__ == "__main__":
    unittest.main()
