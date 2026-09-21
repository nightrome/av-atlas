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


RESULT_HTML = (
    '<div id="gs_res_ccl_mid">'
    '<div class="gs_r gs_or gs_scl" data-cid="CID1" data-did="x" data-rp="0"><div class="gs_ri">'
    '<h3 class="gs_rt"><span class="gs_ctc"><span class="gs_ct1">[PDF]</span></span>'
    '<a href="https://example.org/1">nuScenes: A Multimodal <b>Dataset</b> for Autonomous Driving</a></h3>'
    '<div class="gs_a">H Caesar, V Bankiti, AH Lang - Proceedings of CVPR, 2020 - openaccess.thecvf.com</div></div></div>'
    '<div class="gs_r gs_or gs_scl" data-cid="CID2" data-rp="1"><div class="gs_ri">'
    '<h3 class="gs_rt"><a href="https://example.org/2">A survey citing nuScenes</a></h3>'
    '<div class="gs_a">A Person - Journal, 2023 - pub</div></div></div>'
    '</div>')


class TestSearch(unittest.TestCase):
    def test_parses_results(self):
        rs = f.parse_search_results(RESULT_HTML)
        self.assertEqual([r["cid"] for r in rs], ["CID1", "CID2"])
        self.assertEqual(rs[0]["title"], "nuScenes: A Multimodal Dataset for Autonomous Driving")
        self.assertEqual(rs[0]["year"], 2020)

    def test_exact_title_gives_a_cluster_url(self):
        url = f.match_search_result(f.parse_search_results(RESULT_HTML), PAPER)
        self.assertEqual(url, "https://scholar.google.com/scholar?cluster=CID1&hl=en")

    def test_a_similar_but_not_identical_title_is_not_a_match(self):
        paper = dict(PAPER, title="nuScenes: A Multimodal Dataset for Autonomous Driving (extended)")
        self.assertIsNone(f.match_search_result(f.parse_search_results(RESULT_HTML), paper))

    def test_same_title_from_a_far_off_year_is_rejected(self):
        paper = dict(PAPER, year="2012")
        self.assertIsNone(f.match_search_result(f.parse_search_results(RESULT_HTML), paper))

    def test_citation_record_with_a_short_tail_counts(self):
        page = ('<div class="gs_r gs_or gs_scl" data-cid="CIT1"><h3 class="gs_rt">'
                '<span class="gs_ctu"><span class="gs_ct1">[CITATION]</span><span class="gs_ct2">[C]</span></span> '
                '<span id="x">nuScenes: A multimodal dataset for autonomous driving, CoRR abs/1903.11027</span></h3>'
                '<div class="gs_a">H Caesar - 2019</div></div>')
        rs = f.parse_search_results(page)
        self.assertTrue(rs[0]["citation_only"])
        self.assertEqual(f.match_search_result(rs, PAPER), "https://scholar.google.com/scholar?cluster=CIT1&hl=en")

    def test_a_long_tail_or_a_non_citation_record_does_not_count(self):
        long_tail = [{"title": PAPER["title"] + " " + "and more words " * 6, "cid": "C", "year": None,
                      "citation_only": True}]
        self.assertIsNone(f.match_search_result(long_tail, PAPER))
        real_result = [{"title": PAPER["title"] + " extended", "cid": "C", "year": None, "citation_only": False}]
        self.assertIsNone(f.match_search_result(real_result, PAPER))

    def test_no_results(self):
        self.assertEqual(f.parse_search_results("<html>did not match any articles</html>"), [])


class TestRunSearch(unittest.TestCase):
    def _state(self):
        return {"links": {}, "profiles_done": {}, "searched": {}}

    def setUp(self):
        # Never touch the real output file.
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = f.LINKS_FILE
        f.LINKS_FILE = Path(self._tmp.name) / "links.json"
        self.addCleanup(lambda: setattr(f, "LINKS_FILE", self._orig))

    def _papers(self):
        return [PAPER, {"title": "Some Other Paper", "year": "2021", "authors": ["A B"]}]

    def test_links_matches_and_remembers_misses(self):
        state = self._state()
        papers = self._papers()
        f.run_search(papers, f.index_by_title(papers), state,
                     sleep=lambda s: None, fetch=lambda title: RESULT_HTML)
        self.assertIn(f.normalize_title(PAPER["title"]), state["links"])
        self.assertIn(f.normalize_title("Some Other Paper"), state["searched"])

    def test_already_searched_or_linked_papers_are_skipped(self):
        state = self._state()
        state["links"][f.normalize_title(PAPER["title"])] = "u"
        state["searched"][f.normalize_title("Some Other Paper")] = "2026-01-01"
        papers = self._papers()
        calls = []
        f.run_search(papers, f.index_by_title(papers), state, sleep=lambda s: None,
                     fetch=lambda t: calls.append(t) or RESULT_HTML)
        self.assertEqual(calls, [])

    def test_a_block_stops_the_run_without_recording_a_miss(self):
        state = self._state()
        papers = self._papers()

        def blocked(title):
            raise f.Blocked("HTTP 429")
        f.run_search(papers, f.index_by_title(papers), state, sleep=lambda s: None, fetch=blocked)
        self.assertEqual(state["searched"], {})
        self.assertEqual(state["links"], {})

    def test_with_wait_minutes_a_block_is_waited_out_and_the_paper_retried(self):
        state = self._state()
        papers = [PAPER]
        attempts = []
        sleeps = []

        def flaky(title):
            attempts.append(title)
            if len(attempts) == 1:
                raise f.Blocked("HTTP 429")
            return RESULT_HTML
        f.run_search(papers, f.index_by_title(papers), state, wait_minutes=45,
                     sleep=sleeps.append, fetch=flaky)
        self.assertEqual(len(attempts), 2)
        self.assertIn(45 * 60, sleeps)
        self.assertIn(f.normalize_title(PAPER["title"]), state["links"])


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
