#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_common as fc


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


class TestByCitations(unittest.TestCase):
    def test_sorts_descending_by_in_corpus_citations(self):
        papers = [
            {"title": "Low", "citations_by_source": {"in_corpus": {"count": 5}}},
            {"title": "High", "citations_by_source": {"in_corpus": {"count": 500}}},
            {"title": "Mid", "citations_by_source": {"in_corpus": {"count": 50}}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual([p["title"] for p in result], ["High", "Mid", "Low"])

    def test_missing_or_zero_citations_sorts_last(self):
        papers = [
            {"title": "NoCitationsField"},
            {"title": "Cited", "citations_by_source": {"in_corpus": {"count": 10}}},
            {"title": "EmptySource", "citations_by_source": {}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual(result[0]["title"], "Cited")

    def test_does_not_use_the_dead_top_level_citations_field(self):
        # Confirmed on real data: papers_full.json's own "citations" field
        # is always None (0 of 25,639 AV papers have it set, including
        # nuScenes) -- only citations_by_source.in_corpus.count is real.
        papers = [
            {"title": "FakeHighCitations", "citations": 99999},
            {"title": "RealHighCitations", "citations_by_source": {"in_corpus": {"count": 100}}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual(result[0]["title"], "RealHighCitations")

    def test_ties_broken_by_title_for_determinism(self):
        papers = [
            {"title": "Zebra", "citations_by_source": {"in_corpus": {"count": 10}}},
            {"title": "Apple", "citations_by_source": {"in_corpus": {"count": 10}}},
        ]
        result = fc.by_citations(papers)
        self.assertEqual([p["title"] for p in result], ["Apple", "Zebra"])

    def test_accepts_a_generator(self):
        gen = ({"title": str(i), "citations_by_source": {"in_corpus": {"count": i}}} for i in range(3))
        result = fc.by_citations(gen)
        self.assertEqual([p["title"] for p in result], ["2", "1", "0"])


class TestS2Key(unittest.TestCase):
    def test_normalize_strips_whitespace_quotes_and_name(self):
        for raw in ("abc123", " abc123\n", '"abc123"', "'abc123'", ' " abc123 " ',
                    "SEMANTIC_SCHOLAR_API_KEY=abc123", 'SEMANTIC_SCHOLAR_API_KEY="abc123"\r'):
            self.assertEqual(fc.normalize_s2_key(raw), "abc123", raw)

    def test_normalize_leaves_inner_characters_alone(self):
        self.assertEqual(fc.normalize_s2_key("a'b\"c"), "a'b\"c")

    def test_normalize_empty_is_none(self):
        for raw in (None, "", "  ", '""', "SEMANTIC_SCHOLAR_API_KEY="):
            self.assertIsNone(fc.normalize_s2_key(raw), raw)

    def test_read_prefers_environment_then_env_file(self):
        with tempfile.TemporaryDirectory() as d:
            env_file = Path(d) / ".env"
            env_file.write_text('OTHER=1\nSEMANTIC_SCHOLAR_API_KEY="fromfile"\n', encoding="utf-8")
            self.assertEqual(fc.read_s2_key(env_file, {"SEMANTIC_SCHOLAR_API_KEY": " fromenv\n"}), "fromenv")
            self.assertEqual(fc.read_s2_key(env_file, {"SEMANTIC_SCHOLAR_API_KEY": "  "}), "fromfile")
            self.assertIsNone(fc.read_s2_key(Path(d) / "missing", {}))

    def test_fingerprint_does_not_contain_the_key(self):
        fp = fc.s2_key_fingerprint("supersecretkey")
        self.assertNotIn("supersecretkey", fp)
        self.assertIn("14 chars", fp)


class TestDescribeHttpError(unittest.TestCase):
    def test_includes_body_headers_and_url_without_query(self):
        e = s2_error(403)
        text = fc.describe_http_error(e)
        self.assertIn("HTTP 403", text)
        self.assertIn('{"message":"Forbidden"}', text)
        self.assertIn("x-amzn-errortype=ForbiddenException", text)
        self.assertIn("/graph/v1/paper/batch", text)
        self.assertNotIn("fields=x", text)
        # The body is kept, so a second description still has it.
        self.assertIn('{"message":"Forbidden"}', fc.describe_http_error(e))

    def test_body_is_cut_to_the_limit(self):
        e = s2_error(500, body=b"x" * 1000, errortype=None)
        self.assertEqual(fc.http_error_body(e), "x" * 300)


class TestS2BatchIds(unittest.TestCase):
    def test_well_formed_ids_pass(self):
        for ext in ["DOI:10.1109/CVPR52688.2022.01234", "DOI: https://doi.org/10.1007/978-3-031-19827-4_1 ",
                    "DOI:http://dx.doi.org/10.1177/02783649231234567", "ARXIV:2203.17270",
                    "ARXIV:1412.6980v9", "ARXIV:0704.0001", "ARXIV:hep-th/9901001",
                    "ARXIV:math.GT/0309136", "ARXIV:cs/0112017", "CorpusId:123"]:
            self.assertTrue(fc.looks_like_s2_id(ext), ext)

    def test_malformed_ids_fail(self):
        for ext in ["DOI:", "DOI:10.1/x", "DOI:doi 10.1109/x", "DOI:10.1109/has space",
                    "ARXIV:", "ARXIV:2203.172", "ARXIV:abs/2203.17270", "ARXIV:cs/01120"]:
            self.assertFalse(fc.looks_like_s2_id(ext), ext)

    def test_clean_doi(self):
        self.assertEqual(fc.clean_doi(" https://doi.org/10.1109/x "), "10.1109/x")
        self.assertEqual(fc.clean_doi("HTTPS://DX.DOI.ORG/10.1109/x"), "10.1109/x")

    def test_split_keeps_other_errors(self):
        def fetch(ids):
            raise s2_error(500, body=b"boom", errortype=None)

        with self.assertRaises(urllib.error.HTTPError):
            fc.s2_batch_split(fetch, ["ARXIV:2203.17270"], fc.new_s2_batch_counts(), log=lambda m: None)

    def test_summary_line(self):
        self.assertEqual(fc.describe_s2_batch_counts({"skipped": 2, "unknown": 5, "rejected": 1}),
                         "Semantic Scholar batch: 2 malformed ids skipped, 5 in batches it had no match "
                         "for, 1 rejected")


class TestS2Auth(unittest.TestCase):
    def test_forbidden_drops_the_key_once(self):
        logged = []
        auth = fc.S2Auth.start("k", log=logged.append)
        self.assertEqual(auth.headers(), {"x-api-key": "k"})
        self.assertEqual(auth.delay, fc.S2_KEYED_DELAY)
        self.assertTrue(auth.handle_forbidden(s2_error(403)))
        self.assertEqual(auth.headers(), {})
        self.assertEqual(auth.delay, fc.S2_ANONYMOUS_DELAY)
        self.assertFalse(auth.handle_forbidden(s2_error(403)))
        text = "\n".join(logged)
        self.assertIn("rejected the API key", text)
        self.assertIn("ForbiddenException", text)

    def test_without_a_key_nothing_to_drop(self):
        logged = []
        auth = fc.S2Auth.start(None, log=logged.append)
        self.assertEqual(auth.headers(), {})
        self.assertFalse(auth.handle_forbidden(s2_error(403)))
        self.assertIn("no API key", logged[0])


if __name__ == "__main__":
    unittest.main()
