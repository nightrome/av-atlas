#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The monthly data update, start to finish. .github/workflows/monthly-update.yml
runs this once a month on a GitHub Actions runner. There is no LLM step and no
PDF is ever read or written.

Steps, in order:

  restore       restore_corpus.py -- the gitignored corpus from the backup release
  carry         tracked data files from the last run's bot branch
                (auto/monthly-update) that main doesn't have yet, see carry_forward()
  arxiv         fetch_arxiv_monthly.py -- new AV preprints since the last run
  editions      check_new_editions.py -- runs the fetcher for any new edition
  crossref      fetch_crossref.py journals -- the last 40 days of journal updates
  premerge      fix_suspect_venues.py + merge_corpus.py, so the new papers are in
                papers_full.json before the Semantic Scholar steps look for them
  s2_abstracts  fetch_abstracts_semanticscholar.py -- papers still without one
  s2_apply      apply_abstracts_semanticscholar.py
  s2_refs       fetch_s2_references.py -- ids and reference lists; it skips what
                it already has, so after the first full crawl that means new papers
  build         build_public_site.py -- merge, repairs, citation match, aggregate,
                shrink check, tests; the site version's patch goes up by one
  commit        commits the changed tracked data files to a local bot branch
  publish       deploy.py --skip-build --no-main-commit -- production gh-pages
  backup        backup_corpus.py -- refuses if another machine backed up since
                the restore, so it can't overwrite newer data
  pr            pushes the bot branch and opens or updates its pull request

restore, build and publish are "hard": if one fails, the run stops and nothing
is published. Every other step is "soft": a failure is recorded and the run
carries on, so one source being down doesn't hold back the rest. Any failure
makes the exit status 1, which is what makes the workflow open an issue.

Time: the whole run has a deadline (--deadline-minutes, default 320, under the
job's 350-minute limit and GitHub's 6 hours). Each step has its own cap, and
the steps before the build also leave RESERVE_MINUTES for the build and
publish. A step that runs out of time gets SIGINT, then is killed after
STOP_GRACE_SECONDS. For the two Semantic Scholar crawlers running out of time
is expected and not a failure: they save as they go, the backup keeps their
side files, and the next month picks up where this one stopped. That is how
the first full reference crawl gets done without a single 6-hour job (running
it once on the laptop is quicker; see README.md, "Monthly update").

Why the bot branch is carried forward: new venue files and the arXiv ledger
are tracked files, so this job can only hand them back to main through a pull
request. Nobody has to merge it for the site to stay right. Each run starts
from main plus whatever the bot branch has that main doesn't, and the bot
branch is rebuilt each time as main plus one commit, so the PR always shows
exactly what main is missing. A file that changed on both sides is taken from
main. The branch is never a merge of main: pushing a merge commit that
touches .github/workflows/ needs a token with the workflows permission, and
this job's token doesn't have it.

Only published runs push the bot branch. If the build or the shrink check
fails, the month's tracked changes aren't carried into the next run, so bad
data can't keep blocking the job; the fetchers pick the same papers up again.

