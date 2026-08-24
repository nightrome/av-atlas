#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for fetch_affiliations_arxiv.py's clean_affiliations() --
ar5iv's affiliation-note text often bundles multiple institutions and email
addresses into one run with no clean delimiter (real examples seen while
building this script, not hypothetical).

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_fetch_affiliations_arxiv.py
"""
import sys
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_affiliations_arxiv as fa


class TestCleanAffiliations(unittest.TestCase):
    def test_strips_trailing_email_block(self):
        text = "NVIDIA Research, Technion, Stanford University{pkarkus,bivanovic}@nvidia.com"
        self.assertEqual(fa.clean_affiliations(text), ["NVIDIA Research", "Technion", "Stanford University"])

    def test_single_institution_no_junk(self):
        self.assertEqual(fa.clean_affiliations("Massachusetts Institute of Technology"),
                          ["Massachusetts Institute of Technology"])

    def test_strips_leaked_latex_spacing_command(self):
        # ar5iv occasionally lets a raw LaTeX vertical-spacing command
        # (\[2mm], \[1em], ...) leak through as literal text -- seen on real
        # data: "[2mm] University of Science and Technology of China".
        self.assertEqual(fa.clean_affiliations("[2mm] University of Science and Technology of China"),
                          ["University of Science and Technology of China"])

    def test_strips_bare_email_without_braces(self):
        text = "University of Washington, Robotics at Google yuxiangy@cs.washington.edu"
        result = fa.clean_affiliations(text)
        self.assertNotIn("yuxiangy@cs.washington.edu", " ".join(result))

    def test_empty_and_whitespace_only_fragments_dropped(self):
        self.assertEqual(fa.clean_affiliations("MIT, , Stanford"), ["MIT", "Stanford"])

    def test_no_delimiter_between_institutions_stays_one_string(self):
        # Known imperfect case: some LaTeX templates render two institutions
        # with no separator at all ("Mercedes-Benz AG Ulm University"). Not
        # splittable without a delimiter -- documenting the current (safe,
        # non-harmful) behavior rather than pretending it's solved.
        self.assertEqual(fa.clean_affiliations("Mercedes-Benz AG Ulm University"),
                          ["Mercedes-Benz AG Ulm University"])


class TestParseAr5ivAffiliations(unittest.TestCase):
    def test_merged_multi_author_name_is_rejected(self):
        # IEEE-style \IEEEauthorblockN templates can render several authors'
        # names as one run -- a real person's name is essentially never more
        # than 4 words, so this must be dropped, not recorded as a garbled
        # "person" with a made-up combined name.
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Julian Wiederer Arij Bouazizi Marco Troina</span>
        </span>
        """, "html.parser")
        self.assertEqual(fa.parse_ar5iv_affiliations(soup), [])

    def test_normal_single_author_with_affiliation_is_kept(self):
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Xizhou Zhu</span>
          <span class="ltx_role_affiliation">Affiliation: SenseTime Research</span>
        </span>
        """, "html.parser")
        result = fa.parse_ar5iv_affiliations(soup)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Xizhou Zhu")
        self.assertEqual(result[0]["affiliations"], ["SenseTime Research"])


class TestParseAr5ivReferences(unittest.TestCase):
    def test_extracts_one_entry_per_bibitem(self):
        soup = BeautifulSoup("""
        <ul class="ltx_biblist">
          <li class="ltx_bibitem">Alice Author. A Great Paper. In CVPR, 2022.</li>
          <li class="ltx_bibitem">Bob Builder. Another Paper. In ICCV, 2021.</li>
        </ul>
        """, "html.parser")
        result = fa.parse_ar5iv_references(soup)
        self.assertEqual(len(result), 2)
        self.assertIn("A Great Paper", result[0])
        self.assertIn("Another Paper", result[1])

    def test_short_junk_entries_are_dropped(self):
        soup = BeautifulSoup('<li class="ltx_bibitem">x</li>', "html.parser")
        self.assertEqual(fa.parse_ar5iv_references(soup), [])


if __name__ == "__main__":
    unittest.main()
