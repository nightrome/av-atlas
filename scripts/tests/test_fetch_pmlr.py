#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_pmlr as fp

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestParseVolume(unittest.TestCase):
    def setUp(self):
        self.page = (FIXTURES / "pmlr_v270_excerpt.html").read_text(encoding="utf-8")

    def test_hyphenated_slug_keeps_its_own_url(self):
        # The old CoRL regex only allowed [a-z0-9]+ slugs, so this paper got
        # the next paper's URL (and so its abstract), and the next paper was
        # dropped.
        papers = fp.parse_volume(self.page)
        self.assertEqual(len(papers), 3)
        by_title = {p["title"]: p["url"] for p in papers}
        self.assertEqual(
            by_title["Physically Embodied Gaussian Splatting: A Visually Learnt and Physically "
                     "Grounded 3D Representation for Robotics"],
            "https://proceedings.mlr.press/v270/abou-chakra25a.html")
        self.assertEqual(
            by_title["Guided Reinforcement Learning for Robust Multi-Contact Loco-Manipulation"],
            "https://proceedings.mlr.press/v270/sleiman25a.html")

    def test_authors_are_plain_text(self):
        papers = fp.parse_volume(self.page)
        self.assertEqual(papers[1]["authors"],
                         "Jad Abou-Chakra, Krishan Rana, Feras Dayoub, Niko Suenderhauf")

    def test_unparseable_block_raises_instead_of_dropping_it(self):
        broken = self.page.replace('">abs</a>', '">summary</a>', 1)
        with self.assertRaises(ValueError):
            fp.parse_volume(broken)

    def test_html_entities_in_title_are_decoded(self):
        page = ('<div class="paper"><p class="title">Sim &amp; Real</p>'
                '<span class="authors">A&nbsp;B</span>'
                '<a href="https://proceedings.mlr.press/v1/ab25a.html">abs</a></div>')
        self.assertEqual(fp.parse_volume(page)[0]["title"], "Sim & Real")


class TestParseAbstract(unittest.TestCase):
    def test_abstract_is_extracted_and_whitespace_collapsed(self):
        page = ('<h4>Abstract</h4>\n  <div id="abstract" class="abstract">\n    Line one\n'
                '    line <em>two</em>.\n  </div>\n<h4>Cite this Paper</h4>')
        self.assertEqual(fp.parse_abstract(page), "Line one line two.")

    def test_missing_abstract_is_none(self):
        self.assertIsNone(fp.parse_abstract("<html></html>"))


class TestFetchYear(unittest.TestCase):
    def test_writes_title_authors_abstract_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            listing = [{"title": "T", "authors": "A", "url": "u"}]
            with mock.patch.object(fp, "OUT_DIR", Path(tmp)), \
                    mock.patch.object(fp, "list_papers", return_value=listing), \
                    mock.patch.object(fp, "fetch_abstract", return_value="Abs"), \
                    mock.patch.object(fp, "REQUEST_DELAY", 0):
                self.assertEqual(fp.fetch_year("CoRL", 2024, 270), 1)
            rows = json.loads((Path(tmp) / "corl2024.json").read_text(encoding="utf-8"))
            self.assertEqual(rows, [{"title": "T", "authors": "A", "abstract": "Abs"}])
            self.assertFalse((Path(tmp) / "corl2024.json.partial").exists())

    def test_existing_file_is_skipped_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "icml2025.json").write_text("[]", encoding="utf-8")
            with mock.patch.object(fp, "OUT_DIR", Path(tmp)), \
                    mock.patch.object(fp, "list_papers") as lp:
                self.assertIsNone(fp.fetch_year("ICML", 2025, 267))
            lp.assert_not_called()

    def test_failed_listing_leaves_existing_file_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "corl2024.json"
            f.write_text('[{"title": "kept"}]', encoding="utf-8")
            with mock.patch.object(fp, "OUT_DIR", Path(tmp)), \
                    mock.patch.object(fp, "list_papers", side_effect=ValueError("layout changed")):
                self.assertIsNone(fp.fetch_year("CoRL", 2024, 270, force=True))
            self.assertEqual(f.read_text(encoding="utf-8"), '[{"title": "kept"}]')


class TestVolumes(unittest.TestCase):
    def test_corl_history_uses_the_shared_table(self):
        import fetch_corl_history
        self.assertIs(fetch_corl_history.CORL_VOLUMES, fp.PMLR_VOLUMES["CoRL"])
        self.assertEqual(fp.PMLR_VOLUMES["ICML"][2025], 267)


if __name__ == "__main__":
    unittest.main()
