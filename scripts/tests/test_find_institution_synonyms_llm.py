# -*- coding: utf-8 -*-
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import find_institution_synonyms_llm as s


class AcceptedTests(unittest.TestCase):
    def test_only_real_targets_other_than_the_name_itself(self):
        new = ["EPFL Lausanne", "Facebook", "Nonsense", "Same"]
        answer = [{"i": 1, "same_as": "École polytechnique fédérale de Lausanne"},
                  {"i": 2, "same_as": "Meta"},
                  {"i": 3, "same_as": "Made Up Univ"},
                  {"i": 4, "same_as": "Same"},
                  {"i": 9, "same_as": "Meta"}]
        got = s.accepted(new, answer, {"École polytechnique fédérale de Lausanne", "Meta"}, set(new))
        self.assertEqual(got, {"EPFL Lausanne": "École polytechnique fédérale de Lausanne", "Facebook": "Meta"})

    def test_fenced_answer_is_parsed(self):
        self.assertEqual(s.parse_json_list('```json\n[{"i": 1, "same_as": ""}]\n```'), [{"i": 1, "same_as": ""}])
        self.assertEqual(s.parse_json_list("no idea"), [])


class RunTests(unittest.TestCase):
    def test_answered_names_are_not_asked_again(self):
        state = {"groups": {"NL": {"new": ["A", "B"], "known": ["K"]}}, "done": {"A": ""}}
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return '[{"i": 1, "same_as": "K"}]'
        out = str(Path(tempfile.mkdtemp()) / "o.json")
        s.run(state, out, ask_llm=ask)
        self.assertEqual(len(asked), 1)
        self.assertEqual(state["done"], {"A": "", "B": "K"})
        s.run(state, out, ask_llm=ask)
        self.assertEqual(len(asked), 1)

    def test_a_failed_call_records_nothing(self):
        state = {"groups": {"NL": {"new": ["A"], "known": ["K"]}}}

        def boom(prompt):
            raise RuntimeError("down")
        s.run(state, str(Path(tempfile.mkdtemp()) / "o.json"), ask_llm=boom)
        self.assertEqual(state["done"], {})


if __name__ == "__main__":
    unittest.main()
