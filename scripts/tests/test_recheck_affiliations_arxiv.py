# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import recheck_affiliations_arxiv as r


class ReconcileTests(unittest.TestCase):
    def test_a_coauthors_name_or_an_email_is_never_an_affiliation(self):
        stored = [{"name": "Yuxiao Chen", "affiliations": ["Sushant Veer", "NVIDIA", "a@b.com"]},
                  {"name": "Sushant Veer", "affiliations": ["NVIDIA"]}]
        got, removed, _ = r.reconcile(stored, {"Yuxiao Chen": "NVIDIA", "Sushant Veer": "NVIDIA"})
        self.assertEqual(got["Yuxiao Chen"], ["NVIDIA"])
        self.assertEqual(removed, 2)

    def test_an_author_with_one_is_never_given_more(self):
        stored = [{"name": "A B", "affiliations": ["Virtual Vehicle Research GmbH"]},
                  {"name": "C D", "affiliations": ["Graz University of Technology"]}]
        page = {"A B": "Virtual Vehicle Research GmbH, Graz University of Technology", "C D": "x"}
        self.assertEqual(r.reconcile(stored, page)[0]["A B"], ["Virtual Vehicle Research GmbH"])

    def test_an_author_with_none_gets_the_institution_their_block_names(self):
        stored = [{"name": "A B", "affiliations": []}, {"name": "C D", "affiliations": ["University of Leeds"]},
                  {"name": "E F", "affiliations": []}]
        page = {"A B": "Institute for Transport Studies, University of Leeds, UK", "E F": "Somewhere Else"}
        got, _, added = r.reconcile(stored, page)
        self.assertEqual((got["A B"], got["E F"], added), (["University of Leeds"], [], 1))

    def test_a_country_is_never_added(self):
        stored = [{"name": "A B", "affiliations": []}, {"name": "C D", "affiliations": ["Germany."]}]
        got, _, _ = r.reconcile(stored, {"A B": "University of Freiburg, Germany."}, countries={"germany"})
        self.assertEqual(got["A B"], [])


class RunTests(unittest.TestCase):
    def papers(self):
        return [{"title": "t", "arxiv_url": "https://arxiv.org/abs/2401.00001", "authors_detail_source": "arxiv-html",
                 "authors_detail": [{"name": "A B", "affiliations": ["MIT"]}]}]

    @unittest.skipUnless(__import__("importlib").util.find_spec("bs4"), "needs beautifulsoup4")
    def test_a_page_is_fetched_once(self):
        out = Path(tempfile.mkdtemp()) / "o.json"
        calls = []
        html = '<span class="ltx_creator ltx_role_author"><span class="ltx_personname">A B</span>' \
               '<span class="ltx_role_affiliation">MIT</span></span>'

        def fetch(aid):
            calls.append(aid)
            return html
        r.run(1, fetch_page=fetch, sleep=lambda s: None, out=out, papers=self.papers())
        r.run(1, fetch_page=fetch, sleep=lambda s: None, out=out, papers=self.papers())
        self.assertEqual(calls, ["2401.00001"])

    def test_a_failed_fetch_records_nothing(self):
        out = Path(tempfile.mkdtemp()) / "o.json"

        def boom(aid):
            raise OSError("HTTP 503")
        r.run(1, fetch_page=boom, sleep=lambda s: None, out=out, papers=self.papers())
        self.assertEqual(__import__("json").loads(out.read_text()), {})


if __name__ == "__main__":
    unittest.main()
