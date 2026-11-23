# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_abstracts_web as w


def papers(*titles):
    return [{"title": t, "year": 2020, "abstract": ""} for t in titles]


def source(many):
    s = w.Source("arxiv", lambda t: None, clock=lambda: 0.0, sleep=lambda x: None)
    s.lookup_many = many
    return s


class BatchedRunTests(unittest.TestCase):
    def run_it(self, titles, many, batch=3, state=None, peers=None):
        state = state or {"abstracts": {}, "tried": {}}
        src = source(many)
        stats = w.run_batched(papers(*titles), state, src, batch, peers=peers)
        return state, stats

    def test_hits_are_stored_and_misses_marked_tried(self):
        def many(ts):
            return {t: ("An abstract long enough. " * 5 if t == "A paper" else None) for t in ts}
        state, stats = self.run_it(["A paper", "Other paper"], many)
        self.assertEqual(list(state["abstracts"]), [w.norm("A paper")])
        self.assertEqual(state["tried"], {w.norm("Other paper"): "a"})
        self.assertEqual(stats["asked"], 1)

    def test_titles_are_split_into_batches(self):
        seen = []

        def many(ts):
            seen.append(len(ts))
            return {t: None for t in ts}
        self.run_it([f"Paper number {i}" for i in range(7)], many, batch=3)
        self.assertEqual(seen, [3, 3, 1])

    def test_undecided_titles_are_asked_alone_and_then_settle(self):
        calls = []

        def many(ts):
            calls.append(list(ts))
            return {t: False for t in ts}  # a cut-off answer for everything
        state, _ = self.run_it(["Paper one", "Paper two"], many)
        self.assertEqual([len(c) for c in calls], [2, 1, 1])
        self.assertEqual(sorted(state["tried"]), sorted(w.norm(t) for t in ["Paper one", "Paper two"]))

    def test_failed_request_marks_nothing_and_is_retried(self):
        n = {"calls": 0}

        def many(ts):
            n["calls"] += 1
            if n["calls"] == 1:
                raise w.Unavailable("HTTP 503")
            return {t: None for t in ts}
        state, _ = self.run_it(["Paper one"], many)
        self.assertEqual(n["calls"], 2)
        self.assertEqual(list(state["tried"]), [w.norm("Paper one")])

    def test_papers_other_files_found_are_skipped(self):
        asked = []

        def many(ts):
            asked.extend(ts)
            return {t: None for t in ts}
        self.run_it(["Known paper", "New paper"], many, peers=lambda: {w.norm("Known paper")})
        self.assertEqual(asked, ["New paper"])


if __name__ == "__main__":
    unittest.main()
