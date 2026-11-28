import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import author_name_variants as anv  # noqa: E402


def person(papers=3, ids=(), orcids=(), coauthors=(), inst_years=None, together=()):
    return {"papers": papers, "ids": set(ids), "orcids": set(orcids),
            "coauthors": set(coauthors), "inst_years": dict(inst_years or {}),
            "together": set(together)}


COAUTHORS = {"gomez a", "sotelo m"}


class CompatibilityTests(unittest.TestCase):
    def test_initial_against_full_name(self):
        self.assertTrue(anv.names_compatible("D. Fernández Llorca", "David Fernández Llorca"))

    def test_hyphen_and_diacritics_are_ignored(self):
        self.assertTrue(anv.names_compatible("David Fernández-Llorca", "David Fernandez Llorca"))
        self.assertEqual(anv.name_key("David Fernández-Llorca"), anv.name_key("david fernandez llorca"))

    def test_middle_initial_may_be_dropped(self):
        self.assertTrue(anv.names_compatible("John Dolan", "John M. Dolan"))

    def test_different_first_names_do_not_fit(self):
        self.assertFalse(anv.names_compatible("David Smith", "Daniel Smith"))
        self.assertFalse(anv.names_compatible("D. Smith", "Anna Smith"))

    def test_different_surnames_do_not_fit(self):
        self.assertFalse(anv.names_compatible("David Smith", "David Smyth"))

    def test_second_given_name_must_fit(self):
        self.assertFalse(anv.names_compatible("Maria Jose Lopez", "Maria Garcia Lopez"))


