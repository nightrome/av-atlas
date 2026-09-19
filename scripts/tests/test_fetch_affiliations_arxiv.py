#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for fetch_affiliations_arxiv.py's ar5iv-page parsing.
Institution extraction itself (institution_extraction_llm.extract_institutions,
which parse_ar5iv_affiliations calls) is tested separately in
test_institution_extraction_llm.py -- the tests here mock that call, never a
real network/Ollama call, so parse_ar5iv_affiliations's own logic (name
extraction, cache/registry threading) can be tested without a live model.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_fetch_affiliations_arxiv.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# beautifulsoup4 is a crawler dependency (scripts/requirements.txt), not part
# of the stdlib-only test baseline the CI job checks for -- so on a bare CI
# checkout with no `pip install`, this one module can't run. Skip it cleanly
# there (unittest turns a module-level SkipTest during discovery into a
# skipped test, not an error) rather than failing the whole suite.
try:
    from bs4 import BeautifulSoup
    import fetch_affiliations_arxiv as fa
    import institution_extraction_llm as iel
except ImportError as exc:
    raise unittest.SkipTest(f"beautifulsoup4 not installed: {exc}")


class TestParseAr5ivAffiliations(unittest.TestCase):
    def setUp(self):
        self._original_extract = iel.extract_institutions
        self.addCleanup(setattr, iel, "extract_institutions", self._original_extract)

    def test_merged_multi_author_name_is_rejected(self):
        # IEEE-style \IEEEauthorblockN templates can render several authors'
        # names as one run -- a real person's name is essentially never more
        # than 4 words, so this must be dropped, not recorded as a garbled
        # "person" with a made-up combined name. No affiliation span at all
        # here, so extract_institutions must never even be called.
        iel.extract_institutions = lambda text, registry: (_ for _ in ()).throw(
            AssertionError("must not be called -- no real author to extract for"))
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Julian Wiederer Arij Bouazizi Marco Troina</span>
        </span>
        """, "html.parser")
        self.assertEqual(fa.parse_ar5iv_affiliations(soup, set(), {}), [])

    def test_normal_single_author_with_affiliation_is_kept(self):
        iel.extract_institutions = lambda text, registry: [
            {"name": "SenseTime Research", "matched_existing": False}]
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Xizhou Zhu</span>
          <span class="ltx_role_affiliation">Affiliation: SenseTime Research</span>
        </span>
        """, "html.parser")
        result = fa.parse_ar5iv_affiliations(soup, set(), {})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Xizhou Zhu")
        self.assertEqual(result[0]["affiliations"], ["SenseTime Research"])

    def test_extraction_result_is_cached_by_raw_text(self):
        calls = []
        iel.extract_institutions = lambda text, registry: calls.append(text) or [
            {"name": "MIT", "matched_existing": False}]
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Alice</span>
          <span class="ltx_role_affiliation">Affiliation: MIT</span>
        </span>
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Bob</span>
          <span class="ltx_role_affiliation">Affiliation: MIT</span>
        </span>
        """, "html.parser")
        cache = {}
        fa.parse_ar5iv_affiliations(soup, set(), cache)
        self.assertEqual(len(calls), 1, "identical raw text across two authors must only call the LLM once")
        self.assertIn("MIT", cache)

    def test_new_institution_is_added_to_the_registry(self):
        iel.extract_institutions = lambda text, registry: [
            {"name": "Brand New University", "matched_existing": False}]
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Alice</span>
          <span class="ltx_role_affiliation">Affiliation: Brand New University</span>
        </span>
        """, "html.parser")
        registry = set()
        fa.parse_ar5iv_affiliations(soup, registry, {})
        self.assertIn("Brand New University", registry)

    def test_no_institution_found_yields_empty_affiliations(self):
        # e.g. the raw text was a person's name, an email, a footnote --
        # not a rejection, a real "this text names no institution" answer.
        iel.extract_institutions = lambda text, registry: []
        soup = BeautifulSoup("""
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Alice</span>
          <span class="ltx_role_affiliation">Affiliation: E. Eaton</span>
        </span>
        """, "html.parser")
        result = fa.parse_ar5iv_affiliations(soup, set(), {})
        self.assertEqual(result[0]["affiliations"], [])


class TestParseAr5ivReferences(unittest.TestCase):
    def test_extracts_one_entry_per_bibitem(self):
        soup = BeautifulSoup("""
        <ul class="ltx_biblist">
          <li class="ltx_bibitem">Alice Author. A Great Paper. In CVPR, 2022.</li>
          <li class="ltx_bibitem">Bob Builder. Another Paper. In ICCV, 2021.</li>
        </ul>
        """, "html.parser")
        result = fa.parse_ar5iv_references(soup)
        self.assertEqual(len(result), 2)
        self.assertIn("A Great Paper", result[0])
        self.assertIn("Another Paper", result[1])

    def test_short_junk_entries_are_dropped(self):
        soup = BeautifulSoup('<li class="ltx_bibitem">x</li>', "html.parser")
        self.assertEqual(fa.parse_ar5iv_references(soup), [])


