#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for fetch_crossref.py, with Crossref and Semantic Scholar mocked out.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import email.message
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_crossref as fc


def s2_error(code, body=b'{"message":"Forbidden"}', errortype="ForbiddenException"):
    headers = email.message.Message()
    headers["Content-Type"] = "application/json"
    if errortype:
        headers["x-amzn-ErrorType"] = errortype
    return urllib.error.HTTPError("https://api.semanticscholar.org/graph/v1/paper/batch?fields=x",
                                  code, "Forbidden", headers, io.BytesIO(body))


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def item(title, doi, authors=(("Jane", "Doe"),), print_year=None, online_year=None,
         abstract=None, container=None, subtitle=None):
    it = {"DOI": doi, "title": [title], "type": "journal-article",
          "author": [{"given": g, "family": f} for g, f in authors]}
    if print_year:
        it["published-print"] = {"date-parts": [[print_year, 3]]}
    if online_year:
        it["published-online"] = {"date-parts": [[online_year, 11, 2]]}
    if abstract:
        it["abstract"] = abstract
    if container:
        it["container-title"] = [container]
    if subtitle:
        it["subtitle"] = [subtitle]
    return it


class TestCleaning(unittest.TestCase):
    def test_jats_abstract_loses_heading_and_tags(self):
        raw = ("<jats:title>Abstract</jats:title><jats:p>We use <jats:italic>LiDAR</jats:italic>"
               " &amp; radar.</jats:p><jats:p>Second part.</jats:p>")
        self.assertEqual(fc.clean_abstract(raw), "We use LiDAR & radar. Second part.")

    def test_inline_tags_do_not_split_words(self):
        self.assertEqual(fc.clean_text("H<sub>2</sub>O and B&#233;zier curves"), "H2O and Bézier curves")

    def test_ieee_pretty_printed_title_markup(self):
        # Real T-ITS/RA-L titles as Crossref returns them.
        nl = "\n                    "
        lady = f"LADY:{nl}<u>L</u>{nl}inear{nl}<u>A</u>{nl}ttention for Autonomous{nl}<u>D</u>{nl}riving"
        self.assertEqual(fc.item_title({"title": [lady]}), "LADY: Linear Attention for Autonomous Driving")
        norm = f"Nuclear{nl}<i>ℓ</i>{nl}<sub>1</sub>{nl}-{nl}<i>ℓ</i>{nl}<sub>2</sub>{nl}Norm"
        self.assertEqual(fc.item_title({"title": [norm]}), "Nuclear ℓ1-ℓ2 Norm")
        tii = f"<i>TiI</i>{nl}: A Novel Framework"
        self.assertEqual(fc.item_title({"title": [tii]}), "TiI: A Novel Framework")
        grad = f"With{nl}<b>N</b>{nl}atural Gr{nl}<b>a</b>{nl}dient"
        self.assertEqual(fc.item_title({"title": [grad]}), "With Natural Gradient")

    def test_escaped_angle_bracket_stays_text(self):
        self.assertEqual(fc.clean_text("Speed &lt; 30 km/h"), "Speed < 30 km/h")

    def test_subtitle_is_joined_once(self):
        self.assertEqual(fc.item_title(item("DustNet", "d", subtitle="Attention to Dust")),
                         "DustNet: Attention to Dust")
        self.assertEqual(fc.item_title(item("DustNet: Attention to Dust", "d", subtitle="Attention to Dust")),
                         "DustNet: Attention to Dust")

    def test_authors_given_family_and_organisation(self):
        it = item("T", "d", authors=(("Jane", "Doe"), ("M&#233;lanie", "Bouroche")))
        it["author"].append({"name": "The nuScenes Team"})
        self.assertEqual(fc.item_authors(it), "Jane Doe, Mélanie Bouroche, The nuScenes Team")

    def test_itsc_container_title_carries_edition_number(self):
        self.assertEqual(fc.CONFERENCES["ITSC"](2025),
                         "2025 IEEE 28th International Conference on Intelligent Transportation Systems (ITSC)")
        self.assertEqual(fc.CONFERENCES["ITSC"](2024).split()[2], "27th")
        self.assertEqual(fc.CONFERENCES["ITSC"](2018).split()[2], "21st")


