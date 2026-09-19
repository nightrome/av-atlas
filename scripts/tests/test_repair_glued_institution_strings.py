#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import repair_glued_institution_strings as repair


class TestRepairAffiliations(unittest.TestCase):
    def test_splits_a_known_glued_string(self):
        new_affs, changed = repair.repair_affiliations(
            ["UC Berkeley Stanford UCL Virginia Tech Nvidia"])
        self.assertTrue(changed)
        self.assertEqual(new_affs, ["UC Berkeley", "Stanford", "UCL", "Virginia Tech", "Nvidia"])

    def test_leaves_a_real_institution_unchanged(self):
        new_affs, changed = repair.repair_affiliations(["University of Oxford"])
        self.assertFalse(changed)
        self.assertEqual(new_affs, ["University of Oxford"])

    def test_leaves_empty_or_none_unchanged(self):
        self.assertEqual(repair.repair_affiliations([]), ([], False))
        self.assertEqual(repair.repair_affiliations(None), (None, False))

    def test_splices_in_place_alongside_other_real_entries(self):
        new_affs, changed = repair.repair_affiliations(
            ["Tsinghua University", "UC Berkeley MIT UT Austin"])
        self.assertTrue(changed)
        self.assertEqual(new_affs, ["Tsinghua University", "UC Berkeley", "MIT", "UT Austin"])

    def test_deduplicates_when_splicing_introduces_a_repeat(self):
        # A paper's own list already carrying an institution that a glued
        # string also splits out to shouldn't double it.
        new_affs, changed = repair.repair_affiliations(["NVIDIA", "NVIDIA UCLA Stanford University"])
        self.assertTrue(changed)
        self.assertEqual(new_affs, ["NVIDIA", "UCLA", "Stanford University"])

    def test_idempotent_a_second_pass_is_a_no_op(self):
        first, _ = repair.repair_affiliations(["UC Berkeley Stanford UCL Virginia Tech Nvidia"])
        second, changed = repair.repair_affiliations(first)
        self.assertFalse(changed)
        self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
