#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression test for the real bug caught on nuScenes: apply_cvf_affiliations.py
iterated the raw "authors" STRING character-by-character instead of splitting
it into names first (see repair_garbled_authors_detail.py for the data-repair
side of this fix).

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply_cvf_affiliations as aca


class TestApplyCvfAffiliations(unittest.TestCase):
    def _run(self, papers, affiliations):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        papers_file = Path(tmpdir.name) / "papers_full.json"
        affs_file = Path(tmpdir.name) / "affiliations_cvf.json"
        papers_file.write_text(json.dumps(papers), encoding="utf-8")
        affs_file.write_text(json.dumps({"affiliations": affiliations}), encoding="utf-8")

        orig_papers, orig_affs = aca.PAPERS_FILE, aca.CVF_AFFS_FILE
        aca.PAPERS_FILE, aca.CVF_AFFS_FILE = papers_file, affs_file
        try:
            aca.main()
        finally:
            aca.PAPERS_FILE, aca.CVF_AFFS_FILE = orig_papers, orig_affs
        return json.loads(papers_file.read_text(encoding="utf-8"))

    def test_splits_the_raw_author_string_into_one_entry_per_author(self):
        papers = [{"title": "nuScenes: A Multimodal Dataset for Autonomous Driving",
                   "authors": "A. Researcher, B. Scientist, C. Scholar"}]
        affiliations = {"nuscenesamultimodaldatasetforautonomousdriving":
                         {"affiliations": ["nuTonomy: an APTIV company"]}}
        result = self._run(papers, affiliations)
        names = [a["name"] for a in result[0]["authors_detail"]]
        self.assertEqual(names, ["A. Researcher", "B. Scientist", "C. Scholar"])

    def test_every_author_gets_the_same_shared_affiliation_list(self):
        papers = [{"title": "Paper", "authors": "A One, B Two"}]
        affiliations = {"paper": {"affiliations": ["MIT", "Google"]}}
        result = self._run(papers, affiliations)
        for a in result[0]["authors_detail"]:
            self.assertEqual(a["affiliations"], ["MIT", "Google"])


if __name__ == "__main__":
    unittest.main()
