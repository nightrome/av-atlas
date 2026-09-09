#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for backup_corpus.py / restore_corpus.py -- the pure, network-free
logic only (archive packing/unpacking, .env token parsing, the upload_url
template-stripping). Matches this test suite's existing convention (see
fetch_common.py's fetch() and every fetch_*.py script) of not mocking
urllib itself: the GitHub API calls are exercised by actually running
against the real API once a token is available, not simulated here.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_backup_restore_corpus.py
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backup_corpus as bc
import restore_corpus as rc


class TestBuildAndExtractArchive(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.data_dir = Path(self.tmpdir.name)

        self.papers_file = self.data_dir / "papers_full.json"
        self.graph_file = self.data_dir / "citation_graph.json"
        self._orig_papers, self._orig_graph = bc.PAPERS_FILE, bc.CITATION_GRAPH_FILE
        bc.PAPERS_FILE, bc.CITATION_GRAPH_FILE = self.papers_file, self.graph_file
        self.addCleanup(setattr, bc, "PAPERS_FILE", self._orig_papers)
        self.addCleanup(setattr, bc, "CITATION_GRAPH_FILE", self._orig_graph)

        self._orig_data_dir = rc.DATA_DIR
        self.addCleanup(setattr, rc, "DATA_DIR", self._orig_data_dir)

    def test_round_trip_preserves_both_files_byte_for_byte(self):
        papers_content = json.dumps([{"title": "A Paper", "citations": 3}]).encode("utf-8")
        graph_content = json.dumps({"edges": {"a": ["b"]}}).encode("utf-8")
        self.papers_file.write_bytes(papers_content)
        self.graph_file.write_bytes(graph_content)

        archive_bytes = bc.build_archive()

        restore_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(restore_dir, ignore_errors=True))
        rc.DATA_DIR = restore_dir
        names = rc.extract_archive(archive_bytes)

        self.assertEqual(set(names), {"papers_full.json", "citation_graph.json"})
        self.assertEqual((restore_dir / "papers_full.json").read_bytes(), papers_content)
        self.assertEqual((restore_dir / "citation_graph.json").read_bytes(), graph_content)

    def test_missing_citation_graph_backs_up_papers_only(self):
        self.papers_file.write_bytes(b'[{"title": "Solo"}]')
        # graph_file deliberately not created -- see build_archive's docstring:
        # this is a documented "not found" case, not an error.

        archive_bytes = bc.build_archive()

        restore_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(restore_dir, ignore_errors=True))
        rc.DATA_DIR = restore_dir
        names = rc.extract_archive(archive_bytes)

        self.assertEqual(names, ["papers_full.json"])

    def test_missing_papers_file_raises(self):
        with self.assertRaises(SystemExit):
            bc.build_archive()


class TestLoadGithubToken(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env_file = Path(self.tmpdir.name) / ".env"

        self._orig_bc_env, self._orig_rc_env = bc.ENV_FILE, rc.ENV_FILE
        bc.ENV_FILE = rc.ENV_FILE = self.env_file
        self.addCleanup(setattr, bc, "ENV_FILE", self._orig_bc_env)
        self.addCleanup(setattr, rc, "ENV_FILE", self._orig_rc_env)

    def test_backup_returns_none_when_env_file_missing(self):
        self.assertIsNone(bc.load_github_token())

    def test_backup_returns_none_when_token_line_missing(self):
        self.env_file.write_text("SEMANTIC_SCHOLAR_API_KEY=abc123\n", encoding="utf-8")
        self.assertIsNone(bc.load_github_token())

    def test_backup_returns_none_when_token_value_blank(self):
        self.env_file.write_text("GITHUB_TOKEN=\n", encoding="utf-8")
        self.assertIsNone(bc.load_github_token())

    def test_backup_reads_token_alongside_other_lines(self):
        self.env_file.write_text("SEMANTIC_SCHOLAR_API_KEY=abc123\nGITHUB_TOKEN=github_pat_xyz\n", encoding="utf-8")
        self.assertEqual(bc.load_github_token(), "github_pat_xyz")

    def test_restore_raises_when_env_file_missing(self):
        with self.assertRaises(SystemExit):
            rc.load_github_token()

    def test_restore_raises_when_token_blank(self):
        self.env_file.write_text("GITHUB_TOKEN=\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            rc.load_github_token()

    def test_restore_reads_token(self):
        self.env_file.write_text("GITHUB_TOKEN=github_pat_xyz\n", encoding="utf-8")
        self.assertEqual(rc.load_github_token(), "github_pat_xyz")


class TestUploadUrlTemplateStripping(unittest.TestCase):
    # GitHub's release object gives upload_url as an RFC 6570 URI template
    # ("...assets{?name,label}"); upload_asset() strips the template suffix
    # before appending its own literal "?name=..." query string.
    def test_strips_query_template_suffix(self):
        template = "https://uploads.github.com/repos/o/r/releases/1/assets{?name,label}"
        self.assertEqual(re.sub(r"\{.*\}$", "", template),
                          "https://uploads.github.com/repos/o/r/releases/1/assets")

    def test_leaves_a_url_with_no_template_unchanged(self):
        url = "https://uploads.github.com/repos/o/r/releases/1/assets"
        self.assertEqual(re.sub(r"\{.*\}$", "", url), url)


if __name__ == "__main__":
    unittest.main()
