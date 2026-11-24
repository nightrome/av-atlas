#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bulk backfill of missing abstracts from OpenAlex, venue by venue.

OpenAlex charges for searches (10 credits each, and a free key or IP gets about
1000 credits a day) but a plain list request costs 1, so this does not look
papers up one by one. For each venue with papers still missing an abstract it
finds the OpenAlex source for the venue, then lists the source's works year by
year, 200 per page, with the abstract. Only abstracts of papers we are missing,
matched on exact normalized title, are kept.

Resumable: data/abstracts_web_openalex_bulk.json (written here, applied by
fetch_abstracts_web.py --apply) records the source found for each venue and
which (venue, year) pages are finished. Rate limits are handled by waiting for
Retry-After (OpenAlex sends the seconds until its budget resets, which can be
hours; the script simply sleeps that long) and by exponential backoff for other
errors. An OPENALEX_API_KEY in the environment or .env is used when present.

Usage:
  python fetch_abstracts_openalex.py                 # run until done
  python fetch_abstracts_openalex.py --max-hours 6
  python fetch_abstracts_openalex.py --venue ICRA --min-missing 1
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict

import checked_titles
from atomic_write import write_json_atomic
from fetch_abstracts_web import (BACKOFF_CAP, BACKOFF_START, CONTACT, DATA_DIR, PAPERS_FILE, Unavailable,
                                 clean_abstract, http_get, load_json, norm)
from fetch_common import BASE

OUT_FILE = DATA_DIR / "abstracts_web_openalex_bulk.json"
API = "https://api.openalex.org"
GAP = 0.3
MAX_WAIT = 8 * 3600  # an exhausted daily budget resets within the day
MIN_MISSING = 100

# Venue (as papers_full.json spells it) -> what to search for in OpenAlex's source list.
SEARCH_NAMES = {
    "AAAI": "Proceedings of the AAAI Conference on Artificial Intelligence",
    "ICLR": "International Conference on Learning Representations",
    "ICML": "International Conference on Machine Learning",
    "ICRA": "IEEE International Conference on Robotics and Automation",
    "IROS": "IEEE/RSJ International Conference on Intelligent Robots and Systems",
    "RA-L": "IEEE Robotics and Automation Letters",
    "T-ITS": "IEEE Transactions on Intelligent Transportation Systems",
    "ITSC": "IEEE International Conference on Intelligent Transportation Systems",
    "IV": "IEEE Intelligent Vehicles Symposium",
    "TPAMI": "IEEE Transactions on Pattern Analysis and Machine Intelligence",
    "T-RO": "IEEE Transactions on Robotics",
    "ECCV": "European Conference on Computer Vision",
    "BMVC": "British Machine Vision Conference",
    "IJCV": "International Journal of Computer Vision",
    "T-IV": "IEEE Transactions on Intelligent Vehicles",
}

_last = [0.0]


def api_key():
    key = os.environ.get("OPENALEX_API_KEY")
    env = BASE / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENALEX_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"\'')
    return key


def get_json(path_and_query, sleep=time.sleep, clock=time.monotonic, fetch=http_get):
    """One API call with the waiting rules above; returns parsed JSON."""
    url = API + path_and_query + ("&" if "?" in path_and_query else "?") + "mailto=" + CONTACT
    key = api_key()
    if key:
        url += "&api_key=" + key
    backoff = BACKOFF_START
    while True:
        gap = _last[0] + GAP - clock()
        if gap > 0:
            sleep(gap)
        _last[0] = clock()
        try:
            raw = fetch(url)
            return json.loads(raw) if raw else {}
        except Unavailable as e:
            wait = min(max(e.retry_after, backoff), MAX_WAIT)
            print(f"  OpenAlex {e}; waiting {wait / 60:.0f} min", flush=True)
            sleep(wait)
            backoff = min(backoff * 2, BACKOFF_CAP)


