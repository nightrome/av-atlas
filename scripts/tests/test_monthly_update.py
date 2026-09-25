#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for monthly_update.py: the step order and failure policy with every
script mocked out, the time budgets with a fake clock, the carry-forward
and commit/push of the bot branch against a local bare repo, the failure
issue with a fake gh, and deploy.py publishing from a fresh checkout (no
.deploy-cache) to a remote whose gh-pages already has content. No network.

Usage: python -m unittest discover -s av-atlas/scripts/tests
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import deploy
import monthly_update as mu


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def commit_all(repo, msg):
    git("add", "-A", cwd=repo)
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--quiet", "-m", msg, cwd=repo)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class ScriptedOrchestrator(mu.Orchestrator):
    """Every script and git step replaced: `outcomes` maps a script name to
    (returncode, timed_out, minutes it takes)."""

    missing = ()

    def __init__(self, work_dir, outcomes=None, **kw):
        self.clock_ = FakeClock()
        super().__init__(work_dir, clock=self.clock_, **kw)
        self.outcomes = outcomes or {}
        self.calls = []
        self.envs = {}
        self.budgets = {}

    def run_cmd(self, argv, timeout_min, env):
        script = Path(argv[1]).name
        self.calls.append(script)
        self.envs[script] = env
        self.budgets[script] = timeout_min
        code, timed_out, minutes = self.outcomes.get(script, (0, False, 1))
        self.clock_.t += min(minutes, timeout_min) * 60
        return code, timed_out

    def script_exists(self, name):
        return name not in self.missing

    def git(self, *args, check=True):
        return "" if check else subprocess.CompletedProcess(args, 1, "", "")

    def ensure_env_file(self):
        pass

    def carry_forward(self):
        self.calls.append("carry_forward()")
        return "ok", ""

    def fetch_previous_stats(self):
        self.calls.append("fetch_previous_stats()")
        return "ok", ""

    def commit_data(self):
        self.calls.append("commit_data()")
        self.committed = ["data/venues/x.json"]
        return "ok", ""

    def open_pr(self):
        self.calls.append("open_pr()")
        return "ok", ""

    def file_counts(self, paths):
        return [(p, None, 1) for p in paths]


class StepOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_run_order(self):
        orch = ScriptedOrchestrator(self.work)
        self.assertEqual(orch.run(), 0)
        self.assertEqual(orch.calls, [
            "restore_corpus.py", "carry_forward()", "fetch_previous_stats()", "fetch_arxiv_monthly.py", "check_new_editions.py",
            "fetch_crossref.py", "fix_suspect_venues.py", "merge_corpus.py",
            "fetch_abstracts_semanticscholar.py", "apply_abstracts_semanticscholar.py",
            "fetch_s2_references.py", "build_public_site.py", "commit_data()", "deploy.py",
            "backup_corpus.py", "open_pr()"])
        summary = (self.work / "summary.md").read_text(encoding="utf-8")
        self.assertIn("| publish | ok |", summary)
        self.assertIn("data/venues/x.json", summary)

    def test_soft_failure_still_publishes_but_exits_1(self):
        orch = ScriptedOrchestrator(self.work, {"fetch_arxiv_monthly.py": (1, False, 1)})
        self.assertEqual(orch.run(), 1)
        self.assertIn("deploy.py", orch.calls)
        self.assertIn("open_pr()", orch.calls)
        self.assertEqual(orch.results["arxiv"][0], "failed")

    def test_failed_build_stops_before_publish_backup_and_pr(self):
        orch = ScriptedOrchestrator(self.work, {"build_public_site.py": (1, False, 5)})
        self.assertEqual(orch.run(), 1)
        for later in ("commit_data()", "deploy.py", "backup_corpus.py", "open_pr()"):
            self.assertNotIn(later, orch.calls)
        self.assertEqual(orch.results["publish"][0], "not run")

    def test_failed_restore_runs_nothing_else(self):
        orch = ScriptedOrchestrator(self.work, {"restore_corpus.py": (1, False, 1)})
        self.assertEqual(orch.run(), 1)
        self.assertEqual(orch.calls, ["restore_corpus.py"])

    def test_failed_premerge_skips_the_semantic_scholar_steps(self):
        orch = ScriptedOrchestrator(self.work, {"merge_corpus.py": (1, False, 1)})
        orch.run()
        for script in ("fetch_abstracts_semanticscholar.py", "apply_abstracts_semanticscholar.py",
                       "fetch_s2_references.py"):
            self.assertNotIn(script, orch.calls)
        self.assertEqual(orch.results["s2_refs"], ("skipped", 0, "needs premerge"))
        self.assertIn("build_public_site.py", orch.calls)

    def test_failed_publish_skips_backup_and_pr(self):
        orch = ScriptedOrchestrator(self.work, {"deploy.py": (1, False, 1)})
        self.assertEqual(orch.run(), 1)
        self.assertNotIn("backup_corpus.py", orch.calls)
        self.assertNotIn("open_pr()", orch.calls)

    def test_s2_timeout_is_partial_not_a_failure(self):
        orch = ScriptedOrchestrator(self.work, {"fetch_s2_references.py": (-2, True, 999)})
        self.assertEqual(orch.run(), 0)
        self.assertEqual(orch.results["s2_refs"][0], "partial")

    def test_other_timeouts_are_failures(self):
        orch = ScriptedOrchestrator(self.work, {"check_new_editions.py": (-2, True, 999)})
        self.assertEqual(orch.run(), 1)
        self.assertEqual(orch.results["editions"][0], "timed out")

    def test_publish_runs_without_github_token(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "x", "AV_ATLAS_BOT_TOKEN": "y"}):
            orch = ScriptedOrchestrator(self.work)
            orch.run()
        self.assertNotIn("GITHUB_TOKEN", orch.envs["deploy.py"])
        self.assertEqual(orch.envs["deploy.py"]["AV_ATLAS_BOT_TOKEN"], "y")
        self.assertEqual(orch.envs["backup_corpus.py"]["GITHUB_TOKEN"], "x")
        self.assertEqual(orch.envs["restore_corpus.py"]["GITHUB_TOKEN"], "x")

    def test_skip(self):
        orch = ScriptedOrchestrator(self.work, skip=["arxiv", "s2_refs"])
        self.assertEqual(orch.run(), 0)
        self.assertNotIn("fetch_arxiv_monthly.py", orch.calls)
        self.assertNotIn("fetch_s2_references.py", orch.calls)
        self.assertEqual(orch.results["arxiv"], ("skipped", 0, "--skip"))

    def test_missing_script_is_a_failure(self):
        orch = ScriptedOrchestrator(self.work)
        orch.missing = ("fetch_crossref.py",)
        self.assertEqual(orch.run(), 1)
        self.assertEqual(orch.results["crossref"][0], "failed")
        self.assertIn("not in this checkout", orch.results["crossref"][2])

    def test_dry_run_runs_nothing(self):
        orch = ScriptedOrchestrator(self.work, dry_run=True)
        with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("ran something")):
            self.assertEqual(orch.run(), 0)
        self.assertEqual(orch.calls, [])
        self.assertTrue(all(r[0] == "dry run" for r in orch.results.values()))
        self.assertIn("Dry run", (self.work / "summary.md").read_text(encoding="utf-8"))


class BudgetTests(unittest.TestCase):
    def test_before_build_steps_leave_the_reserve(self):
        s2 = mu.Step("s2_refs", "soft", 180, before_build=True)
        self.assertEqual(mu.step_budget(s2, elapsed=0, deadline=320, reserve=100), 180)
        self.assertEqual(mu.step_budget(s2, elapsed=100, deadline=320, reserve=100), 120)
        self.assertEqual(mu.step_budget(s2, elapsed=219, deadline=320, reserve=100), 0)

    def test_build_uses_the_reserve(self):
        build = mu.Step("build", "hard", 150)
        self.assertEqual(mu.step_budget(build, elapsed=220, deadline=320, reserve=100), 100)

    def test_slow_sources_squeeze_the_s2_crawl_not_the_build(self):
        orch = ScriptedOrchestrator(tempfile.mkdtemp(), {
            "check_new_editions.py": (0, False, 120), "fetch_crossref.py": (0, False, 45),
            "fetch_s2_references.py": (-2, True, 999)})
        self.assertEqual(orch.run(), 0)
        # 1 + 1 + 120 + 45 + 1 + 1 + 1 + 1 = 171 minutes used, 320 - 100 - 171 left
        self.assertAlmostEqual(orch.budgets["fetch_s2_references.py"], 49)
        self.assertGreaterEqual(orch.budgets["build_public_site.py"], 100)


