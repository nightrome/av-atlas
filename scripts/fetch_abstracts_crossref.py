#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bulk backfill of missing abstracts for journals from the Crossref REST API.

For each journal with papers still missing an abstract this lists the journal's
works year by year (only records that carry an abstract, 1000 per page, cursor
paging) and keeps the abstracts of papers we are missing, matched on exact
normalized title. Many publishers (IEEE above all) deposit few or no abstracts
to Crossref, so the yield varies a lot per journal.

Resumable: data/abstracts_web_crossref.json (written here, applied by
fetch_abstracts_web.py --apply) records which "issn:year" pages are finished.
A year is only marked done after all its pages came back; failures are waited
out (Retry-After first, then exponential backoff) and never mark anything done.

Usage:
  python fetch_abstracts_crossref.py                  # run until done
  python fetch_abstracts_crossref.py --max-hours 6
  python fetch_abstracts_crossref.py --venue IJCV --years 2020-2021
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict
from html import unescape

import checked_titles
from atomic_write import write_json_atomic
from fetch_abstracts_web import (BACKOFF_CAP, BACKOFF_START, CONTACT, DATA_DIR, PAPERS_FILE, Unavailable,
                                 clean_abstract, http_get, load_json, norm)

OUT_FILE = DATA_DIR / "abstracts_web_crossref.json"
API = "https://api.crossref.org"
GAP = 0.3
MAX_WAIT = 3600
MIN_MISSING = 100

# Venue (as papers_full.json spells it) -> Crossref ISSN (verified by journal name).
# One ISSN is enough: /journals/{issn} resolves the same journal for print and electronic.
ISSNS = {
    "TPAMI": "0162-8828",   # IEEE Transactions on Pattern Analysis and Machine Intelligence
    "IJCV": "0920-5691",    # International Journal of Computer Vision
    "IJRR": "0278-3649",    # The International Journal of Robotics Research
    "T-RO": "1552-3098",    # IEEE Transactions on Robotics
    "T-ITS": "1524-9050",   # IEEE Transactions on Intelligent Transportation Systems
    "RA-L": "2377-3766",    # IEEE Robotics and Automation Letters
}

_last = [0.0]


def get_json(path_and_query, sleep=time.sleep, clock=time.monotonic, fetch=http_get):
    """One API call with the waiting rules above; returns parsed JSON."""
    url = API + path_and_query + ("&" if "?" in path_and_query else "?") + "mailto=" + CONTACT
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
            print(f"  Crossref {e}; waiting {wait / 60:.1f} min", flush=True)
            sleep(wait)
            backoff = min(backoff * 2, BACKOFF_CAP)


def clean_crossref_abstract(text):
    """JATS-ish string -> plain text without the leading 'Abstract' heading, or None."""
    if not text:
        return None
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^abstract\b[\s:.\-—–]*", "", text, flags=re.I)
    return clean_abstract(text)


def harvest_year(issn, year, wanted, found, fetch_json=get_json):
    """List the journal's works with abstracts for one year; keep wanted titles.

    Returns (pages, seen, kept)."""
    cursor = "*"
    pages = seen = kept = 0
    while cursor:
        data = fetch_json(f"/journals/{issn}/works?filter=from-pub-date:{year},until-pub-date:{year},has-abstract:true"
                          f"&select=title,abstract,DOI&rows=1000&cursor={urllib.parse.quote(cursor)}")
        items = (data.get("message") or {}).get("items") or []
        pages += 1
        for work in items:
            seen += 1
            for title in work.get("title") or []:
                nt = norm(title)
                if nt in wanted and nt not in found:
                    text = clean_crossref_abstract(work.get("abstract"))
                    if text:
                        found[nt] = {"text": text, "source": "crossref"}
                        kept += 1
                    break
        cursor = (data.get("message") or {}).get("next-cursor") if items else None
    return pages, seen, kept


def missing_by_venue(papers, skip=()):
    out = defaultdict(lambda: defaultdict(set))
    for p in papers:
        if (p.get("abstract") or "").strip():
            continue
        nt = norm(p.get("title"))
        if nt and nt not in skip and p.get("venue") and p.get("year"):
            out[p["venue"]][p["year"]].add(nt)
    return out


def parse_years(spec):
    if not spec:
        return None
    a, _, b = spec.partition("-")
    return range(int(a), int(b or a) + 1)


def run(missing, state, venues, years=None, max_seconds=None, save=None, clock=time.monotonic, fetch_json=get_json):
    """Harvest every (venue, year) not yet done. Returns True when finished, False on time-out."""
    started = clock()
    save = save or (lambda s: None)
    for venue in venues:
        issn = ISSNS[venue]
        for year in sorted(missing.get(venue, {}), reverse=True):
            if years is not None and year not in years:
                continue
            key = f"{issn}:{year}"
            wanted = checked_titles.left(state, key, {t for t in missing[venue][year] if t not in state["abstracts"]},
                                         state["done"])
            if not wanted:
                continue
            if max_seconds and clock() - started > max_seconds:
                save(state)
                return False
            pages, seen, kept = harvest_year(issn, year, wanted, state["abstracts"], fetch_json)
            checked_titles.mark(state, key, wanted)
            save(state)
            print(f"{venue} {year}: {seen} works with abstract in {pages} pages, "
                  f"{kept} of {len(missing[venue][year])} missing abstracts found", flush=True)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--venue", default=None, help="only this venue, as papers_full.json spells it")
    ap.add_argument("--years", default=None, help="a year or range, e.g. 2020 or 2018-2021")
    ap.add_argument("--min-missing", type=int, default=MIN_MISSING)
    args = ap.parse_args(argv)
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    state = load_json(OUT_FILE, {"abstracts": {}, "done": []})
    state.setdefault("abstracts", {})
    state.setdefault("done", [])
    missing = missing_by_venue(papers)
    totals = Counter({v: sum(len(t) for t in ys.values()) for v, ys in missing.items()})
    venues = [v for v, n in totals.most_common()
              if v in ISSNS and n >= args.min_missing and (not args.venue or v == args.venue)]
    if args.venue and args.venue not in venues:
        print(f"{args.venue}: no ISSN known or fewer than {args.min_missing} missing", flush=True)
    print(f"{len(venues)} journals to harvest", flush=True)
    done = run(missing, state, venues, parse_years(args.years),
               args.max_hours * 3600 if args.max_hours else None,
               save=lambda s: write_json_atomic(OUT_FILE, s))
    print(("Finished" if done else "Time budget used up") + f": {len(state['abstracts'])} abstracts in total.",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
