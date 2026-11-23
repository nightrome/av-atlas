import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fetch_abstracts_pmlr as pm  # noqa: E402
from fetch_abstracts_web import Unavailable, norm  # noqa: E402

LONG = "We study the problem of testing parsers on a small fixture and we keep going so that it is long enough."

BIB = r"""

@Proceedings{ICML2023,
  title =     {Proceedings of the 40th International Conference on Machine Learning},
  volume =    202
}

@InProceedings{pmlr-v202-a23a,
  title = 	 {{C}luster{F}u{G}: Clustering by {M}ulticut},
  author =       {Abbas, Ahmed},
  year = 	 {2023},
  abstract = 	 {We study the problem of testing parsers on a small fixture and we keep going so that it is long enough. It has $k_{i}$ terms and 50\% gains.}
}

@InProceedings{pmlr-v202-b23a,
  title = 	 {Schr{\"o}dinger and {\'E}cole},
  year = 	 {2023},
  abstract = 	 {Another long abstract with {braces} inside, written to pass the length filter of the cleaner for sure.}
}

@InProceedings{pmlr-v202-c23a,
  title = 	 {No abstract here},
  year = 	 {2023}
}
"""

INDEX = """
  <li><a href="v306"><b>Volume 306</b></a> Proceedings of ICML 2026</li>
  <li><a href="v292"><b>Volume 292</b></a> TerraBytes at ICML 2025</li>
  <li><a href="v251"><b>Volume 251</b></a> Proceedings of GRaM at ICML 2024</li>
  <li><a href="v235"><b>Volume 235</b></a> Proceedings of ICML 2024</li>
  <li><a href="v184"><b>Volume 184</b></a> Proceedings of ICML 2022 Workshop on Healthcare AI</li>
  <li><a href="v48"><b>Volume 48</b></a> ICML 2016 Proceedings</li>
  <li><a href="v27"><b>Volume 27</b></a> Unsupervised and Transfer Learning Workshop at ICML 2011</li>
"""


class ParseTests(unittest.TestCase):
    def test_index_keeps_only_main_volumes(self):
        self.assertEqual(pm.parse_index(INDEX), {2026: 306, 2024: 235, 2016: 48})

    def test_bib_entries(self):
        entries = pm.parse_bib(BIB)
        self.assertEqual(len(entries), 2)  # proceedings header and entry without abstract are skipped
        self.assertEqual(entries[0][0], "ClusterFuG: Clustering by Multicut")
        self.assertIn("$k_{i}$", entries[0][1])
        self.assertIn("50% gains", entries[0][1])
        self.assertEqual(entries[1][0], "Schrödinger and École")
        self.assertIn("{braces}".strip("{}"), entries[1][1])
        self.assertNotIn("{", entries[1][1])

    def test_accented_title_matches_corpus_title(self):
        entries = pm.parse_bib(BIB)
        self.assertEqual(norm(entries[1][0]), norm("Schrödinger and École"))


class HarvestTests(unittest.TestCase):
    def test_only_wanted_titles_kept(self):
        entries = pm.parse_bib(BIB)
        found = {}
        wanted = {norm("ClusterFuG: Clustering by Multicut")}
        self.assertEqual(pm.harvest_volume(entries, wanted, found), 1)
        self.assertEqual(list(found), list(wanted))
        self.assertEqual(found[next(iter(wanted))]["source"], "pmlr")

    def test_missing_by_year(self):
        papers = [
            {"venue": "ICML", "year": 2023, "title": "A Paper", "abstract": ""},
            {"venue": "ICML", "year": 2023, "title": "Has One", "abstract": "x" * 100},
            {"venue": "ICLR", "year": 2023, "title": "Other Venue"},
            {"venue": "ICML", "year": 2012, "title": "Old"},
        ]
        m = pm.missing_by_year(papers)
        self.assertEqual(m, {2023: {norm("A Paper")}, 2012: {norm("Old")}})


class RunTests(unittest.TestCase):
    PAPERS = [
        {"venue": "ICML", "year": 2023, "title": "ClusterFuG: Clustering by Multicut"},
        {"venue": "ICML", "year": 2024, "title": "Another"},
        {"venue": "ICML", "year": 2010, "title": "Ancient"},
    ]

    def test_run_and_resume(self):
        calls = []

        def fetch_bib(v):
            calls.append(v)
            return BIB.encode()

        state = {"abstracts": {}, "done": []}
        logs = []
        self.assertTrue(pm.run(self.PAPERS, state, {2023: 202, 2024: 235}, fetch_bib=fetch_bib, log=logs.append))
        self.assertEqual(sorted(calls), [202, 235])
        self.assertEqual(sorted(state["done"]), [202, 235])
        self.assertEqual(list(state["abstracts"]), [norm("ClusterFuG: Clustering by Multicut")])
        self.assertTrue(any("not on PMLR" in line for line in logs))
        calls.clear()
        pm.run(self.PAPERS, state, {2023: 202, 2024: 235}, fetch_bib=fetch_bib, log=logs.append)
        self.assertEqual(calls, [])  # finished volumes are not fetched again

    def test_papers_with_abstract_are_not_wanted(self):
        state = {"abstracts": {}, "done": []}
        pm.run([{"venue": "ICML", "year": 2024, "title": "ClusterFuG: Clustering by Multicut",
                 "abstract": "already have one " * 10}], state, {2024: 235},
               fetch_bib=lambda v: BIB.encode(), log=lambda s: None)
        self.assertEqual(state["abstracts"], {})

    def test_time_budget_stops(self):
        ticks = iter([0, 0, 100, 100, 100])
        state = {"abstracts": {}, "done": []}
        done = pm.run(self.PAPERS, state, {2023: 202, 2024: 235}, max_seconds=10, clock=lambda: next(ticks),
                      fetch_bib=lambda v: BIB.encode(), log=lambda s: None)
        self.assertFalse(done)
        self.assertEqual(len(state["done"]), 1)

    def test_404_marks_done_without_abstracts(self):
        state = {"abstracts": {}, "done": []}
        pm.run(self.PAPERS, state, {2023: 202}, only_year=2023, fetch_bib=lambda v: None, log=lambda s: None)
        self.assertEqual(state["done"], [202])
        self.assertEqual(state["abstracts"], {})


class GetTests(unittest.TestCase):
    def setUp(self):
        pm._last[0] = 0.0

    def test_failure_waits_then_retries_and_never_returns_junk(self):
        sleeps = []
        answers = [Unavailable("HTTP 429", 120), Unavailable("HTTP 503", 0), b"ok"]

        def fetch(url):
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

        t = [0.0]
        out = pm.get("http://x", sleep=sleeps.append, clock=lambda: t[0], fetch=fetch)
        self.assertEqual(out, b"ok")
        self.assertIn(120, sleeps)  # Retry-After honoured
        self.assertTrue(any(s >= 60 for s in sleeps if s != 120))  # second wait is backed off

    def test_exhausted_failure_leaves_volume_not_done(self):
        state = {"abstracts": {}, "done": []}

        def boom(v):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            pm.run([{"venue": "ICML", "year": 2023, "title": "T"}], state, {2023: 202}, fetch_bib=boom,
                   log=lambda s: None)
        self.assertEqual(state["done"], [])


if __name__ == "__main__":
    unittest.main()
