# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_affiliations_llm as f

GOOD = '```json\n[{"name": "Wei Zhang", "institutions": ["Northwestern Polytechnical University, Xi\'an"]}]\n```'
LONG = "x" * 400


class CheckTests(unittest.TestCase):
    def test_fenced_json_is_parsed_and_kept(self):
        data, why = f.check_answer(f.parse_answer(GOOD), "Wei Zhang, Li Wang")
        self.assertEqual(why, "ok")
        self.assertEqual(data[0]["affiliations"], ["Northwestern Polytechnical University, Xi'an"])

    def test_wrong_authors_are_rejected(self):
        self.assertEqual(f.check_answer(f.parse_answer(GOOD), "Anna Meier")[1], "authors do not match")

    def test_garbage_and_empty_institutions_are_rejected(self):
        self.assertEqual(f.check_answer(f.parse_answer("I cannot help"), "Wei Zhang")[1], "not json")
        self.assertEqual(f.check_answer([{"name": "Wei Zhang", "institutions": []}], "Wei Zhang")[1], "no institutions")
        self.assertEqual(f.check_answer([{"name": "Wei Zhang", "institutions": ["x" * 400]}], "Wei Zhang")[1],
                         "no institutions")


class RunTests(unittest.TestCase):
    def go(self, targets, state=None, **kw):
        state = state or {"results": {}, "tried": {}}
        f.run(targets, state, str(Path(__import__("tempfile").mkdtemp()) / "o.json"), None,
              sleep=lambda s: None, **kw)
        return state

    def t(self, key):
        return {"key": key, "arxiv_id": "1", "title": key, "authors": "Wei Zhang"}

    def test_answers_are_stored_and_tried_ones_skipped(self):
        asked = []

        def ask(text):
            asked.append(text)
            return GOOD
        s = self.go([self.t("a")], download=lambda i: b"", page_text=lambda b: LONG, ask=ask)
        self.assertEqual(s["tried"], {"a": "ok"})
        self.assertIn("a", s["results"])
        self.go([self.t("a")], state=s, download=lambda i: b"", page_text=lambda b: LONG, ask=ask)
        self.assertEqual(len(asked), 1)

    def test_rejected_answer_is_tried_but_not_stored(self):
        s = self.go([self.t("a")], download=lambda i: b"", page_text=lambda b: LONG, ask=lambda t: "nonsense")
        self.assertEqual(s["tried"], {"a": "not json"})
        self.assertEqual(s["results"], {})

    def test_page_without_text_is_tried_without_asking(self):
        def ask(t):
            raise AssertionError("must not ask")
        s = self.go([self.t("a")], download=lambda i: b"", page_text=lambda b: "short", ask=ask)
        self.assertEqual(s["tried"], {"a": "no text"})

    def test_failure_records_nothing(self):
        def boom(i):
            raise OSError("HTTP 429")
        s = self.go([self.t("a")], download=boom, page_text=lambda b: LONG, ask=lambda t: GOOD)
        self.assertEqual(s["tried"], {})


class StandardizeTests(unittest.TestCase):
    def test_numbered_answer_maps_back_to_the_raw_strings(self):
        batch = ["Dept of EE, MIT, Cambridge, USA", "Huawei Noah's Ark Lab"]
        got = f.parse_names('[{"i": 1, "name": "Massachusetts Institute of Technology", "country": "us"},'
                            ' {"i": 2, "name": "Huawei", "country": ""}, {"i": 9, "name": "x"}]', batch)
        self.assertEqual(got[batch[0]], {"name": "Massachusetts Institute of Technology",
                                         "native": "Massachusetts Institute of Technology", "country": "US"})
        self.assertEqual(got[batch[1]]["name"], "Huawei")
        self.assertEqual(len(got), 2)

    def test_unusable_entries_are_left_out(self):
        batch = ["a", "b"]
        got = f.parse_names('[{"i": 1, "name": "X", "country": "USA"}, {"i": 2, "name": "' + "y" * 200 + '"}]', batch)
        self.assertEqual(got, {})

    def test_strings_already_mapped_are_not_asked_again(self):
        state = {"results": {"k": [{"name": "A", "affiliations": ["Raw one", "Raw two"]}]},
                 "tried": {}, "names_version": f.NAME_VERSION,
                 "names": {"Raw one": {"name": "One", "country": "NL"}}}
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return '[{"i": 1, "name": "Two", "country": "DE"}]'
        f.standardize(state, str(Path(__import__("tempfile").mkdtemp()) / "o.json"), ask_names=ask)
        self.assertEqual(len(asked), 1)
        self.assertNotIn("Raw one", asked[0])
        self.assertEqual(state["names"]["Raw two"]["name"], "Two")

    def test_the_spelling_the_corpus_has_wins_and_english_is_the_default(self):
        state = {"names": {"a": {"name": "Delft University of Technology", "native": "Technische Universiteit Delft",
                                 "country": "NL"},
                           "b": {"name": "Polytechnic University of Milan", "native": "Politecnico di Milano",
                                 "country": "IT"},
                           "c": {"name": "Brunel University London", "native": "Brunel University London",
                                 "country": "GB"}}}
        known = {f.name_key("Delft University of Technology"), f.name_key("Politecnico di Milano")}
        self.assertEqual(f.standard_affiliations(state, ["a", "b", "c"], known=known)[0],
                         ["Delft University of Technology", "Politecnico di Milano", "Brunel University London"])
        self.assertEqual(f.standard_affiliations(state, ["a"])[0], ["Delft University of Technology"])

    def test_standard_affiliations_dedupes_and_skips_non_institutions(self):
        state = {"names": {"a": {"name": "Uni X", "country": "NL"}, "b": {"name": "Uni X", "country": "NL"},
                           "c": {"name": "", "country": ""}}}
        self.assertEqual(f.standard_affiliations(state, ["a", "b", "c", "unknown"]), (["Uni X"], ["NL"]))


if __name__ == "__main__":
    unittest.main()
