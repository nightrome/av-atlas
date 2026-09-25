#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for publish_gate.py, the check that stops a build whose stats.json
shrank. No network: the live-site fallback is passed in as a stub.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import publish_gate as gate


def make_stats(n_papers=100, with_inst=80, abstracts=90, citations_each=2, venues=20):
    papers = [{"title": f"p{i}", "institutions": ["X"] if i < with_inst else [], "citations": citations_each}
              for i in range(n_papers)]
    return {"all_papers": papers,
            "corpus_stats": {"pipeline_stages": {"3_abstract": abstracts},
                             "venue_coverage": {f"V{i}": {} for i in range(venues)}}}


class MetricsTests(unittest.TestCase):
    def test_reads_every_gated_number(self):
        self.assertEqual(gate.corpus_metrics(make_stats()), {
            "av_papers": 100, "av_with_institution": 80, "av_with_abstract": 90,
            "in_corpus_citations": 200, "venues": 20})

    def test_numbers_an_older_stats_file_lacks_are_left_out(self):
        m = gate.corpus_metrics({"all_papers": [{"citations": None}]})
        self.assertEqual(m, {"av_papers": 1, "av_with_institution": 0, "in_corpus_citations": 0})

    def test_small_drop_and_growth_pass_big_drop_fails(self):
        old = gate.corpus_metrics(make_stats())
        rows = gate.compare(old, gate.corpus_metrics(make_stats(n_papers=98, with_inst=90)))
        self.assertFalse(any(r[-1] for r in rows))
        rows = gate.compare(old, gate.corpus_metrics(make_stats(with_inst=70)))
        self.assertEqual([r[0] for r in rows if r[-1]], ["AV papers with an institution"])

    def test_metric_missing_on_one_side_is_not_compared(self):
        rows = gate.compare({"av_papers": 10, "venues": 5}, {"av_papers": 10})
        self.assertEqual([r[0] for r in rows], ["AV papers"])


class BaselineAndCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.stats, self.baseline = root / "stats.json", root / "baseline.json"

    def write_stats(self, **kw):
        self.stats.write_text(json.dumps(make_stats(**kw)))

    def no_fetch(self):
        raise AssertionError("should not fetch the live site")

    def save(self, fetch=None):
        return gate.save_baseline(self.stats, self.baseline, fetch or self.no_fetch)

    def check(self, allow_shrink=False):
        gate.check(self.stats, self.baseline, allow_shrink=allow_shrink)

    def test_shrunk_build_is_stopped_and_keeps_its_baseline(self):
        self.write_stats()
        self.save()
        self.write_stats(n_papers=50, with_inst=40)
        with self.assertRaises(SystemExit) as cm:
            self.check()
        self.assertIn("--allow-shrink", str(cm.exception))
        # The next build must compare with the good numbers, not the shrunk
        # stats.json the stopped build left on disk.
        self.assertTrue(self.save())
        self.assertEqual(json.loads(self.baseline.read_text())["metrics"]["av_papers"], 100)
        with self.assertRaises(SystemExit):
            self.check()

    def test_passing_build_clears_the_baseline(self):
        self.write_stats()
        self.save()
        self.write_stats(n_papers=120, with_inst=90)
        self.check()
        self.assertFalse(self.baseline.exists())

    def test_allow_shrink_lets_the_drop_through(self):
        self.write_stats()
        self.save()
        self.write_stats(n_papers=50, with_inst=40)
        self.check(allow_shrink=True)
        self.assertFalse(self.baseline.exists())

    def test_no_local_stats_falls_back_to_the_live_site(self):
        self.assertTrue(self.save(fetch=lambda: {"av_papers": 100}))
        self.write_stats(n_papers=50)
        with self.assertRaises(SystemExit):
            self.check()

    def test_live_site_unreachable_warns_and_skips_the_check(self):
        def fail():
            raise OSError("no network")
        self.assertFalse(self.save(fetch=fail))
        self.write_stats(n_papers=1)
        self.check()  # nothing to compare with: passes


if __name__ == "__main__":
    unittest.main()
