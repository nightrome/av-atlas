#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared PMLR (proceedings.mlr.press) fetcher, used for ICML and CoRL. A
volume's index page lists every paper's title and authors inline; the
abstract needs one request per paper.

The index is parsed one <div class="paper"> block at a time. The old CoRL
parser ran a single lazy regex over the whole page and only accepted slugs
matching [a-z0-9]+, so a hyphenated slug (abou-chakra24a) made the match run
on into the next paper's "abs" link: paper k got paper k+1's URL and
abstract, and paper k+1 was dropped. That hit 11 CoRL papers across 2019-2024
and would have hit 68 of ICML 2025's 3,330. Parsing per block means one bad
block can only lose itself, and list_papers() raises instead of returning a
short list, so a layout change can't quietly truncate a venue file.

The volume number for each venue-year is in PMLR_VOLUMES (PMLR numbers
volumes in publication order, so it can't be derived from the year). A year
that isn't in the table can be fetched with --volume.

Writes data/venues/<venue><year>.json with title, authors and abstract per
paper (conference/year come from the filename, see merge_corpus.py). A year
whose file already exists is skipped unless --force is given.

Usage:
  python fetch_pmlr.py ICML 2025
  python fetch_pmlr.py CoRL 2017 2025 --force
  python fetch_pmlr.py ICML 2026 --volume 300
"""
import argparse
import html
import json
import re
import sys
import time

from fetch_common import OUT_DIR, fetch

PMLR_VOLUMES = {
    "CoRL": {
        2017: 78, 2018: 87, 2019: 100, 2020: 155, 2021: 164,
        2022: 205, 2023: 229, 2024: 270, 2025: 305,
    },
    "ICML": {2025: 267},
}

# PMLR rate-limits nothing we've seen, but it is a small volunteer-run site.
REQUEST_DELAY = 0.15
RETRY = dict(max_retries=3, retry_status=(429, 500, 502, 503, 504), backoff=10)

_BLOCK_RE = re.compile(r'<div class="paper">(.*?)</div>', re.S)
_TITLE_RE = re.compile(r'<p class="title">(.*?)</p>', re.S)
_AUTHORS_RE = re.compile(r'<span class="authors">(.*?)</span>', re.S)
_ABS_LINK_RE = re.compile(
    r'<a href="(https://proceedings\.mlr\.press/v\d+/[a-z0-9-]+\.html)">abs</a>')
_ABSTRACT_RE = re.compile(r'<div id="abstract" class="abstract">\s*(.*?)\s*</div>', re.S)


def _clean(text):
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text.replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()


def parse_volume(page):
    """Every paper on a PMLR volume index page, as {title, authors, url}.
    Raises ValueError if any <div class="paper"> block can't be parsed, so a
    changed page layout stops the fetch instead of writing a partial file."""
    papers = []
    bad = 0
    for block in _BLOCK_RE.findall(page):
        title = _TITLE_RE.search(block)
        authors = _AUTHORS_RE.search(block)
        url = _ABS_LINK_RE.search(block)
        if not (title and url):
            bad += 1
            continue
        papers.append({
            "title": _clean(title.group(1)),
            "authors": _clean(authors.group(1)) if authors else "",
            "url": url.group(1),
        })
    if bad:
        raise ValueError(f"{bad} of {bad + len(papers)} paper blocks could not be parsed")
    return papers


def parse_abstract(page):
    m = _ABSTRACT_RE.search(page)
    return (_clean(m.group(1)) or None) if m else None


def list_papers(volume):
    return parse_volume(fetch(f"https://proceedings.mlr.press/v{volume}/", **RETRY))


def fetch_abstract(url):
    return parse_abstract(fetch(url, **RETRY))


def fetch_year(venue, year, volume, force=False):
    """Fetches one venue-year into data/venues/. Returns the number of papers
    written, or None if the year was skipped."""
    out_file = OUT_DIR / f"{venue.lower()}{year}.json"
    if out_file.exists() and not force:
        print(f"{venue}{year}: {out_file.name} exists, skipping (use --force to refetch)", flush=True)
        return None
    try:
        papers = list_papers(volume)
    except Exception as e:
        print(f"{venue}{year} (v{volume}): listing failed ({e}), skipping", flush=True)
        return None
    if not papers:
        print(f"{venue}{year} (v{volume}): empty listing, skipping", flush=True)
        return None
    print(f"{venue}{year} (v{volume}): {len(papers)} papers found, fetching abstracts...", flush=True)

    results = []
    failed = 0
    # Progress goes to a side file; the venue file itself is only written
    # once the whole list is done, so an interrupted --force run can't leave
    # a half-length venue file behind.
    tmp_file = out_file.with_suffix(".json.partial")
    for i, p in enumerate(papers, 1):
        try:
            abstract = fetch_abstract(p["url"])
        except Exception as e:
            print(f"  [{venue}{year} {i}/{len(papers)}] FAILED: {e}", flush=True)
            abstract = None
            failed += 1
        results.append({"title": p["title"], "authors": p["authors"], "abstract": abstract})
        if i % 200 == 0:
            tmp_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        time.sleep(REQUEST_DELAY)
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    tmp_file.unlink(missing_ok=True)
    print(f"{venue}{year}: done, {len(results)} papers, {failed} abstract fetches failed", flush=True)
    return len(results)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fetch a PMLR-hosted venue (ICML, CoRL) with abstracts.")
    ap.add_argument("venue", choices=sorted(PMLR_VOLUMES))
    ap.add_argument("start_year", type=int)
    ap.add_argument("end_year", type=int, nargs="?")
    ap.add_argument("--volume", type=int, help="PMLR volume, for a single year not yet in PMLR_VOLUMES")
    ap.add_argument("--force", action="store_true", help="refetch years whose file already exists")
    args = ap.parse_args(argv)
    end_year = args.end_year or args.start_year
    if args.volume and end_year != args.start_year:
        ap.error("--volume only makes sense for a single year")

    for year in range(args.start_year, end_year + 1):
        volume = args.volume or PMLR_VOLUMES[args.venue].get(year)
        if not volume:
            print(f"{args.venue}{year}: no PMLR volume known, skipping", flush=True)
            continue
        fetch_year(args.venue, year, volume, force=args.force)
    print(f"ALL DONE: {args.venue} {args.start_year}-{end_year}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