class HelperTests(unittest.TestCase):
    def test_parse_porcelain(self):
        out = " M data/a.json\0?? data/venues/new.json\0R  data/b.json\0data/old.json\0 D data/c.json\0"
        self.assertEqual(mu.parse_porcelain(out), [
            (" M", "data/a.json"), ("??", "data/venues/new.json"), ("R ", "data/b.json"), (" D", "data/c.json")])

    def test_pr_candidates(self):
        entries = [(" M", "data/venues/tits_all.json"), ("??", "data/venues/iv2026.json"),
                   ("??", "data/arxiv_monthly_state.json"), (" D", "data/venues/cvpr2020.json"),
                   ("??", "data/corpus_backup_state.json"), ("??", "data/notes.txt"),
                   (" M", "scripts/aggregate.py"), ("??", "data/venues/huge.json")]
        paths, notes = mu.pr_candidates(entries, lambda p: 10 ** 9 if "huge" in p else 10)
        self.assertEqual(paths, ["data/arxiv_monthly_state.json", "data/venues/iv2026.json",
                                 "data/venues/tits_all.json"])
        self.assertEqual(len(notes), 2)

    def test_count_records(self):
        self.assertEqual(mu.count_records("[1, 2, 3]"), 3)
        self.assertEqual(mu.count_records('{"papers": [1]}'), 1)
        self.assertIsNone(mu.count_records('{"ids": {}}'))
        self.assertIsNone(mu.count_records("not json"))

    def test_redact(self):
        text = ("key abc123secret and ghp_" + "a" * 36 + " and github_pat_" + "b" * 40
                + " push https://x-access-token:tok@github.com/x")
        out = mu.redact(text, ["abc123secret"])
        self.assertNotIn("abc123secret", out)
        self.assertNotIn("ghp_a", out)
        self.assertNotIn("github_pat_b", out)
        self.assertNotIn("tok@", out)
        self.assertIn("https://***@github.com/x", out)

    def test_code_block_fence_outlasts_backticks(self):
        block = mu.code_block("a ```` b")
        self.assertTrue(block.startswith("`````\n"))

    def test_env_file_gets_the_key_once(self):
        with tempfile.TemporaryDirectory() as d:
            orch = mu.Orchestrator(d, base=d)
            (Path(d) / ".env").write_text("OTHER=1", encoding="utf-8")
            with mock.patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": "k1"}):
                orch.ensure_env_file()
                orch.ensure_env_file()
            self.assertEqual((Path(d) / ".env").read_text(encoding="utf-8"),
                             "OTHER=1\nSEMANTIC_SCHOLAR_API_KEY=k1\n")


