#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for fetch_bmvc.py's parsers, against trimmed copies of the real BMVC
2025 listing (bmvc2025.bmva.org/proceedings/conference-proceedings/) and of
one paper page. BMVC changes its site layout every year, so these pin down
what the 2025 layout looks like; a new year that doesn't parse should fail
here first, not write an empty or garbled venue file.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_bmvc as fb

LISTING = """<main role="main" class="container">
      <div class="row pl-2 pr-2 pt-2 pb-2 mx-auto justify-content-left">
    <table class="table table-striped table-bordered">
        <tbody>
        <tr id="paper">
            <td class="text-center"><strong> </strong><br /><span style="opacity: 0.5;"><strong>12</strong></span></td>
            <td><strong><a href="/proceedings/12/">Part Segmentation and Motion Estimation for Articulated Objects with Dynamic 3D Gaussians</a></strong><br />
            Jun-Jee Chao (University of Minnesota); Qingyuan Jiang (University of Minnesota); Volkan Isler (University of Texas at Austin)<br />
            <a class="btn btn-primary btn-sm mt-1" href="https://bmva-archive.org.uk/bmvc/2025/assets/papers/Paper_12/paper.pdf" role="button">PDF</a>&nbsp;
            </td>
        </tr>
        <tr id="paper">
            <td class="text-center"><strong> </strong><br /><span style="opacity: 0.5;"><strong>18</strong></span></td>
            <td><strong><a href="/proceedings/18/">Volumetric Temporal Texture for Smoke Stylization using Dynamic Radiance Fields</a></strong><br />
            Dongqing Wang (École Polytechnique Fédérale de Lausanne (EPFL)); Sabine Süsstrunk (École Polytechnique Fédérale de Lausanne (EPFL))<br />
            <a class="btn btn-primary btn-sm mt-1" href="https://bmva-archive.org.uk/bmvc/2025/assets/papers/Paper_18/paper.pdf" role="button">PDF</a>&nbsp;
            </td>
        </tr>
        </tbody>
    </table>
    <a href="/proceedings/workshop-proceedings/">Workshop proceedings</a>
"""

PAPER = """<!DOCTYPE html><html lang="en-US"><head><meta charset="utf-8" /><title>Part Segmentation and Motion Estimation for Articulated Objects with Dynamic 3D Gaussians</title></head><body><div class="wrapper"><section><h2 class="project-name" style="font-weight:normal; font-size: 167%;" align="center">Part Segmentation and Motion Estimation for Articulated Objects with Dynamic 3D Gaussians</h2><br><h5 style="font-weight:normal; color: black;" align="center">Jun-Jee Chao (University of Minnesota), Qingyuan Jiang (University of Minnesota), Volkan Isler (University of Texas at Austin)</h5><div class="cta"><a href="https://bmva-archive.org.uk/bmvc/2025/assets/papers/Paper_12/paper.pdf" role="button">PDF</a><br></div><h2 id="abstract">Abstract</h2>Part segmentation and motion estimation are two fundamental problems for articulated object modeling.<br><h2>Citation</h2><div class="highlighter-rouge"><pre class="highlight"><code>@inproceedings{Chao_2025_BMVC,
year      = {2025},
}
</code></pre></div></section></div></body></html>
"""


class TestSplitAuthor(unittest.TestCase):
    def test_keeps_parentheses_inside_the_affiliation(self):
        self.assertEqual(fb.split_author("Tong Zhang (École Polytechnique Fédérale de Lausanne (EPFL))"),
                         ("Tong Zhang", "École Polytechnique Fédérale de Lausanne (EPFL)"))

    def test_author_without_affiliation(self):
        self.assertEqual(fb.split_author(" Jane Doe "), ("Jane Doe", None))


class TestParseListing(unittest.TestCase):
    def test_reads_title_authors_and_affiliations(self):
        papers = fb.parse_listing(LISTING)
        self.assertEqual([p["path"] for p in papers], ["/proceedings/12/", "/proceedings/18/"])
        self.assertEqual(papers[0]["title"],
                         "Part Segmentation and Motion Estimation for Articulated Objects with Dynamic 3D Gaussians")
        self.assertEqual(papers[0]["authors"], ["Jun-Jee Chao", "Qingyuan Jiang", "Volkan Isler"])
        self.assertEqual(papers[0]["affiliations"],
                         ["University of Minnesota", "University of Minnesota", "University of Texas at Austin"])
        self.assertEqual(papers[1]["authors"], ["Dongqing Wang", "Sabine Süsstrunk"])

    def test_a_changed_layout_parses_to_nothing_rather_than_garbage(self):
        self.assertEqual(fb.parse_listing(LISTING.replace('<tr id="paper">', "<tr>")), [])


class TestParseAbstract(unittest.TestCase):
    def test_stops_before_the_citation_block(self):
        self.assertEqual(fb.parse_abstract(PAPER),
                         "Part segmentation and motion estimation are two fundamental problems for "
                         "articulated object modeling.")

    def test_missing_abstract_is_none(self):
        self.assertIsNone(fb.parse_abstract(PAPER.replace('<h2 id="abstract">Abstract</h2>', "")))


if __name__ == "__main__":
    unittest.main()
