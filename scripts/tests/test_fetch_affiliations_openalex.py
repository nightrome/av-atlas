# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_affiliations_openalex as fa


class AffiliationTests(unittest.TestCase):
    def test_arxiv_papers_use_their_datacite_doi(self):
        self.assertEqual(fa.paper_doi({"arxiv_url": "https://arxiv.org/abs/2301.01234v2"}),
                         "10.48550/arxiv.2301.01234")

    def test_real_doi_wins_over_arxiv_id(self):
        self.assertEqual(fa.paper_doi({"doi": "https://doi.org/10.1109/IV.2020.1", "arxiv_url": "https://arxiv.org/abs/1"}),
                         "10.1109/iv.2020.1")

    def test_works_without_any_institution_give_nothing(self):
        work = {"authorships": [{"author": {"display_name": "A B"}, "institutions": []}]}
        self.assertEqual(fa.details_from_work(work), [])

    def test_existing_authors_keep_their_fields_and_gain_affiliations(self):
        existing = [{"name": "Ann Lee", "affiliations": [], "orcid": "x"}]
        found = [{"name": "ann  lee", "affiliations": ["TU Delft"], "countries": ["NL"], "openalex_id": "A1"}]
        merged, changed = fa.merge_details(existing, found)
        self.assertTrue(changed)
        self.assertEqual(merged[0]["affiliations"], ["TU Delft"])
        self.assertEqual(merged[0]["orcid"], "x")

    def test_no_existing_authors_takes_the_new_list(self):
        found = [{"name": "A", "affiliations": ["X"], "countries": []}]
        self.assertEqual(fa.merge_details(None, found), (found, True))


if __name__ == "__main__":
    unittest.main()
