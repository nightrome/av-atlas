#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single deploy command for AV Atlas.

1. Runs build_public_site.py's full pipeline (merge_corpus -> aggregate ->
   run_tests -> build), which aborts before publishing if anything fails.
2. Commits and pushes any source changes to `main`.
3. Publishes public/ to the `gh-pages` branch, via a throwaway git worktree
   so this never needs a second full checkout.

Usage: python scripts/deploy.py
   or: python scripts/deploy.py --no-main-commit   # publish without touching main
   or: python scripts/deploy.py --skip-build       # HTML/JS/CSS-only change: skip
                                                   # the corpus rebuild + tests
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PUBLIC_DIR = BASE / "public"


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd or BASE, check=check)


def _clear_readonly_and_retry(func, path, exc_info):
    import os
    import stat
    os.chmod(path, stat.S_IWRITE)
    func(path)


def rmtree_retry(path, attempts=5, delay=0.5):
    for i in range(attempts):
        try:
            shutil.rmtree(path, onexc=_clear_readonly_and_retry)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def build(skip_build=False):
    print("--- Building AV Atlas ---")
    cmd = [sys.executable, "build_public_site.py"]
    if skip_build:
        cmd.append("--publish-only")
    subprocess.run(cmd, cwd=BASE / "scripts", check=True)


def commit_sources(msg):
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=BASE, capture_output=True, text=True).stdout.strip()
    run(["git", "add", "-A"])
    # Check staged changes *after* `git add`, not before -- otherwise stale
    # line-ending-only diffs (or any other change `git add`'s clean filters
    # would normalize away) can make `git status` claim there's something to
    # commit when `git add` actually stages nothing, and `git commit` then
    # fails outright with no clear error. See family-portal's deploy.py for
    # the concrete case this was found in.
    result = subprocess.run(["git", "status", "--porcelain"], cwd=BASE, capture_output=True, text=True)
    if not result.stdout.strip():
        print("Sources: nothing to commit.")
        return False
    run(["git", "commit", "-m", msg])
    push = run(["git", "push", "origin", f"HEAD:{branch}"], check=False)
    if push.returncode != 0:
        raise SystemExit(
            f"Committed to '{branch}' but the push was rejected. Reconcile with the "
            f"remote (git pull --rebase origin {branch}) and re-run, or re-run with "
            "--no-main-commit to publish the site without pushing sources.")
    print(f"Sources committed and pushed to '{branch}'.")
    return True


def deploy_gh_pages():
    print("\n--- Deploying to gh-pages ---")
    if not PUBLIC_DIR.exists():
        raise SystemExit(f"{PUBLIC_DIR} not found -- build did not produce output")

    # Always publishes as a single fresh orphan commit, force-pushed --
    # never builds on top of gh-pages' existing history. gh-pages is 100%
    # generated build output (stats.json/stats_adjacent.json, mostly, ~140MB
    # per snapshot) with no reviewable diffs and no reason anyone would ever
    # want an old commit back; committing on top of history the normal way
    # made every deploy add a genuinely new, largely non-delta-compressible
    # multi-MB chunk to the repo forever (confirmed: `git verify-pack`
    # showed only 17 of 315 objects in the pack had ANY delta chain -- 24
    # accumulated deploys were purely additive, not shrinking via reuse).
    # Squashing to one commit keeps gh-pages' contribution to repo size
    # flat at ~one snapshot, regardless of how many more times the site
    # gets deployed -- see DECISIONS.md's "Keep GitHub repo size small"
    # entry. No `git fetch origin gh-pages` needed anymore either, since
    # nothing here reads its prior content.
    worktree = Path(tempfile.mkdtemp(prefix="av-atlas-ghp-"))
    worktree.rmdir()
    try:
        run(["git", "worktree", "add", "--detach", str(worktree)])
        run(["git", "checkout", "--orphan", "gh-pages-publish"], cwd=worktree)
        run(["git", "rm", "-rf", "--quiet", "."], cwd=worktree, check=False)

        for item in worktree.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                rmtree_retry(item)
            else:
                item.unlink()
        for item in PUBLIC_DIR.iterdir():
            dst = worktree / item.name
            if item.is_dir():
                shutil.copytree(item, dst)
            else:
                shutil.copy2(item, dst)

        run(["git", "add", "-A"], cwd=worktree)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=worktree, capture_output=True, text=True)
        if not status.stdout.strip():
            print("gh-pages: nothing changed.")
            return
        run(["git", "commit", "-m", "Publish AV Atlas"], cwd=worktree)
        run(["git", "push", "--force", "origin", "HEAD:gh-pages"], cwd=worktree)
        print("gh-pages pushed (squashed to a single commit).")
    finally:
        run(["git", "worktree", "remove", "--force", str(worktree)], check=False)
        rmtree_retry(worktree) if worktree.exists() else None
        run(["git", "worktree", "prune"], check=False)
        # `git worktree remove` above only detaches the worktree, it doesn't
        # delete the local branch created inside it -- without this, the
        # SECOND deploy's `git checkout --orphan gh-pages-publish` fails
        # outright (branch already exists), confirmed the hard way: the
        # deploy immediately after this squash-to-one-commit change shipped
        # broke on exactly this. -D (not -d) since an orphan branch has no
        # merge-base with anything, so git can't tell it's "merged" the
        # normal way -d checks for.
        run(["git", "branch", "-D", "gh-pages-publish"], check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-main-commit", action="store_true",
                         help="Skip committing/pushing source changes to main; just build and deploy gh-pages.")
    parser.add_argument("--skip-build", action="store_true",
                         help="Skip the corpus rebuild (merge_corpus/aggregate) and the test suite; just "
                              "re-copy the current pages and the existing data/stats.json into public/ and "
                              "publish. Only safe for HTML/JS/CSS-only changes where nothing under data/ moved; "
                              "a previous full build must have left data/stats.json in place.")
    args = parser.parse_args()

    build(skip_build=args.skip_build)
    if not args.no_main_commit:
        commit_sources("Update AV Atlas")
    deploy_gh_pages()
    print("\nDone.")


if __name__ == "__main__":
    main()
