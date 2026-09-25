#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single deploy command for AV Atlas.

Two-stage flow (preview first, then promote):

    python scripts/deploy.py --preview    # build, publish to the staging site only
    python scripts/deploy.py --promote    # publish the previewed build to production

    python scripts/deploy.py              # one-shot: build + production, no preview

A full run:
1. Builds public/ (build_public_site.py), which also gives the build its site
   version (one patch above what production serves). --promote reuses the
   previewed build, so production gets the same version staging showed. The corpus rebuild is skipped
   automatically when nothing that feeds the corpus changed since the last
   full build (data/ or the pipeline scripts, see build_fingerprint); tests always run. --full forces the
   rebuild, --skip-build skips it and the tests (HTML/JS/CSS-only changes).
   The build stops if the corpus shrank by more than 3% (see
   publish_gate.py); --allow-shrink lets an intended drop through.
2. Commits and pushes any source changes to `main` (production only).
3. Publishes public/ to the target repo's `gh-pages` branch through a
   persistent clone in .deploy-cache/, so only files that changed since the
   last deploy are re-hashed and uploaded.

Targets:
  production -- this repo's gh-pages (https://nightrome.github.io/av-atlas/)
  staging    -- a second repo's gh-pages (default nightrome/av-atlas-staging);
                HTML gets noindex + a PREVIEW banner, robots.txt disallows all,
                canonical/og URLs point at the staging URL. See DECISIONS.md.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PUBLIC_DIR = BASE / "public"
CACHE_DIR = BASE / ".deploy-cache"
STATE_FILE = CACHE_DIR / "state.json"

PROD_URL = "https://nightrome.github.io/av-atlas"
STAGING_REPO = os.environ.get("AV_ATLAS_STAGING_REPO", "https://github.com/nightrome/av-atlas-staging.git")
STAGING_URL = os.environ.get("AV_ATLAS_STAGING_URL", "https://nightrome.github.io/av-atlas-staging")

BUILD_INFO_NAME = "BUILD_INFO.json"
TRANSFORMED_SUFFIXES = (".html",)
TRANSFORMED_NAMES = ("robots.txt", "sitemap.xml")

PREVIEW_BANNER = (
    '<div style="position:fixed;bottom:0;left:0;right:0;z-index:99999;background:#f59e0b;'
    'color:#000;font:600 12px/1 system-ui,sans-serif;text-align:center;padding:6px;'
    'pointer-events:none">PREVIEW BUILD &mdash; not the public site</div>'
)


def run(cmd, cwd=None, check=True, **kw):
    return subprocess.run(cmd, cwd=cwd or BASE, check=check, **kw)


