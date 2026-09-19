#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for fetch_github_paper_lists.py's markdown parsers.

Each fixture below is a trimmed, real-shaped excerpt of an actual repo's
format (not synthetic) -- three genuinely different table-of-contents /
author-list conventions were found across just 13 real repos while building
this fetcher, each one silently producing corrupted data (category names
read as paper titles, institution names read as author names) before being
caught by hand-inspecting real output. These tests exist so a future change
to the parser can't reintroduce any of those specific failure modes.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_github_paper_lists as fg


class TestParseBullets(unittest.TestCase):
    def test_skips_a_named_toc_heading_topics_or_categories(self):
        text = (
            "# ICRA 2021 Paper List\n\n"
            "# Topics\n"
            "- Active Perception in Robotics\n"
            "- Aerial Robotics\n\n"
            "## Active Perception in Robotics\n"
            "- Active Bayesian Multi-Class Mapping from Range and Semantic Segmentation\n"
            "- Attention-Based Probabilistic Planning with Active Perception\n"
        )
        papers = fg.parse_bullets(text)
        titles = [p["title"] for p in papers]
        self.assertNotIn("Active Perception in Robotics", titles, "a TOC entry must not be read as a paper")
        self.assertIn("Active Bayesian Multi-Class Mapping from Range and Semantic Segmentation", titles)
        self.assertEqual(len(titles), 2)

    def test_skips_toc_bullets_with_no_dedicated_toc_heading(self):
        # Real case: a repo puts its TOC bullet list directly after the
        # doc's own title heading, with no "# Topics" sub-heading at all --
        # confirmed on real data (dectrfov/IROS2021PaperList) that this
        # silently included "Aerial Systems: Mechanics and Control" (a
        # category name, later repeated as a real ## heading) as if it
        # were a paper.
        text = (
            "# IROS2021PaperList\n\n"
            "Some intro text about the review process.\n\n"
            "- Actuation and Joint Mechanisms\n"
            "- Aerial Systems: Mechanics and Control\n\n"
            "## Aerial Systems: Mechanics and Control\n"
            "- Autonomous Cooperative Multi-Vehicle System for Real-Time Sensing\n"
        )
        papers = fg.parse_bullets(text)
        titles = [p["title"] for p in papers]
        self.assertNotIn("Aerial Systems: Mechanics and Control", titles)
        self.assertEqual(titles, ["Autonomous Cooperative Multi-Vehicle System for Real-Time Sensing"])

    def test_real_category_headings_at_h1_level_are_not_mistaken_for_toc(self):
        # A different repo in the same family uses h1 (not h2) for its real
        # category headings, with no separate TOC section at all -- must
        # not be swept up by a heading-LEVEL-based gate.
        text = (
            "# IROS2022-paper-list\n\n"
            "# Award Session I\n"
            "- SpeedFolding: Learning Efficient Bimanual Folding of Garments\n"
            "- FAR Planner: Fast, Attemptable Route Planner\n"
        )
        papers = fg.parse_bullets(text)
        titles = [p["title"] for p in papers]
        self.assertEqual(titles, [
            "SpeedFolding: Learning Efficient Bimanual Folding of Garments",
            "FAR Planner: Fast, Attemptable Route Planner",
        ])

    def test_bullets_have_no_authors_field(self):
        text = "# T\n\n## C\n- Some Paper Title\n"
        papers = fg.parse_bullets(text)
        self.assertEqual(papers[0]["authors"], "")


class TestParseTable(unittest.TestCase):
    def test_finds_the_real_title_authors_table_among_unrelated_tables(self):
        # Real case: ryanbgriffiths/IROS2023PaperList has "Keywords/Count"
        # and "Organisations/Count" tables BEFORE the real Title/Authors
        # one -- neither must be mistaken for it.
        text = (
            "| Keywords | Count |\n|---|---|\n| Control | 364 |\n\n"
            "| Organisations | Count |\n|---|---|\n| MIT | 90 |\n\n"
            "|Title|Authors|\n|-|-|\n"
            "|Robust Fusion for Bayesian Semantic Mapping|Morilla-Cabello, David, Univ|"
        )
        papers = fg.parse_table(text, "table_br_lastfirst")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "Robust Fusion for Bayesian Semantic Mapping")

    def test_header_matched_by_substring_not_exact_equality(self):
        # Real case: PaoPaoRobot/ICRA2019-paper-list's header cell is
        # literally "paper title", not the bare "title" an exact-equality
        # check against the cell list would require.
        text = "|index|paper title|\n|:---:|:---|\n|0007|High-Fidelity Grasping in Virtual Reality|"
        papers = fg.parse_table(text, "table_comma")
        self.assertEqual(papers[0]["title"], "High-Fidelity Grasping in Virtual Reality")
        self.assertEqual(papers[0]["authors"], "", "no author column exists in this table at all")

    def test_table_comma_splits_plain_full_names(self):
        text = "|Title|Authors|Session|\n|-|-|-|\n|A Paper|Yuchen Wu, David Yoon, Keenan Burnett|SLAM 1|"
        papers = fg.parse_table(text, "table_comma")
        self.assertEqual(papers[0]["authors"], "Yuchen Wu, David Yoon, Keenan Burnett")

    def test_table_semicolon_reformats_last_first_pairs(self):
        text = "|Title|Authors|\n|-|-|\n|A Paper|Compton, William;Csomay-Shanklin, Noel|"
        papers = fg.parse_table(text, "table_semicolon")
        self.assertEqual(papers[0]["authors"], "William Compton, Noel Csomay-Shanklin")

    def test_table_br_lastfirst_keeps_only_name_discards_institutions(self):
        # Real case: each "<br>"-separated author segment is
        # "Last, First, Institution1, Institution2..." with a VARYING
        # institution count -- only the first two comma-separated pieces
        # are trustworthy as the name.
        text = (
            "|Title|Authors|\n|-|-|\n"
            "|SCENE: Reasoning about Traffic Scenes|"
            "Schmidt, Julian, Mercedes-Benz AG, Ulm University<br>"
            "Monninger, Thomas, Mercedes-Benz AG|"
        )
        papers = fg.parse_table(text, "table_br_lastfirst")
        self.assertEqual(papers[0]["authors"], "Julian Schmidt, Thomas Monninger")

    def test_a_row_before_the_header_is_seen_is_ignored(self):
        text = "|not a header|still not one|\n|Title|Authors|\n|-|-|\n|Real Paper|Real Author|"
        papers = fg.parse_table(text, "table_comma")
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["title"], "Real Paper")


if __name__ == "__main__":
    unittest.main()
