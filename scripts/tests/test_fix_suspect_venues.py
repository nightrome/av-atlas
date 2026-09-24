#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
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

    def _use_temp_data_file(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        orig = fix.DATA_FILE
        fix.DATA_FILE = Path(tmpdir.name) / "arxiv_s2_citing.json"
        self.addCleanup(setattr, fix, "DATA_FILE", orig)

    def test_missing_s2_file_is_a_notice_not_a_crash(self):
        # Build step 1 on a fresh clone, before restore_corpus.py has run.
        self._use_temp_data_file()
        fix.main()
        self.assertFalse(fix.DATA_FILE.exists())

    def test_main_rewrites_the_file_when_something_changed(self):
        self._use_temp_data_file()
        fix.DATA_FILE.write_text(json.dumps([{"conference": "Delta"}, {"conference": "CVPR"}]), encoding="utf-8")
        fix.main()
        entries = json.loads(fix.DATA_FILE.read_text(encoding="utf-8"))
        self.assertEqual(entries[0]["venue_status"], "missing")
        self.assertEqual(entries[1], {"conference": "CVPR"})


if __name__ == "__main__":
    unittest.main()
