#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import repair_openalex_institution_errors as repair


class TestRepairAffiliations(unittest.TestCase):
    def test_drops_a_known_wrong_institution(self):
        new_affs, changed = repair.repair_affiliations(
            ["TH Bingen University of Applied Sciences", "Max Planck Institute for Intelligent Systems"])
        self.assertTrue(changed)
        self.assertEqual(new_affs, ["Max Planck Institute for Intelligent Systems"])

    def test_can_leave_an_empty_list_when_it_was_the_only_entry(self):
        new_affs, changed = repair.repair_affiliations(["TH Bingen University of Applied Sciences"])
        self.assertTrue(changed)
        self.assertEqual(new_affs, [])

    def test_leaves_a_real_institution_unchanged(self):
        new_affs, changed = repair.repair_affiliations(["University of Tübingen"])
        self.assertFalse(changed)
        self.assertEqual(new_affs, ["University of Tübingen"])

    def test_leaves_empty_or_none_unchanged(self):
        self.assertEqual(repair.repair_affiliations([]), ([], False))
        self.assertEqual(repair.repair_affiliations(None), (None, False))

    def test_idempotent_a_second_pass_is_a_no_op(self):
        first, _ = repair.repair_affiliations(["TH Bingen University of Applied Sciences", "MPI-IS"])
        second, changed = repair.repair_affiliations(first)
        self.assertFalse(changed)
        self.assertEqual(second, first)


if __name__ == "__main__":
    unittest.main()
