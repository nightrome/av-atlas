#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for build_citation_graph.py's CVF PDF-URL construction and
its offline match phase.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pymupdf is imported lazily inside fetch_pdf_text, so this imports fine on
# a bare CI checkout with no `pip install`.
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


class TestMatchReferences(unittest.TestCase):
    TITLES = ["PointPillars: Fast Encoders for Object Detection from Point Clouds",
              "BEVFormer",  # shorter than PREFIX_LEN once normalized
              "Deep Learning for Vision"]  # no distinctive word at all: never matched

    def match(self, entry):
        index = bcg.build_corpus_match_index([{"title": t} for t in self.TITLES])
        return bcg.match_references([entry], index)

    def test_title_anywhere_in_the_entry_matches(self):
        self.assertEqual(self.match("[3] A. Lang et al. PointPillars: fast encoders for object detection "
                                    "from point clouds. In CVPR, 2019."),
                         {"pointpillarsfastencodersforobjectdetectionfrompointclouds"})

    def test_short_title_matches_through_its_word(self):
        self.assertEqual(self.match("Z. Li. BEVFormer: learning bird's-eye-view representation. ECCV 2022."),
                         {"bevformer"})

    def test_title_with_no_distinctive_word_never_matches(self):
        self.assertEqual(self.match("Y. LeCun. Deep learning for vision. Nature, 2015."), set())

    def test_partial_title_does_not_match(self):
        self.assertEqual(self.match("PointPillars: fast encoders for object detection. arXiv."), set())


class TestMatchPhase(unittest.TestCase):
    def patch_paths(self, root):
        patches = [mock.patch.object(bcg, name, root / fname) for name, fname in (
            ("REFS_CVF_FILE", "reference_lists_cvf.json"), ("REFS_ARXIV_FILE", "reference_lists_arxiv.json"),
            ("GRAPH_FILE", "citation_graph.json"))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_no_reference_lists_keeps_the_existing_graph(self):
        # A build on a machine without the saved reference lists must not
        # replace a real graph with an empty one.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.patch_paths(root)
            (root / "citation_graph.json").write_text('{"edges": {"a": ["b"]}}')
            self.assertFalse(bcg.match_phase({}))
            self.assertEqual(json.loads((root / "citation_graph.json").read_text()), {"edges": {"a": ["b"]}})

    def test_rematches_saved_lists_against_the_corpus(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.patch_paths(root)
            (root / "reference_lists_arxiv.json").write_text(json.dumps(
                {"citer": ["[1] A. Author. PointPillars: Fast Encoders for Object Detection. CVPR 2019."]}))
            index = bcg.build_corpus_match_index(
                [{"title": "PointPillars: Fast Encoders for Object Detection"}, {"title": "citer"}])
            self.assertTrue(bcg.match_phase(index))
            graph = json.loads((root / "citation_graph.json").read_text())
            self.assertEqual(graph["edges"], {"citer": ["pointpillarsfastencodersforobjectdetection"]})


if __name__ == "__main__":
    unittest.main()
