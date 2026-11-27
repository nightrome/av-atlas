#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the institution audit: spelling variants folded together, units
rolled into their parent, sectors for research institutes and companies, and
countries for institutions that had none."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aggregate as ag

COUNTRIES_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "institution_countries.json"


class TestAuditAliases(unittest.TestCase):
    def test_spelling_variants_fold_into_one_name(self):
        cases = {
            "Technische Universität Berlin": "TU Berlin",
            "Berlin Institute of Technology": "TU Berlin",
            "University of Wisconsin -- Madison": "University of Wisconsin - Madison",
            "University of the Bundeswehr Munich": "Universität der Bundeswehr München",
            "UT Austin": "University of Texas at Austin",
            "Purdue University West Lafayette": "Purdue University",
            "University of Maryland": "University of Maryland, College Park",
            "University of Minnesota System": "University of Minnesota",
            "Massachusetts Institute of Techonology": "Massachusetts Institute of Technology",
            "Computer Vision Center (CVC)": "Computer Vision Center",
            "Ruhr-Universität Bochum": "Ruhr University Bochum",
            "PSL University": "Université Paris Sciences et Lettres",
            "MINES ParisTech": "École Nationale Supérieure des Mines de Paris",
            "École des Ponts ParisTech": "École nationale des ponts et chaussées",
            "IP Paris": "Institut Polytechnique de Paris",
        }
        for raw, want in cases.items():
            self.assertEqual(ag.normalize_institution(raw), want, raw)

    def test_inria_spellings_share_one_name(self):
        inria = ag.normalize_institution("INRIA")
        self.assertEqual(ag.normalize_institution("Inria"), inria)
        self.assertEqual(ag.normalize_institution(
            "Institut national de recherche en sciences et technologies du numérique"), inria)
        self.assertEqual(ag.normalize_institution("Inria Sophia Antipolis Méditerranée"), inria)

    def test_daimler_is_mercedes_benz_but_daimler_truck_is_not(self):
        self.assertEqual(ag.normalize_institution("Daimler"), "Mercedes-Benz")
        self.assertEqual(ag.normalize_institution("Daimler AG"), "Mercedes-Benz")
        self.assertEqual(ag.normalize_institution("Daimler Truck AG"), "Daimler Truck")

    def test_units_roll_up_into_their_parent(self):
        cases = {
            "CASIA": "Chinese Academy of Sciences",
            "Chinese Academy of Sciences (CASIA)": "Chinese Academy of Sciences",
            "National Laboratory of Pattern Recognition (NLPR)": "Chinese Academy of Sciences",
            "S-Lab": "Nanyang Technological University",
            "OpenGVLab": "Shanghai AI Laboratory",
            "DAMO Academy": "Alibaba",
            "ESAT-PSI": "KU Leuven",
            "GIPSA-Lab": "Université Grenoble Alpes",
            "Beijing National Research Center for Information Science and Technology": "Tsinghua University",
            "City University of Hong Kong, Shenzhen Research Institute": "City University of Hong Kong",
            "Department of Aeronautical & Aviation Engineering The Hong Kong Polytechnic University Kowloon":
                "The Hong Kong Polytechnic University",
            "Robotics Institute Carnegie Mellon University Pittsburgh": "Carnegie Mellon University",
        }
        for raw, want in cases.items():
            self.assertEqual(ag.normalize_institution(raw), want, raw)

    def test_no_alias_points_at_another_alias(self):
        # One lookup must be enough: a target that is itself a key would
        # silently depend on the order the lookups run in.
        for key, target in ag.INSTITUTION_ALIASES.items():
            if target != key and target in ag.INSTITUTION_ALIASES:
                self.assertEqual(ag.INSTITUTION_ALIASES[target], target,
                                 f"{key} -> {target} -> {ag.INSTITUTION_ALIASES[target]}")

    def test_every_audit_alias_target_is_a_valid_institution(self):
        for target in set(ag.INSTITUTION_ALIASES.values()):
            self.assertTrue(ag.is_valid_institution(target), target)


class TestAuditSplits(unittest.TestCase):
    def test_two_institutions_in_one_string_are_split(self):
        self.assertEqual(ag.split_institution("Tsinghua University PhiGent Robotics"),
                         ["Tsinghua University", "PhiGent Robotics"])
        self.assertEqual(ag.split_institution("HKUST(GZ); HKUST"),
                         ["HKUST (GZ)", "Hong Kong University of Science and Technology"])

    def test_a_unit_after_a_semicolon_is_dropped(self):
        self.assertEqual(ag.split_institution("Tsinghua University; System Thurst"),
                         ["Tsinghua University"])


class TestAuditSectors(unittest.TestCase):
    def test_research_institutes_are_academic(self):
        for name in ("KU Leuven", "Technion", "Computer Vision Center",
                     "Max Planck Institute for Intelligent Systems",
                     "German Aerospace Center (DLR)", "Fortiss",
                     "Fraunhofer IVI", "Fraunhofer Institute for Cognitive Systems",
                     "Gwangju Institute of Science and Technology"):
            self.assertEqual(ag.classify_institution_sector(name), "academic", name)

    def test_companies_are_industry(self):
        for name in ("Samsung Electronics", "Volvo Cars", "Meituan", "Li Auto",
                     "Mitsubishi Electric", "Samsung Advanced Institute of Technology"):
            self.assertEqual(ag.classify_institution_sector(name), "industry", name)


class TestAuditCountries(unittest.TestCase):
    def setUp(self):
        raw = json.loads(COUNTRIES_FILE.read_text(encoding="utf-8"))
        self.codes = ag.institution_country_map(raw)

    def test_countries_added_by_the_audit(self):
        want = {"Samsung Electronics": "KR", "Volvo": "SE", "Sony": "JP", "Denso": "JP",
                "Siemens": "DE", "IBM": "US", "Woven by Toyota": "JP",
                "The Hong Kong Polytechnic University": "CN",
                "Université Paris Sciences et Lettres": "FR", "Meta": "US"}
        for name, code in want.items():
            self.assertEqual(self.codes.get(name), code, name)

    def test_merged_names_keep_a_country(self):
        for name in ("INRIA", "Mercedes-Benz", "TU Berlin"):
            self.assertIn(name, self.codes)


if __name__ == "__main__":
    unittest.main()
