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
  3. aggregate.py -- rebuilds data/stats.json + data/stats_non_av.json.
  4. run_tests.py -- the full test suite (Python + JS + smoke + regression).
  5. build_public_site() below -- publishes the built HTML/stats.

This exists because crawler scripts (mine_abstracts.py,
backfill_citing_venues.py, enrich_av_authors.py, ...) write straight into
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
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data_release  # noqa: E402  (needs the sys.path line above)

BASE = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
# The site's source files -- pages, shared JS/CSS, logo, the vendored asset
# tree -- live under site/, not the repo root.
SITE_DIR = BASE / "site"
PUBLIC_DIR = BASE / "public"

# This site is meant to go public and be discoverable -- indexing is
# allowed rather than blocked.
ROBOTS_TXT = "User-agent: *\nAllow: /\n"

# Exactly the pages that ship. An allowlist, not a denylist.
#
# This used to be "every *.html at the app root, minus these three", which
# leaked twice: label_relevance.html (a hand-labeling tool with candidate
# paper data embedded in it) and authors_sql_prototype.html (a local-only
# spike, broken if actually visited, since its vendored dependencies are
# never copied here) both reached gh-pages before anyone thought to add them
# to the exclusion list. A denylist fails open -- a new dev file is published
# by default and only stops being published once someone remembers. This
# fails closed: a new page ships when it is added here, and nothing else
# ever does. Dev-only pages now also live in dev/ rather than at the root,
# so they are not candidates in the first place.
PUBLISHED_HTML = [
    "index.html", "authors.html", "institutions.html", "venues.html",
    "countries.html", "categories.html", "network.html", "insights.html",
    "about.html", "author.html", "institution.html", "venue.html", "paper.html",
    "compare.html",
]


def html_pages():
    pages = [SITE_DIR / name for name in PUBLISHED_HTML]
    missing = [p.name for p in pages if not p.exists()]
    if missing:
        raise SystemExit(f"PUBLISHED_HTML lists page(s) that don't exist: {', '.join(missing)}")
    # A page in site/ that nobody added to the list is almost always a new
    # page someone forgot to register, not a deliberate omission -- say so
    # rather than silently not publishing it.
    unlisted = sorted(p.name for p in SITE_DIR.glob("*.html") if p.name not in set(PUBLISHED_HTML))
    if unlisted:
        print(f"  note: not publishing unlisted site/ page(s): {', '.join(unlisted)}"
              f" -- add to PUBLISHED_HTML in {Path(__file__).name} if they should ship")
    return pages


SITE_URL = "https://nightrome.github.io/av-atlas"

# How many of each kind of detail page to list in the sitemap. Every author,
# paper, institution and venue page is a query string on one of four HTML
# files, so a crawler has no way to discover them except by following links
# from a listing page -- which only ever shows the current page of results.
# The sitemap is what makes the rest reachable.
#
# Not everything is listed: the corpus holds ~25k papers and ~55k author
# names, and a sitemap of 80k URLs whose long tail is single-paper authors
# and name-collision artifacts is mostly noise, both to crawlers and to
# anyone who lands on one. Capped at the entities substantial enough to be
# worth landing on, ranked by citations.
SITEMAP_LIMITS = {"papers": 5000, "authors": 3000, "institutions": 1000, "venues": 300}


