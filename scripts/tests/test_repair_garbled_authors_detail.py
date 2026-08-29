#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import repair_garbled_authors_detail as repair


class TestIsGarbled(unittest.TestCase):
    def test_detects_character_split_detail(self):
        detail = [{"name": "H"}, {"name": "o"}, {"name": "l"}]
        self.assertTrue(repair.is_garbled(detail))

    def test_real_full_names_are_not_garbled(self):
        detail = [{"name": "A. Researcher"}, {"name": "B. Scientist"}]
        self.assertFalse(repair.is_garbled(detail))

    def test_empty_detail_is_not_garbled(self):
        self.assertFalse(repair.is_garbled([]))
        self.assertFalse(repair.is_garbled(None))

    def test_short_but_real_initials_style_names_are_flagged(self):
        # A known limitation, not a false negative to fix here: single-token
        # abbreviated names ("H.", "JJ") also fall under the <=2-char rule.
        # Rare in practice (this dataset's abbreviated names are usually
        # "C Sima"-style, 3+ chars) and safer to over-flag than under-flag,
        # since the repair only rebuilds from the paper's own "authors"
        # string -- it can't make a real name shorter than it already is.
        detail = [{"name": "Al"}, {"name": "Bo"}]
        self.assertTrue(repair.is_garbled(detail))


if __name__ == "__main__":
    unittest.main()