Secrets come from the environment (the workflow sets them from repo secrets):
  SEMANTIC_SCHOLAR_API_KEY  written to .env for the scripts that read it there
  GITHUB_TOKEN              restore_corpus.py / backup_corpus.py (the bot token)
  AV_ATLAS_BOT_TOKEN        git pushes (through the workflow's credential helper)
  GH_TOKEN                  the gh CLI, for the pull request
GITHUB_TOKEN is taken out of deploy.py's environment, so deploy.py's own
backup call skips and the backup runs once, as its own step.

Usage:
  python scripts/monthly_update.py --dry-run             # print the plan, run nothing
  python scripts/monthly_update.py                       # the real thing (CI)
  python scripts/monthly_update.py --skip arxiv --skip s2_refs
  python scripts/monthly_update.py --report-failure LOG  # open or update the failure issue
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE = SCRIPTS_DIR.parent
ENV_FILE = BASE / ".env"

REPO = os.environ.get("GITHUB_REPOSITORY", "nightrome/av-atlas")
BASE_BRANCH = "main"
BOT_BRANCH = "auto/monthly-update"
PR_TITLE = "Monthly data update"
ISSUE_TITLE = "Monthly update failed"
BOT_NAME = "AV Atlas monthly update"
BOT_EMAIL = "holger@it-caesar.com"

DEFAULT_DEADLINE_MINUTES = 320
# Kept free for build, publish, backup and the PR by the steps before them.
RESERVE_MINUTES = 100
# A step that would get less than this is skipped rather than started.
MIN_STEP_MINUTES = 3
STOP_GRACE_SECONDS = 120
# check_new_editions.py stops any one fetcher after this long.
EDITION_FETCH_TIMEOUT = 40 * 60

# Never put into the bot PR, even if a .gitignore entry for them is missing:
# local state and build leftovers.
LOCAL_ONLY_FILES = {"data/corpus_backup_state.json", "data/publish_gate_baseline.json"}
MAX_PR_FILE_BYTES = 50 * 1000 * 1000
LOG_EXCERPT_LINES = 150


@dataclass
class Step:
    name: str
    kind: str                 # "hard" or "soft"
    cap: float                # minutes
    cmds: tuple = ()          # argv lists, script first, run from scripts/
    func: str = None          # name of an Orchestrator method instead of cmds
    needs: tuple = ()
    timeout_ok: bool = False  # running out of time is progress, not failure
    before_build: bool = False
    drop_env: tuple = ()


def plan(work_dir):
    w = str(work_dir)
    return [
        Step("restore", "hard", 30, cmds=(("restore_corpus.py",),)),
        Step("carry", "soft", 10, func="carry_forward", before_build=True),
        Step("arxiv", "soft", 60, before_build=True,
             cmds=(("fetch_arxiv_monthly.py", "--summary-json", f"{w}/arxiv.json"),)),
        Step("editions", "soft", 120, before_build=True,
             cmds=(("check_new_editions.py", "--json", f"{w}/editions.json", "--markdown", f"{w}/editions.md",
                    "--fetch-timeout", str(EDITION_FETCH_TIMEOUT)),)),
        Step("crossref", "soft", 45, before_build=True, cmds=(("fetch_crossref.py", "journals"),)),
        Step("premerge", "soft", 45, before_build=True,
             cmds=(("fix_suspect_venues.py",), ("merge_corpus.py",))),
        Step("s2_abstracts", "soft", 45, before_build=True, needs=("premerge",), timeout_ok=True,
             cmds=(("fetch_abstracts_semanticscholar.py",),)),
        Step("s2_apply", "soft", 15, before_build=True, needs=("premerge",),
             cmds=(("apply_abstracts_semanticscholar.py",),)),
        Step("s2_refs", "soft", 180, before_build=True, needs=("premerge",), timeout_ok=True,
             cmds=(("fetch_s2_references.py",),)),
        Step("build", "hard", 150, cmds=(("build_public_site.py",),)),
        Step("commit", "soft", 5, func="commit_data", needs=("build",)),
        Step("publish", "hard", 45, needs=("build",), drop_env=("GITHUB_TOKEN",),
             cmds=(("deploy.py", "--skip-build", "--no-main-commit"),)),
        Step("backup", "soft", 30, needs=("publish",), cmds=(("backup_corpus.py",),)),
        Step("pr", "soft", 10, func="open_pr", needs=("publish", "commit")),
    ]


STEP_NAMES = [s.name for s in plan(".")]


# ---------------------------------------------------------------- pure helpers

def step_budget(step, elapsed, deadline, reserve=RESERVE_MINUTES):
    """Minutes this step may run, or 0 if it shouldn't start. Steps before
    the build leave `reserve` minutes for the build and everything after it."""
    left = deadline - elapsed - (reserve if step.before_build else 0)
    budget = min(step.cap, left)
    return budget if budget >= MIN_STEP_MINUTES else 0


def parse_porcelain(out):
    """`git status --porcelain=v1 -z` -> [(xy, path)]."""
    entries, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if len(item) < 4:
            continue
        xy, path = item[:2], item[3:]
        if "R" in xy or "C" in xy:
            i += 1  # the rename source follows as its own entry
        entries.append((xy, path))
    return entries


def pr_candidates(entries, size_of=lambda p: 0):
    """Which changed paths go into the bot PR: tracked files under data/ that
    were modified or added, and new .json files under data/ that git doesn't
    ignore. Deletions are never proposed (no fetcher deletes a tracked file
    on purpose), nor local state files or anything oversized.
    Returns (paths, notes)."""
    paths, notes = [], []
    for xy, path in entries:
        if not path.startswith("data/") or path in LOCAL_ONLY_FILES:
            continue
        if "D" in xy:
            notes.append(f"{path} was deleted during the run; not proposing that")
            continue
        if xy == "??" and not path.endswith(".json"):
            continue
        if size_of(path) > MAX_PR_FILE_BYTES:
            notes.append(f"{path} is over {MAX_PR_FILE_BYTES // 1000000} MB; left out of the PR")
            continue
        paths.append(path)
    return sorted(set(paths)), notes


def count_records(text):
    """Papers in a venue file's JSON text (a list, or a dict with a "papers"
    list), else None."""
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        data = data.get("papers")
    return len(data) if isinstance(data, list) else None


SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"),
]
SECRET_ENV = ("GITHUB_TOKEN", "GH_TOKEN", "AV_ATLAS_BOT_TOKEN", "SEMANTIC_SCHOLAR_API_KEY")


