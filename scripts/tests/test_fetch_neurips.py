#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for fetch_neurips.py's listing lookup. From 2025 on,
proceedings.neurips.cc/paper_files/paper/<year> shows one small book (the
Creative AI track) and links to the main conference as its own volume page,
so reading the year page directly found 0 main-track papers and the year was
skipped without an error. The fixtures are trimmed copies of the real 2025
and 2024 year pages.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_neurips as fn

YEAR_PAGE_2025 = """<div class="container-fluid">
<ul class="paper-list">
<a href="/paper_files/paper/2025/vol38-main-conference">Advances in Neural Information Processing Systems 38 Main Conference </a>
    <li class="creative_ai_track" data-track="creative_ai_track">
        <div class="paper-content">
            <a title="paper title" href="/paper_files/paper/2025/hash/04440b43c10100305d21cdeb60262e98-Abstract-Creative_AI_Track.html">Gaze to the Stars: AI and Public Art from Personal Affect and Collective Empathy</a>
        </div>
    </li>
</ul>
</div>
"""

MAIN_VOLUME_2025 = """<ul class="paper-list">
    <li class="conference">
        <div class="paper-content">
            <a title="paper title" href="/paper_files/paper/2025/hash/0010031a1b4910aa67edbda26a705518-Abstract-Conference.html">NeuralPLexer3: Accurate Biomolecular Complex Structure Prediction with Flow Models</a>
        </div>
    </li>
    <li class="datasets_and_benchmarks_track">
        <div class="paper-content">
            <a title="paper title" href="/paper_files/paper/2025/hash/0013efa1327c079e73154d4061c3a396-Abstract-Datasets_and_Benchmarks_Track.html">EngiBench: A Framework for Data-Driven Engineering Design Research</a>
        </div>
    </li>
</ul>
"""

YEAR_PAGE_2019 = """<ul class="paper-list">
    <li class="" data-track="none">
        <div class="paper-content">
            <a title="paper title" href="/paper_files/paper/2019/hash/00989c20ff1386dc386d8124ebcba1a5-Abstract.html">Compositional Plan Vectors</a>
            <span class="paper-authors">Coline Devin, Daniel Geng, Pieter Abbeel, Trevor Darrell, Sergey Levine</span>
        </div>
    </li>
</ul>
"""


class TestMainTrackUrl(unittest.TestCase):
    def test_finds_the_main_conference_volume(self):
        self.assertEqual(fn.main_track_url(2025, YEAR_PAGE_2025),
                         "https://proceedings.neurips.cc/paper_files/paper/2025/vol38-main-conference")

    def test_older_year_pages_have_no_volume_link(self):
        self.assertIsNone(fn.main_track_url(2019, YEAR_PAGE_2019))

    def test_ignores_a_volume_link_for_another_year(self):
        self.assertIsNone(fn.main_track_url(2026, YEAR_PAGE_2025))


class TestListPapers(unittest.TestCase):
    def test_follows_the_volume_link_and_keeps_main_track_only(self):
        pages = {
            "https://proceedings.neurips.cc/paper_files/paper/2025": YEAR_PAGE_2025,
            "https://proceedings.neurips.cc/paper_files/paper/2025/vol38-main-conference": MAIN_VOLUME_2025,
        }
        with mock.patch.object(fn, "fetch", side_effect=lambda url: pages[url]):
            papers = fn.list_papers(2025)
        self.assertEqual([p["title"] for p in papers],
                         ["NeuralPLexer3: Accurate Biomolecular Complex Structure Prediction with Flow Models"])

    def test_old_layout_still_reads_the_year_page(self):
        with mock.patch.object(fn, "fetch", return_value=YEAR_PAGE_2019) as fetch:
            papers = fn.list_papers(2019)
        self.assertEqual([p["title"] for p in papers], ["Compositional Plan Vectors"])
        fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()