class FindMergesTests(unittest.TestCase):
    def merge(self, people, excluded=(), common=()):
        return anv.find_merges(people, set(excluded), set(common))

    def test_merge_with_shared_coauthors_prefers_fullest_spelling(self):
        people = {
            "D. Fernández Llorca": person(2, coauthors=COAUTHORS),
            "David Fernández-Llorca": person(5, coauthors=COAUTHORS),
            "David Fernández Llorca": person(1, coauthors=COAUTHORS),
        }
        merges, _, groups = self.merge(people)
        self.assertEqual(len(groups), 1)
        shown = groups[0]["name"]
        self.assertIn(shown, ("David Fernández-Llorca", "David Fernández Llorca"))
        self.assertEqual(set(merges) | {shown}, set(people))
        self.assertNotIn("D. Fernández Llorca", {shown})

    def test_same_openalex_id_is_enough(self):
        people = {"J. Garcia": person(ids={"A1"}), "Juan Garcia": person(ids={"A1"})}
        merges, _, _ = self.merge(people)
        self.assertEqual(merges, {"J. Garcia": "Juan Garcia"})

    def test_same_orcid_is_enough(self):
        o = "0000-0001-5099-6297"
        people = {"J. Garcia": person(orcids={o}), "Juan Garcia": person(orcids={o})}
        self.assertEqual(self.merge(people)[0], {"J. Garcia": "Juan Garcia"})

    def test_same_institution_needs_overlapping_years(self):
        inst = "University of Alcalá"
        overlap = {"J. Garcia": person(inst_years={inst: (2018, 2020)}),
                   "Juan Garcia": person(inst_years={inst: (2020, 2022)})}
        apart = {"J. Garcia": person(inst_years={inst: (2010, 2012)}),
                 "Juan Garcia": person(inst_years={inst: (2020, 2022)})}
        self.assertEqual(self.merge(overlap)[0], {"J. Garcia": "Juan Garcia"})
        self.assertEqual(self.merge(apart)[0], {})

    def test_no_merge_for_two_people_with_same_surname_and_initial(self):
        # David and Daniel Smith share a lab and one co-author each way, but
        # "D. Smith" fits both, so nothing may be folded together.
        people = {
            "David Smith": person(coauthors={"lee k", "park j"}),
            "Daniel Smith": person(coauthors={"lee k", "park j"}),
            "D. Smith": person(coauthors={"lee k", "park j"}),
        }
        merges, near, _ = self.merge(people)
        self.assertEqual(merges, {})
        self.assertTrue(near)

    def test_no_merge_without_evidence(self):
        people = {"D. Smith": person(coauthors={"lee k"}), "David Smith": person(coauthors={"park j"})}
        merges, near, _ = self.merge(people)
        self.assertEqual(merges, {})
        self.assertEqual(len(near), 1)

    def test_one_shared_coauthor_is_not_enough(self):
        people = {"D. Smith": person(coauthors={"lee k", "x y"}),
                  "David Smith": person(coauthors={"lee k", "p q"})}
        self.assertEqual(self.merge(people)[0], {})

    def test_common_surname_needs_id_or_orcid(self):
        people = {"Z. Wang": person(coauthors=COAUTHORS), "Zhe Wang": person(coauthors=COAUTHORS)}
        self.assertEqual(self.merge(people, common={"wang"})[0], {})
        people = {"Z. Wang": person(ids={"A9"}), "Zhe Wang": person(ids={"A9"})}
        self.assertEqual(self.merge(people, common={"wang"})[0], {"Z. Wang": "Zhe Wang"})

    def test_excluded_names_are_never_merged(self):
        people = {"J. Garcia": person(ids={"A1"}), "Juan Garcia": person(ids={"A1"})}
        self.assertEqual(self.merge(people, excluded={"Juan Garcia"})[0], {})

    def test_names_listed_together_on_a_paper_are_two_people(self):
        people = {"J. Garcia": person(ids={"A1"}, together={"Juan Garcia"}),
                  "Juan Garcia": person(ids={"A1"}, together={"J. Garcia"})}
        self.assertEqual(self.merge(people)[0], {})

    def test_extra_spelled_out_given_name_needs_more(self):
        people = {"Hao Cheng": person(coauthors=COAUTHORS | {"x y"}),
                  "Hao Bo Cheng": person(coauthors=COAUTHORS | {"x y"})}
        self.assertEqual(self.merge(people)[0], {})
        o = "0000-0002-9400-8446"
        people = {"Hao Cheng": person(orcids={o}), "Hao Bo Cheng": person(orcids={o})}
        self.assertEqual(len(self.merge(people)[0]), 1)

    def test_junk_names_are_skipped(self):
        people = {"Ganesh Sistu": person(ids={"A1"}), "Ganesh Sistu ganesh.sistu": person(ids={"A1"})}
        self.assertEqual(self.merge(people)[0], {})

    def test_display_name_keeps_diacritics(self):
        shown = anv.pick_display_name(["Sertac Karaman", "Sertaç Karaman"], {"Sertac Karaman": 9, "Sertaç Karaman": 1})
        self.assertEqual(shown, "Sertaç Karaman")


class AggregateIntegrationTests(unittest.TestCase):
    def test_resolve_applies_merges(self):
        import aggregate
        saved = aggregate.AUTHOR_NAME_MERGES
        aggregate.AUTHOR_NAME_MERGES = {"D. Fernández Llorca": "David Fernández Llorca"}
        try:
            self.assertEqual(aggregate.resolve_ambiguous_author_identity("D. Fernández Llorca", "T"),
                             "David Fernández Llorca")
            self.assertEqual(aggregate.resolve_ambiguous_author_identity("Someone Else", "T"), "Someone Else")
        finally:
            aggregate.AUTHOR_NAME_MERGES = saved

    def test_committed_merges_never_touch_shared_names(self):
        import aggregate
        shared = set(aggregate.AUTHOR_IDENTITIES)
        for variant, shown in aggregate.AUTHOR_NAME_MERGES.items():
            self.assertNotIn(variant, shared)
            self.assertNotIn(shown, shared)
            self.assertTrue(anv.names_compatible(variant, shown), (variant, shown))


if __name__ == "__main__":
    unittest.main()
