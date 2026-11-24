# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checked_titles as c


class CheckedTitlesTests(unittest.TestCase):
    def test_marked_titles_are_not_asked_again(self):
        state = {}
        self.assertEqual(c.left(state, "S1:2020", {"a", "b"}), {"a", "b"})
        c.mark(state, "S1:2020", {"a", "b"})
        self.assertEqual(c.left(state, "S1:2020", {"a", "b"}), set())

    def test_a_paper_added_later_is_asked_once(self):
        state = {}
        c.mark(state, "S1:2020", {"a"})
        self.assertEqual(c.left(state, "S1:2020", {"a", "new"}), {"new"})
        c.mark(state, "S1:2020", {"new"})
        self.assertEqual(c.left(state, "S1:2020", {"a", "new"}), set())

    def test_keys_are_independent(self):
        state = {}
        c.mark(state, "S1:2020", {"a"})
        self.assertEqual(c.left(state, "S1:2021", {"a"}), {"a"})

    def test_a_key_the_old_format_finished_counts_as_checked(self):
        state = {}
        self.assertEqual(c.left(state, "S1:2020", {"a", "b"}, legacy=["S1:2020"]), set())
        self.assertEqual(c.left(state, "S1:2020", {"a", "b", "c"}), {"c"})


if __name__ == "__main__":
    unittest.main()