def write_sitemap(page_dir, stats_path):
    """A sitemap.xml covering the listing pages plus the top detail pages.

    Detail pages set their own <title>/description at runtime (see
    setDetailPageMeta in filters.js); this is what gets a crawler to them in
    the first place.
    """
    import xml.sax.saxutils as sx

    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    urls = [f"{SITE_URL}/{p.name}" for p in sorted(html_pages(), key=lambda p: p.name)]

    def add(page, key, values):
        for v in values:
            urls.append(f"{SITE_URL}/{page}?{key}={urllib.parse.quote(str(v), safe='')}")

    papers = sorted((stats.get("all_papers") or []),
                    key=lambda p: p.get("citations") or 0, reverse=True)
    add("paper.html", "title",
        [p["title"] for p in papers[:SITEMAP_LIMITS["papers"]] if p.get("title")])
    # Authors and institutions are counted here rather than read from
    # stats.json's top_authors/top_institutions: those are leaderboards
    # capped at 100 entries, which would have limited the sitemap to 100
    # author pages out of 55,000 -- the exact pages this exists to expose.
    author_papers, inst_papers = {}, {}
    for p in papers:
        for name in set(p.get("authors") or []):
            author_papers[name] = author_papers.get(name, 0) + 1
        for inst in set(p.get("institutions") or []):
            inst_papers[inst] = inst_papers.get(inst, 0) + 1
    by_papers = lambda d, n: sorted(d, key=lambda k: -d[k])[:n]  # noqa: E731
    add("author.html", "name", by_papers(author_papers, SITEMAP_LIMITS["authors"]))
    add("institution.html", "name", by_papers(inst_papers, SITEMAP_LIMITS["institutions"]))
    add("venue.html", "name",
        list((stats.get("corpus_stats") or {}).get("by_venue", {}))[:SITEMAP_LIMITS["venues"]])

    today = time.strftime("%Y-%m-%d")
    body = "\n".join(
        f"  <url><loc>{sx.escape(u)}</loc><lastmod>{today}</lastmod></url>" for u in urls)
    (page_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n", encoding="utf-8", newline="\n")
    print(f"  sitemap.xml: {len(urls)} URLs")


def build_public_site():
    index_path = SITE_DIR / "index.html"
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
    non_av_path = BASE / "data" / "stats_non_av.json"
    if non_av_path.exists():
        shutil.copy2(non_av_path, page_dir / "stats_non_av.json")
    shutil.copy2(SITE_DIR / "theme.css", page_dir / "theme.css")
    # AV Atlas's own light/modern re-theme, layered on top of theme.css --
    # see theme-light.css's own header comment.
    shutil.copy2(SITE_DIR / "theme-light.css", page_dir / "theme-light.css")
    shutil.copy2(SITE_DIR / "logo.svg", page_dir / "logo.svg")
    shutil.copy2(SITE_DIR / "og-image.png", page_dir / "og-image.png")

    # Vendored static assets (institution/venue logos with no stable
    # third-party URL, so the fetch scripts point at a local assets/... path
    # rather than hotlinking a Google image-cache URL that rots within days).
    # The tree is copied verbatim so the path baked into stats.json resolves.
    # The stale-file sweep below only inspects top-level files, so nothing
    # under assets/ needs adding to `expected`.
    assets_src = SITE_DIR / "assets"
    if assets_src.exists():
        for asset in assets_src.rglob("*"):
            if asset.is_file():
                dst = page_dir / asset.relative_to(SITE_DIR)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, dst)

    # Shared static assets referenced by the HTML pages (e.g. nav.js) but not
    # matched by the *.html glob above.
    for js_path in SITE_DIR.glob("*.js"):
        shutil.copy2(js_path, page_dir / js_path.name)

    # page_dir persists across runs (rmtree-ing it hits a real, previously-hit
    # OneDrive directory-lock issue -- see the "Fix build scripts hanging on
    # OneDrive cloud-placeholder directories" fix elsewhere in this repo), so
    # a file this script no longer generates -- an old page that's been
    # renamed, or a dev-only file that got copied in by hand -- would
    # otherwise stay published forever. Delete anything present that isn't
    # part of the current expected output. Caught in practice: label_relevance.html
    # (a dev tool, never meant to publish) briefly shipped to gh-pages this way.
    expected = {p.name for p in html_pages()} | {p.name for p in SITE_DIR.glob("*.js")} \
        | {"stats.json", "stats_non_av.json", "theme.css", "theme-light.css", "logo.svg",
           "og-image.png", "sitemap.xml", "robots.txt"}
    for existing in page_dir.iterdir():
        if existing.is_file() and existing.name not in expected:
            existing.unlink()
            print(f"  removed stale {existing.name}")

    (PUBLIC_DIR / "robots.txt").write_text(
        ROBOTS_TXT + f"\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8", newline="\n")
    write_sitemap(page_dir, stats_path)

    print(f"Wrote {PUBLIC_DIR}")
    for html_path in html_pages():
        print(f"  {html_path.name}")
    for js_path in SITE_DIR.glob("*.js"):
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
    # The downloadable corpus, built from the same stats.json the pages read,
    # so the download can never describe a different corpus than the site.
    print("\n--- Building data release ---")
    graph_path = BASE / "data" / "citation_graph.json"
    build_data_release.build(
        json.loads((BASE / "data" / "stats.json").read_text(encoding="utf-8")),
        json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else {},
    )


if __name__ == "__main__":
    main()
