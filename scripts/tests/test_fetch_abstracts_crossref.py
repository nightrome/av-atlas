import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fetch_abstracts_crossref as fc  # noqa: E402
from fetch_abstracts_web import Unavailable, norm  # noqa: E402

LONG = "We propose a method for detecting objects in lidar point clouds that works well in the real world."


class CleanTests(unittest.TestCase):
    def test_strips_jats_and_heading(self):
        raw = f"<jats:p>Abstract: {LONG}</jats:p>"
        self.assertEqual(fc.clean_crossref_abstract(raw), LONG)

    def test_title_tag_heading(self):
        raw = f"<jats:title>Abstract</jats:title><jats:p>{LONG}</jats:p>"
        self.assertEqual(fc.clean_crossref_abstract(raw), LONG)

    def test_entities_and_short(self):
        self.assertIsNone(fc.clean_crossref_abstract("<jats:p>Too short</jats:p>"))
        self.assertIsNone(fc.clean_crossref_abstract(None))
        self.assertIn("&", fc.clean_crossref_abstract(f"<p>{LONG} A &amp; B</p>"))

    def test_word_starting_with_abstract_kept(self):
        text = "Abstraction layers help. " + LONG
        self.assertEqual(fc.clean_crossref_abstract(text), text)


class HarvestTests(unittest.TestCase):
    def pages(self, *page_items):
        pages = list(page_items)

        def fetch(path):
            items = pages.pop(0) if pages else []
            return {"message": {"items": items, "next-cursor": "c%d" % len(pages)}}
        return fetch

    def test_matches_only_wanted_titles(self):
        wanted = {norm("Paper One"), norm("Paper Two")}
        fetch = self.pages([{"title": ["Paper One!"], "abstract": f"<p>{LONG}</p>"},
                            {"title": ["Other"], "abstract": f"<p>{LONG}</p>"}], [])
        found = {}
        pages, seen, kept = fc.harvest_year("1234-5678", 2020, wanted, found, fetch)
        self.assertEqual((pages, seen, kept), (2, 2, 1))
        self.assertEqual(found[norm("Paper One")], {"text": LONG, "source": "crossref"})

    def test_missing_by_venue_skips_present(self):
        papers = [{"title": "A", "venue": "IJCV", "year": 2020, "abstract": ""},
                  {"title": "B", "venue": "IJCV", "year": 2020, "abstract": "x"}]
        m = fc.missing_by_venue(papers)
        self.assertEqual(m["IJCV"][2020], {"a"})

    def test_resume_and_failure(self):
        missing = {"IJCV": {2020: {"a"}, 2021: {"b"}}}
        state = {"abstracts": {}, "done": [f"{fc.ISSNS['IJCV']}:2021"]}
        calls = []

        def fetch(path):
            calls.append(path)
            return {"message": {"items": [], "next-cursor": None}}
        self.assertTrue(fc.run(missing, state, ["IJCV"], fetch_json=fetch))
        self.assertEqual(len(calls), 1)
        self.assertIn("from-pub-date:2020", calls[0])
        self.assertIn(f"{fc.ISSNS['IJCV']}:2020", state["done"])

        # a failure propagates and marks nothing done
        state2 = {"abstracts": {}, "done": []}

        def boom(path):
            raise Unavailable("HTTP 500")
        with self.assertRaises(Unavailable):
            fc.run(missing, state2, ["IJCV"], fetch_json=boom)
        self.assertEqual(state2["done"], [])

    def test_get_json_backs_off_and_honours_retry_after(self):
        seq = [Unavailable("HTTP 429", 120), Unavailable("HTTP 503", 0), b'{"ok": 1}']
        sleeps = []

        def fetch(url):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        out = fc.get_json("/x", sleep=sleeps.append, clock=lambda: 1e9, fetch=fetch)
        self.assertEqual(out, {"ok": 1})
        self.assertIn(120, sleeps)
        self.assertTrue(any(s >= 60 for s in sleeps))


if __name__ == "__main__":
    unittest.main()