class TestDetectCodeLink(unittest.TestCase):
    def test_github_link_in_abstract_detected(self):
        soup = BeautifulSoup(
            '<div class="ltx_abstract">Code available at '
            '<a href="https://github.com/nvlabs/example">github.com/nvlabs/example</a>.</div>',
            "html.parser")
        self.assertTrue(fa.detect_code_link(soup))

    def test_gitlab_and_bitbucket_also_detected(self):
        for host in ("https://gitlab.com/team/repo", "https://bitbucket.org/team/repo"):
            soup = BeautifulSoup(f'<p><a href="{host}">code</a></p>', "html.parser")
            self.assertTrue(fa.detect_code_link(soup), host)

    def test_no_code_link_returns_false(self):
        soup = BeautifulSoup('<p><a href="https://arxiv.org/abs/1234.5678">related work</a></p>', "html.parser")
        self.assertFalse(fa.detect_code_link(soup))

    def test_github_link_only_in_bibliography_not_counted(self):
        # A cited work's own repo link isn't a signal about THIS paper.
        soup = BeautifulSoup(
            '<li class="ltx_bibitem">Some Prior Work. Code: '
            '<a href="https://github.com/other/prior-work">link</a></li>',
            "html.parser")
        self.assertFalse(fa.detect_code_link(soup))

    def test_github_link_outside_bibliography_counted_even_with_bibliography_present(self):
        soup = BeautifulSoup(
            '<div class="ltx_abstract">Code: <a href="https://github.com/us/our-repo">link</a></div>'
            '<li class="ltx_bibitem">Prior Work. <a href="https://github.com/other/prior-work">link</a></li>',
            "html.parser")
        self.assertTrue(fa.detect_code_link(soup))

    def test_ar5iv_own_footer_github_link_not_counted(self):
        # Every ar5iv page injects its own "Report an issue" link pointing
        # at github.com/dginev/ar5iv -- site chrome, not a signal that THIS
        # paper released code. Regression test for the bug that made
        # detect_code_link() return True on nearly every paper.
        soup = BeautifulSoup(
            '<div class="ltx_abstract">No code release mentioned here.</div>'
            '<div class="ar5iv-footer">'
            '<a class="ar5iv-text-button" '
            'href="https://github.com/dginev/ar5iv/issues/new?title=Improve+article">Report an issue</a>'
            '</div>',
            "html.parser")
        self.assertFalse(fa.detect_code_link(soup))

    def test_real_repo_link_still_counted_alongside_ar5iv_footer(self):
        soup = BeautifulSoup(
            '<div class="ltx_abstract">Code: <a href="https://github.com/us/our-repo">link</a></div>'
            '<div class="ar5iv-footer">'
            '<a class="ar5iv-text-button" '
            'href="https://github.com/dginev/ar5iv/issues/new?title=Improve+article">Report an issue</a>'
            '</div>',
            "html.parser")
        self.assertTrue(fa.detect_code_link(soup))

    def test_huggingface_and_other_non_git_hosts_detected(self):
        # user-flagged: "code cannot just be at github/gitlab".
        for host in ("https://huggingface.co/us/our-model", "https://paperswithcode.com/paper/ours",
                     "https://codeberg.org/us/our-repo", "https://gitee.com/us/our-repo"):
            soup = BeautifulSoup(f'<p><a href="{host}">link</a></p>', "html.parser")
            self.assertTrue(fa.detect_code_link(soup), host)

    def test_text_statement_of_availability_detected_with_no_link_at_all(self):
        soup = BeautifulSoup(
            '<div class="ltx_abstract">Our code is available at our project page.</div>',
            "html.parser")
        self.assertTrue(fa.detect_code_link(soup))
        soup2 = BeautifulSoup(
            '<div class="ltx_abstract">We release our implementation to support future work.</div>',
            "html.parser")
        self.assertTrue(fa.detect_code_link(soup2))

    def test_bare_mention_of_code_without_availability_verb_not_detected(self):
        # Must not false-positive on ordinary methods text.
        soup = BeautifulSoup(
            '<div class="ltx_abstract">We implement our approach in code using PyTorch and evaluate on KITTI.</div>',
            "html.parser")
        self.assertFalse(fa.detect_code_link(soup))

    def test_text_statement_inside_bibliography_not_counted(self):
        soup = BeautifulSoup(
            '<li class="ltx_bibitem">Some Prior Work. Code is available at their repository.</li>',
            "html.parser")
        self.assertFalse(fa.detect_code_link(soup))

    def test_text_detection_does_not_mutate_the_caller_soup(self):
        # detect_code_link must leave `soup` intact -- parse_ar5iv_references/
        # parse_ar5iv_affiliations still read it afterward in the real
        # fetch_affiliations_arxiv.py main() loop.
        soup = BeautifulSoup(
            '<li class="ltx_bibitem">Some Prior Work.</li>'
            '<div class="ltx_abstract">Code is available at our page.</div>',
            "html.parser")
        fa.detect_code_link(soup)
        self.assertIsNotNone(soup.select_one("li.ltx_bibitem"))


if __name__ == "__main__":
    unittest.main()
