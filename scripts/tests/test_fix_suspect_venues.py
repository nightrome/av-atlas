#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fix_suspect_venues as fix


class TestFixSuspectVenues(unittest.TestCase):
    def test_renames_a_verified_wrong_name_and_keeps_the_raw_one(self):
        e = {"conference": "Machine-mediated learning"}
        self.assertTrue(fix.fix_entry(e))
        self.assertEqual(e["conference"], "Machine Learning")
        self.assertEqual(e["venue_raw"], "Machine-mediated learning")
        self.assertNotIn("venue_status", e)

    def test_marks_an_unverifiable_short_name_as_missing_not_unparsed(self):
        e = {"conference": "Delta"}
        self.assertTrue(fix.fix_entry(e))
        self.assertEqual(e["conference"], "arXiv preprint")
        self.assertEqual(e["venue_status"], "missing")
        self.assertEqual(e["venue_raw"], "Delta")

    def test_leaves_real_venues_and_unparsed_records_alone(self):
        for venue in ("CVPR", "Nature", "arXiv preprint"):
            e = {"conference": venue}
            self.assertFalse(fix.fix_entry(e))
            self.assertNotIn("venue_status", e)

    def test_second_pass_renames_and_missing(self):
        e = {"conference": "International Conference on Information Control Systems & Technologies"}
        self.assertTrue(fix.fix_entry(e))
        self.assertEqual(e["conference"], "ICST")
        e = {"conference": "Robotics"}
        fix.fix_entry(e)
        self.assertEqual(e["conference"], "RSS")
        e = {"conference": "Interacción"}
        fix.fix_entry(e)
        self.assertEqual(e["venue_status"], "missing")

    def test_is_idempotent(self):
        e = {"conference": "Make"}
        fix.fix_entry(e)
        self.assertFalse(fix.fix_entry(e))


if __name__ == "__main__":
    unittest.main()