class BotBranchTests(unittest.TestCase):
    """carry_forward, commit_data and open_pr against a real local remote."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        subprocess.run(["git", "init", "--bare", "--quiet", "-b", "main", str(self.remote)], check=True)
        self.seed = root / "seed"
        subprocess.run(["git", "clone", "--quiet", str(self.remote), str(self.seed)], check=True,
                       capture_output=True)
        git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.seed)
        (self.seed / "data" / "venues").mkdir(parents=True)
        (self.seed / "data" / "venues" / "a.json").write_text("[1]", encoding="utf-8")
        (self.seed / "data" / "shared.json").write_text("{}", encoding="utf-8")
        (self.seed / ".gitignore").write_text("data/ignored.json\n", encoding="utf-8")
        commit_all(self.seed, "main 1")
        git("push", "--quiet", "origin", "main", cwd=self.seed)
        self.work = root / "work"

    def tearDown(self):
        self.tmp.cleanup()

    def make_bot_branch(self):
        """Last month's run: a new venue file and an edit to shared.json."""
        git("checkout", "--quiet", "-b", mu.BOT_BRANCH, cwd=self.seed)
        (self.seed / "data" / "venues" / "iv2026.json").write_text("[1, 2]", encoding="utf-8")
        (self.seed / "data" / "shared.json").write_text('{"bot": 1}', encoding="utf-8")
        commit_all(self.seed, "bot")
        git("push", "--quiet", "origin", mu.BOT_BRANCH, cwd=self.seed)
        git("checkout", "--quiet", "main", cwd=self.seed)

    def clone(self):
        repo = Path(self.tmp.name) / "runner"
        subprocess.run(["git", "clone", "--quiet", "-b", "main", str(self.remote), str(repo)], check=True,
                       capture_output=True)
        return repo

    def orch(self, repo):
        o = mu.Orchestrator(self.work, base=repo)
        o.start_sha = git("rev-parse", "HEAD", cwd=repo)
        return o

    def test_no_bot_branch(self):
        o = self.orch(self.clone())
        self.assertEqual(o.carry_forward(), ("ok", "no bot branch yet"))
        self.assertEqual(o.bot_remote_sha, "")

    def test_carry_takes_bot_files_main_lacks_and_keeps_mains_edits(self):
        self.make_bot_branch()
        # Since then main changed shared.json itself (e.g. the PR was squash-merged).
        (self.seed / "data" / "shared.json").write_text('{"main": 2}', encoding="utf-8")
        commit_all(self.seed, "main 2")
        git("push", "--quiet", "origin", "main", cwd=self.seed)

        repo = self.clone()
        o = self.orch(repo)
        status, note = o.carry_forward()
        self.assertEqual(status, "ok")
        self.assertEqual(o.carried, ["data/venues/iv2026.json"])
        self.assertIn("main's version kept", note)
        self.assertEqual((repo / "data" / "venues" / "iv2026.json").read_text(encoding="utf-8"), "[1, 2]")
        self.assertEqual((repo / "data" / "shared.json").read_text(encoding="utf-8"), '{"main": 2}')
        # Carried files are left unstaged, like anything a fetcher writes.
        self.assertEqual(git("diff", "--cached", "--name-only", cwd=repo), "")

    def test_commit_and_pr_rebuild_the_branch_as_main_plus_one_commit(self):
        self.make_bot_branch()
        repo = self.clone()
        o = self.orch(repo)
        o.carry_forward()
        (repo / "data" / "venues" / "a.json").write_text("[1, 2, 3]", encoding="utf-8")
        (repo / "data" / "arxiv_monthly_state.json").write_text("{}", encoding="utf-8")
        (repo / "data" / "ignored.json").write_text("{}", encoding="utf-8")
        (repo / "data" / "corpus_backup_state.json").write_text("{}", encoding="utf-8")
        o.results["build"] = ("ok", 1, "")

        self.assertEqual(o.commit_data(), ("ok", "3 file(s)"))
        self.assertEqual(o.committed, ["data/arxiv_monthly_state.json", "data/venues/a.json",
                                       "data/venues/iv2026.json"])
        gh_calls = []

        def fake_gh(*args):
            gh_calls.append(args)
            return "" if args[:2] == ("pr", "list") else "https://github.com/x/y/pull/7"

        o.gh = fake_gh
        self.assertEqual(o.open_pr(), ("ok", "opened https://github.com/x/y/pull/7"))

        main_sha = git("rev-parse", "main", cwd=self.remote)
        bot = mu.BOT_BRANCH
        self.assertEqual(git("rev-parse", f"{bot}~1", cwd=self.remote), main_sha)
        changed = git("diff", "--name-only", "main", bot, cwd=self.remote).split()
        self.assertEqual(changed, o.committed)
        self.assertEqual([a[:2] for a in gh_calls], [("pr", "list"), ("pr", "create")])
        body = (self.work / "pr_body.md").read_text(encoding="utf-8")
        self.assertIn("| data/venues/a.json | 1 | 3 |", body)
        self.assertIn("| data/venues/iv2026.json | new | 2 |", body)

    def test_existing_pr_is_edited(self):
        repo = self.clone()
        o = self.orch(repo)
        o.carry_forward()
        (repo / "data" / "venues" / "b.json").write_text("[]", encoding="utf-8")
        o.commit_data()
        calls = []
        o.gh = lambda *a: calls.append(a) or ("12" if a[:2] == ("pr", "list") else "")
        self.assertEqual(o.open_pr(), ("ok", "updated PR #12"))
        self.assertEqual(calls[1][:3], ("pr", "edit", "12"))

    def test_push_refuses_if_the_branch_moved_since_the_carry(self):
        self.make_bot_branch()
        repo = self.clone()
        o = self.orch(repo)
        o.carry_forward()
        # Someone pushes to the bot branch while the job runs.
        git("checkout", "--quiet", mu.BOT_BRANCH, cwd=self.seed)
        (self.seed / "data" / "late.json").write_text("{}", encoding="utf-8")
        commit_all(self.seed, "late")
        git("push", "--quiet", "origin", mu.BOT_BRANCH, cwd=self.seed)
        (repo / "data" / "venues" / "c.json").write_text("[]", encoding="utf-8")
        o.commit_data()
        o.gh = lambda *a: ""
        with self.assertRaises(RuntimeError):
            o.open_pr()

    def test_nothing_changed_means_no_commit_and_no_pr(self):
        repo = self.clone()
        o = self.orch(repo)
        self.assertEqual(o.commit_data(), ("ok", "no tracked data changes"))
        self.assertEqual(o.open_pr(), ("ok", "nothing to propose"))
        self.assertEqual(git("rev-parse", "--abbrev-ref", "HEAD", cwd=repo), "main")


