#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches RSS (Robotics: Science and Systems) papers from the conference's own
proceedings site, www.roboticsproceedings.org. One table-of-contents page per
volume plus one page per paper, which carries the title, authors, abstract and
a DOI (10.15607/RSS.<year>...).

The volume number is year - 2004 (rss21 is 2025, rss22 is 2026). Each paper
page also states its year in citation_publication_date and in the BibTeX
entry, and we check that against the year asked for, so a wrong volume guess
fails loudly instead of filing papers under the wrong year.

RSS 2012-2024 still come from DBLP (fetch_dblp_listing.py, no abstracts).
This script can refetch those years too, but that hasn't been done.

Writes data/venues/rss<year>.json. Exits non-zero if any paper in the
listing couldn't be fetched, so a half-finished run doesn't pass for a
complete one.

Usage:
  python scripts/fetch_rss.py 2025            # one year
  python scripts/fetch_rss.py 2025 2026       # a range of years
  python scripts/fetch_rss.py 2026 --limit 5  # bounded test batch
"""
import html
import json
import re
import sys
import time

from fetch_common import OUT_DIR, fetch

RSS_BASE = "https://www.roboticsproceedings.org"
FIRST_YEAR = 2005  # rss01


def volume_for_year(year):
    return f"rss{year - 2004:02d}"


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def parse_index(page):
    """[{"page": "p001.html", "title": ...}, ...] from a volume's index.html,
    in listing order. Only the pNNN.html links count; the menu on the left
    links to every other volume and must not be read as papers."""
    entries = re.findall(r'<a href="(p\d+\.html)">(.*?)</a>', page, re.S)
    seen = set()
    papers = []
    for href, title in entries:
        if href in seen:
            continue
        seen.add(href)
        papers.append({"page": href, "title": _clean(title)})
    return papers


def parse_paper(page):
    """Title, authors, abstract, DOI and year from one paper page."""
    title_m = re.search(r'<meta name="citation_title" content="([^"]*)"', page)
    authors = [_clean(a) for a in re.findall(r'<meta name="citation_author" content="([^"]*)"', page)]
    abstract_m = re.search(r"<b>Abstract:</b>\s*</p>\s*<p[^>]*>(.*?)</p>", page, re.S)
    doi_m = re.search(r"DOI\s*=\s*\{([^}]+)\}", page)
    year_m = (re.search(r'<meta name="citation_publication_date" content="(\d{4})', page)
              or re.search(r"YEAR\s*=\s*\{(\d{4})\}", page))
    return {
        "title": _clean(title_m.group(1)) if title_m else None,
        "authors": ", ".join(a for a in authors if a) or None,
        "abstract": (_clean(abstract_m.group(1)) or None) if abstract_m else None,
        "doi": f"https://doi.org/{doi_m.group(1).strip()}" if doi_m else None,
        "year": int(year_m.group(1)) if year_m else None,
    }


def fetch_retrying(url, attempts=3, pause=10):
    """fetch() with a retry on any network error. The site sometimes lets a
    single request time out in the middle of a run (WinError 10060), which
    fetch_common's retry doesn't cover because it isn't a ConnectionError."""
    for attempt in range(attempts):
        try:
            return fetch(url, max_retries=3)
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause)


def fetch_year(year, limit=None, delay=0.5):
    """Returns (papers, n_listed, n_failed) for one RSS year."""
    vol_url = f"{RSS_BASE}/{volume_for_year(year)}"
    listing = parse_index(fetch(f"{vol_url}/index.html", max_retries=3))
    n_listed = len(listing)
    if limit:
        listing = listing[:limit]
    results = []
    n_failed = 0
    for i, entry in enumerate(listing, 1):
        url = f"{vol_url}/{entry['page']}"
        try:
            detail = parse_paper(fetch_retrying(url))
        except Exception as e:
            print(f"  [RSS{year} {i}/{len(listing)}] FAILED {url}: {e}", flush=True)
            n_failed += 1
            continue
        if detail["year"] != year:
            raise SystemExit(f"{url} says year {detail['year']}, expected {year}: "
                             f"the volume-number rule no longer holds, stopping")
        results.append({
            "title": detail["title"] or entry["title"],
            "authors": detail["authors"],
            "abstract": detail["abstract"],
            "doi": detail["doi"],
            "source_url": url,
        })
        time.sleep(delay)
    return results, n_listed, n_failed


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: python fetch_rss.py <YEAR> [<END_YEAR>] [--limit N]")
    start = int(args[0])
    end = int(args[1]) if len(args) > 1 else start
    if start < FIRST_YEAR:
        raise SystemExit(f"roboticsproceedings.org starts at {FIRST_YEAR}")
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    any_failed = False
    for year in range(start, end + 1):
        papers, n_listed, n_failed = fetch_year(year, limit)
        if not n_listed:
            print(f"RSS{year}: empty listing, skipping", flush=True)
            continue
        out_file = OUT_DIR / f"rss{year}.json"
        out_file.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        n_abs = sum(1 for p in papers if p["abstract"])
        print(f"RSS{year}: wrote {len(papers)} of {n_listed} listed papers to {out_file} "
              f"({n_abs} with an abstract)", flush=True)
        if n_failed:
            print(f"RSS{year}: {n_failed} papers failed, rerun this year", flush=True)
            any_failed = True
    if any_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
