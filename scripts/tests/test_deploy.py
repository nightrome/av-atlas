#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for deploy.py's pure helpers (preview transform, tree hashing, mirror
sync) plus one end-to-end publish against a local bare repo standing in for
the staging remote -- no network involved.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
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

    def test_sync_drops_the_previous_versions_download_files(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as d:
            src, dst = Path(s), Path(d)
            (src / "download").mkdir()
            (src / "download" / "av-atlas-v0.1.3-papers.csv.gz").write_bytes(b"new")
            (dst / "download").mkdir()
            (dst / "download" / "av-atlas-v0.1.2-papers.csv.gz").write_bytes(b"old")
            (dst / "download" / "av-atlas-papers.csv.gz").write_bytes(b"older")
            deploy.sync_tree(src, dst)
            self.assertEqual(sorted(p.name for p in (dst / "download").iterdir()),
                             ["av-atlas-v0.1.3-papers.csv.gz"])

    def test_sync_transform_applies_to_html_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as s, tempfile.TemporaryDirectory() as d:
            src, dst = Path(s), Path(d)
            (src / "index.html").write_text(PAGE)
            (src / "stats.json").write_text("{}")
            deploy.sync_tree(src, dst, deploy.make_preview_bytes)
            self.assertIn("PREVIEW BUILD", (dst / "index.html").read_text())
            self.assertEqual((dst / "stats.json").read_text(), "{}")
            self.assertEqual(deploy.sync_tree(src, dst, deploy.make_preview_bytes), (0, 0))


class FingerprintTests(unittest.TestCase):
    """The corpus rebuild is skipped only when nothing that feeds it changed."""

    STEPS = ('    run_step("Rebuilding corpus", "merge_corpus.py")\n'
             '    run_step("Rebuilding stats", "aggregate.py")\n')

    def make_repo(self, root):
        (root / "scripts").mkdir()
        (root / "data" / "venues").mkdir(parents=True)
        for name in ("merge_corpus.py", "aggregate.py", "deploy.py", "run_tests.py",
                     "backup_corpus.py", "build_data_release.py"):
            (root / "scripts" / name).write_text("# original\n")
        (root / "scripts" / "build_public_site.py").write_text("def main():\n" + self.STEPS)
        (root / "data" / "papers_full.json").write_text("{}")
        (root / "data" / "venues" / "cvpr.json").write_text("[]")
        git("init", "--quiet", cwd=root)
        git("add", "data/venues/cvpr.json", cwd=root)

    def fp_after(self, root, path, text):
        (root / path).write_text(text)
        return deploy.build_fingerprint(root)

    def test_only_corpus_inputs_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            base = deploy.build_fingerprint(root)

            # Scripts that can't affect stats.json: no change.
            for name in ("deploy.py", "run_tests.py", "backup_corpus.py", "build_data_release.py"):
                self.assertEqual(self.fp_after(root, f"scripts/{name}", "# edited, and longer\n"), base, name)
            # Editing the rest of build_public_site.py (not its step list): no change.
            self.assertEqual(self.fp_after(
                root, "scripts/build_public_site.py",
                "# a new comment\ndef main():\n" + self.STEPS + "    copy_pages()\n"), base)

            # A pipeline script, a new script, tracked data, papers_full.json: change.
            self.assertNotEqual(self.fp_after(root, "scripts/merge_corpus.py", "# edited, and longer\n"), base)
            fp = deploy.build_fingerprint(root)
            self.assertNotEqual(self.fp_after(root, "scripts/repair_new.py", "# new\n"), fp)
            fp = deploy.build_fingerprint(root)
            self.assertNotEqual(self.fp_after(root, "data/venues/cvpr.json", '[{"title": "x"}]'), fp)
            fp = deploy.build_fingerprint(root)
            self.assertNotEqual(self.fp_after(root, "data/papers_full.json", '{"a": 1}'), fp)

    def test_changing_the_pipeline_step_list_changes_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            base = deploy.build_fingerprint(root)
            added = "def main():\n" + self.STEPS + '    run_step("Repair", "repair_new.py")\n'
            self.assertNotEqual(self.fp_after(root, "scripts/build_public_site.py", added), base)
            reordered = "def main():\n" + "".join(reversed(self.STEPS.splitlines(True)))
            self.assertNotEqual(self.fp_after(root, "scripts/build_public_site.py", reordered), base)

    def test_pipeline_steps_reads_script_names_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            self.assertEqual(deploy.pipeline_steps(root), ["merge_corpus.py", "aggregate.py"])
            self.assertEqual(deploy.pipeline_steps(root / "nowhere"), [])


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

    def test_publish_keeps_the_version_the_build_assigned(self):
        # build_public_site.py writes the version into public/BUILD_INFO.json.
        # Publishing (preview or promote) must carry it over, not work out a
        # new one, and a production deploy records it for the offline fallback.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
            public = root / "public"
            public.mkdir()
            (public / "index.html").write_text(PAGE)
            (public / deploy.BUILD_INFO_NAME).write_text(
                json.dumps({"version": "0.1.2", "built_at": "2026-09-24T10:00:00+0200"}))
            repo = root / "repo"
            repo.mkdir()
            git("init", "--quiet", cwd=repo)
            git("remote", "add", "origin", str(remote), cwd=repo)
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "x", cwd=repo)

            cache = root / "cache"
            with mock.patch.object(deploy, "PUBLIC_DIR", public), \
                 mock.patch.object(deploy, "CACHE_DIR", cache), \
                 mock.patch.object(deploy, "STATE_FILE", cache / "state.json"), \
                 mock.patch.object(deploy, "BASE", repo):
                deploy.deploy_staging(str(remote))
                staged = json.loads(git("show", f"gh-pages:{deploy.BUILD_INFO_NAME}", cwd=remote))
                deploy.deploy_production()
                live = json.loads(git("show", f"gh-pages:{deploy.BUILD_INFO_NAME}", cwd=remote))
                state = deploy.load_state()

            self.assertEqual(staged["version"], "0.1.2")
            self.assertEqual(live["version"], "0.1.2")
            self.assertEqual(live["built_at"], "2026-09-24T10:00:00+0200")
            self.assertEqual(state["published_version"], "0.1.2")
            self.assertEqual(git("log", "-1", "--format=%s", "gh-pages", cwd=remote), "Publish AV Atlas v0.1.2")


if __name__ == "__main__":
    unittest.main()
