#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for regions.py, the taxonomy behind the Authors page's region filter."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import regions as r

STATS = Path(__file__).resolve().parent.parent.parent / "data" / "stats.json"


class TestTaxonomy(unittest.TestCase):
    def test_every_country_has_exactly_one_region(self):
        seen = {}
        for region, countries in r._BY_REGION.items():
            self.assertIn(region, r.REGIONS)
            for c in countries:
                self.assertNotIn(c, seen, f"{c} is in {seen.get(c)} and {region}")
                seen[c] = region
        self.assertEqual(set(r._BY_REGION), set(r.REGIONS))

    def test_un_scheme_edge_cases(self):
        for country, region in [
            ("United States", "Northern America"), ("Canada", "Northern America"),
            ("Mexico", "Latin America & Caribbean"), ("Brazil", "Latin America & Caribbean"),
            ("Puerto Rico", "Latin America & Caribbean"),
            ("Russia", "Europe"), ("United Kingdom", "Europe"), ("Ukraine", "Europe"),
            ("Turkey", "Asia"), ("Israel", "Asia"), ("Cyprus", "Asia"), ("Taiwan", "Asia"),
            ("Australia", "Oceania"), ("South Africa", "Africa"),
        ]:
            self.assertEqual(r.region_of_country(country), region, country)
        self.assertIsNone(r.region_of_country("Atlantis"))

    def test_every_country_in_the_corpus_is_mapped(self):
        if not STATS.exists():
            self.skipTest("data/stats.json not built")
        stats = json.loads(STATS.read_text(encoding="utf-8"))
        names = {c for p in stats["all_papers"] for c in (p.get("countries") or [])}
        names |= set((stats.get("institution_countries") or {}).values())
        self.assertEqual(sorted(c for c in names if not r.region_of_country(c)), [])


class TestAuthorRegion(unittest.TestCase):
    inst_countries = {"TU Delft": "Netherlands", "MIT": "United States", "Tsinghua": "China"}

    def region(self, institutions, countries=()):
        return r.author_region(institutions, list(countries), self.inst_countries)

    def test_most_recent_dated_affiliation_wins(self):
        self.assertEqual(self.region([
            {"name": "MIT", "first_year": 2015, "last_year": 2019},
            {"name": "TU Delft", "first_year": 2020, "last_year": 2025},
        ]), "Europe")

    def test_undated_affiliation_only_when_nothing_else(self):
        self.assertEqual(self.region([
            {"name": "Tsinghua", "first_year": None, "last_year": None},
            {"name": "MIT", "first_year": 2018, "last_year": 2020},
        ]), "Northern America")
        self.assertEqual(self.region([{"name": "Tsinghua", "first_year": None, "last_year": None}]), "Asia")

    def test_unknown_institution_falls_back_to_a_single_country_only(self):
        self.assertEqual(self.region([{"name": "Nowhere Lab"}], ["Canada"]), "Northern America")
        self.assertIsNone(self.region([{"name": "Nowhere Lab"}], ["Canada", "China"]))
        self.assertIsNone(self.region([], []))


if __name__ == "__main__":
    unittest.main()