class TestYear(unittest.TestCase):
    def test_print_year_wins_over_online_year(self):
        self.assertEqual(fc.item_year(item("T", "d", print_year=2026, online_year=2025)), (2026, True))

    def test_early_access_falls_back_to_online_year(self):
        self.assertEqual(fc.item_year(item("T", "d", online_year=2025)), (2025, False))


class TestMergeRecords(unittest.TestCase):
    def test_new_paper_is_appended_with_year_and_doi(self):
        records, stats = fc.merge_records([], [item("A New Paper", "10.1/X", print_year=2026)], True)
        self.assertEqual(records, [{"year": 2026, "title": "A New Paper", "authors": "Jane Doe",
                                    "abstract": None, "doi": "10.1/x"}])
        self.assertEqual(stats["added"], 1)

    def test_existing_title_gets_doi_but_keeps_its_fields(self):
        existing = [{"year": 2026, "title": "Road Scene Understanding &amp; More",
                     "authors": "Jane Doe 0001", "abstract": None}]
        records, stats = fc.merge_records(
            existing, [item("Road Scene Understanding & More", "10.1/y", print_year=2026)], True)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["doi"], "10.1/y")
        self.assertEqual(records[0]["authors"], "Jane Doe 0001")
        self.assertEqual(records[0]["title"], "Road Scene Understanding &amp; More")
        self.assertEqual(stats["doi_added"], 1)
        self.assertEqual(existing[0].get("doi"), None, "input list must not be mutated")

    def test_early_access_year_is_corrected_once_in_an_issue(self):
        records, _ = fc.merge_records([], [item("P", "10.1/z", online_year=2025)], True)
        self.assertEqual(records[0]["year"], 2025)
        records, stats = fc.merge_records(records, [item("P", "10.1/z", print_year=2026, online_year=2025)], True)
        self.assertEqual(records[0]["year"], 2026)
        self.assertEqual(stats["year_fixed"], 1)

    def test_online_year_never_overrides_an_existing_year(self):
        existing = [{"year": 2026, "title": "P", "authors": "A", "abstract": None}]
        records, _ = fc.merge_records(existing, [item("P", "10.1/z", online_year=2025)], True)
        self.assertEqual(records[0]["year"], 2026)

    def test_existing_abstract_is_never_overwritten(self):
        existing = [{"title": "P", "authors": "A", "abstract": "Original."}]
        records, stats = fc.merge_records(existing, [item("P", "10.1/z", abstract="<jats:p>New.</jats:p>")], False)
        self.assertEqual(records[0]["abstract"], "Original.")
        self.assertEqual(stats["abstract_added"], 0)

    def test_repeated_title_in_the_file_is_left_alone(self):
        existing = [{"year": 2025, "title": "Scanning the Issue", "authors": "A", "abstract": None},
                    {"year": 2026, "title": "Scanning the Issue", "authors": "A", "abstract": None}]
        records, stats = fc.merge_records(existing, [item("Scanning the Issue", "10.1/s", print_year=2024)], True)
        self.assertEqual(records, existing)
        self.assertEqual(stats["added"] + stats["doi_added"] + stats["year_fixed"], 0)

    def test_records_without_authors_or_before_2012_are_skipped(self):
        toc = item("Table of Contents", "10.1/toc", authors=(), print_year=2026)
        old = item("Old Paper", "10.1/old", print_year=2009)
        records, stats = fc.merge_records([], [toc, old], True)
        self.assertEqual(records, [])
        self.assertEqual(stats["skipped"], 2)

    def test_conference_records_have_no_year_field(self):
        records, _ = fc.merge_records([], [item("P", "10.1109/IV.1", print_year=2026)], False)
        self.assertNotIn("year", records[0])


