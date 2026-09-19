#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generic DBLP proceedings-listing fetcher -- title + authors + year, no
abstract (DBLP doesn't have those). Two uses:

1. Fallback for CVF years where openaccess.thecvf.com's ?day=all listing is
   broken server-side (confirmed for CVPR 2018-2020: "Error 1525: Incorrect
   DATE value: 'all'").
2. Primary source for venues with no bulk-scrapable proceedings site at all --
   RSS (roboticsproceedings.org has titles/authors but no abstracts either,
   and volume-number-to-year isn't reliably inferable from the site itself;
   DBLP already resolves that), ICLR (OpenReview's bulk API now requires a
   browser-solvable challenge, see DECISIONS.md), and AAAI (ojs.aaai.org's
   archive page is JS-rendered, not scrapable via a plain HTTP fetch).

Papers from this script always have `"abstract": None` --
aggregate.py's is_fully_processed() only requires title + year, not an
abstract (see DECISIONS.md), so these still reach the site; classify.py
falls back to title-only keyword matching for them.

A third mode (--journal) handles DBLP's journals/ namespace, structurally
different from conf/ -- pages are per VOLUME, not per year, and a journal's
volume numbering has no fixed relationship to the calendar (TPAMI is on
its ~46th volume since 1979; RA-L only started in 2016). Rather than hand-
mapping volume numbers to years per journal, this walks volumes backward
from the newest and reads each entry's own DBLP record key (which always
ends in a 2-digit year, e.g. ".../LiuJZCQHS24" -> 2024) for its real year,
stopping once a volume's entries are entirely older than the requested
start year.

Usage: python fetch_dblp_listing.py CVPR 2019
       python fetch_dblp_listing.py RSS 2013 2024
       python fetch_dblp_listing.py ICLR 2013 2025
       python fetch_dblp_listing.py AAAI 2013 2026
       python fetch_dblp_listing.py GCPR 2013 2025
       python fetch_dblp_listing.py --journal TPAMI 2013
"""
import json
import re
import sys
import time
import urllib.error

from fetch_common import BASE, OUT_DIR, fetch as _fetch

# Most conferences: DBLP directory name == filename prefix (conf/cvpr/cvpr2024.html).
# GCPR is the one exception seen so far -- DBLP keeps it filed under the
# still-live "dagm" directory (its predecessor conference, DAGM Symposium,
# which GCPR succeeded in 2013) but with "gcpr" filenames from 2013 on
# (conf/dagm/gcpr2013.html, not conf/gcpr/gcpr2013.html, which 404s) -- so
# each entry is (directory, filename_prefix); a plain string means they're
# the same.
CONF_DBLP_PATH = {
    "CVPR": "cvpr", "ICCV": "iccv", "WACV": "wacv", "RSS": "rss", "ICLR": "iclr",
    "AAAI": "aaai", "ECCV": "eccv", "ITSC": "itsc",
    # DBLP's conf/iv/ is NOT the IEEE Intelligent Vehicles Symposium -- it's
    # the unrelated "International Conference on Information Visualisation"
    # (confirmed live: conf/iv's own DBLP page, entirely treemap/word-cloud/
    # graph-layout papers). DBLP disambiguates the vehicles symposium as
    # conf/ivs/ instead (confirmed live: conf/ivs's own DBLP page says "IEEE
    # Intelligent Vehicles Symposium"). Using "iv" here previously pulled
    # 1131 Information-Visualisation papers under the "IV" venue label,
    # user-reported as "IV has only 0.4% AV-relevant papers... it is
    # literally called Intelligent Vehicles" -- the real IV Symposium's
    # papers were never fetched at all.
    "IV": "ivs",
    "GCPR": ("dagm", "gcpr"),
    # Found by mining our own papers' reference lists for venue names that
    # come up often but aren't covered yet (user-requested) -- ICML showed
    # 207 raw citations from this corpus alone, a top-tier ML venue missing
    # entirely; the rest are the next most-cited gaps. All confirmed live
    # against DBLP's own index page before adding, same as every entry
    # above (the "iv" mistake is exactly why that check matters).
    "ICML": "icml", "BMVC": "bmvc", "ACCV": "accv", "ICPR": "icpr",
    "ICASSP": "icassp", "ICIP": "icip",
}

# journals/<key>/<key><volume>.html -- display name -> DBLP key.
JOURNAL_DBLP_PATH = {
    "IJCV": "ijcv", "RA-L": "ral", "T-RO": "trob", "TPAMI": "pami",
    "IJRR": "ijrr", "T-ITS": "tits", "TOG": "tog",
}


# DBLP throttles under sustained use (confirmed: a run of GCPR/TPAMI/ITSC
# requests back to back started getting 503s partway through) -- retry with
# backoff on 503, same tuning as before this moved into fetch_common.
def fetch(url, timeout=30):
    return _fetch(url, timeout=timeout, max_retries=5, retry_status=(503,), backoff=10)


def parse_dblp_html(html, conf, year):
    entries = re.split(r'<li class="entry inproceedings', html)[1:]
    papers = []
    for entry in entries:
        title_m = re.search(r'<span class="title" itemprop="name">([^<]*)</span>', entry)
        if not title_m:
            continue
        title = title_m.group(1).strip().rstrip(".")
        authors = re.findall(r'itemprop="author"[^>]*>.*?title="([^"]*)"', entry)
        papers.append({"conference": conf, "year": int(year), "title": title,
                        "authors": ", ".join(authors), "abstract": None})
    return papers


def fetch_year(conf, dblp_path, year):
    dblp_dir, dblp_file = dblp_path if isinstance(dblp_path, tuple) else (dblp_path, dblp_path)
    # GCPR only exists as "gcpr"-prefixed from 2013 (the year it succeeded
    # DAGM Symposium) -- 2012 and earlier are still filed as "dagm<year>"
    # under the same conf/dagm directory, same conference lineage either way.
    if conf == "GCPR" and year < 2013:
        dblp_file = "dagm"
    base_url = f"https://dblp.org/db/conf/{dblp_dir}/{dblp_file}{year}"
    try:
        return parse_dblp_html(fetch(f"{base_url}.html"), conf, year)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
    # Some conferences (ECCV) publish proceedings as several numbered parts
    # instead of one page per year (e.g. eccv2016-1.html .. eccv2016-8.html,
    # plus eccv2016w*.html workshop volumes this fetcher deliberately skips
    # -- workshops are a different, much larger and noisier population than
    # the main-track papers every other venue in this corpus draws from).
    papers = []
    part = 1
    while True:
        try:
            papers += parse_dblp_html(fetch(f"{base_url}-{part}.html"), conf, year)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break
            raise
        part += 1
        time.sleep(1)
    return papers


def parse_journal_entries(html, venue):
    entries = re.split(r'<li class="entry article"', html)[1:]
    papers = []
    for entry in entries:
        title_m = re.search(r'<span class="title" itemprop="name">([^<]*)</span>', entry)
        key_m = re.search(r'id="journals/\w+/[^"]*?(\d{2})[a-z]?"', entry)
        if not title_m or not key_m:
            continue
        title = title_m.group(1).strip().rstrip(".")
        authors = re.findall(r'itemprop="author"[^>]*>.*?title="([^"]*)"', entry)
        # DBLP record keys always end in a 2-digit year (optionally +letter
        # for same-year disambiguation, e.g. "...24a") -- no article past
        # 2000 in this corpus's window, so 20xx is always the right
        # expansion, unlike a general-purpose 2-digit-year parser.
        year = 2000 + int(key_m.group(1))
        papers.append({"conference": venue, "year": year, "title": title,
                        "authors": ", ".join(authors), "abstract": None})
    return papers


def latest_volume(dblp_key):
    html = fetch(f"https://dblp.org/db/journals/{dblp_key}/index.html")
    volumes = [int(m) for m in re.findall(rf'{dblp_key}(\d+)\.html', html)]
    return max(volumes) if volumes else None


def fetch_journal(venue, dblp_key, start_year):
    vol = latest_volume(dblp_key)
    if vol is None:
        print(f"{venue}: could not find any volume on DBLP, skipping")
        return []
    papers = []
    # Walk backward from the newest volume until an entire volume's entries
    # are older than start_year -- see module docstring for why (no fixed
    # volume-to-year mapping to look up instead).
    while vol > 0:
        try:
            html = fetch(f"https://dblp.org/db/journals/{dblp_key}/{dblp_key}{vol}.html")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                vol -= 1
                continue
            raise
        vol_papers = parse_journal_entries(html, venue)
        recent = [p for p in vol_papers if p["year"] >= start_year]
        papers.extend(recent)
        print(f"  {venue} vol {vol}: {len(vol_papers)} papers ({len(recent)} in range)", flush=True)
        if vol_papers and not recent:
            break
        vol -= 1
        time.sleep(2)
    return papers


def main():
    if "--journal" in sys.argv:
        idx = sys.argv.index("--journal")
        venue = sys.argv[idx + 1]
        start_year = int(sys.argv[idx + 2])
        dblp_key = JOURNAL_DBLP_PATH[venue]
        papers = fetch_journal(venue, dblp_key, start_year)
        out_file = OUT_DIR / f"{venue.lower().replace('-', '')}_all.json"
        out_file.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"{venue}: wrote {len(papers)} papers to {out_file} (via DBLP, no abstracts)")
        return

    if len(sys.argv) < 3:
        raise SystemExit("Usage: python fetch_dblp_listing.py <CONF> <YEAR> [END_YEAR]\n"
                          "       python fetch_dblp_listing.py --journal <VENUE> <START_YEAR>")
    conf = sys.argv[1]
    start_year = int(sys.argv[2])
    end_year = int(sys.argv[3]) if len(sys.argv) > 3 else start_year
    dblp_conf = CONF_DBLP_PATH[conf]

    for year in range(start_year, end_year + 1):
        try:
            papers = fetch_year(conf, dblp_conf, year)
        except urllib.error.HTTPError as e:
            print(f"{conf}{year}: {e}, skipping (DBLP may not have this year)")
            continue
        if not papers:
            print(f"{conf}{year}: no entries found, skipping")
            continue
        out_file = OUT_DIR / f"{conf.lower()}{year}.json"
        out_file.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"{conf}{year}: wrote {len(papers)} papers to {out_file} (via DBLP, no abstracts)")
        if end_year > start_year:
            time.sleep(2)


if __name__ == "__main__":
    main()
