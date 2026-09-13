#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for build_citation_graph.py's CVF PDF-URL construction.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_citation_graph as bcg


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


if __name__ == "__main__":
    unittest.main()