class TestFillAbstractsByDoi(unittest.TestCase):
    def test_batches_and_only_fills_missing(self):
        records = [{"title": "A", "doi": "10.1/a", "abstract": None},
                   {"title": "B", "doi": "10.1/b", "abstract": "Kept."},
                   {"title": "C", "doi": None, "abstract": None},
                   {"title": "D", "doi": "10.1/d", "abstract": ""}]
        calls = []

        def fake_batch(dois):
            calls.append(list(dois))
            return [{"abstract": "Found <i>A</i>."}, None]

        n = fc.fill_abstracts_by_doi(records, batch=fake_batch)
        self.assertEqual(calls, [["10.1/a", "10.1/d"]])
        self.assertEqual(n, 1)
        self.assertEqual(records[0]["abstract"], "Found A.")
        self.assertEqual(records[1]["abstract"], "Kept.")
        self.assertEqual(records[3]["abstract"], "")

    def test_splits_into_500_per_call(self):
        records = [{"title": str(i), "doi": f"10.1/{i}", "abstract": None} for i in range(1200)]
        sizes = []
        fc.fill_abstracts_by_doi(records, batch=lambda d: sizes.append(len(d)) or [None] * len(d))
        self.assertEqual(sizes, [500, 500, 200])


class TestRun(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = patch.object(fc, "OUT_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pages(self, *pages):
        """A fake crossref_get serving the given item lists as cursor pages."""
        seen = []

        def fake(params):
            seen.append(params)
            i = len(seen) - 1
            return {"items": pages[i] if i < len(pages) else [], "total-results": sum(map(len, pages)),
                    "next-cursor": f"c{i}"}
        return fake, seen

    def test_conference_keeps_only_the_exact_container(self):
        iv = "2026 IEEE Intelligent Vehicles Symposium (IV)"
        fake, seen = self._pages([item("Main Track Paper", "10.1109/iv.1", container=iv),
                                  item("Workshop Paper", "10.1109/ivw.1",
                                       container="2026 IEEE Intelligent Vehicles Symposium Workshops (IV Workshops)")])
        with patch.object(fc, "crossref_get", fake):
            fc.main(["IV", "2026", "--no-abstracts"])
        out = json.loads((self.dir / "iv2026.json").read_text(encoding="utf-8"))
        self.assertEqual([p["title"] for p in out], ["Main Track Paper"])
        self.assertIn(f"container-title:{iv}", seen[0]["filter"])

    def test_journal_merges_into_existing_all_file_with_crlf(self):
        path = self.dir / "tits_all.json"
        existing = [{"year": 2025, "title": "Old One", "authors": "A", "abstract": None}]
        path.write_bytes(json.dumps(existing, indent=2).replace("\n", "\r\n").encode("utf-8"))
        page = [item("Old One", "10.1109/tits.1", print_year=2025), item("New One", "10.1109/tits.2", print_year=2026)]
        fake, seen = self._pages(page)
        with patch.object(fc, "crossref_get", fake), \
                patch.object(fc, "s2_batch", lambda dois, counts=None: [{"abstract": "S2 text."} for _ in dois]):
            fc.main(["T-ITS", "--since", "2026-08-01"])
        raw = path.read_bytes()
        self.assertIn(b"\r\n", raw)
        self.assertFalse(raw.endswith(b"\n"))
        out = json.loads(raw.decode("utf-8"))
        self.assertEqual([p["title"] for p in out], ["Old One", "New One"])
        self.assertEqual(out[0]["doi"], "10.1109/tits.1")
        self.assertEqual(out[1]["abstract"], "S2 text.")
        f = seen[0]["filter"]
        self.assertIn("issn:1524-9050", f)
        self.assertIn("issn:1558-0016", f)
        self.assertIn("from-update-date:2026-08-01", f)

    def test_empty_conference_writes_no_file(self):
        fake, _ = self._pages([])
        with patch.object(fc, "crossref_get", fake):
            fc.main(["ITSC", "2026", "--no-abstracts"])
        self.assertFalse((self.dir / "itsc2026.json").exists())

    def test_deep_paging_follows_the_cursor(self):
        fc_rows = fc.ROWS
        fc.ROWS = 2
        self.addCleanup(setattr, fc, "ROWS", fc_rows)
        fake, seen = self._pages([item("A", "1"), item("B", "2")], [item("C", "3")])
        with patch.object(fc, "crossref_get", fake):
            items = fc.crossref_items(["issn:x"])
        self.assertEqual(len(items), 3)
        self.assertEqual([p["cursor"] for p in seen], ["*", "c0"])


class TestS2BatchRejectedKey(unittest.TestCase):
    def setUp(self):
        fc.s2_auth.cache_clear()
        self.addCleanup(fc.s2_auth.cache_clear)

    def test_403_with_key_falls_back_to_anonymous(self):
        sent = []

        def urlopen(req, timeout):
            sent.append(req.get_header("X-api-key"))
            if req.get_header("X-api-key"):
                raise s2_error(403)
            return FakeResponse([{"abstract": "A."}])

        with patch.object(fc, "read_s2_key", return_value="bad"), \
                patch.object(fc.time, "sleep"), \
                patch.object(fc.urllib.request, "urlopen", side_effect=urlopen), \
                patch("builtins.print"):
            self.assertEqual(fc.s2_batch(["10.1109/x"]), [{"abstract": "A."}])
        self.assertEqual(sent, ["bad", None])


class TestS2BatchBadIds(unittest.TestCase):
    def setUp(self):
        fc.s2_auth.cache_clear()
        self.addCleanup(fc.s2_auth.cache_clear)

    def test_unknown_and_malformed_dois_are_handled_quietly(self):
        # What S2 does (checked live): unknown ids next to a known one get
        # null, a batch of only unknown ids gets 400 "No valid paper ids given".
        known = {"DOI:10.1109/a", "DOI:10.1109/c"}
        sent = []

        def urlopen(req, timeout):
            ids = json.loads(req.data)["ids"]
            sent.append(ids)
            if not known & set(ids):
                raise s2_error(400, body=b'{"error":"No valid paper ids given"}', errortype=None)
            return FakeResponse([{"abstract": "A."} if i in known else None for i in ids])

        counts = fc.new_s2_batch_counts()
        with patch.object(fc, "read_s2_key", return_value=None),                 patch.object(fc.time, "sleep"),                 patch.object(fc.urllib.request, "urlopen", side_effect=urlopen),                 patch("builtins.print") as log:
            out = fc.s2_batch(["10.1109/a", "https://doi.org/10.1109/c ", "10.1109/new", "junk"], counts)
            new = fc.s2_batch(["10.1109/new1", "10.1109/new2"], counts)
        self.assertEqual(out, [{"abstract": "A."}, {"abstract": "A."}, None, None])
        self.assertEqual(new, [None, None])
        self.assertEqual(sent, [["DOI:10.1109/a", "DOI:10.1109/c", "DOI:10.1109/new"],
                                ["DOI:10.1109/new1", "DOI:10.1109/new2"]])
        self.assertEqual(counts, {"skipped": 1, "unknown": 2, "rejected": 0})
        lines = [c.args[0] for c in log.call_args_list if c.args]
        self.assertIn("  Skipping malformed id DOI:junk", lines)
        self.assertFalse(any("HTTP 400" in line for line in lines))


class TestJournalsKeepGoing(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = patch.object(fc, "OUT_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_one_failing_journal_does_not_stop_the_rest(self):
        def crossref_get(params):
            if "issn:1524-9050" in params["filter"]:
                raise s2_error(500, body=b"boom", errortype=None)
            return {"items": [], "total-results": 0, "next-cursor": None}

        with patch.object(fc, "crossref_get", crossref_get), patch("builtins.print"):
            code = fc.main(["journals", "--since", "2026-08-01", "--no-abstracts"])
        self.assertEqual(code, 1)
        # Every journal after T-ITS still got its (empty) file written.
        self.assertFalse((self.dir / "tits_all.json").exists())
        self.assertTrue((self.dir / "ral_all.json").exists())
        self.assertTrue((self.dir / "ijrr_all.json").exists())

    def test_s2_failure_keeps_the_crossref_records(self):
        def crossref_get(params):
            return {"items": [item("New One", "10.1109/tits.2", print_year=2026)],
                    "total-results": 1, "next-cursor": None}

        def s2_down(dois, counts=None):
            raise s2_error(403)

        with patch.object(fc, "crossref_get", crossref_get), \
                patch.object(fc, "s2_batch", s2_down), \
                patch("builtins.print"):
            self.assertIsNone(fc.main(["T-ITS", "--since", "2026-08-01"]))
        out = json.loads((self.dir / "tits_all.json").read_text(encoding="utf-8"))
        self.assertEqual([p["title"] for p in out], ["New One"])


if __name__ == "__main__":
    unittest.main()
