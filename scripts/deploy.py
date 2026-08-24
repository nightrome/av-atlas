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


def build():
    print("--- Building AV Atlas ---")
    subprocess.run([sys.executable, "build_public_site.py"], cwd=BASE / "scripts", check=True)


def commit_sources(msg):
    result = subprocess.run(["git", "status", "--porcelain"], cwd=BASE, capture_output=True, text=True)
    if not result.stdout.strip():
        print("Sources: nothing to commit.")
        return False
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=BASE, capture_output=True, text=True).stdout.strip()
    run(["git", "add", "-A"])
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
    run(["git", "fetch", "origin", "gh-pages"], check=False)

    worktree = Path(tempfile.mkdtemp(prefix="av-atlas-ghp-"))
    worktree.rmdir()
    try:
        added = run(["git", "worktree", "add", "--detach", str(worktree), "origin/gh-pages"], check=False)
        if added.returncode != 0:
            # No gh-pages branch yet -- first-ever deploy.
            run(["git", "worktree", "add", "--detach", "-B", "gh-pages", str(worktree)])

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
        run(["git", "push", "origin", "HEAD:gh-pages"], cwd=worktree)
        print("gh-pages pushed.")
    finally:
        run(["git", "worktree", "remove", "--force", str(worktree)], check=False)
        rmtree_retry(worktree) if worktree.exists() else None
        run(["git", "worktree", "prune"], check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-main-commit", action="store_true",
                         help="Skip committing/pushing source changes to main; just build and deploy gh-pages.")
    args = parser.parse_args()

    build()
    if not args.no_main_commit:
        commit_sources("Update AV Atlas")
    deploy_gh_pages()
    print("\nDone.")


if __name__ == "__main__":
    main()
