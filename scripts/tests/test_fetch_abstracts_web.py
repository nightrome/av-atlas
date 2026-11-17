import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_abstracts_web as fw

TEXT = "An abstract that is long enough to count as one. " * 3


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def papers():
    return [
        {"title": "Old Non AV Paper", "year": 2019, "av_relevance": "non-AV"},
        {"title": "Fresh AV Paper", "year": 2025, "av_relevance": "AV"},
        {"title": "Has One", "year": 2025, "av_relevance": "AV", "abstract": "already here"},
    ]


class TestRun(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = {"abstracts": {}, "tried": {}}

    def source(self, name, lookup):
        return fw.Source(name, lookup, clock=self.clock.now, sleep=self.clock.sleep)

    def run_(self, sources, **kw):
        return fw.run(papers(), self.state, sources, clock=self.clock.now, sleep=self.clock.sleep, **kw)

    def test_av_first_and_papers_with_abstract_skipped(self):
        seen = []
        def look(title):
            seen.append(title)
            return TEXT
        self.run_([self.source("openalex", look)])
        self.assertEqual(seen, ["Fresh AV Paper", "Old Non AV Paper"])
        self.assertEqual(self.state["abstracts"][fw.norm("Fresh AV Paper")]["source"], "openalex")

    def test_next_source_is_asked_after_a_miss(self):
        calls = []
        def oa(title):
            calls.append(("oa", title))
            return None
        def ax(title):
            calls.append(("ax", title))
            return TEXT
        self.run_([self.source("openalex", oa), self.source("arxiv", ax)])
        self.assertEqual(self.state["abstracts"][fw.norm("Old Non AV Paper")]["source"], "arxiv")
        self.assertEqual(len(calls), 4)

    def test_rate_limit_backs_off_and_does_not_mark_the_paper_tried(self):
        n = {"calls": 0}
        def flaky(title):
            n["calls"] += 1
            if n["calls"] <= 2:
                raise fw.Unavailable("HTTP 429", 0)
            return TEXT
        self.run_([self.source("openalex", flaky)])
        self.assertEqual(self.clock.slept[:2], [30, 60])
        self.assertEqual(len(self.state["abstracts"]), 2)

    def test_retry_after_header_is_honoured(self):
        n = {"calls": 0}
        def once(title):
            n["calls"] += 1
            if n["calls"] == 1:
                raise fw.Unavailable("HTTP 429", 400)
            return TEXT
        self.run_([self.source("openalex", once)])
        self.assertIn(400, self.clock.slept)

    def test_a_dead_source_cools_down_and_the_other_carries_on(self):
        def dead(title):
            raise fw.Unavailable("HTTP 503", 0)
        def ok(title):
            return TEXT
        self.run_([self.source("openalex", dead), self.source("arxiv", ok)], limit=2)
        self.assertEqual(len(self.state["abstracts"]), 2)
        self.assertTrue(all(v["source"] == "arxiv" for v in self.state["abstracts"].values()))

    def test_budget_stops_the_run(self):
        def slow(title):
            self.clock.t += 100
            return TEXT
        stats = self.run_([self.source("openalex", slow)], max_seconds=50)
        self.assertEqual(stats["hit"], 1)

    def test_preknown_sources_are_not_asked_again(self):
        asked = []
        def look(title):
            asked.append(title)
            return TEXT
        self.run_([self.source("openalex", look)], preknown={fw.norm("Fresh AV Paper"): "o"})
        self.assertEqual(asked, ["Old Non AV Paper"])


class TestParsing(unittest.TestCase):
    def test_clean_abstract_rejects_stubs(self):
        self.assertIsNone(fw.clean_abstract("No abstract available."))
        self.assertIsNone(fw.clean_abstract("short"))
        self.assertEqual(fw.clean_abstract("<p>" + TEXT + "</p>"), TEXT.strip())


if __name__ == "__main__":
    unittest.main()
