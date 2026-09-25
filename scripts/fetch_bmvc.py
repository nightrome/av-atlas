#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches BMVC main-conference papers from the conference's own proceedings
site. The listing page gives each paper's title and its authors with their
affiliations; one page per paper adds the abstract.

BMVC moves to a new site with a new layout every year, so there's no single
URL pattern to follow. LISTINGS maps each supported year to its listing page,
and the parser below is written against that year's layout. A new year needs
its URL added here and a check that the layout still matches (the test
fixtures in scripts/tests/test_fetch_bmvc.py show what 2025 looks like).
Workshop papers are left out, as for every other venue.

BMVC 2012-2024 still come from DBLP (fetch_dblp_listing.py, no abstracts).

Writes data/venues/bmvc<year>.json. Exits non-zero if any listed paper
couldn't be fetched.

Usage:
  python scripts/fetch_bmvc.py 2025            # one year
  python scripts/fetch_bmvc.py 2025 --limit 5  # bounded test batch
"""
import html
import json
import re
import sys
import time
from urllib.parse import urljoin

from fetch_common import OUT_DIR, fetch

LISTINGS = {
    2025: "https://bmvc2025.bmva.org/proceedings/conference-proceedings/",
}


def _clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def split_author(entry):
    """("Name", "Affiliation") from "Name (Affiliation)". The affiliation can
    carry its own parentheses ("... Lausanne (EPFL)"), so only the first
    " (" splits, and only the one closing parenthesis at the end is dropped."""
    entry = entry.strip()
    name, sep, rest = entry.partition(" (")
    if not sep:
        return entry, None
    if rest.endswith(")"):
        rest = rest[:-1]
    return name.strip(), rest.strip() or None


def parse_listing(page):
    """[{"path": "/proceedings/12/", "title": ..., "authors": [...],
    "affiliations": [...]}, ...] from the 2025 listing table. Authors are
    separated by "; " there, each followed by its affiliation in brackets."""
    papers = []
    seen = set()
    for row in re.findall(r'<tr id="paper">(.*?)</tr>', page, re.S):
        m = re.search(r'<a href="(/proceedings/\d+/)">(.*?)</a></strong><br\s*/?>(.*?)<br\s*/?>', row, re.S)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        people = [split_author(a) for a in _clean(m.group(3)).split(";") if a.strip()]
        papers.append({
            "path": m.group(1),
            "title": _clean(m.group(2)),
            "authors": [n for n, _ in people],
            "affiliations": [a for _, a in people],
        })
    return papers


def parse_abstract(page):
    m = re.search(r'<h2 id="abstract">Abstract</h2>(.*?)<h2', page, re.S)
    return (_clean(m.group(1)) or None) if m else None


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
    """Returns (papers, n_listed, n_failed) for one BMVC year."""
    listing_url = LISTINGS[year]
    listing = parse_listing(fetch(listing_url, max_retries=3))
    n_listed = len(listing)
    if limit:
        listing = listing[:limit]
    results = []
    n_failed = 0
    for i, entry in enumerate(listing, 1):
        url = urljoin(listing_url, entry["path"])
        try:
            abstract = parse_abstract(fetch_retrying(url))
        except Exception as e:
            print(f"  [BMVC{year} {i}/{len(listing)}] FAILED {url}: {e}", flush=True)
            n_failed += 1
            continue
        results.append({
            "title": entry["title"],
            "authors": ", ".join(entry["authors"]) or None,
            "affiliations": entry["affiliations"],
            "abstract": abstract,
            "source_url": url,
        })
        time.sleep(delay)
    return results, n_listed, n_failed


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit("Usage: python fetch_bmvc.py <YEAR> [--limit N]")
    year = int(args[0])
    if year not in LISTINGS:
        raise SystemExit(f"No listing URL for BMVC {year}; add it to LISTINGS "
                         f"(known: {', '.join(map(str, sorted(LISTINGS)))})")
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    papers, n_listed, n_failed = fetch_year(year, limit)
    if not n_listed:
        raise SystemExit(f"BMVC{year}: the listing parsed to 0 papers, so the layout has "
                         f"probably changed; nothing written")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"bmvc{year}.json"
    out_file.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    n_abs = sum(1 for p in papers if p["abstract"])
    print(f"BMVC{year}: wrote {len(papers)} of {n_listed} listed papers to {out_file} "
          f"({n_abs} with an abstract)", flush=True)
    if n_failed:
        print(f"BMVC{year}: {n_failed} papers failed, rerun this year", flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
