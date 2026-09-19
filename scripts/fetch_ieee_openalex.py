#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bulk pull for IEEE-hosted venues (ICRA, IROS) via OpenAlex instead of
IEEE Xplore -- IEEE Xplore blocks direct HTTP requests entirely (confirmed
HTTP 418), and even via a real browser it only exposes abstracts one
paper-click at a time with no bulk API, so it can't run unattended.
OpenAlex indexes IEEE's own metadata (title, authors, abstract via inverted
index, citation count) and, unlike IEEE Xplore, exposes it through a
paginated bulk API -- no keyword filtering, just venue+year, so this is
still the complete population.

OpenAlex splits each conference into a SEPARATE source per year (e.g.
"2022 International Conference on Robotics and Automation (ICRA)" is its
own source, distinct from other years) rather than one stable ID across
years, so this first resolves the source ID for each requested year, then
paginates its works.

Usage: python fetch_ieee_openalex.py ICRA 2013 2026
       python fetch_ieee_openalex.py IROS 2013 2026
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
from datetime import datetime, timezone

from fetch_common import BASE, OUT_DIR, HEADERS, fetch as _fetch

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")
CONTACT_EMAIL = "holger@it-caesar.com"

VENUE_SEARCH_TERMS = {
    "ICRA": "International Conference on Robotics and Automation",
    "IROS": "Intelligent Robots and Systems",
}


def fetch_json(url, max_retries=4):
    # OpenAlex rate-limits with 429s under sustained use -- exponential
    # backoff (5s, 10s, 20s, ...) rather than fetch_common's default linear
    # schedule, kept as its own loop here since that's a real, deliberate
    # difference from DBLP's tuning, not incidental duplication.
    delay = 5.0
    for attempt in range(max_retries):
        try:
            return json.loads(_fetch(url, timeout=20, headers=HEADERS))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def find_source_id(conf, year):
    term = VENUE_SEARCH_TERMS[conf]
    params = urllib.parse.urlencode({"search": term, "per-page": 50, "mailto": CONTACT_EMAIL})
    data = fetch_json(f"https://api.openalex.org/sources?{params}")
    candidates = []
    for s in data.get("results", []):
        name = s.get("display_name", "")
        # Require the exact acronym in parentheses -- e.g. "(ICRA)" -- not just
        # overlapping generic words, which also match unrelated conferences
        # like CACRE/ICARCV/AQTR that happen to share "robotics"/"automation".
        # This confirmed a real bug: "ICRA2020"/"ICRA2021" had matched CACRE.
        if str(year) in name and f"({conf})" in name:
            candidates.append(s)
    if not candidates:
        return None
    candidates.sort(key=lambda s: s.get("works_count", 0), reverse=True)
    return candidates[0]["id"].rsplit("/", 1)[-1]


def reconstruct_abstract(inverted_index):
    if not inverted_index:
        return None
    words = sorted(((pos, word) for word, positions in inverted_index.items() for pos in positions))
    return " ".join(w for _, w in words)


# OpenAlex titles for IEEE-hosted papers occasionally carry raw HTML markup
# from the source metadata (e.g. "R<sup>3</sup>LIVE") -- strip it so titles
# render as plain text like every other venue's, instead of literal tags.
def strip_html(s):
    return re.sub(r"<[^>]+>", "", s) if s else s


def fetch_works(source_id):
    papers = []
    cursor = "*"
    while cursor:
        params = urllib.parse.urlencode({
            "filter": f"primary_location.source.id:{source_id}",
            "per-page": 200, "cursor": cursor, "mailto": CONTACT_EMAIL,
        })
        data = fetch_json(f"https://api.openalex.org/works?{params}")
        for w in data.get("results", []):
            authors = [a.get("author", {}).get("display_name") for a in (w.get("authorships") or [])]
            papers.append({
                "title": strip_html(w.get("title")),
                "authors": ", ".join(a for a in authors if a),
                "abstract": strip_html(reconstruct_abstract(w.get("abstract_inverted_index"))),
                # Deliberately NOT capturing w["cited_by_count"] -- the site
                # only ever ranks/displays the in-corpus citation graph (see
                # citation_count() in aggregate.py), never an external
                # provider's count. Storing it here anyway once let a paper
                # rank in the top 50 by this number while displaying 0
                # citations client-side, a real user-reported bug -- dropped
                # at the source so it can't recur.
                "doi": w.get("doi"),
            })
        cursor = (data.get("meta") or {}).get("next_cursor")
        time.sleep(0.15)
    return papers


def main():
    if len(sys.argv) < 4:
        raise SystemExit("Usage: python fetch_ieee_openalex.py <ICRA|IROS> <START_YEAR> <END_YEAR>")
    conf, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    for year in range(start, end + 1):
        try:
            source_id = find_source_id(conf, year)
        except Exception as e:
            print(f"{conf}{year}: source lookup failed ({e}), skipping", flush=True)
            continue
        if not source_id:
            print(f"{conf}{year}: no matching OpenAlex source found, skipping", flush=True)
            continue
        print(f"{conf}{year}: source {source_id}, fetching works...", flush=True)
        try:
            papers = fetch_works(source_id)
        except Exception as e:
            print(f"{conf}{year}: works fetch failed ({e})", flush=True)
            continue
        out_file = OUT_DIR / f"{conf.lower()}{year}.json"
        for p in papers:
            p["conference"], p["year"] = conf, year
        out_file.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        n_with_abstract = sum(1 for p in papers if p["abstract"])
        print(f"{conf}{year}: done, {len(papers)} papers ({n_with_abstract} with abstract)", flush=True)

    print(f"ALL DONE: {conf} {start}-{end}", flush=True)


if __name__ == "__main__":
    main()
