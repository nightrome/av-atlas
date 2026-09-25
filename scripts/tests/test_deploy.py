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

    def test_untracked_and_ignored_inputs_change_the_fingerprint(self):
        # These are all real build inputs that git doesn't track: the S2
        # citing dump and the citation graph are gitignored, and a venue
        # file a fetcher just wrote isn't added yet.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            (root / ".gitignore").write_text("data/citation_graph.json\ndata/venues/arxiv_s2_citing.json\n")
            (root / "data" / "citation_graph.json").write_text("{}")
            (root / "data" / "venues" / "arxiv_s2_citing.json").write_text("[]")
            fp = deploy.build_fingerprint(root)
            for path, text in (("data/venues/arxiv_s2_citing.json", '[{"title": "x"}]'),
                               ("data/citation_graph.json", '{"edges": {}}'),
                               ("data/venues/cvpr2027.json", "[]"),
                               ("data/reference_lists_s2.json", "{}")):
                new = self.fp_after(root, path, text)
                self.assertNotEqual(new, fp, path)
                fp = new

    def test_build_outputs_and_pdfs_dont_change_the_fingerprint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.make_repo(root)
            for sub in ("abstracts", "non_av_papers", "pdfs_cvf"):
                (root / "data" / sub).mkdir()
            base = deploy.build_fingerprint(root)
            for path in ("data/stats.json", "data/stats_non_av.json", "data/publish_gate_baseline.json",
                         "data/abstracts/shard-00.json", "data/non_av_papers/shard-00.json",
                         "data/pdfs_cvf/somepaper.pdf", "data/pdfs_cvf/x.json"):
                self.assertEqual(self.fp_after(root, path, "{}"), base, path)

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

    def test_real_build_rematches_and_applies_citations_before_aggregate(self):
        steps = deploy.pipeline_steps()
        self.assertLess(steps.index("merge_corpus.py"), steps.index("build_citation_graph.py"))
        self.assertLess(steps.index("build_citation_graph.py"), steps.index("apply_citation_sources.py"))
        self.assertLess(steps.index("apply_citation_sources.py"), steps.index("aggregate.py"))


class BuildModeTests(unittest.TestCase):
    """--allow-shrink reaches build_public_site.py in every build mode."""

    def build_calls(self, mode, allow_shrink, fingerprint_matches=False):
        calls = []
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "data").mkdir()
            (Path(d) / "data" / "stats.json").write_text("{}")
            with mock.patch.object(deploy, "BASE", Path(d)), \
                 mock.patch.object(deploy.subprocess, "run", side_effect=lambda cmd, **kw: calls.append(cmd)), \
                 mock.patch.object(deploy, "load_state", return_value={"build_fingerprint": "fp"}), \
                 mock.patch.object(deploy, "build_fingerprint",
                                   return_value="fp" if fingerprint_matches else "other"), \
                 mock.patch.object(deploy, "save_state"):
                deploy.build(mode, allow_shrink=allow_shrink)
        return [c for c in calls if "build_public_site.py" in c]

    def test_allow_shrink_is_passed_through(self):
        for mode, matches in (("full", False), ("skip", False), ("auto", False), ("auto", True)):
            (cmd,) = self.build_calls(mode, True, matches)
            self.assertIn("--allow-shrink", cmd, (mode, matches))
            (cmd,) = self.build_calls(mode, False, matches)
            self.assertNotIn("--allow-shrink", cmd, (mode, matches))
        # An unchanged fingerprint still only re-publishes.
        self.assertIn("--publish-only", self.build_calls("auto", False, True)[0])


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
