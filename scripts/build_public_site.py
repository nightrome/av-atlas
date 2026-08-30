#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single entry point for building AV Atlas's public site: rebuilds the
corpus and stats from scratch, runs the full test suite, then publishes
public/ -- the published copy of the AV research dashboard, ready to be
pushed to gh-pages by scripts/deploy.py.

Runs, in order, and aborts (non-zero exit) if any step fails:
  1. merge_corpus.py -- rebuilds data/papers_full.json from data/venues/*.json
     plus arXiv, carrying over enrichment (author detail, citations,
     abstracts, ...) from the previous run, and reclassifies every paper.
  2. repair_garbled_authors_detail.py / repair_glued_institution_strings.py --
     idempotent one-off fixes for real, already-shipped data bugs. Run here
     (not left as a step to remember by hand) so a full recrawl-from-scratch
     reproduces the same corpus: both patch papers_full.json directly, which
     step 1 rebuilds from data/venues/*.json alone and would otherwise
     silently drop them.
  3. aggregate.py -- rebuilds data/stats.json + data/stats_adjacent.json.
  4. run_tests.py -- the full test suite (Python + JS + smoke + regression).
  5. build_public_site() below -- publishes the built HTML/stats.

This exists because crawler scripts (mine_abstracts.py,
backfill_citing_venues.py, enrich_core_authors.py, ...) write straight into
data/papers_full.json or data/venues/*.json and stop there -- nothing about
running one used to guarantee its results ever reached stats.json or the
live site. That happened for real: a session mined ~1,400 new abstracts,
then published without remembering that stats.json still needed a fresh
aggregate.py run to see them. Folding every step into this one script (the
same one scripts/deploy.py already calls) makes "crawled but never
published" structurally impossible instead of a step to remember, and
means a single command is always both the test run and the deploy.

Usage: python build_public_site.py
   or: python build_public_site.py --publish-only   # steps 1-4 skipped; only re-copy
                                                    # current pages + existing stats.json
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE / "public"

# This site is meant to go public and be discoverable -- indexing is
# allowed rather than blocked.
ROBOTS_TXT = "User-agent: *\nAllow: /\n"

# Dev-only HTML files that live at the app's top level (so they'd otherwise
# match the *.html glob below and get published) but must never ship --
# label_relevance.html embeds candidate paper data for hand-labeling and has
# no business being reachable from a live URL, guessable slug or not. This
# was caught after it briefly WAS published (see DECISIONS.md).
# authors_sql_prototype.html is a local-only spike (see DECISIONS.md's
# "sql.js-httpvfs prototype" entry) that was
# published anyway by this same glob -- broken if actually visited live,
# since its scripts/vendor/sqljs-httpvfs/ dependencies and the .db file it
# queries are never copied into the published output below.
EXCLUDED_HTML = {"label_relevance.html", "authors_sql_prototype.html",
                 # Parked markup+JS for the Insights "Open-source code"
                 # panel, pulled until the LLM code-link classifier has
                 # trustworthy coverage. Not a real page.
                 "_deferred_open_source.html"}


def html_pages():
    return [p for p in BASE.glob("*.html") if p.name not in EXCLUDED_HTML]


def build_public_site():
    index_path = BASE / "index.html"
    stats_path = BASE / "data" / "stats.json"
    if not index_path.exists():
        raise SystemExit(f"{index_path} not found")
    if not stats_path.exists():
        raise SystemExit(f"{stats_path} not found -- run aggregate.py first")

    page_dir = PUBLIC_DIR
    page_dir.mkdir(parents=True, exist_ok=True)

    # A version query string appended to every shared-asset reference
    # (filters.js, nav.js, sortable.js, theme.css, theme-light.css) below --
    # confirmed real bug: a reader whose browser had cached an old filters.js
    # loaded a freshly-deployed HTML page that called a function only the
    # new filters.js has (renderMinPapersControl), throwing
    # "ReferenceError: ... is not defined" and blanking the page via the
    # page's own top-level .catch(). Browsers key their cache on the full
    # URL including the query string, so bumping this on every build forces
    # a fresh fetch of every shared asset alongside every HTML change,
    # instead of relying on the reader to notice and hit the ↻ hard-reload
    # button. Wall-clock time, not a content hash, is fine here -- this
    # only needs to change on every deploy, not be reproducible.
    build_version = str(int(time.time()))
    ASSET_REF_RE = re.compile(r'((?:src|href)="(?:filters|nav|sortable)\.js|(?:src|href)="theme(?:-light)?\.css)"')

    def add_cache_bust(html):
        return ASSET_REF_RE.sub(lambda m: f'{m.group(1)}?v={build_version}"', html)

    # Every top-level *.html page in the app (index.html, authors.html, ...)
    # gets published, so new pages don't need a build-script change to ship.
    for html_path in html_pages():
        html = html_path.read_text(encoding="utf-8")
        html = add_cache_bust(html)
        (page_dir / html_path.name).write_text(html, encoding="utf-8", newline="\n")

    shutil.copy2(stats_path, page_dir / "stats.json")
    # Sharded abstract files (see aggregate.py's ABSTRACTS_DIR comment) --
    # paper.html fetches one shard at a time, not stats.json's whole payload.
    # The shard set is fixed (always exactly ABSTRACT_SHARD_COUNT files,
    # fixed names), so this copies each file over rather than rmtree+
    # copytree-ing the whole directory -- avoids a real OneDrive
    # directory-lock PermissionError rmtree hit in practice here.
    abstracts_src = BASE / "data" / "abstracts"
    abstracts_dst = page_dir / "abstracts"
    if abstracts_src.exists():
        abstracts_dst.mkdir(parents=True, exist_ok=True)
        for shard_path in abstracts_src.glob("*.json"):
            shutil.copy2(shard_path, abstracts_dst / shard_path.name)
    # Lazily fetched by index.html only when its AV-relevance filter is
    # switched away from the default -- not every deploy necessarily has
    # one yet (aggregate.py writes it, but an older stats.json could still
    # be lying around from before that existed), so this copy is optional,
    # unlike stats.json itself above.
    adjacent_path = BASE / "data" / "stats_adjacent.json"
    if adjacent_path.exists():
        shutil.copy2(adjacent_path, page_dir / "stats_adjacent.json")
    shutil.copy2(BASE / "theme.css", page_dir / "theme.css")
    # AV Atlas's own light/modern re-theme, layered on top of theme.css --
    # see theme-light.css's own header comment.
    shutil.copy2(BASE / "theme-light.css", page_dir / "theme-light.css")
    shutil.copy2(BASE / "logo.svg", page_dir / "logo.svg")
    shutil.copy2(BASE / "og-image.png", page_dir / "og-image.png")

    # Shared static assets referenced by the HTML pages (e.g. nav.js) but not
    # matched by the *.html glob above.
    for js_path in BASE.glob("*.js"):
        shutil.copy2(js_path, page_dir / js_path.name)

    # page_dir persists across runs (rmtree-ing it hits a real, previously-hit
    # OneDrive directory-lock issue -- see the "Fix build scripts hanging on
    # OneDrive cloud-placeholder directories" fix elsewhere in this repo), so
    # a file this script no longer generates -- an old page that's been
    # renamed, or a dev-only file that got copied in by hand -- would
    # otherwise stay published forever. Delete anything present that isn't
    # part of the current expected output. Caught in practice: label_relevance.html
    # (a dev tool, never meant to publish) briefly shipped to gh-pages this way.
    expected = {p.name for p in html_pages()} | {p.name for p in BASE.glob("*.js")} \
        | {"stats.json", "stats_adjacent.json", "theme.css", "theme-light.css", "logo.svg", "og-image.png"}
    for existing in page_dir.iterdir():
        if existing.is_file() and existing.name not in expected:
            existing.unlink()
            print(f"  removed stale {existing.name}")

    (PUBLIC_DIR / "robots.txt").write_text(ROBOTS_TXT, encoding="utf-8", newline="\n")

    print(f"Wrote {PUBLIC_DIR}")
    for html_path in html_pages():
        print(f"  {html_path.name}")
    for js_path in BASE.glob("*.js"):
        print(f"  {js_path.name}")
    print(f"  stats.json")
    if abstracts_dst.exists():
        print(f"  abstracts/ ({len(list(abstracts_dst.iterdir()))} shards)")
    print(f"  robots.txt (allow all)")


def run_step(label, script_name):
    print(f"\n--- {label} ---")
    result = subprocess.run([sys.executable, script_name], cwd=SCRIPTS_DIR)
    if result.returncode != 0:
        raise SystemExit(f"{script_name} failed (exit {result.returncode}) -- aborting before publish.")


def main():
    parser = argparse.ArgumentParser(description="Build (and optionally just re-publish) the AV Atlas public site.")
    parser.add_argument("--publish-only", action="store_true",
                        help="Skip the corpus rebuild (merge_corpus/aggregate), the one-off repairs and the "
                             "test suite; only re-copy the current HTML/JS/CSS and the EXISTING "
                             "data/stats.json into public/. Use this only for a pages-only change where "
                             "nothing under data/ moved -- a previous full build must have left "
                             "data/stats.json (and data/abstracts/) in place.")
    args = parser.parse_args()

    if args.publish_only:
        print("--- Publish-only: skipping corpus rebuild, repairs and tests ---")
    else:
        run_step("Rebuilding corpus (merge_corpus.py)", "merge_corpus.py")
        # One-off data repairs, applied here (not just left as scripts to remember
        # to run by hand) so a full recrawl-from-scratch reproduces the same
        # corpus without a manual step: merge_corpus.py rebuilds papers_full.json
        # from data/venues/*.json alone, which would otherwise silently drop
        # these fixes since they patch papers_full.json directly rather than the
        # tracked venue sources. Both are idempotent (a no-op once already
        # applied), so re-running them on every build is safe and cheap.
        run_step("Repairing garbled authors_detail", "repair_garbled_authors_detail.py")
        run_step("Repairing glued institution strings", "repair_glued_institution_strings.py")
        run_step("Rebuilding stats (aggregate.py)", "aggregate.py")
        run_step("Running tests (run_tests.py)", "run_tests.py")
    print("\n--- Publishing public site ---")
    build_public_site()


if __name__ == "__main__":
    main()
