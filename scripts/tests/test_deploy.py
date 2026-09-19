#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for deploy.py's pure helpers (preview transform, tree hashing, mirror
sync) plus one end-to-end publish against a local bare repo standing in for
the staging remote -- no network involved.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deploy


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


PAGE = ('<html><head>\n<link rel="canonical" href="https://nightrome.github.io/av-atlas/index.html">'
        '</head><body>hi</body></html>')


class PreviewTransformTests(unittest.TestCase):
    def test_html_gets_noindex_banner_and_staging_urls(self):
        out = deploy.make_preview_html(PAGE, "https://nightrome.github.io/av-atlas", "https://x.io/stage")
        self.assertIn('<meta name="robots" content="noindex,nofollow">', out)
        self.assertIn("PREVIEW BUILD", out)
        self.assertIn("https://x.io/stage/index.html", out)
        self.assertNotIn("nightrome.github.io/av-atlas/index.html", out)

    def test_robots_disallows_everything(self):
        self.assertEqual(deploy.make_preview_bytes("robots.txt", b"Allow: /"), b"User-agent: *\nDisallow: /\n")

    def test_sitemap_urls_rewritten(self):
        out = deploy.make_preview_bytes("sitemap.xml", f"<loc>{deploy.PROD_URL}/a</loc>".encode())
        self.assertIn(deploy.STAGING_URL.encode(), out)


class TreeTests(unittest.TestCase):
    def test_hash_ignores_mtime_and_build_info_but_sees_content(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "a.json").write_text("1")
            h1 = deploy.hash_tree(root)
            (root / deploy.BUILD_INFO_NAME).write_text("whatever")
            (root / "a.json").touch()
            self.assertEqual(deploy.hash_tree(root), h1)
            (root / "a.json").write_text("2")
            self.assertNotEqual(deploy.hash_tree(root), h1)

    def test_sync_copies_changes_removes_stale_and_keeps_git(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as d:
            src, dst = Path(s), Path(d)
            (src / "sub").mkdir()
            (src / "sub" / "x.json").write_text("data")
            (src / "index.html").write_text(PAGE)
            (dst / ".git").mkdir()
            (dst / ".git" / "HEAD").write_text("ref")
            (dst / "old.json").write_text("stale")
            written, removed = deploy.sync_tree(src, dst)
            self.assertEqual((written, removed), (2, 1))
            self.assertTrue((dst / ".git" / "HEAD").exists())
            self.assertFalse((dst / "old.json").exists())
            # Second run is a no-op: nothing re-written.
            self.assertEqual(deploy.sync_tree(src, dst), (0, 0))

    def test_sync_transform_applies_to_html_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as d:
            src, dst = Path(s), Path(d)
            (src / "index.html").write_text(PAGE)
            (src / "stats.json").write_text("{}")
            deploy.sync_tree(src, dst, deploy.make_preview_bytes)
            self.assertIn("PREVIEW BUILD", (dst / "index.html").read_text())
            self.assertEqual((dst / "stats.json").read_text(), "{}")
            self.assertEqual(deploy.sync_tree(src, dst, deploy.make_preview_bytes), (0, 0))


class PublishEndToEndTests(unittest.TestCase):
    def test_publish_force_pushes_single_commit_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
            public = root / "public"
            public.mkdir()
            (public / "index.html").write_text(PAGE)
            (public / "stats.json").write_text('{"v": 1}')
            repo = root / "repo"
            repo.mkdir()
            git("init", "--quiet", cwd=repo)
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "x", cwd=repo)

            with mock.patch.object(deploy, "PUBLIC_DIR", public), \
                 mock.patch.object(deploy, "CACHE_DIR", root / "cache"), \
                 mock.patch.object(deploy, "BASE", repo):
                h1 = deploy.publish("staging", str(remote), deploy.make_preview_bytes, "staging", "one")
                (public / "stats.json").write_text('{"v": 2}')
                h2 = deploy.publish("staging", str(remote), deploy.make_preview_bytes, "staging", "two")

            self.assertNotEqual(h1, h2)
            # Every publish is a parentless commit: history never accumulates.
            self.assertEqual(git("rev-list", "--count", "gh-pages", cwd=remote), "1")
            self.assertEqual(git("log", "-1", "--format=%s", "gh-pages", cwd=remote), "two")
            self.assertIn('"v": 2', git("show", "gh-pages:stats.json", cwd=remote))
            self.assertIn("PREVIEW BUILD", git("show", "gh-pages:index.html", cwd=remote))
            self.assertIn(h2, git("show", f"gh-pages:{deploy.BUILD_INFO_NAME}", cwd=remote))


if __name__ == "__main__":
    unittest.main()