class ReportFailureTests(unittest.TestCase):
    def run_report(self, listed):
        calls = []

        def gh(*args):
            calls.append(args)
            if args[:2] == ("issue", "list"):
                return json.dumps(listed)
            return "https://github.com/x/y/issues/3"

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "log.txt"
            log.write_text("\n".join(f"line {i}" for i in range(400)) + "\nsecret-value-123\n", encoding="utf-8")
            (Path(d) / "summary.md").write_text("| build | failed |", encoding="utf-8")
            with mock.patch.dict(os.environ, {"GH_TOKEN": "secret-value-123"}):
                result = mu.report_failure(log, d, gh, repo="x/y", run_url="https://run/1")
            body = (Path(d) / "issue_body.md").read_text(encoding="utf-8")
        return result, calls, body

    def test_creates_an_issue_with_the_log_tail(self):
        result, calls, body = self.run_report([{"number": 9, "title": "Monthly update failed (old)"}])
        self.assertEqual(result, "opened https://github.com/x/y/issues/3")
        self.assertEqual(calls[-1][:2], ("issue", "create"))
        self.assertIn("https://run/1", body)
        self.assertIn("| build | failed |", body)
        self.assertIn("line 399", body)
        self.assertNotIn("line 200", body)
        self.assertNotIn("secret-value-123", body)

    def test_comments_on_the_open_issue(self):
        result, calls, _ = self.run_report([{"number": 4, "title": mu.ISSUE_TITLE}])
        self.assertEqual(result, "commented on issue #4")
        self.assertEqual(calls[-1][:3], ("issue", "comment", "4"))


class DeployFromFreshRunnerTests(unittest.TestCase):
    """What the monthly job does: no .deploy-cache yet, and production's
    gh-pages already holds last month's site."""

    def test_first_publish_replaces_the_live_tree(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            remote = root / "remote.git"
            subprocess.run(["git", "init", "--bare", "--quiet", str(remote)], check=True)
            old = root / "old"
            old.mkdir()
            git("init", "--quiet", cwd=old)
            (old / "index.html").write_text("old", encoding="utf-8")
            (old / "gone.html").write_text("stale", encoding="utf-8")
            commit_all(old, "old site")
            git("push", "--quiet", str(remote), "HEAD:refs/heads/gh-pages", cwd=old)

            public = root / "public"
            public.mkdir()
            (public / "index.html").write_text("new", encoding="utf-8")
            (public / "stats.json").write_text("{}", encoding="utf-8")
            repo = root / "repo"
            repo.mkdir()
            git("init", "--quiet", cwd=repo)
            git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "--allow-empty", "-m", "x", cwd=repo)
            cache = root / "cache"
            self.assertFalse(cache.exists())

            with mock.patch.object(deploy, "PUBLIC_DIR", public), \
                 mock.patch.object(deploy, "CACHE_DIR", cache), \
                 mock.patch.object(deploy, "BASE", repo):
                deploy.publish("production", str(remote), None, "production", "Publish AV Atlas")

            files = git("ls-tree", "--name-only", "gh-pages", cwd=remote).split()
            self.assertEqual(sorted(files), [deploy.BUILD_INFO_NAME, "index.html", "stats.json"])
            self.assertEqual(git("show", "gh-pages:index.html", cwd=remote), "new")
            self.assertEqual(git("rev-list", "--count", "gh-pages", cwd=remote), "1")


if __name__ == "__main__":
    unittest.main()