def git_out(args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


# ---------------------------------------------------------------- pure helpers

def make_preview_html(html, prod_url=PROD_URL, preview_url=STAGING_URL):
    """Turn a production page into its staging twin: staging URLs in the
    canonical/og/twitter tags, noindex so search engines never see two copies,
    and a visible banner so nobody mistakes it for the live site."""
    html = html.replace(prod_url, preview_url)
    if "<head>" in html:
        html = html.replace("<head>", '<head>\n<meta name="robots" content="noindex,nofollow">', 1)
    if "</body>" in html:
        html = html.replace("</body>", PREVIEW_BANNER + "\n</body>", 1)
    return html


def make_preview_bytes(name, data, prod_url=PROD_URL, preview_url=STAGING_URL):
    """Preview transform for one published file, keyed by file name."""
    if name == "robots.txt":
        return b"User-agent: *\nDisallow: /\n"
    text = data.decode("utf-8")
    if name == "sitemap.xml":
        return text.replace(prod_url, preview_url).encode("utf-8")
    return make_preview_html(text, prod_url, preview_url).encode("utf-8")


def hash_tree(root):
    """Content hash of every file under root (BUILD_INFO excluded), independent
    of mtimes -- identifies exactly what a build contains."""
    root = Path(root)
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rel = p.relative_to(root).as_posix()
        if rel == BUILD_INFO_NAME:
            continue
        h.update(rel.encode() + b"\0")
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest()


def _is_transformed(rel):
    return rel.endswith(TRANSFORMED_SUFFIXES) or rel in TRANSFORMED_NAMES


def sync_tree(src, dst, transform=None):
    """Mirror src into dst, touching only what changed. Untransformed files are
    compared by size+exact mtime (copy2 preserves mtime, so unchanged data files are
    skipped and git's stat cache in dst stays valid); files the transform
    rewrites are compared by content. Never touches dst/.git. Returns
    (written, removed) counts."""
    src, dst = Path(src), Path(dst)
    written = removed = 0
    wanted = set()
    for p in src.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(src).as_posix()
        wanted.add(rel)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if transform and _is_transformed(rel):
            new = transform(p.name, p.read_bytes())
            if not target.exists() or target.read_bytes() != new:
                target.write_bytes(new)
                written += 1
            continue
        if target.exists():
            a, b = p.stat(), target.stat()
            if a.st_size == b.st_size and a.st_mtime_ns == b.st_mtime_ns:
                continue
        shutil.copy2(p, target)
        written += 1
    for p in list(dst.rglob("*")):
        if not p.is_file() or ".git" in p.relative_to(dst).parts:
            continue
        rel = p.relative_to(dst).as_posix()
        if rel not in wanted and rel != BUILD_INFO_NAME:
            os.chmod(p, stat.S_IWRITE)
            p.unlink()
            removed += 1
    return written, removed


# Scripts whose edits can't change stats.json, so they shouldn't trigger a full
# rebuild. build_public_site.py is in here too, but its list of pipeline steps is
# hashed separately (see pipeline_steps), since adding or reordering a step there
# does change what a full build produces.
NON_CORPUS_SCRIPTS = {
    "deploy.py", "run_tests.py", "backup_corpus.py", "restore_corpus.py",
    "build_data_release.py", "build_public_site.py", "publish_gate.py",
}
# What the build writes under data/, as opposed to what it reads. Left out of
# the fingerprint so a build doesn't look like a change to the next one.
# data/pdfs_cvf/ and the shard folders are never looked at in the first
# place (see build_fingerprint).
BUILD_OUTPUTS = {"stats.json", "stats_non_av.json", "stats_adjacent.json", "publish_gate_baseline.json",
                 "new_papers.json"}
STEP_RE = re.compile(r'run_step\(\s*"[^"]*"\s*,\s*"([^"]+)"')


def pipeline_steps(base=BASE):
    """The scripts build_public_site.py runs as pipeline steps, in order."""
    try:
        text = (Path(base) / "scripts" / "build_public_site.py").read_text(encoding="utf-8")
    except OSError:
        return []
    return STEP_RE.findall(text)


def build_fingerprint(base=BASE):
    """Fingerprint of everything a full corpus rebuild reads: the pipeline
    scripts (except ones that can't affect stats.json), the order of steps in
    build_public_site.py, and every data file the build reads, tracked or
    not. Cheap (stat only: size and mtime, no hashing of the 100-300 MB files).
    If it matches the fingerprint saved after the last full build,
    merge_corpus/aggregate would just reproduce the stats.json already on disk.

    Data files are found by globbing data/*.json and data/venues/*.json, not
    by asking git: several real inputs are gitignored (papers_full.json,
    citation_graph.json, the reference lists, venues/arxiv_s2_citing.json,
    the abstract and affiliation side files), and a venue file a fetcher just
    wrote isn't tracked until someone adds it. Asking git missed all of
    those, so a deploy after a crawl could skip the rebuild and publish the
    old stats.json. Tracked files elsewhere under data/ are still included.
    data/pdfs_cvf/ and the build's own shard folders are never read."""
    base = Path(base)
    data = base / "data"
    files = [f for f in sorted((base / "scripts").glob("*.py")) if f.name not in NON_CORPUS_SCRIPTS]
    files += [f for f in data.glob("*.json") if f.name not in BUILD_OUTPUTS]
    files += list((data / "venues").glob("*.json"))
    tracked = subprocess.run(["git", "ls-files", "data"], cwd=base, capture_output=True, text=True).stdout.split()
    files += [base / t for t in tracked if not t.startswith("data/pdfs_cvf/")]
    h = hashlib.sha256()
    for f in sorted(set(files)):
        try:
            st = f.stat()
        except OSError:
            continue
        h.update(f"{f.relative_to(base).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    h.update(("steps:" + ",".join(pipeline_steps(base))).encode())
    return h.hexdigest()


def build_info(public_dir=None):
    """The BUILD_INFO.json build_public_site.py wrote into public/: the site
    version it assigned and when. Publishing copies the version from here
    rather than working out a new one, so --promote ships the previewed build
    under the same version."""
    try:
        return json.loads((Path(public_dir or PUBLIC_DIR) / BUILD_INFO_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(**updates):
    state = load_state()
    state.update(updates)
    CACHE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------- steps

def build(mode, allow_shrink=False):
    """mode: 'auto' (skip the corpus rebuild if the fingerprint is unchanged),
    'full', or 'skip' (publish-only, no tests). allow_shrink is passed on to
    build_public_site.py's shrink check."""
    print("--- Building AV Atlas ---")
    scripts = BASE / "scripts"
    extra = ["--allow-shrink"] if allow_shrink else []
    if mode == "skip":
        subprocess.run([sys.executable, "build_public_site.py", "--publish-only", *extra], cwd=scripts, check=True)
        return
    stats_ok = (BASE / "data" / "stats.json").exists()
    if mode == "auto" and stats_ok and load_state().get("build_fingerprint") == build_fingerprint():
        print("Corpus inputs unchanged since the last full build -- skipping merge/aggregate, running tests.")
        subprocess.run([sys.executable, "run_tests.py"], cwd=scripts, check=True)
        subprocess.run([sys.executable, "build_public_site.py", "--publish-only", *extra], cwd=scripts, check=True)
        return
    subprocess.run([sys.executable, "build_public_site.py", *extra], cwd=scripts, check=True)
    # Fingerprint *after* the build: merge_corpus rewrites papers_full.json.
    save_state(build_fingerprint=build_fingerprint())


def commit_sources(msg):
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=BASE, capture_output=True, text=True).stdout.strip()
    run(["git", "add", "-A"])
    # Check staged changes *after* `git add`, not before -- otherwise stale
    # line-ending-only diffs (or any other change `git add`'s clean filters
    # would normalize away) can make `git status` claim there's something to
    # commit when `git add` actually stages nothing, and `git commit` then
    # fails outright with no clear error.
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


def ensure_cache_clone(name, remote):
    """Persistent clone used only to build gh-pages commits. Primed once with the
    remote's current tip (index only) so git knows which blobs the server
    already has and a push sends just what changed."""
    clone = CACHE_DIR / name
    if (clone / ".git").exists():
        if git_out(["remote", "get-url", "origin"], clone) != remote:
            run(["git", "remote", "set-url", "origin", remote], cwd=clone)
        return clone
    clone.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "--quiet"], cwd=clone)
    run(["git", "config", "core.autocrlf", "false"], cwd=clone)
    run(["git", "remote", "add", "origin", remote], cwd=clone)
    fetched = run(["git", "fetch", "--quiet", "--depth", "1", "origin", "gh-pages"], cwd=clone, check=False)
    if fetched.returncode == 0:
        run(["git", "read-tree", "FETCH_HEAD"], cwd=clone)
    return clone


def publish(name, remote, transform, label, commit_msg):
    """Sync public/ into the cache clone and force-push it to gh-pages as a single
    parentless commit (gh-pages is pure build output -- see DECISIONS.md)."""
    print(f"\n--- Deploying to {label} ---")
    if not PUBLIC_DIR.exists():
        raise SystemExit(f"{PUBLIC_DIR} not found -- build did not produce output")
    clone = ensure_cache_clone(name, remote)
    written, removed = sync_tree(PUBLIC_DIR, clone, transform)
    content_hash = hash_tree(PUBLIC_DIR)
    built = build_info(PUBLIC_DIR)
    info = {
        "target": name,
        "version": built.get("version"),
        "content_hash": content_hash,
        "source_commit": git_out(["rev-parse", "--short", "HEAD"], BASE),
        "built_at": built.get("built_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (clone / BUILD_INFO_NAME).write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Synced: {written} file(s) written, {removed} removed.")

    if info["version"]:
        commit_msg = f"{commit_msg} v{info['version']}"

    run(["git", "add", "-A"], cwd=clone)
    tree = git_out(["write-tree"], clone)
    commit = git_out(["-c", "user.name=AV Atlas deploy", "-c", "user.email=holger@it-caesar.com",
                      "commit-tree", tree, "-m", commit_msg], clone)
    run(["git", "push", "--force", "origin", f"{commit}:refs/heads/gh-pages"], cwd=clone)
    print(f"{label}: pushed (single commit {commit[:8]}).")
    return content_hash


def deploy_production():
    content_hash = publish("production", git_out(["remote", "get-url", "origin"], BASE), None,
                           "production gh-pages", "Publish AV Atlas")
    # What build_public_site.py counts on from if it can't reach production
    # next time.
    version = build_info().get("version")
    if version:
        save_state(published_version=version)
    return content_hash


def deploy_staging(remote):
    return publish("staging", remote, make_preview_bytes, "staging preview", "Publish AV Atlas preview")


def backup_corpus():
    print("\n--- Backing up the corpus (backup_corpus.py) ---")
    # Runs last, after the site is already live. No token configured is a
    # silent skip (exit 0 from backup_corpus.py). Anything else -- a failed
    # upload, an expired token, a newer backup from another machine -- used
    # to scroll past above a cheerful "Done." and let the backup go stale
    # unnoticed, so it now ends the deploy with a non-zero exit instead.
    result = subprocess.run([sys.executable, "backup_corpus.py"], cwd=BASE / "scripts", check=False)
    if result.returncode != 0:
        raise SystemExit("\nBACKUP FAILED: the site is published, but the corpus backup was not "
                         "updated (see the error above). Rerun scripts/backup_corpus.py once it's fixed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    stage = parser.add_mutually_exclusive_group()
    stage.add_argument("--preview", action="store_true",
                       help="Build and publish to the staging site only; production and main are untouched.")
    stage.add_argument("--promote", action="store_true",
                       help="Publish the current public/ (no rebuild) to production, after checking it is "
                            "the build that was last previewed.")
    parser.add_argument("--staging-repo", default=STAGING_REPO, help="Git URL of the staging repo.")
    parser.add_argument("--no-main-commit", action="store_true",
                        help="Skip committing/pushing source changes to main.")
    parser.add_argument("--full", action="store_true",
                        help="Force the corpus rebuild even if the inputs look unchanged.")
    parser.add_argument("--skip-build", action="store_true",
                        help="Skip the corpus rebuild and the tests; just re-copy the current pages and the "
                             "existing data/stats.json into public/. Only safe for HTML/JS/CSS-only changes.")
    parser.add_argument("--force-promote", action="store_true",
                        help="With --promote: publish even if public/ differs from the last preview.")
    parser.add_argument("--allow-shrink", action="store_true",
                        help="Passed on to build_public_site.py: publish even if the corpus shrank by more "
                             "than 3%% on a gated number (for an intended drop, e.g. after a matcher fix).")
    args = parser.parse_args()

    if args.promote:
        if not PUBLIC_DIR.exists():
            raise SystemExit("No public/ to promote -- run --preview first.")
        previewed = load_state().get("previewed_hash")
        current = hash_tree(PUBLIC_DIR)
        if previewed != current and not args.force_promote:
            raise SystemExit(
                "public/ is not the build that was last previewed "
                f"(previewed {str(previewed)[:12]}, current {current[:12]}). Run --preview again, "
                "or pass --force-promote to publish it anyway.")
        if not args.no_main_commit:
            commit_sources("Update AV Atlas")
        save_state(promoted_hash=deploy_production())
        backup_corpus()
        print("\nDone.")
        return

    build("skip" if args.skip_build else "full" if args.full else "auto", allow_shrink=args.allow_shrink)
    if args.preview:
        save_state(previewed_hash=deploy_staging(args.staging_repo))
        print(f"\nPreview: {STAGING_URL}/\nLooks right? python scripts/deploy.py --promote")
        return
    if not args.no_main_commit:
        commit_sources("Update AV Atlas")
    save_state(promoted_hash=deploy_production())
    backup_corpus()
    print("\nDone.")


if __name__ == "__main__":
    main()
