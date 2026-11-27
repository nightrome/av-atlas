#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for build_data_release.py: the release files are named with the site
version, and only the current version's files are left in the output folder.

Usage: python -m unittest discover -s scripts/tests
"""
import csv
import gzip
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_data_release as bdr

STATS = {
    "all_papers": [
        {"title": "Paper A", "year": 2024, "venue": "CVPR", "citations": 1,
         "authors": ["Ann", "Bob"], "institutions": ["TU Delft"], "countries": ["Netherlands"]},
        {"title": "Paper B", "year": 2025, "venue": "ICRA", "citations": 0, "authors": ["Bob"]},
    ],
    "institution_countries": {"TU Delft": "Netherlands"},
}
GRAPH = {"edges": {"paperb": ["papera", "notinthecorpus"]}}


def build(out_dir, version):
    with redirect_stdout(io.StringIO()):
        bdr.build(STATS, GRAPH, version, out_dir=out_dir)


def read_csv(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.reader(fh))


class ReleaseTests(unittest.TestCase):
    def test_file_names_carry_the_version(self):
        self.assertEqual(bdr.release_file_names("0.1.2"), [
            "av-atlas-v0.1.2-papers.csv.gz",
            "av-atlas-v0.1.2-authorship.csv.gz",
            "av-atlas-v0.1.2-citations.csv.gz",
            "av-atlas-v0.1.2-institutions.csv.gz",
            "av-atlas-v0.1.2-authors.csv.gz",
            "av-atlas-v0.1.2-paper_versions.csv.gz",
            "av-atlas-v0.1.2-similar_papers.csv.gz",
            "av-atlas-v0.1.2-embeddings.npy",
            "av-atlas-v0.1.2-embeddings_paper_ids.csv.gz",
            "av-atlas-v0.1.2-README.md",
        ])

    def test_writes_exactly_the_versioned_files(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            build(out, "0.1.2")
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             sorted(bdr.release_file_names("0.1.2")))
            self.assertEqual(len(read_csv(out / "av-atlas-v0.1.2-papers.csv.gz")), 3)
            self.assertEqual(read_csv(out / "av-atlas-v0.1.2-citations.csv.gz")[1:], [["paperb", "papera"]])
            readme = (out / "av-atlas-v0.1.2-README.md").read_text(encoding="utf-8")
            self.assertIn("Version 0.1.2,", readme)
            self.assertIn("`av-atlas-v0.1.2-papers.csv.gz`", readme)

    def test_authors_versions_similar_and_embeddings(self):
        stats = {"all_papers": [
            {"title": "Paper A", "year": 2024, "venue": "CVPR", "citations": 4, "authors": ["Ann", "Bob"],
             "also_published_as": [{"title": "Paper A preprint", "venue": "arXiv", "year": 2023,
                                    "url": "https://arxiv.org/abs/1"}]},
            {"title": "Paper B", "year": "2025", "venue": "ICRA", "citations": 1, "authors": ["Bob"]},
        ]}
        detail = {"Bob": {"orcid": "0000-0001-0000-0001",
                          "orcids": [{"orcid": "0000-0001-0000-0001", "papers": 2},
                                     {"orcid": "0000-0002-0000-0002", "papers": 1}],
                          "institutions": [{"name": "TU Delft"}], "countries": ["Netherlands"]}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "similar").mkdir()
            (root / "similar" / "shard-00.json").write_text(
                '{"Paper A": [["Paper B", 0.8], ["Not in corpus", 0.7]]}', encoding="utf-8")
            (root / "emb").mkdir()
            (root / "emb" / "keys.json").write_text('["Paper A", "Paper B"]', encoding="utf-8")
            (root / "emb" / "int8_512.npy").write_bytes(bdr.empty_npy(512))
            out = root / "out"
            with redirect_stdout(io.StringIO()):
                bdr.build(stats, {}, "1.0.0", out_dir=out, author_detail=detail,
                          similar_dir=root / "similar", embeddings_dir=root / "emb")
            authors = read_csv(out / "av-atlas-v1.0.0-authors.csv.gz")
            self.assertEqual(authors[0][:3], ["author_name", "orcid", "other_orcids"])
            bob = [r for r in authors if r[0] == "Bob"][0]
            self.assertEqual(bob[1:7], ["0000-0001-0000-0001", "0000-0002-0000-0002", "2", "5", "2024", "2025"])
            self.assertEqual(bob[7:9], ["TU Delft", "Netherlands"])
            self.assertEqual(read_csv(out / "av-atlas-v1.0.0-paper_versions.csv.gz")[1],
                             ["papera", "Paper A preprint", "arXiv", "2023", "https://arxiv.org/abs/1"])
            self.assertEqual(read_csv(out / "av-atlas-v1.0.0-similar_papers.csv.gz")[1:],
                             [["papera", "1", "paperb", "0.8"]])
            self.assertEqual(read_csv(out / "av-atlas-v1.0.0-embeddings_paper_ids.csv.gz")[1:],
                             [["0", "papera", "Paper A"], ["1", "paperb", "Paper B"]])
            self.assertTrue((out / "av-atlas-v1.0.0-embeddings.npy").read_bytes().startswith(bytes([0x93]) + b"NUMPY"))

    def test_no_stale_files_from_an_older_version_or_the_unversioned_names(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            (out / "av-atlas-papers.csv.gz").write_bytes(b"old")
            (out / "README.md").write_text("old", encoding="utf-8")
            build(out, "0.1.2")
            build(out, "0.1.3")
            self.assertEqual(sorted(p.name for p in out.iterdir()),
                             sorted(bdr.release_file_names("0.1.3")))


if __name__ == "__main__":
    unittest.main()
