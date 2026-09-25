#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for build_citation_graph.py's CVF PDF-URL construction and
for how its match phase merges in the Semantic Scholar edges.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# pymupdf is a crawler dependency (scripts/requirements.txt), not part of
# the stdlib-only test baseline the CI job checks for -- so on a bare CI
# checkout with no `pip install`, this module can't be imported at all (its
# top-level `import pymupdf` fires even though the tests below only
# exercise pure-string helpers that never touch a PDF). Skip cleanly there,
# same pattern as test_fetch_affiliations_arxiv.py's beautifulsoup4 guard
# (unittest turns a module-level SkipTest during discovery into a skipped
# test, not an error, so it doesn't fail the whole suite).
try:
    import build_citation_graph as bcg
except ImportError as exc:
    raise unittest.SkipTest(f"pymupdf not installed: {exc}")


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


class TestS2Edges(unittest.TestCase):
    IDS = {"ids": {"nuscenes": 100, "pointpillars": 200, "centerpoint": 300, "droppedpaper": 400}}

    def test_reference_ids_map_back_to_corpus_keys(self):
        refs = {"references": {"centerpoint": [100, 200, 999], "pointpillars": [200, 100]}}
        edges = bcg.s2_edges(refs, self.IDS)
        # 999 isn't a corpus paper, and a paper never cites itself.
        self.assertEqual(edges, {"centerpoint": {"nuscenes", "pointpillars"}, "pointpillars": {"nuscenes"}})

    def test_papers_no_longer_in_the_corpus_are_dropped(self):
        refs = {"references": {"centerpoint": [100, 400], "droppedpaper": [100]}}
        edges = bcg.s2_edges(refs, self.IDS, corpus_keys={"nuscenes", "pointpillars", "centerpoint"})
        self.assertEqual(edges, {"centerpoint": {"nuscenes"}})

    def test_missing_side_files_give_no_edges(self):
        self.assertEqual(bcg.s2_edges({}, {}), {})


class TestMatchPhaseMergesS2(unittest.TestCase):
    def test_text_and_s2_edges_are_merged_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            files = {
                "REFS_CVF_FILE": {"succeeded": ["centerpoint"], "failed": {}, "references": {
                    "centerpoint": ["[1] H. Caesar et al. nuScenes. In CVPR, 2020."]}},
                "REFS_ARXIV_FILE": {},
                "REFS_S2_FILE": {"references": {"centerpoint": [100, 200], "bevformer": [100]}},
                "S2_IDS_FILE": {"ids": {"nuscenes": 100, "pointpillars": 200, "centerpoint": 300,
                                        "bevformer": 500}},
            }
            patches = []
            for name, data in files.items():
                path = tmp / f"{name}.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                patches.append(patch.object(bcg, name, path))
            patches.append(patch.object(bcg, "GRAPH_FILE", tmp / "graph.json"))
            for p in patches:
                p.start()
            try:
                word_index = bcg.build_corpus_match_index(
                    [{"title": t} for t in ("nuScenes", "PointPillars", "CenterPoint", "BEVFormer")])
                bcg.match_phase(word_index, {"nuscenes", "pointpillars", "centerpoint", "bevformer"})
            finally:
                for p in patches:
                    p.stop()
            graph = json.loads((tmp / "graph.json").read_text(encoding="utf-8"))
        self.assertEqual(graph["edges"], {"bevformer": ["nuscenes"], "centerpoint": ["nuscenes", "pointpillars"]})
        self.assertEqual(graph["sources_scanned"], {"cvf": 1, "arxiv": 0, "s2": 2})


if __name__ == "__main__":
    unittest.main()
