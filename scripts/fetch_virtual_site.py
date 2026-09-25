#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Accepted papers from a conference's "virtual site" (the miniconf platform
behind iclr.cc, icml.cc, neurips.cc and eccv.ecva.net). Each edition has one
static JSON dump of every oral and poster event:

  https://<host>/static/virtual/data/<venue>-<year>-orals-posters.json

It's one unauthenticated request with titles and authors, which makes it the
quickest full listing for an edition that DBLP (behind a bot check) and
OpenReview (403 ChallengeRequiredError) won't serve us, and for ECCV before
ecva.net publishes its own page.

What's kept: only events whose OpenReview group is the main conference
(".../<year>/Conference") or, for ICML, the position paper track, which is
part of the ICML proceedings (PMLR v267 has 3,330 papers; the ICML 2025 dump
has 3,331 unique titles on those two tracks). Blog posts, TMLR/JMLR journal
presentations, workshops and Findings aren't. Orals and spotlights are listed
a second time next to their poster, so events are deduplicated by title.

Abstracts: some dumps carry them inline (ICLR 2025, ICML 2024 and 2025);
the 2026 ones don't. With --abstracts, each paper's poster page
(https://<host>/virtual/<year>/poster/<id>) is fetched for it, one request per
paper, about 1.2s each. Abstracts already in an existing output file are
reused, so rerunning with --force after an interruption picks up where the
last run stopped. Don't use --abstracts for ECCV: its poster pages show text
extracted from the PDF with the spaces at line breaks missing ("pedestriansis
critical"), which is worse than waiting for ecva.net or the arXiv/Semantic
Scholar backfills.

Writes data/venues/<venue><year>.json (title, authors, abstract). An existing
file is left alone unless --force is given.

Usage:
  python fetch_virtual_site.py ICLR 2026 --abstracts
  python fetch_virtual_site.py ECCV 2026
  python fetch_virtual_site.py ICML 2026 --abstracts --force
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.error

from fetch_common import OUT_DIR, fetch

HOSTS = {
    "ICLR": "iclr.cc",
    "ICML": "icml.cc",
    "NeurIPS": "neurips.cc",
    "ECCV": "eccv.ecva.net",
}
# OpenReview group suffixes that count as the main proceedings, per venue.
TRACKS = {
    "ICML": ("Conference", "Position_Paper_Track"),
}
DEFAULT_TRACKS = ("Conference",)

REQUEST_DELAY = 0.5
RETRY = dict(max_retries=3, retry_status=(429, 500, 502, 503, 504), backoff=15)
# Give up on per-paper fetches after this many failures in a row: the site is
# down or is telling us to stop, and hammering it won't help.
MAX_CONSECUTIVE_FAILURES = 5

_ABSTRACT_RE = re.compile(r'<div class="abstract-text-inner">(.*?)</div>', re.S)


def _clean(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _title_key(title):
    return re.sub(r"[^a-z0-9]", "", _clean(title).lower())


def dump_url(venue, year):
    return f"https://{HOSTS[venue]}/static/virtual/data/{venue.lower()}-{year}-orals-posters.json"


def select_papers(dump, venue, year):
    """The main-track papers in a virtual-site dump, one per title, as
    {title, authors, abstract, page}. `page` is the poster page path, used
    only for fetching the abstract."""
    tracks = TRACKS.get(venue, DEFAULT_TRACKS)
    track_re = re.compile(r"/%d/(%s)$" % (year, "|".join(tracks)))
    papers = {}
    for ev in dump.get("results") or []:
        if not track_re.search(ev.get("sourceurl") or ""):
            continue
        title = _clean(ev.get("name"))
        key = _title_key(title)
        if not key:
            continue
        abstract = _clean(ev.get("abstract")) or None
        page = ev.get("virtualsite_url") or None
        prev = papers.get(key)
        if prev:
            # Same paper listed again as an oral/spotlight. Keep the first
            # record but take anything it was missing.
            prev["abstract"] = prev["abstract"] or abstract
            if "/poster/" in (page or "") and "/poster/" not in (prev["page"] or ""):
                prev["page"] = page
            continue
        authors = ", ".join(_clean(a.get("fullname")) for a in ev.get("authors") or [] if a.get("fullname"))
        papers[key] = {"title": title, "authors": authors, "abstract": abstract, "page": page}
    return list(papers.values())


def parse_poster_abstract(page):
    m = _ABSTRACT_RE.search(page)
    return (_clean(m.group(1)) or None) if m else None


def load_existing_abstracts(out_file):
    if not out_file.exists():
        return {}
    try:
        old = json.loads(out_file.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {_title_key(p.get("title")): p["abstract"] for p in old if p.get("abstract")}


def fill_abstracts(papers, host, save, delay=REQUEST_DELAY):
    """Fetches the poster page of every paper still missing an abstract.
    Calls save() every 200 papers. Returns (requests made, failures)."""
    requests = failures = consecutive = 0
    todo = [p for p in papers if not p["abstract"] and p["page"]]
    print(f"  {len(todo)} poster pages to fetch", flush=True)
    for i, p in enumerate(todo, 1):
        requests += 1
        try:
            p["abstract"] = parse_poster_abstract(fetch(f"https://{host}{p['page']}", **RETRY))
            consecutive = 0
        except Exception as e:
            failures += 1
            consecutive += 1
            print(f"  [{i}/{len(todo)}] FAILED {p['page']}: {e}", flush=True)
            if consecutive >= MAX_CONSECUTIVE_FAILURES or (
                    isinstance(e, urllib.error.HTTPError) and e.code in (403, 429)):
                print("  stopping: the site is refusing requests; rerun later to resume", flush=True)
                break
        if i % 200 == 0:
            save()
            print(f"  [{i}/{len(todo)}] progress saved", flush=True)
        time.sleep(delay)
    return requests, failures


def write_papers(out_file, papers):
    rows = [{"title": p["title"], "authors": p["authors"], "abstract": p["abstract"]} for p in papers]
    out_file.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch an edition's accepted papers from its virtual-site JSON.")
    ap.add_argument("venue", choices=sorted(HOSTS))
    ap.add_argument("year", type=int)
    ap.add_argument("--abstracts", action="store_true", help="fetch missing abstracts from the poster pages")
    ap.add_argument("--force", action="store_true", help="rewrite an existing venue file")
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY, help="seconds between poster-page requests")
    args = ap.parse_args(argv)
    venue, year = args.venue, args.year

    out_file = OUT_DIR / f"{venue.lower()}{year}.json"
    if out_file.exists() and not args.force:
        print(f"{out_file.name} exists, skipping (use --force to rewrite it)", flush=True)
        return 0

    dump = json.loads(fetch(dump_url(venue, year), timeout=120, **RETRY))
    papers = select_papers(dump, venue, year)
    print(f"{venue}{year}: {dump.get('count')} events, {len(papers)} main-track papers", flush=True)
    # A dump for an edition that hasn't happened yet comes back as 200 with
    # count 0 (NeurIPS 2026 in September 2026). Never overwrite with that.
    if len(papers) < 100:
        print(f"{venue}{year}: too few papers, not writing {out_file.name}", flush=True)
        return 1

    existing = load_existing_abstracts(out_file)
    for p in papers:
        p["abstract"] = p["abstract"] or existing.get(_title_key(p["title"]))

    if args.abstracts:
        requests, failures = fill_abstracts(papers, HOSTS[venue], lambda: write_papers(out_file, papers),
                                            delay=args.delay)
        print(f"  {requests} poster-page requests, {failures} failed", flush=True)
    write_papers(out_file, papers)
    n_abs = sum(1 for p in papers if p["abstract"])
    print(f"{venue}{year}: wrote {len(papers)} papers ({n_abs} with abstracts) to {out_file.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
