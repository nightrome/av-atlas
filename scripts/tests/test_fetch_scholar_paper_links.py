#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_scholar_paper_links as f

ROW = ('<tr class="gsc_a_tr"><td class="gsc_a_t"><a href="/citations?view_op=view_citation&amp;hl=en'
       '&amp;user=U1&amp;citation_for_view=U1:abc_D-1" class="gsc_a_at">nuScenes: A Multimodal Dataset '
       'for Autonomous Driving</a><div class="gs_gray">H Caesar, V Bankiti, AH Lang, ...</div>'
       '<div class="gs_gray">CVPR<span class="gs_oph">, 2020</span></div></td>'
       '<td class="gsc_a_c"></td><td class="gsc_a_y"><span class="gsc_a_h gs_ibl">2020</span></td></tr>')

PAPER = {"title": "nuScenes: A Multimodal Dataset for Autonomous Driving", "year": "2020",
         "authors": ["Holger Caesar", "Varun Bankiti"]}


class TestParse(unittest.TestCase):
    def test_parses_row(self):
        (r,) = f.parse_profile_rows("<table>" + ROW + "</table>")
        self.assertEqual(r["citation_for_view"], "U1:abc_D-1")
        self.assertEqual(r["year"], 2020)
        self.assertEqual(r["authors"], "H Caesar, V Bankiti, AH Lang, ...")
        self.assertEqual(r["venue"], "CVPR, 2020")

    def test_no_rows(self):
        self.assertEqual(f.parse_profile_rows("<html>nothing</html>"), [])


class TestMatch(unittest.TestCase):
    def row(self, **kw):
        r = f.parse_profile_rows(ROW)[0]
        r.update(kw)
        return r

    def test_confirmed(self):
        self.assertTrue(f.row_matches_paper(self.row(), PAPER))

    def test_title_differs(self):
        self.assertFalse(f.row_matches_paper(self.row(title="nuScenes lidarseg"), PAPER))

    def test_no_author_agreement(self):
        self.assertFalse(f.row_matches_paper(self.row(authors="X Other, Y Person"), PAPER))

    def test_same_surname_other_initial_is_not_agreement(self):
        self.assertFalse(f.row_matches_paper(self.row(authors="J Caesar"), PAPER))

    def test_year_far_off(self):
        self.assertFalse(f.row_matches_paper(self.row(year=2016), PAPER))

    def test_year_one_off_ok(self):
        self.assertTrue(f.row_matches_paper(self.row(year=2019), PAPER))

    def test_accents_fold(self):
        p = {"title": "T x", "year": "2020", "authors": ["Marius Zöllner"]}
        r = {"title": "T x", "authors": "JM Zollner", "year": 2020}
        self.assertTrue(f.row_matches_paper(r, p))


class TestFindLinks(unittest.TestCase):
    def test_link_built_from_row(self):
        rows = f.parse_profile_rows(ROW)
        idx = f.index_by_title([PAPER])
        out = f.find_links(rows, idx, "U1")
        self.assertEqual(list(out.values()), [
            "https://scholar.google.com/citations?view_op=view_citation&hl=en&citation_for_view=U1:abc_D-1"])

    def test_row_listed_twice_is_skipped(self):
        rows = f.parse_profile_rows(ROW + ROW.replace("abc_D-1", "zzz"))
        self.assertEqual(f.find_links(rows, f.index_by_title([PAPER]), "U1"), {})

    def test_title_shared_by_two_target_papers_is_skipped(self):
        idx = f.index_by_title([PAPER, dict(PAPER, year="2019")])
        self.assertEqual(f.find_links(f.parse_profile_rows(ROW), idx, "U1"), {})


class TestProfileOrdering(unittest.TestCase):
    def test_users_ordered_by_most_cited_paper(self):
        papers = [{"title": "A", "authors": ["Bo Two"]}, {"title": "B", "authors": ["Al One", "Bo Two"]}]
        profiles = {"Al One": {"scholar_url": "https://scholar.google.com/citations?user=U1"},
                    "Bo Two": {"scholar_url": "https://scholar.google.com/citations?user=U2"}}
        got = f.profile_users_for(papers, profiles)
        self.assertEqual([u for u, _ in got], ["U2", "U1"])
        self.assertEqual(len(got[0][1]), 2)


if __name__ == "__main__":
    unittest.main()
