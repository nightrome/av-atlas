# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import recheck_affiliations_arxiv as r


class ReconcileTests(unittest.TestCase):
    def test_wrong_affiliation_is_dropped_and_a_missing_one_copied_from_a_coauthor(self):
        stored = [{"name": "Chun-Peng Chang", "affiliations": ["DFKI Augmented Vision", "TU Delft"]},
                  {"name": "Chen-Yu Wang", "affiliations": []},
                  {"name": "Holger Caesar", "affiliations": ["DFKI Augmented Vision", "TU Delft"]}]
        page = {"Chun-Peng Chang": "Affiliation: DFKI Augmented Vision Affiliation: TU Delft",
                "Chen-Yu Wang": "Affiliation: DFKI Augmented Vision",
                "Holger Caesar": "Affiliation: TU Delft"}
        got, removed, added = r.reconcile(stored, page, {r.norm("DFKI Augmented Vision"), r.norm("TU Delft")})
        self.assertEqual(got, {"Chun-Peng Chang": ["DFKI Augmented Vision", "TU Delft"],
                               "Chen-Yu Wang": ["DFKI Augmented Vision"],
                               "Holger Caesar": ["TU Delft"]})
        self.assertEqual((removed, added), (1, 1))

    def test_a_coauthors_name_is_never_an_affiliation(self):
        stored = [{"name": "Yuxiao Chen", "affiliations": ["Sushant Veer", "NVIDIA"]},
                  {"name": "Sushant Veer", "affiliations": ["NVIDIA"]}]
        page = {"Yuxiao Chen": "NVIDIA Sushant Veer", "Sushant Veer": "NVIDIA"}
        got, _, _ = r.reconcile(stored, page)
        self.assertEqual(got["Yuxiao Chen"], ["NVIDIA"])

    def test_authors_the_page_does_not_list_are_left_alone(self):
        stored = [{"name": "A B", "affiliations": ["MIT"]}]
        self.assertEqual(r.reconcile(stored, {"C D": "Stanford"})[0], {"A B": ["MIT"]})

    def test_nothing_is_added_when_no_coauthor_value_matches(self):
        stored = [{"name": "A B", "affiliations": []}, {"name": "C D", "affiliations": ["MIT"]}]
        got, _, added = r.reconcile(stored, {"A B": "Stanford University", "C D": "MIT"})
        self.assertEqual((got["A B"], added), ([], 0))


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