def invert(index):
    slots = {}
    for word, positions in (index or {}).items():
        for pos in positions:
            slots[pos] = word
    return " ".join(slots[i] for i in sorted(slots))


def resolve_source(venue):
    q = urllib.parse.quote(SEARCH_NAMES.get(venue, venue))
    results = get_json(f"/sources?search={q}&per-page=5&select=id,display_name,works_count").get("results", [])
    # The proceedings/journal itself is the best match; ties go to the one with most works.
    results.sort(key=lambda r: -(r.get("works_count") or 0))
    return [{"id": r["id"].rsplit("/", 1)[-1], "name": r.get("display_name")} for r in results[:1]]


def harvest_year(source_id, year, wanted, found):
    """List all works of one source and year; keep abstracts of the wanted titles."""
    cursor = "*"
    pages = seen = 0
    while cursor:
        data = get_json(f"/works?filter=primary_location.source.id:{source_id},publication_year:{year}"
                        f"&per-page=200&select=title,abstract_inverted_index&cursor={urllib.parse.quote(cursor)}")
        pages += 1
        for work in data.get("results", []):
            seen += 1
            nt = norm(work.get("title"))
            if nt in wanted and nt not in found:
                text = clean_abstract(invert(work.get("abstract_inverted_index")))
                if text:
                    found[nt] = {"text": text, "source": "openalex"}
        cursor = (data.get("meta") or {}).get("next_cursor") if data.get("results") else None
    return pages, seen


def missing_by_venue(papers, skip=()):
    out = defaultdict(lambda: defaultdict(set))
    for p in papers:
        if (p.get("abstract") or "").strip():
            continue
        nt = norm(p.get("title"))
        if nt and nt not in skip and p.get("venue") and p.get("year"):
            out[p["venue"]][p["year"]].add(nt)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--venue", default=None, help="only this venue, as papers_full.json spells it")
    ap.add_argument("--min-missing", type=int, default=MIN_MISSING,
                    help="skip venues with fewer papers missing an abstract (default %(default)s)")
    args = ap.parse_args(argv)
    started = time.monotonic()
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    state = load_json(OUT_FILE, {"abstracts": {}, "sources": {}, "done": []})
    state.setdefault("abstracts", {})
    state.setdefault("sources", {})
    state.setdefault("done", [])
    missing = missing_by_venue(papers)
    totals = Counter({v: sum(len(t) for t in ys.values()) for v, ys in missing.items()})
    venues = [v for v, n in totals.most_common() if n >= args.min_missing and (not args.venue or v == args.venue)]
    print(f"{len(venues)} venues with at least {args.min_missing} papers missing an abstract", flush=True)
    for venue in venues:
        if venue not in state["sources"]:
            state["sources"][venue] = resolve_source(venue)
            print(f"{venue}: OpenAlex source {state['sources'][venue]}", flush=True)
            write_json_atomic(OUT_FILE, state)
        for src in state["sources"][venue]:
            for year in sorted(missing[venue], reverse=True):
                key = f"{src['id']}:{year}"
                wanted = checked_titles.left(state, key, {t for t in missing[venue][year] if t not in state["abstracts"]},
                                             state["done"])
                if not wanted:
                    continue
                if args.max_hours and time.monotonic() - started > args.max_hours * 3600:
                    print("Time budget used up; stopping.", flush=True)
                    write_json_atomic(OUT_FILE, state)
                    return 0
                before = len(state["abstracts"])
                pages, seen = harvest_year(src["id"], year, wanted, state["abstracts"])
                checked_titles.mark(state, key, wanted)
                write_json_atomic(OUT_FILE, state)
                print(f"{venue} {year}: {seen} works in {pages} pages, "
                      f"{len(state['abstracts']) - before} of {len(missing[venue][year])} missing abstracts found",
                      flush=True)
    print(f"Finished: {len(state['abstracts'])} abstracts in total.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
