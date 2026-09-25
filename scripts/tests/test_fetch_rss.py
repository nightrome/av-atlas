#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for fetch_rss.py's roboticsproceedings.org parsers. The fixtures are
trimmed copies of the real rss22 (RSS 2026) index and paper pages.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_rss as fr

INDEX = """<html>
<head><title>Robotics: Science and Systems XXII - Online Proceedings</title></head>
<body>
<div class="menu">
<ul>
<li class="here"><a class="menu" href="../index.html">Home</a></li>
<li class="label">RSS XXII</span></li>
<li class="menu"><a class="menu" href="../rss22/index.html">Content</a></li>
<li class="menu"><a class="menu" href="../rss22/authors.html">Authors</a></li>
<li class="label">RSS XXI</span></li>
<li class="menu"><a class="menu" href="../rss21/index.html">Content</a></li>
</ul>
</div>
<div class="content">
<h1>Robotics: Science and Systems XXII</h1>
<h2>Table of Contents</h2>
<table>
<tr><td width=500>
<a href="p001.html">One-Shot Real-World Demonstration Synthesis for Scalable Bimanual Manipulation</a><br>
<i>Huayi Zhou, Kui Jia</i><br>
</td><td valign=top>
<a href="p001.pdf" target="_blank" onclick="_gaq.push(['_trackEvent','rss10/p001.pdf','PDF',this.href]);"><img src="../icon-pdf.png" border=0></a>
</td></tr>
<tr><td>&nbsp</td></tr>
<tr><td width=500>
<a href="p115.html">QuickLAP: Quick Language–Action Preference Learning for Autonomous Driving Agents</a><br>
<i>Jordan Abi Nader, David Lee, Nathaniel S. Dennler, Andreea Bobu</i><br>
</td><td valign=top>
<a href="p115.pdf" target="_blank"><img src="../icon-pdf.png" border=0></a>
</td></tr>
</table>
</div>
</body>
</html>
"""

PAPER = """<html>
<meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
<head>
<meta name="citation_title" content="One-Shot Real-World Demonstration Synthesis for Scalable Bimanual Manipulation" />
<meta name="citation_author" content="Huayi Zhou" />
<meta name="citation_author" content="Kui Jia" />
<meta name="citation_publication_date" content="2026/07/13" />
<meta name="citation_conference_title" content="Robotics: Science and Systems XXII" />
<meta name="citation_volume" content="22" />
<title>Robotics: Science and Systems XXII - Online Proceedings</title>
</head>
<body>
<div class="content">
<h1>Robotics: Science and Systems XXII</h1>
<h3>One-Shot Real-World Demonstration Synthesis for Scalable Bimanual Manipulation</h3>
<i>Huayi Zhou, Kui Jia</i><br>
<p>
<b>Abstract:</b>
</p><p style="text-align: justify;">
Learning dexterous bimanual manipulation policies critically depends on
large-scale, high-quality demonstrations &amp; more.
</p>
<p>
<b>Download:</b> <a href="p001.pdf" target="_blank"><img src="../icon-pdf.png" border=0></a>
</p>
<p>
<b>Bibtex:</b>
<pre>
@INPROCEEDINGS{ZhouH-RSS-26,
    AUTHOR    = {Huayi Zhou AND Kui Jia},
    TITLE     = {{One-Shot Real-World Demonstration Synthesis for Scalable Bimanual Manipulation}},
    BOOKTITLE = {Proceedings of Robotics: Science and Systems},
    YEAR      = {2026},
    ADDRESS   = {Sydney, Australia},
    MONTH     = {July},
    DOI       = {10.15607/RSS.2026.XXII.001}
}
</pre>
</p>
</div>
</body>
</html>
"""


class TestVolume(unittest.TestCase):
    def test_volume_is_year_minus_2004(self):
        self.assertEqual(fr.volume_for_year(2025), "rss21")
        self.assertEqual(fr.volume_for_year(2026), "rss22")
        self.assertEqual(fr.volume_for_year(2012), "rss08")


class TestParseIndex(unittest.TestCase):
    def test_reads_only_paper_links_not_the_volume_menu(self):
        papers = fr.parse_index(INDEX)
        self.assertEqual([p["page"] for p in papers], ["p001.html", "p115.html"])
        self.assertEqual(papers[1]["title"],
                         "QuickLAP: Quick Language–Action Preference Learning for Autonomous Driving Agents")


class TestParsePaper(unittest.TestCase):
    def test_reads_every_field(self):
        p = fr.parse_paper(PAPER)
        self.assertEqual(p["title"], "One-Shot Real-World Demonstration Synthesis for Scalable Bimanual Manipulation")
        self.assertEqual(p["authors"], "Huayi Zhou, Kui Jia")
        self.assertEqual(p["abstract"], "Learning dexterous bimanual manipulation policies critically depends on "
                                        "large-scale, high-quality demonstrations & more.")
        self.assertEqual(p["doi"], "https://doi.org/10.15607/RSS.2026.XXII.001")
        self.assertEqual(p["year"], 2026)

    def test_missing_abstract_is_none_not_an_error(self):
        page = PAPER.replace("<b>Abstract:</b>", "<b>Summary:</b>")
        self.assertIsNone(fr.parse_paper(page)["abstract"])

    def test_year_falls_back_to_the_bibtex_entry(self):
        page = PAPER.replace('<meta name="citation_publication_date" content="2026/07/13" />', "")
        self.assertEqual(fr.parse_paper(page)["year"], 2026)


if __name__ == "__main__":
    unittest.main()
