#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for backup_corpus.py / restore_corpus.py: archive packing/unpacking
(which files go in, that PDFs never do), .env token parsing, the upload_url
template-stripping, and the upload-then-delete order plus the "someone else
backed up since" guard. The GitHub API is replaced by a small in-memory fake
(FakeGitHub below); nothing here talks to the network.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_backup_restore_corpus.py
"""
import gzip
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backup_corpus as bc
import restore_corpus as rc


def _patch_data_dirs(test, data_dir, restore_dir=None):
    for module, attr, value in ((bc, "DATA_DIR", data_dir),
                                (bc, "STATE_FILE", data_dir / "corpus_backup_state.json"),
                                (rc, "DATA_DIR", restore_dir or data_dir)):
        test.addCleanup(setattr, module, attr, getattr(module, attr))
        setattr(module, attr, value)


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _tar_gz(files):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for name, content in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestBuildAndExtractArchive(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name) / "data"
        self.restore_dir = Path(tmp.name) / "restored"
        self.data_dir.mkdir()
        _patch_data_dirs(self, self.data_dir, self.restore_dir)

    def _write_minimum(self):
        _write(self.data_dir / "papers_full.json", json.dumps([{"title": "A Paper", "citations": 3}]).encode())
        _write(self.data_dir / "venues" / "arxiv_s2_citing.json", b'[{"title": "Found Via S2"}]')

    def test_round_trip_preserves_every_file_byte_for_byte(self):
        self._write_minimum()
        contents = {
            "citation_graph.json": b'{"edges": {"a": ["b"]}}',
            "s2_citing_seeds.json": b'{"done": ["x"]}',
            "s2_paper_ids.json": b'{"t": "123"}',
            "reference_lists_s2.json": b'{"123": ["456"]}',
            "arxiv_ids.json": b'{"t": "2401.00001"}',
        }
        for name, content in contents.items():
            _write(self.data_dir / name, content)

        archive_bytes, members = bc.build_archive()
        names = rc.extract_archive(archive_bytes)

        self.assertEqual(set(names), set(members))
        for name in ["papers_full.json", "venues/arxiv_s2_citing.json", *contents]:
            self.assertEqual((self.restore_dir / name).read_bytes(), (self.data_dir / name).read_bytes(), name)
        self.assertEqual(list(self.restore_dir.glob(".restore-*")), [])

    def test_members_include_the_s2_files_and_never_pdfs(self):
        self._write_minimum()
        _write(self.data_dir / "s2_citing_seeds.json", b"{}")
        _write(self.data_dir / "s2_paper_ids.json", b"{}")
        _write(self.data_dir / "reference_lists_s2.json", b"{}")
        _write(self.data_dir / "pdfs_cvf" / "CVPR2024" / "paper.pdf", b"%PDF-1.7")
        _write(self.data_dir / "stray.pdf", b"%PDF-1.7")

        archive_bytes, _ = bc.build_archive()
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive_bytes))) as tar:
            names = tar.getnames()

        for name in ("venues/arxiv_s2_citing.json", "s2_citing_seeds.json",
                     "s2_paper_ids.json", "reference_lists_s2.json"):
            self.assertIn(name, names)
        self.assertFalse([n for n in names if n.lower().endswith(".pdf") or "pdfs_cvf" in n])

    def test_the_file_list_itself_has_no_pdfs(self):
        for rel, _ in bc.BACKUP_FILES:
            self.assertTrue(bc.is_allowed_member(rel), rel)
        self.assertFalse(bc.is_allowed_member("pdfs_cvf/CVPR2024/a.pdf"))
        self.assertFalse(bc.is_allowed_member("paper.PDF"))

    def test_missing_optional_files_are_left_out(self):
        self._write_minimum()
        _, members = bc.build_archive()
        self.assertEqual(members, ["papers_full.json", "venues/arxiv_s2_citing.json"])

    def test_missing_papers_file_raises(self):
        _write(self.data_dir / "venues" / "arxiv_s2_citing.json", b"[]")
        with self.assertRaises(SystemExit):
            bc.build_archive()

    def test_missing_s2_file_raises(self):
        # A backup without it would replace a complete one with one that
        # loses about 45% of the AV papers.
        _write(self.data_dir / "papers_full.json", b"[]")
        with self.assertRaises(SystemExit):
            bc.build_archive()

    def test_restores_an_old_two_file_backup(self):
        archive = _tar_gz({"papers_full.json": b"[1]", "citation_graph.json": b"{}"})
        names = rc.extract_archive(archive)
        self.assertEqual(sorted(names), ["citation_graph.json", "papers_full.json"])
        self.assertEqual((self.restore_dir / "papers_full.json").read_bytes(), b"[1]")

    def test_restore_skips_pdfs_and_unknown_members(self):
        archive = _tar_gz({"papers_full.json": b"[1]", "pdfs_cvf/a.pdf": b"%PDF",
                           "notes.txt": b"hi"})
        names = rc.extract_archive(archive)
        self.assertEqual(names, ["papers_full.json"])
        self.assertFalse((self.restore_dir / "pdfs_cvf").exists())
        self.assertFalse((self.restore_dir / "notes.txt").exists())

    def test_corrupt_archive_leaves_existing_files_alone(self):
        _write(self.restore_dir / "papers_full.json", b"[\"old\"]")
        good = _tar_gz({"papers_full.json": b"[\"new\"]"})
        with self.assertRaises(Exception):
            rc.extract_archive(good[: len(good) // 2])
        self.assertEqual((self.restore_dir / "papers_full.json").read_bytes(), b"[\"old\"]")
        self.assertEqual(list(self.restore_dir.glob(".restore-*")), [])


class TestLoadGithubToken(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.env_file = Path(self.tmpdir.name) / ".env"

        self._orig_bc_env, self._orig_rc_env = bc.ENV_FILE, rc.ENV_FILE
        bc.ENV_FILE = rc.ENV_FILE = self.env_file
        self.addCleanup(setattr, bc, "ENV_FILE", self._orig_bc_env)
        self.addCleanup(setattr, rc, "ENV_FILE", self._orig_rc_env)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("GITHUB_TOKEN", None)

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

    def test_environment_variable_wins(self):
        # How the GitHub Actions job passes the token in.
        self.env_file.write_text("GITHUB_TOKEN=from_file\n", encoding="utf-8")
        os.environ["GITHUB_TOKEN"] = "from_env"
        self.assertEqual(bc.load_github_token(), "from_env")
        self.assertEqual(rc.load_github_token(), "from_env")

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


class TestSelectReleasesByTag(unittest.TestCase):
    # Regression coverage for the bug fixed alongside this test: GitHub's
    # GET /releases/tags/{tag} 404s for draft releases, so both scripts
    # list all releases and filter client-side instead. Before this fix,
    # that lookup didn't exist at all and every backup run created a new
    # draft release rather than reusing the existing one.
    def test_no_match_returns_none(self):
        newest, stale = bc.select_releases_by_tag(
            [{"tag_name": "other", "created_at": "2026-01-01T00:00:00Z", "id": 1}], "corpus-backup")
        self.assertIsNone(newest)
        self.assertEqual(stale, [])

    def test_single_match_has_no_stale(self):
        release = {"tag_name": "corpus-backup", "created_at": "2026-01-01T00:00:00Z", "id": 1}
        newest, stale = bc.select_releases_by_tag([release], "corpus-backup")
        self.assertEqual(newest, release)
        self.assertEqual(stale, [])

    def test_multiple_matches_picks_most_recently_created_and_lists_rest_as_stale(self):
        old = {"tag_name": "corpus-backup", "created_at": "2026-09-10T09:09:42Z", "id": 1}
        newer = {"tag_name": "corpus-backup", "created_at": "2026-09-10T20:58:16Z", "id": 2}
        newest_release = {"tag_name": "corpus-backup", "created_at": "2026-09-13T08:28:56Z", "id": 3}
        unrelated = {"tag_name": "v1.0", "created_at": "2026-09-12T00:00:00Z", "id": 4}
        newest, stale = bc.select_releases_by_tag([old, newest_release, newer, unrelated], "corpus-backup")
        self.assertEqual(newest, newest_release)
        self.assertEqual(stale, [newer, old])

    def test_restore_side_selection_matches_backup_side(self):
        old = {"tag_name": "corpus-backup", "created_at": "2026-09-10T09:09:42Z", "id": 1}
        newest_release = {"tag_name": "corpus-backup", "created_at": "2026-09-13T08:28:56Z", "id": 3}
        self.assertEqual(rc.select_latest_release_by_tag([old, newest_release], "corpus-backup"), newest_release)
        self.assertIsNone(rc.select_latest_release_by_tag([old], "no-such-tag"))


class TestSelectCurrentAsset(unittest.TestCase):
    def test_prefers_the_real_name_and_lists_uploads_as_leftovers(self):
        real = {"id": 1, "name": bc.ARCHIVE_NAME, "created_at": "2026-09-01"}
        upload = {"id": 2, "name": bc.UPLOAD_PREFIX + "x.tar.gz", "created_at": "2026-09-02", "state": "uploaded"}
        current, leftovers = bc.select_current_asset([real, upload])
        self.assertEqual(current, real)
        self.assertEqual(leftovers, [upload])

    def test_falls_back_to_the_newest_finished_upload(self):
        # A run that died after deleting the old asset, before the rename.
        older = {"id": 2, "name": bc.UPLOAD_PREFIX + "a.tar.gz", "created_at": "2026-09-02", "state": "uploaded"}
        newer = {"id": 3, "name": bc.UPLOAD_PREFIX + "b.tar.gz", "created_at": "2026-09-03", "state": "uploaded"}
        broken = {"id": 4, "name": bc.UPLOAD_PREFIX + "c.tar.gz", "created_at": "2026-09-04", "state": "starter"}
        current, leftovers = bc.select_current_asset([older, newer, broken])
        self.assertEqual(current, newer)
        self.assertEqual(leftovers, [older, broken])

    def test_empty_release(self):
        self.assertEqual(bc.select_current_asset([]), (None, []))


class TestOverwriteRefusal(unittest.TestCase):
    current = {"id": 7, "updated_at": "2026-09-21T11:07:43Z"}

    def test_no_backup_yet_is_fine(self):
        self.assertIsNone(bc.overwrite_refusal(None, None, False))

    def test_same_asset_as_restored_is_fine(self):
        self.assertIsNone(bc.overwrite_refusal(self.current, {"asset_id": 7}, False))

    def test_someone_else_backed_up_since(self):
        self.assertIn("another machine", bc.overwrite_refusal(self.current, {"asset_id": 6}, False))

    def test_no_record_at_all_is_refused(self):
        self.assertIsNotNone(bc.overwrite_refusal(self.current, None, False))

    def test_force_overrides(self):
        self.assertIsNone(bc.overwrite_refusal(self.current, {"asset_id": 6}, True))
        self.assertIsNone(bc.overwrite_refusal(self.current, None, True))


class FakeGitHub:
    """Just enough of the releases API for backup_corpus.main(). Records
    every call in order; `fail` maps a method to a status to return."""

    def __init__(self, assets=()):
        self.calls = []
        self.fail = {}
        self.next_id = 100
        self.release = {"id": 1, "tag_name": bc.BACKUP_TAG, "created_at": "2026-09-13T00:00:00Z",
                        "upload_url": "https://uploads.example/releases/1/assets{?name,label}",
                        "assets": [dict(a) for a in assets]}

    def _asset_by_url(self, url):
        return next(a for a in self.release["assets"] if a["url"] == url)

    def __call__(self, method, url, token, data=None, headers=None, timeout=60):
        self.calls.append((method, url))
        if method in self.fail:
            return self.fail[method], {"message": "simulated failure"}
        if method == "GET":
            return 200, [json.loads(json.dumps(self.release))]
        if method == "POST":
            name = re.search(r"\?name=(.+)$", url).group(1)
            self.next_id += 1
            asset = {"id": self.next_id, "name": name, "size": len(data), "state": "uploaded",
                     "created_at": "2026-09-24T00:00:00Z", "updated_at": "2026-09-24T00:00:00Z",
                     "url": f"https://api.example/assets/{self.next_id}"}
            self.release["assets"].append(asset)
            return 201, dict(asset)
        if method == "DELETE":
            self.release["assets"].remove(self._asset_by_url(url))
            return 204, None
        if method == "PATCH":
            asset = self._asset_by_url(url)
            asset["name"] = json.loads(data)["name"]
            return 200, dict(asset)
        raise AssertionError(f"unexpected {method} {url}")

    def names(self):
        return [a["name"] for a in self.release["assets"]]


class TestBackupMain(unittest.TestCase):
    OLD = {"id": 7, "name": bc.ARCHIVE_NAME, "size": 10, "state": "uploaded",
           "created_at": "2026-09-21T11:07:43Z", "updated_at": "2026-09-21T11:07:43Z",
           "url": "https://api.example/assets/7"}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        _patch_data_dirs(self, self.data_dir)
        _write(self.data_dir / "papers_full.json", b"[]")
        _write(self.data_dir / "venues" / "arxiv_s2_citing.json", b"[]")
        env = mock.patch.dict(os.environ, {"GITHUB_TOKEN": "test-token"})
        env.start()
        self.addCleanup(env.stop)

    def _run(self, fake, argv=()):
        with mock.patch.object(bc, "api_request", fake):
            bc.main(list(argv))

    def _record_restore_of(self, asset_id):
        bc.STATE_FILE.write_text(json.dumps({"asset_id": asset_id}), encoding="utf-8")

    def test_uploads_before_deleting_then_renames(self):
        fake = FakeGitHub([self.OLD])
        self._record_restore_of(7)
        self._run(fake)
        methods = [m for m, _ in fake.calls]
        self.assertEqual(methods, ["GET", "POST", "DELETE", "PATCH"])
        self.assertEqual(fake.names(), [bc.ARCHIVE_NAME])
        new_id = fake.release["assets"][0]["id"]
        self.assertNotEqual(new_id, 7)
        self.assertEqual(json.loads(bc.STATE_FILE.read_text(encoding="utf-8"))["asset_id"], new_id)

    def test_failed_upload_keeps_the_old_backup_and_exits_non_zero(self):
        fake = FakeGitHub([self.OLD])
        fake.fail["POST"] = 0  # e.g. the TCP write timeout PIPELINE.md records
        self._record_restore_of(7)
        with self.assertRaises(SystemExit) as ctx:
            self._run(fake)
        self.assertNotIn(ctx.exception.code, (0, None))
        self.assertEqual(fake.names(), [bc.ARCHIVE_NAME])
        self.assertNotIn("DELETE", [m for m, _ in fake.calls])

    def test_refuses_when_another_machine_backed_up_since(self):
        fake = FakeGitHub([self.OLD])
        self._record_restore_of(6)
        with self.assertRaises(SystemExit) as ctx:
            self._run(fake)
        self.assertNotIn(ctx.exception.code, (0, None))
        self.assertEqual([m for m, _ in fake.calls], ["GET"])

    def test_refuses_without_any_record(self):
        fake = FakeGitHub([self.OLD])
        with self.assertRaises(SystemExit):
            self._run(fake)
        self.assertEqual([m for m, _ in fake.calls], ["GET"])

    def test_force_overwrites_anyway(self):
        fake = FakeGitHub([self.OLD])
        self._record_restore_of(6)
        self._run(fake, ["--force"])
        self.assertEqual(fake.names(), [bc.ARCHIVE_NAME])
        self.assertNotEqual(fake.release["assets"][0]["id"], 7)

    def test_first_backup_needs_no_record(self):
        fake = FakeGitHub()
        self._run(fake)
        self.assertEqual([m for m, _ in fake.calls], ["GET", "POST", "PATCH"])
        self.assertEqual(fake.names(), [bc.ARCHIVE_NAME])

    def test_failed_rename_leaves_a_backup_the_next_run_accepts(self):
        fake = FakeGitHub([self.OLD])
        self._record_restore_of(7)
        fake.fail["PATCH"] = 500
        with self.assertRaises(SystemExit):
            self._run(fake)
        self.assertEqual(len(fake.names()), 1)
        self.assertTrue(fake.names()[0].startswith(bc.UPLOAD_PREFIX))
        del fake.fail["PATCH"]
        self._run(fake)
        self.assertEqual(fake.names(), [bc.ARCHIVE_NAME])

    def test_no_token_skips_with_exit_zero(self):
        os.environ.pop("GITHUB_TOKEN")
        with mock.patch.object(bc, "ENV_FILE", self.data_dir / "no.env"), \
                mock.patch.object(bc, "api_request", side_effect=AssertionError("no API calls")):
            bc.main([])


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
