#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for build_citation_graph.py's CVF PDF-URL construction
and its reference matcher.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pymupdf is imported inside fetch_pdf_text() only, so this imports (and
# these tests run) on a bare CI checkout without it.
import aggregate as ag
import build_citation_graph as bcg

# The four false matches found in the September 2026 audit, each as the
# shorter corpus title, the longer title it used to be credited for, and a
# reference to the longer one.
FALSE_MATCH_PAIRS = [
    ("Decision Making for Autonomous Vehicles", 2023,
     "Planning and Decision-Making for Autonomous Vehicles", 2018,
     "W. Schwarting, J. Alonso-Mora, and D. Rus. Planning and decision-making for "
     "autonomous vehicles. Annual Review of Control, Robotics, and Autonomous Systems, 1:187-210, 2018."),
    ("Deep Reinforcement Learning for Autonomous Driving", 2018,
     "Deep Reinforcement Learning for Autonomous Driving: A Survey", 2022,
     "B. R. Kiran, I. Sobh, V. Talpaert, P. Mannion, A. A. Al Sallab, S. Yogamani, and P. Perez. "
     "Deep reinforcement learning for autonomous driving: A survey. IEEE T-ITS, 23(6):4909-4926, 2022."),
    ("Learning To Simulate", 2019,
     "TrafficSim: Learning To Simulate Realistic Multi-Agent Behaviors", 2021,
     "[41] Simon Suo, Sebastian Regalado, Sergio Casas, and Raquel Urtasun. TrafficSim: Learning to\n"
     "simulate realistic multi-agent behaviors. In CVPR, pages 10400-10409, 2021."),
    ("Objects as Points", 2019,
     "Tracking Objects as Points", 2020,
     "Xingyi Zhou, Vladlen Koltun, and Philipp Krähenbühl. Tracking objects as points. "
     "In ECCV, pages 474-490. Springer, 2020."),
]


def index_of(*titles_and_years):
    return bcg.TitleIndex([{"title": t, "year": y} for t, y in titles_and_years])


def key(title):
    return bcg.normalize_title(title)


class TestPdfUrlFromPath(unittest.TestCase):
    def test_regular_year_keeps_consistent_casing(self):
        url = bcg.pdf_url_from_path("content_iccv_2015/html/Malinowski_Ask_Your_Neurons_ICCV_2015_paper.html")
        self.assertEqual(url, "https://openaccess.thecvf.com/content_iccv_2015/papers/"
                               "Malinowski_Ask_Your_Neurons_ICCV_2015_paper.pdf")

    def test_iccv_2017_papers_directory_is_uppercased(self):
        # ICCV 2017 uniquely directories its PDFs under "content_ICCV_2017"
        # even though its own html/ listing pages live under lowercase
        # "content_iccv_2017" -- confirmed live against openaccess.thecvf.com
        # (naively lowercasing this, like every other year, 404s). See
        # pdf_url_from_path's own comment and DECISIONS.md.
        url = bcg.pdf_url_from_path(
            "content_iccv_2017/html/Campbell_Globally-Optimal_Inlier_Set_ICCV_2017_paper.html")
        self.assertEqual(url, "https://openaccess.thecvf.com/content_ICCV_2017/papers/"
                               "Campbell_Globally-Optimal_Inlier_Set_ICCV_2017_paper.pdf")

    def test_iccv_2017_html_directory_is_not_touched(self):
        # Only the papers/ substitution should be case-corrected -- a path
        # that already says "papers/" for a different reason shouldn't be
        # double-touched, and non-2017 "iccv_2017"-shaped substrings (there
        # are none in practice, but the replace is a plain string match)
        # aren't a concern here since the fixture below never contains one.
        url = bcg.pdf_url_from_path("content_iccv_2015/html/some_iccv_2017_named_paper.html")
        self.assertEqual(url, "https://openaccess.thecvf.com/content_iccv_2015/papers/"
                               "some_iccv_2017_named_paper.pdf")


class TestMatchEntry(unittest.TestCase):
    def test_shorter_title_inside_a_longer_one_is_not_matched(self):
        # Only the shorter title is in the index here, which is the harder
        # case: "Planning and Decision-Making for Autonomous Vehicles" isn't
        # a corpus paper, so nothing longer is around to win over it.
        for short, short_year, _, _, reference in FALSE_MATCH_PAIRS:
            with self.subTest(short):
                self.assertEqual(bcg.match_entry(reference, index_of((short, short_year))), set())

    def test_longer_title_is_still_matched_when_both_are_in_the_corpus(self):
        for short, short_year, long_title, long_year, reference in FALSE_MATCH_PAIRS:
            with self.subTest(long_title):
                index = index_of((short, short_year), (long_title, long_year))
                self.assertEqual(bcg.match_entry(reference, index), {key(long_title)})

    def test_exact_citation_of_the_shorter_title_still_matches(self):
        index = index_of(("Objects as Points", 2019), ("Tracking Objects as Points", 2020))
        reference = ("X. Zhou, D. Wang, and P. Krähenbühl. Objects as points. "
                     "arXiv preprint arXiv:1904.07850, 2019.")
        self.assertEqual(bcg.match_entry(reference, index), {key("Objects as Points")})

    def test_ieee_quoted_title_matches(self):
        index = index_of(("Are we ready for autonomous driving? The KITTI vision benchmark suite", 2012))
        reference = ("[5] A. Geiger, P. Lenz, and R. Urtasun, “Are we ready for autonomous driving? "
                     "the KITTI vision benchmark suite,” in CVPR, 2012.")
        self.assertEqual(len(bcg.match_entry(reference, index)), 1)

    def test_glued_pdf_text_matches(self):
        # Two-column CVF PDFs often lose their spaces and hyphenate across lines.
        index = index_of(("nuScenes: A Multimodal Dataset for Autonomous Driving", 2020))
        reference = ("ancarlo Baldan, and Oscar Beijbom. nuScenes: A Multi-\n"
                     "modalDatasetforAutonomousDriving. InIEEE/CVFCon-")
        self.assertEqual(bcg.match_entry(reference, index),
                         {key("nuScenes: A Multimodal Dataset for Autonomous Driving")})

    def test_ar5iv_title_straight_after_the_authors_matches(self):
        index = index_of(("nuScenes: A Multimodal Dataset for Autonomous Driving", 2020))
        reference = ("Caesar et al. (2020) H. Caesar, V. Bankiti, A. H. Lang, and O. Beijbom "
                     "Nuscenes: a multimodal dataset for autonomous driving. In CVPR, 2020.")
        self.assertEqual(len(bcg.match_entry(reference, index)), 1)

    def test_title_inside_a_word_run_does_not_match(self):
        index = index_of(("Multi Lane Detection", 2022))
        reference = "A. Author. Robust multi-lane detection and tracking. In IV, 2016."
        self.assertEqual(bcg.match_entry(reference, index), set())

    def test_short_title_needs_a_whole_segment_and_its_year(self):
        index = index_of(("Welcome", 2022))
        self.assertEqual(bcg.match_entry("J. Doe. Welcome to the jungle, 2022.", index), set())
        self.assertEqual(bcg.match_entry("J. Doe. Welcome. In IROS, 2015.", index), set())
        self.assertEqual(bcg.match_entry("IROS chairs. Welcome. In IROS, 2022.", index), {"welcome"})

    def test_short_title_next_to_a_year_matches(self):
        index = index_of(("Segment Anything", 2023))
        for reference in ("A. Kirillov, and R. Girshick. Segment anything, 2023.",
                          "A. Kirillov, and R. B. Girshick (2023) Segment anything. In ICCV.",
                          "N. Ravi, and C. Feichtenhofer. SAM 2: Segment anything in images and videos, 2024."):
            with self.subTest(reference):
                expected = set() if "SAM 2" in reference else {key("Segment Anything")}
                self.assertEqual(bcg.match_entry(reference, index), expected)

    def test_title_after_a_line_of_the_other_column_matches(self):
        # Two-column PDF text interleaves the columns line by line, so the
        # title often follows a line break or a hyphenated line end of the
        # other column instead of the authors' period.
        index = index_of(("Deep Residual Learning for Image Recognition", 2016),
                         ("Self-Supervised Monocular Depth Hints", 2019))
        self.assertEqual(bcg.match_entry(
            "[16] KaimingHe,XiangyuZhang,ShaoqingRen,andJianSun. basedsemanticsegmentationfor au-\n"
            "Deepresiduallearningforimagerecognition. InProceed-", index),
            {key("Deep Residual Learning for Image Recognition")})
        self.assertEqual(bcg.match_entry(
            "[43] P.Luc,C.Couprie,Y.LeCun,andJ.Verbeek. Predictingfu- "
            "Self-supervisedmonoculardepthhints. InICCV,2019.", index),
            {key("Self-Supervised Monocular Depth Hints")})


class TestYearGuard(unittest.TestCase):
    def test_citer_much_older_than_the_cited_paper_is_dropped(self):
        index = index_of(("Old Paper About Driving", 2017), ("New Paper About Driving", 2023),
                         ("Preprint Era Paper Here", 2021))
        edges = {key("Old Paper About Driving"): {key("New Paper About Driving")},
                 key("Preprint Era Paper Here"): {key("New Paper About Driving")}}
        kept, dropped = bcg.apply_year_guard(edges, index)
        self.assertEqual(dropped, 1)
        # 2021 citing 2023 is within the two years of slack a journal
        # version of an earlier preprint needs.
        self.assertEqual(kept, {key("Preprint Era Paper Here"): {key("New Paper About Driving")}})

    def test_unknown_year_keeps_the_edge(self):
        index = bcg.TitleIndex([{"title": "No Year Paper Title"}, {"title": "Dated Paper Title Here", "year": 2024}])
        edges = {key("No Year Paper Title"): {key("Dated Paper Title Here")}}
        self.assertEqual(bcg.apply_year_guard(edges, index), (edges, 0))


class TestEdgeSources(unittest.TestCase):
    def test_reference_list_edges_leave_out_self_matches(self):
        index = index_of(("Objects as Points", 2019), ("Tracking Objects as Points", 2020))
        refs = {key("Tracking Objects as Points"): [
            "X. Zhou. Objects as points. arXiv, 2019.", "X. Zhou. Tracking objects as points. ECCV, 2020."]}
        self.assertEqual(bcg.reference_list_edges(refs, index),
                         {key("Tracking Objects as Points"): {key("Objects as Points")}})

    def test_sources_are_unioned(self):
        merged = bcg.merge_edge_sources([{"a": {"x"}}, {"a": ["y"], "b": {"x"}}])
        self.assertEqual(merged, {"a": {"x", "y"}, "b": {"x"}})

    def test_graph_keys_still_rekey_in_aggregate(self):
        # aggregate.py joins the graph on its own title key, which also drops
        # a final "s"; the matcher's output must stay in the plain key space
        # rekey_citation_graph() expects.
        index = index_of(("Fusion Against Missing Sensor Modalities", 2024), ("Citing Paper For Tests", 2025))
        refs = {key("Citing Paper For Tests"): ["A. B. Fusion against missing sensor modalities. In IV, 2024."]}
        graph = {"edges": {k: sorted(v) for k, v in bcg.reference_list_edges(refs, index).items()}}
        rekeyed = ag.rekey_citation_graph(graph)
        self.assertEqual(rekeyed["edges"], {"citingpaperfortest": ["fusionagainstmissingsensormodalitie"]})
        self.assertEqual(rekeyed["edges"]["citingpaperfortest"][0],
                         ag.normalize_title("Fusion Against Missing Sensor Modalities"))


if __name__ == "__main__":
    unittest.main()