def redact(text, secrets=()):
    """Masks secret values and anything token-shaped. GitHub masks secrets in
    the job log, but not in an issue body this job posts itself."""
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, "***")
    for pat in SECRET_PATTERNS:
        text = pat.sub(lambda m: (m.group(1) + "***@") if m.groups() else "***", text)
    return text


def code_block(text):
    """A fenced block whose fence can't be closed early by backticks in text."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text}\n{fence}"


# ---------------------------------------------------------------- orchestrator

class Orchestrator:
    def __init__(self, work_dir, dry_run=False, skip=(), deadline=DEFAULT_DEADLINE_MINUTES,
                 clock=time.monotonic, base=BASE, repo=REPO):
        self.work_dir = Path(work_dir)
        self.dry_run = dry_run
        self.skip = set(skip)
        self.deadline = deadline
        self.clock = clock
        self.base = Path(base)
        self.repo = repo
        self.results = {}
        self.notes = []
        self.start = None
        self.start_sha = None
        self.bot_remote_sha = ""
        self.carried = []
        self.committed = []

    # -- plumbing (tests replace these)

    def log(self, msg):
        print(msg, flush=True)

    def elapsed(self):
        return (self.clock() - self.start) / 60

    def run_cmd(self, argv, timeout_min, env):
        """(returncode, timed_out). SIGINT first, so a crawler can save."""
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
        proc = subprocess.Popen(argv, cwd=SCRIPTS_DIR, env=env, **kwargs)
        try:
            return proc.wait(timeout=timeout_min * 60), False
        except subprocess.TimeoutExpired:
            self.log(f"  time budget of {timeout_min:.0f} min reached, asking it to stop")
            proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
            try:
                proc.wait(timeout=STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            return proc.returncode, True

    def script_exists(self, name):
        return (SCRIPTS_DIR / name).exists()

    def git(self, *args, check=True):
        r = subprocess.run(["git", *args], cwd=self.base, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip() if check else r

    def gh(self, *args):
        r = subprocess.run(["gh", *args], cwd=self.base, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    # -- the run

    def run(self):
        self.start = self.clock()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        if not self.dry_run:
            self.ensure_env_file()
            self.start_sha = self.git("rev-parse", "HEAD")
        stopped = None
        for step in plan(self.work_dir):
            if stopped:
                self.results[step.name] = ("not run", 0, f"stopped after {stopped} failed")
                continue
            status, minutes, note = self.run_step(step)
            self.results[step.name] = (status, minutes, note)
            self.log(f"=== {step.name}: {status}" + (f" ({note})" if note else ""))
            self.write_summary()
            if step.kind == "hard" and status not in ("ok", "dry run", "skipped"):
                stopped = step.name
        self.write_summary()
        return 1 if self.failed() else 0

    def failed(self):
        return any(s in ("failed", "timed out") for s, _, _ in self.results.values())

    def run_step(self, step):
        self.log(f"\n=== {step.name} ({step.kind}) ===")
        if step.name in self.skip:
            return "skipped", 0, "--skip"
        for need in step.needs:
            if self.results.get(need, ("",))[0] not in ("ok", "partial", "dry run"):
                return "skipped", 0, f"needs {need}"
        budget = step_budget(step, self.elapsed(), self.deadline)
        if budget <= 0:
            return ("failed" if step.kind == "hard" else "skipped"), 0, "no time left"
        if self.dry_run:
            for cmd in step.cmds:
                self.log(f"  would run: python scripts/{' '.join(cmd)}")
            if step.func:
                self.log(f"  would run: {step.func}()")
            self.log(f"  time budget: {budget:.0f} min")
            return "dry run", 0, ""
        began = self.clock()
        try:
            status, note = (getattr(self, step.func)() if step.func else self.run_cmds(step, budget))
        except Exception as e:  # noqa: BLE001 -- a soft step's crash is recorded, not raised
            status, note = "failed", str(e)
        return status, round((self.clock() - began) / 60, 1), note

    def run_cmds(self, step, budget):
        env = {k: v for k, v in os.environ.items() if k not in step.drop_env}
        env.setdefault("PYTHONUNBUFFERED", "1")
        began = self.clock()
        for cmd in step.cmds:
            if not self.script_exists(cmd[0]):
                return "failed", f"scripts/{cmd[0]} is not in this checkout"
            left = budget - (self.clock() - began) / 60
            if left <= 0:
                return ("partial" if step.timeout_ok else "timed out"), f"no time left for {cmd[0]}"
            code, timed_out = self.run_cmd([sys.executable, *cmd], left, env)
            if timed_out:
                if step.timeout_ok:
                    return "partial", f"{cmd[0]} stopped at its time budget; it resumes next month"
                return "timed out", f"{cmd[0]} ran past {budget:.0f} min"
            if code != 0:
                return "failed", f"{cmd[0]} exited with {code}"
        return "ok", ""

    def ensure_env_file(self):
        """The Semantic Scholar scripts read their key from .env only."""
        key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        if not key:
            return
        env_file = self.base / ".env"
        text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        if re.search(r"^SEMANTIC_SCHOLAR_API_KEY=\S", text, re.M):
            return
        if text and not text.endswith("\n"):
            text += "\n"
        env_file.write_text(text + f"SEMANTIC_SCHOLAR_API_KEY={key}\n", encoding="utf-8")
        try:
            os.chmod(env_file, 0o600)
        except OSError:
            pass

    # -- git steps

    def carry_forward(self):
        """Bring in the data files the bot branch changed that main hasn't.
        See the module docstring."""
        line = self.git("ls-remote", "origin", f"refs/heads/{BOT_BRANCH}")
        self.bot_remote_sha = line.split()[0] if line else ""
        if not self.bot_remote_sha:
            return "ok", "no bot branch yet"
        remote_ref = f"refs/remotes/origin/{BOT_BRANCH}"
        self.git("fetch", "--quiet", "origin", f"+refs/heads/{BOT_BRANCH}:{remote_ref}")
        merge_base = self.git("merge-base", "HEAD", remote_ref)
        bot_files = self.git("diff", "--name-only", "--no-renames", merge_base, remote_ref, "--", "data").split()
        main_files = set(self.git("diff", "--name-only", "--no-renames", merge_base, "HEAD", "--", "data").split())
        kept_main = []
        for path in bot_files:
            if path in main_files:
                kept_main.append(path)
                continue
            if self.git("cat-file", "-e", f"{remote_ref}:{path}", check=False).returncode != 0:
                continue  # deleted on the bot branch; not carried
            self.git("checkout", remote_ref, "--", path)
            self.carried.append(path)
        if self.carried:
            self.git("reset", "--quiet", "--", "data")
        note = f"carried {len(self.carried)} file(s) from {BOT_BRANCH}"
        if kept_main:
            note += f"; {len(kept_main)} changed on main too, main's version kept"
        return "ok", note

    def changed_data_files(self):
        out = self.git("status", "--porcelain=v1", "-z", "--untracked-files=all", "--", "data")
        paths, notes = pr_candidates(parse_porcelain(out),
                                     lambda p: (self.base / p).stat().st_size if (self.base / p).exists() else 0)
        self.notes += notes
        return paths

    def commit_data(self):
        """Commit the changed data files on a local bot branch, before
        publishing, so the published BUILD_INFO names a commit that the PR
        then pushes."""
        paths = self.changed_data_files()
        if not paths:
            return "ok", "no tracked data changes"
        self.git("checkout", "--quiet", "-B", BOT_BRANCH)
        self.git("add", "--", *paths)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        msg = self.work_dir / "commit_message.txt"
        msg.write_text(f"Monthly data update {date.today().isoformat()}\n\n"
                       f"Written by scripts/monthly_update.py. Files:\n"
                       + "".join(f"  {p}\n" for p in paths), encoding="utf-8")
        self.git("-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "--quiet", "-F", str(msg))
        self.committed = paths
        return "ok", f"{len(paths)} file(s)"

    def open_pr(self):
        if not self.committed:
            return "ok", "nothing to propose"
        self.git("push", "--quiet", f"--force-with-lease=refs/heads/{BOT_BRANCH}:{self.bot_remote_sha}",
                 "origin", f"HEAD:refs/heads/{BOT_BRANCH}")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        body = self.work_dir / "pr_body.md"
        body.write_text(self.pr_body(), encoding="utf-8")
        number = self.gh("pr", "list", "--repo", self.repo, "--head", BOT_BRANCH, "--state", "open",
                         "--json", "number", "--jq", ".[0].number // empty")
        if number:
            self.gh("pr", "edit", number, "--repo", self.repo, "--body-file", str(body))
            return "ok", f"updated PR #{number}"
        url = self.gh("pr", "create", "--repo", self.repo, "--base", BASE_BRANCH, "--head", BOT_BRANCH,
                      "--title", PR_TITLE, "--body-file", str(body))
        return "ok", f"opened {url}"

    # -- reporting

    def file_counts(self, paths):
        rows = []
        for p in paths:
            new = count_records((self.base / p).read_text(encoding="utf-8")) if (self.base / p).exists() else None
            old_r = self.git("show", f"{self.start_sha}:{p}", check=False) if self.start_sha else None
            old = count_records(old_r.stdout) if old_r is not None and old_r.returncode == 0 else None
            rows.append((p, old, new))
        return rows

    def summary(self):
        lines = [f"## Monthly update {date.today().isoformat()}", ""]
        if self.dry_run:
            lines += ["Dry run: nothing was fetched, built or published.", ""]
        lines += ["| Step | Result | Minutes | Note |", "| --- | --- | --- | --- |"]
        for name in STEP_NAMES:
            if name in self.results:
                status, minutes, note = self.results[name]
                lines.append(f"| {name} | {status} | {minutes} | {note.replace('|', '/')} |")
        arxiv = self.read_json(self.work_dir / "arxiv.json")
        if isinstance(arxiv, dict):
            flat = [f"{k}: {v}" for k, v in arxiv.items() if isinstance(v, (int, float, str))]
            if flat:
                lines += ["", "### arXiv", ""] + [f"- {x}" for x in flat]
        editions_md = self.work_dir / "editions.md"
        if editions_md.exists():
            lines += ["", "### New editions", "", editions_md.read_text(encoding="utf-8").strip()]
        if self.committed:
            lines += ["", "### Tracked files changed", "", "| File | Before | After |", "| --- | --- | --- |"]
            for p, old, new in self.file_counts(self.committed):
                lines.append(f"| {p} | {'new' if old is None else old} | {'-' if new is None else new} |")
        if self.carried:
            lines += ["", f"Carried forward from the previous run: {', '.join(self.carried)}"]
        if self.notes:
            lines += ["", "### Notes", ""] + [f"- {n}" for n in self.notes]
        return "\n".join(lines) + "\n"

    def pr_body(self):
        text = (self.summary() + "\n---\n"
                "Opened by the monthly update job (`scripts/monthly_update.py`). The site is already "
                "published from this data. Merging keeps main in step with it, but nothing breaks if "
                "this sits unmerged: the next run starts from main plus this branch.\n")
        return text[:60000]

    @staticmethod
    def read_json(path):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def write_summary(self):
        text = self.summary()
        (self.work_dir / "summary.md").write_text(text, encoding="utf-8")
        return text


def report_failure(log_path, work_dir, gh, repo=REPO, run_url=None):
    """Open an issue for a failed run, or comment on the open one."""
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = ["(no log file)"]
    summary_path = Path(work_dir) / "summary.md"
    summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
    secrets = [os.environ.get(k, "") for k in SECRET_ENV]
    excerpt = redact("\n".join(lines[-LOG_EXCERPT_LINES:]), secrets)
    run_url = run_url or (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{repo}"
                          f"/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}")
    body = (f"The monthly update failed: {run_url}\n\n{redact(summary, secrets)}\n"
            f"Last {min(len(lines), LOG_EXCERPT_LINES)} lines of the log:\n\n{code_block(excerpt)}\n")[:60000]
    body_file = Path(work_dir) / "issue_body.md"
    body_file.parent.mkdir(parents=True, exist_ok=True)
    body_file.write_text(body, encoding="utf-8")
    listed = json.loads(gh("issue", "list", "--repo", repo, "--state", "open", "--search",
                           f'"{ISSUE_TITLE}" in:title', "--json", "number,title") or "[]")
    existing = [i["number"] for i in listed if i.get("title") == ISSUE_TITLE]
    if existing:
        gh("issue", "comment", str(existing[0]), "--repo", repo, "--body-file", str(body_file))
        return f"commented on issue #{existing[0]}"
    return "opened " + gh("issue", "create", "--repo", repo, "--title", ISSUE_TITLE, "--body-file", str(body_file))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=str(Path(tempfile.gettempdir()) / "av-atlas-monthly-update"),
                    help="where step summaries, the PR body and the run summary go")
    ap.add_argument("--dry-run", action="store_true", help="print what would run, run nothing")
    ap.add_argument("--skip", action="append", default=[], choices=STEP_NAMES, metavar="STEP",
                    help=f"skip a step (repeatable): {', '.join(STEP_NAMES)}")
    ap.add_argument("--deadline-minutes", type=float, default=DEFAULT_DEADLINE_MINUTES)
    ap.add_argument("--report-failure", metavar="LOG",
                    help="open or update the failure issue with the end of this log, then exit")
    args = ap.parse_args(argv)

    if args.report_failure:
        orch = Orchestrator(args.work_dir)
        print(report_failure(args.report_failure, args.work_dir, orch.gh))
        return 0

    orch = Orchestrator(args.work_dir, dry_run=args.dry_run, skip=args.skip, deadline=args.deadline_minutes)
    code = orch.run()
    summary = orch.write_summary()
    print("\n" + summary)
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8") as f:
            f.write(summary)
    return code


if __name__ == "__main__":
    sys.exit(main())
