#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Single entry point for building AV Atlas's public site: rebuilds the
corpus and stats from scratch, runs the full test suite, then publishes
public/ -- the published copy of the AV research dashboard, ready to be
pushed to gh-pages by scripts/deploy.py.

Runs, in order, and aborts (non-zero exit) if any step fails:
  0. fix_suspect_venues.py / email_addresses.py -- clean the tracked sources
     under data/ that a fresh crawl can make dirty again: wrong venue names,
     and email addresses (each replaced by its domain, see email_addresses.py).
  1. merge_corpus.py -- rebuilds data/papers_full.json from data/venues/*.json
     plus arXiv, carrying over enrichment (author detail, citations,
     abstracts, ...) from the previous run, and reclassifies every paper.
  2. repair_garbled_authors_detail.py / repair_glued_institution_strings.py /
     repair_openalex_institution_errors.py -- idempotent one-off fixes for
     real, already-shipped data bugs. Run here (not left as a step to
     remember by hand) so a full recrawl-from-scratch reproduces the same
     corpus: all three patch papers_full.json directly, which step 1
     rebuilds from data/venues/*.json alone and would otherwise silently
     drop them.
  3. build_citation_graph.py --match-only, then apply_citation_sources.py --
     rematches the saved reference lists against the rebuilt corpus (offline,
     no PDFs or network) and writes the in-corpus counts onto papers_full.json,
     so a new or fixed graph always reaches stats.json.
  4. aggregate.py -- rebuilds data/stats.json + the sharded data/non_av_papers/.
  5. publish_gate.py -- stops the build if the new stats.json lost more than
     3% on any of a few headline numbers (see its docstring); --allow-shrink
     lets an intended drop through.
  6. run_tests.py -- the full test suite (Python + JS + smoke + regression).
  7. build_public_site() below -- publishes the built HTML/stats.

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

Every build also gets a site version, major.minor.patch. major.minor comes
from the tracked VERSION file at the repo root, which only the maintainer
edits. The patch is worked out from what production serves right now (see
resolve_version below), so it goes up by one with every published build.

Usage: python build_public_site.py
   or: python build_public_site.py --publish-only   # steps 1-6 skipped; only re-copy
                                                    # current pages + existing stats.json
   or: python build_public_site.py --allow-shrink   # publish even if the corpus shrank
   or: python build_public_site.py --version 0.1.7  # use this version, don't ask production
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data_release  # noqa: E402  (needs the sys.path line above)
import publish_gate  # noqa: E402

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
# spike, since deleted, that was broken if actually visited because its
# vendored dependencies were never copied here) both reached gh-pages before anyone thought to add them
# to the exclusion list. A denylist fails open -- a new dev file is published
# by default and only stops being published once someone remembers. This
# fails closed: a new page ships when it is added here, and nothing else
# ever does. Dev-only pages now also live in dev/ rather than at the root,
# so they are not candidates in the first place.
PUBLISHED_HTML = [
    "index.html", "authors.html", "institutions.html", "venues.html",
    "countries.html", "categories.html", "network.html", "insights.html",
    "about.html", "author.html", "institution.html", "venue.html", "country.html",
    "paper.html", "compare.html",
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


# data/stats.json holds more than the pages read: aggregate.py also writes it
# for the data release, the sitemap and the tests. Every page downloads the
# published copy before it can show anything (4.7 MB gzip in September 2026),
# so the build ships a slimmer one and leaves data/stats.json as it is.
#
# Top-level keys no page reads. top_papers is only touched by filters.js to
# keep it in step with all_papers when something swaps the paper list.
UNUSED_STATS_KEYS = (
    "best_by_venue", "best_by_year", "top_papers", "top_authors",
    "top_institutions", "venue_images",
)
# Per-paper fields no page reads. Null fields are dropped as well: every page
# checks paper fields with `!= null` or plain truthiness, so a missing key
# reads the same as a null one.
UNUSED_PAPER_FIELDS = ("citations_updated", "has_code_link", "cd_n_citers", "venue_status")

# What about.html reads. It only needs corpus totals and coverage numbers, so
# it gets its own small file instead of the whole stats.json.
ABOUT_KEYS = (
    "generated_at", "generated_from", "content_updated", "content_hash",
    "av_relevant", "corpus_stats", "verification", "top_countries",
)


def is_version_key(key):
    return key == "version" or key.startswith("version_") or key.endswith("_version")


def slim_stats(stats):
    """The stats.json the pages get: data/stats.json minus what no page reads."""
    out = {k: v for k, v in stats.items() if k not in UNUSED_STATS_KEYS}
    out["all_papers"] = [
        {k: v for k, v in p.items() if v is not None and k not in UNUSED_PAPER_FIELDS}
        for p in stats.get("all_papers") or []
    ]
    return out


def paper_sources(stats):
    """How the AV papers got into the corpus, for the About page's text.

    A paper either came from a venue's complete listing, or was found
    because it cites a paper already in the corpus (the Semantic Scholar
    crawl). Of the second kind, the ones whose venue is arXiv are preprints
    that were never published anywhere we index.
    """
    papers = stats.get("all_papers") or []
    listed = sum(1 for p in papers if p.get("source") == "venue_listing")
    found = [p for p in papers if p.get("source") != "venue_listing"]
    preprints = sum(1 for p in found if "arxiv" in (p.get("venue") or "").lower())
    return {"total": len(papers), "venue_listing": listed,
            "citation_found": len(found), "arxiv_only": preprints}


def about_payload(stats):
    """The small about.json that about.html reads instead of stats.json."""
    out = {k: stats[k] for k in stats if k in ABOUT_KEYS or is_version_key(k)}
    out["paper_sources"] = paper_sources(stats)
    return out


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8", newline="\n")


def write_page_payloads(stats, page_dir):
    """Writes the slimmed stats.json and about.json into page_dir."""
    write_json(page_dir / "stats.json", slim_stats(stats))
    write_json(page_dir / "about.json", about_payload(stats))


SITE_URL = "https://nightrome.github.io/av-atlas"

# Site versioning. VERSION holds major.minor and is only ever changed by the
# maintainer. The patch number isn't stored anywhere in the repo: it is one
# more than whatever production's BUILD_INFO.json says, so a preview and the
# promote of that same build share a number (promote doesn't rebuild), two
# previews in a row don't burn two numbers, and the monthly CI job, which has
# no .deploy-cache/, still counts on from the live site.
VERSION_FILE = BASE / "VERSION"
PRODUCTION_BUILD_INFO_URL = f"{SITE_URL}/BUILD_INFO.json"
# deploy.py records the last version it pushed to production here. Only used
# when production can't be reached.
DEPLOY_STATE_FILE = BASE / ".deploy-cache" / "state.json"
# The live site was already v0.1.1 before BUILD_INFO.json carried a version.
UNVERSIONED_PRODUCTION = (0, 1, 1)
# Replaced with the build's version in every published page (in practice the
# About page's download file names). The nav bar doesn't show the version;
# the About page reads it from about.json's site_version.
VERSION_PLACEHOLDER = "__AV_ATLAS_VERSION__"
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def parse_version(text):
    """'0.1.2' -> (0, 1, 2); None for anything that isn't major.minor.patch."""
    m = VERSION_RE.match(str(text or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def format_version(v):
    return ".".join(str(x) for x in v)


def read_major_minor(path=VERSION_FILE):
    m = re.match(r"^(\d+)\.(\d+)$", Path(path).read_text(encoding="utf-8").strip())
    if not m:
        raise SystemExit(f"{path} must hold major.minor, e.g. 0.1")
    return int(m.group(1)), int(m.group(2))


def next_version(major_minor, live):
    """The version for a new build, given the version production serves.

    Same major.minor as production: one more patch. Different major.minor:
    the maintainer has bumped VERSION, so the patch starts again at 0."""
    if tuple(live[:2]) == tuple(major_minor):
        return (*major_minor, live[2] + 1)
    return (*major_minor, 0)


def fetch_production_version(url=PRODUCTION_BUILD_INFO_URL, timeout=15):
    """The version production serves, UNVERSIONED_PRODUCTION if its
    BUILD_INFO.json has no version (or doesn't exist). Raises OSError if
    production can't be reached at all."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return UNVERSIONED_PRODUCTION
        raise OSError(f"HTTP {e.code} from {url}") from e
    except ValueError as e:
        raise OSError(f"{url} is not valid JSON") from e
    return parse_version((info or {}).get("version")) or UNVERSIONED_PRODUCTION


def last_published_version(state_file=DEPLOY_STATE_FILE):
    try:
        state = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return parse_version(state.get("published_version"))


def resolve_version(override=None, major_minor=None, fetch=fetch_production_version,
                    state_file=DEPLOY_STATE_FILE):
    """The full version string for this build.

    override (the --version flag) wins outright. Otherwise the patch counts
    on from production. If production can't be reached, it counts on from
    the last version deploy.py recorded locally, with a warning, since a
    wrong guess here only means a skipped or repeated patch number."""
    if override:
        if not parse_version(override):
            raise SystemExit(f"--version must look like 0.1.2, got {override!r}")
        return override
    major_minor = major_minor or read_major_minor()
    try:
        live = fetch()
    except OSError as e:
        live = last_published_version(state_file)
        print(f"  WARNING: couldn't read production's version ({e}); counting on from "
              f"{format_version(live) + ' (last recorded deploy)' if live else 'v0.1.1, the last unversioned release'}")
        live = live or UNVERSIONED_PRODUCTION
    if tuple(live[:2]) > tuple(major_minor):
        print(f"  WARNING: production is already on {format_version(live)}, newer than "
              f"VERSION {major_minor[0]}.{major_minor[1]}. Is this checkout out of date?")
    return format_version(next_version(major_minor, live))


def stamp_json_version(path, version):
    """Add "site_version" to a published JSON object, in place.

    stats.json is tens of MB, so the key is spliced in after the opening
    brace rather than re-serialising the whole file. That keeps its
    formatting byte-for-byte apart from the new key."""
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except ValueError:
        return False
    if not isinstance(data, dict):
        return False
    if "site_version" in data:
        data["site_version"] = version
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8", newline="\n")
        return True
    brace = text.index("{")
    rest = text[brace + 1:]
    sep = "" if not data else ", "
    path.write_text(text[:brace + 1] + json.dumps("site_version") + ": " + json.dumps(version)
                    + sep + rest, encoding="utf-8", newline="\n")
    return True


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

# Pages that only show something with a ?name=/?title= (or, for compare,
# ?names=) parameter. Bare, they render "Unknown author" and the like, so
# they are left out of the sitemap; their real URLs are listed below.
DETAIL_TEMPLATES = {"author.html", "paper.html", "institution.html", "venue.html",
                    "country.html", "compare.html"}


def write_sitemap(page_dir, stats_path):
    """A sitemap.xml covering the listing pages plus the top detail pages.

    Detail pages set their own <title>/description at runtime (see
    setDetailPageMeta in filters.js); this is what gets a crawler to them in
    the first place.
    """
    import xml.sax.saxutils as sx

    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    urls = [f"{SITE_URL}/{p.name}" for p in sorted(html_pages(), key=lambda p: p.name)
            if p.name not in DETAIL_TEMPLATES]

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
    # Only venues with AV papers: by_venue counts the whole corpus, and a
    # venue page for, say, a medical imaging journal says "0 AV papers".
    av_venues = {p.get("venue") for p in papers}
    add("venue.html", "name",
        [v for v in (stats.get("corpus_stats") or {}).get("by_venue", {})
         if v in av_venues][:SITEMAP_LIMITS["venues"]])
    # There are only ~55 countries, so every one gets a page in the sitemap.
    add("country.html", "name",
        sorted({c for p in papers for c in (p.get("countries") or [])}))

    # When the content last changed (see content_updated in aggregate.py),
    # not the build date, so a rebuild of the same data does not tell
    # crawlers that all 9,000 pages are new.
    lastmod = stats.get("content_updated") or time.strftime("%Y-%m-%d")
    body = "\n".join(
        f"  <url><loc>{sx.escape(u)}</loc><lastmod>{lastmod}</lastmod></url>" for u in urls)
    (page_dir / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n</urlset>\n", encoding="utf-8", newline="\n")
    print(f"  sitemap.xml: {len(urls)} URLs")


def build_public_site(version):
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
        html = add_cache_bust(html).replace(VERSION_PLACEHOLDER, version)
        (page_dir / html_path.name).write_text(html, encoding="utf-8", newline="\n")

    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    write_page_payloads(stats, page_dir)
    # Not in hash_tree's content hash (deploy.py adds its own fields to it at
    # publish time), but the version it records is also baked into about.html
    # and stats.json, which are.
    (page_dir / "BUILD_INFO.json").write_text(json.dumps({
        "version": version,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }, indent=2), encoding="utf-8", newline="\n")

    # Every sharded data directory aggregate.py writes (see its AUTHOR_
    # DETAIL_DIR/ABSTRACTS_DIR/etc. comments) -- copied file-by-file rather
    # than rmtree+copytree-ing the whole directory, since an rmtree here hit
    # a real OneDrive directory-lock PermissionError in practice. Optional:
    # not every deploy necessarily has one yet (e.g. a fresh checkout before
    # its first full aggregate.py run).
    def copy_shard_dir(name):
        src = BASE / "data" / name
        dst = page_dir / name
        if not src.exists():
            return None
        dst.mkdir(parents=True, exist_ok=True)
        for shard_path in src.glob("*.json"):
            shutil.copy2(shard_path, dst / shard_path.name)
        return dst

    abstracts_dst = copy_shard_dir("abstracts")
    # Lazily fetched by index.html and friends only when the AV-relevance
    # filter is switched away from the default.
    non_av_dst = copy_shard_dir("non_av_papers")
    # Lazily fetched by index.html/network.html/paper.html/author.html for
    # just the titles they actually need.
    citations_dst = copy_shard_dir("citations")
    # Lazily fetched by author.html/authors.html/countries.html/
    # institution.html/paper.html for just the names they actually need --
    # see aggregate.py's AUTHOR_DETAIL_DIR comment.
    author_detail_dst = copy_shard_dir("author_detail")
    institution_authors_dst = copy_shard_dir("institution_authors")
    non_av_author_stats_dst = copy_shard_dir("non_av_author_stats")

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
    # matched by the *.html glob above. Copied as they are: no script carries
    # the version.
    for js_path in SITE_DIR.glob("*.js"):
        js = js_path.read_text(encoding="utf-8")
        (page_dir / js_path.name).write_text(js, encoding="utf-8", newline="\n")

    # page_dir persists across runs (rmtree-ing it hits a real, previously-hit
    # OneDrive directory-lock issue -- see the "Fix build scripts hanging on
    # OneDrive cloud-placeholder directories" fix elsewhere in this repo), so
    # a file this script no longer generates -- an old page that's been
    # renamed, or a dev-only file that got copied in by hand -- would
    # otherwise stay published forever. Delete anything present that isn't
    # part of the current expected output. Caught in practice: label_relevance.html
    # (a dev tool, never meant to publish) briefly shipped to gh-pages this way.
    expected = {p.name for p in html_pages()} | {p.name for p in SITE_DIR.glob("*.js")} \
        | {"stats.json", "about.json", "theme.css", "theme-light.css",
           "logo.svg", "og-image.png", "sitemap.xml", "robots.txt", "BUILD_INFO.json"}
    for existing in page_dir.iterdir():
        if existing.is_file() and existing.name not in expected:
            existing.unlink()
            print(f"  removed stale {existing.name}")

    # Every published JSON object at the top level (stats.json and any slim
    # copy of it) says which build it came from. BUILD_INFO.json already does.
    for json_path in sorted(page_dir.glob("*.json")):
        if json_path.name != "BUILD_INFO.json" and stamp_json_version(json_path, version):
            print(f"  {json_path.name}: site_version {version}")

    (PUBLIC_DIR / "robots.txt").write_text(
        ROBOTS_TXT + f"\nSitemap: {SITE_URL}/sitemap.xml\n", encoding="utf-8", newline="\n")
    write_sitemap(page_dir, stats_path)

    print(f"Wrote {PUBLIC_DIR}")
    for html_path in html_pages():
        print(f"  {html_path.name}")
    for js_path in SITE_DIR.glob("*.js"):
        print(f"  {js_path.name}")
    print(f"  stats.json, about.json")
    for name, dst in (
        ("abstracts", abstracts_dst), ("non_av_papers", non_av_dst), ("citations", citations_dst),
        ("author_detail", author_detail_dst), ("institution_authors", institution_authors_dst),
        ("non_av_author_stats", non_av_author_stats_dst),
    ):
        if dst is not None:
            print(f"  {name}/ ({len(list(dst.iterdir()))} shards)")
    print(f"  robots.txt (allow all)")


def run_step(label, script_name, *args):
    print(f"\n--- {label} ---")
    result = subprocess.run([sys.executable, script_name, *args], cwd=SCRIPTS_DIR)
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
    parser.add_argument("--allow-shrink", action="store_true",
                        help="Publish even if the new stats.json dropped more than 3%% on a gated number "
                             "(AV papers, papers with institutions or abstracts, in-corpus citations, "
                             "venues). For an intended drop, e.g. the first build after a matcher fix.")
    parser.add_argument("--version", dest="site_version", metavar="X.Y.Z",
                        help="Use this site version instead of counting on from production's "
                             "BUILD_INFO.json. Meant for tests and one-off rebuilds.")
    args = parser.parse_args()

    if args.publish_only:
        print("--- Publish-only: skipping corpus rebuild, repairs and tests ---")
        # A baseline left behind means the last full build was stopped by the
        # shrink check (or crashed). Check what's on disk against it, so
        # --publish-only can't be a way around that.
        if publish_gate.BASELINE_FILE.exists():
            print("\n--- Checking the corpus didn't shrink ---")
            publish_gate.check(allow_shrink=args.allow_shrink)
    else:
        print("\n--- Saving the shrink-check baseline ---")
        publish_gate.save_baseline()
        run_step("Fixing suspect venue names", "fix_suspect_venues.py")
        run_step("Stripping email addresses from tracked data", "email_addresses.py")
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
        run_step("Repairing wrong OpenAlex institutions", "repair_openalex_institution_errors.py")
        # The graph used to be rebuilt only when someone ran
        # build_citation_graph.py by hand, and the counts only when someone
        # then remembered apply_citation_sources.py too -- so a new graph
        # could sit on disk while stats.json kept the old counts. Both are
        # offline here: --match-only reads just the saved reference lists.
        run_step("Rematching citations (build_citation_graph.py --match-only)",
                 "build_citation_graph.py", "--match-only")
        run_step("Applying citation counts", "apply_citation_sources.py")
        run_step("Rebuilding stats (aggregate.py)", "aggregate.py")
        print("\n--- Checking the corpus didn't shrink ---")
        publish_gate.check(allow_shrink=args.allow_shrink)
        run_step("Running tests (run_tests.py)", "run_tests.py")
    print("\n--- Publishing public site ---")
    version = resolve_version(args.site_version)
    print(f"  site version {version}")
    build_public_site(version)
    # The downloadable corpus, built from the same stats.json the pages read,
    # so the download can never describe a different corpus than the site.
    print("\n--- Building data release ---")
    graph_path = BASE / "data" / "citation_graph.json"
    build_data_release.build(
        json.loads((BASE / "data" / "stats.json").read_text(encoding="utf-8")),
        json.loads(graph_path.read_text(encoding="utf-8")) if graph_path.exists() else {},
        version,
    )


if __name__ == "__main__":
    main()
