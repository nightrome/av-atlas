#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Checks on data/scholar_paper_links.json, which is edited by hand (see
DECISIONS.md, "Paper Scholar links are added by hand"), and on the rule
behind that: no pipeline script queries Google Scholar.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_scholar_paper_links.py
"""
import ast
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
import aggregate as ag  # noqa: E402


def link_problem(url):
    """None when url is a paper's own Scholar page, otherwise what's wrong with it."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "scholar.google.com":
        return "not an https://scholar.google.com/ link"
    query = parse_qs(parts.query)
    if "q" in query:
        return "a search, which can land on a different paper"
    if parts.path == "/citations":
        if query.get("view_op") == ["view_citation"] and query.get("citation_for_view"):
            return None
        return "a /citations link that isn't a paper's citation page"
    if parts.path == "/scholar" and (query.get("cites") or query.get("cluster")):
        return None
    return "not a citation, Cited by or versions page"


def scholar_urls_in_code(source):
    """Line numbers of string literals (docstrings aside) that name a Scholar host."""
    tree = ast.parse(source)
    docstrings = {id(node.value) for node in ast.walk(tree)
                  if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)}
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in docstrings and "scholar.google" in node.value]


class TestLinkProblem(unittest.TestCase):
    def test_a_papers_own_scholar_pages_are_accepted(self):
        for url in [
            "https://scholar.google.com/citations?view_op=view_citation&hl=en"
            "&citation_for_view=SrVnrPcAAAAJ:Se3iqnhoufwC",
            "https://scholar.google.com/scholar?cites=25915332133459342&hl=en",
            "https://scholar.google.com/scholar?cluster=12319477714873931942&hl=en",
        ]:
            self.assertIsNone(link_problem(url), url)

    def test_searches_profiles_and_other_hosts_are_rejected(self):
        for url in [
            "https://scholar.google.com/scholar?hl=en&q=%22nuScenes%22",
            "https://scholar.google.com/scholar?cites=1&q=lidar",
            "https://scholar.google.com/citations?user=4PRFXzwAAAAJ",
            "https://scholar.google.com/scholar?hl=en",
            "http://scholar.google.com/scholar?cites=1",
            "https://scholar.google.de/scholar?cites=1",
            "https://www.semanticscholar.org/paper/abc",
        ]:
            self.assertIsNotNone(link_problem(url), url)


class TestLinksFile(unittest.TestCase):
    def setUp(self):
        self.assertTrue(ag.SCHOLAR_PAPER_LINKS_FILE.exists(), "the links file is tracked")
        self.links = ag.load_scholar_paper_links()

    def test_keys_are_normalized_titles(self):
        # aggregate.py looks a link up by normalize_title(paper title), so a key
        # typed in any other form is never shown.
        bad = [k for k in self.links if not k or ag.normalize_title(k) != k]
        self.assertEqual(bad, [], "use aggregate.normalize_title(title) as the key")

    def test_every_link_is_a_papers_own_scholar_page(self):
        bad = {k: (url, link_problem(url)) for k, url in self.links.items() if link_problem(url)}
        self.assertEqual(bad, {})


class TestNoScriptQueriesScholar(unittest.TestCase):
    def test_finds_a_url_in_code_but_not_in_a_docstring_or_comment(self):
        source = ('"""Reads https://scholar.google.com/ pages."""\n'
                  '# https://scholar.google.com/citations?user=U1\n'
                  'def f(user):\n'
                  '    """Not this one: scholar.google.com."""\n'
                  '    return f"https://scholar.google.com/citations?user={user}"\n')
        self.assertEqual(scholar_urls_in_code(source), [5])

    def test_no_pipeline_script_has_a_scholar_url_in_its_code(self):
        # Scholar's terms don't allow automated queries. A page link built for
        # the site would trip this too; if that is ever needed, allow that one
        # file here and say why.
        found = {}
        for path in sorted(SCRIPTS_DIR.glob("*.py")):
            lines = scholar_urls_in_code(path.read_text(encoding="utf-8"))
            if lines:
                found[path.name] = lines
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
