#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-year, with-abstracts pull for a CVF Open Access conference (CVPR,
ICCV, WACV, ACCV). Loops the given year range, skips years with no listing
(404), and for each year fetches every paper's title+authors+abstract
(one request per paper -- this is the slow, thorough version, meant to run
unattended in the background for a long time).

Progress is saved every 200 papers to <conf><year>.json.partial next to the
real file (merge_corpus.py only reads *.json, so a half-done file never
reaches the corpus). The real data/venues/<conf><year>.json is only written
once every paper on the listing has been fetched. Papers that fail are
retried a few times, then once more in a second pass at the end of the
year; if some still fail, the year is reported and the old file is left
alone. A paper page that answers 404 is a dead link on CVF's side (the
listing still has it, the page is gone -- CVPR 2022, CVPR 2024 and ICCV 2017
each have one or two), so it's logged and skipped rather than retried or
counted as missing. This used to write whatever it had: CVPR 2022 sat at 774 of 2,074
papers for a long time because a run stopped partway through and nothing
noticed. tests/test_venue_listing_counts.py pins the expected counts too.

Usage: python fetch_cvf_history.py CVPR 2013 2026
       python fetch_cvf_history.py ACCV 2020 2024
"""
import json
import sys
import time
import urllib.error
from pathlib import Path

import fetch_cvf as fc

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "venues"

PAPER_ATTEMPTS = 3
RETRY_PAUSE = 5


def fetch_with_retries(path, attempts=PAPER_ATTEMPTS, pause=RETRY_PAUSE, fetch_paper=None):
    """One paper page, tried up to `attempts` times. Returns the detail dict
    or raises the last error."""
    fetch_paper = fetch_paper or fc.fetch_paper
    for attempt in range(attempts):
        try:
            return fetch_paper(path)
        except urllib.error.HTTPError as e:
            if e.code == 404 or attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))


def should_write(n_fetched, n_listed, n_dead, n_existing):
    """Only replace a venue file with a complete fetch (every listed paper
    except dead links), and never with one that has fewer papers than the
    file already on disk."""
    return n_fetched >= n_listed - n_dead and n_fetched >= n_existing


def existing_count(path):
    if not path.exists():
        return 0
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return 0


def _write(path, results):
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def fetch_year(conf, year):
    """Returns True if the year's file was written (or there was nothing
    to fetch), False if the fetch came up short."""
    try:
        papers = fc.list_papers(conf, year)
    except urllib.error.HTTPError as e:
        print(f"{conf}{year}: no listing ({e.code}), skipping", flush=True)
        return True
    except Exception as e:
        print(f"{conf}{year}: listing failed ({e}), skipping", flush=True)
        return False
    if not papers:
        print(f"{conf}{year}: empty listing, skipping", flush=True)
        return True

    print(f"{conf}{year}: {len(papers)} papers found, fetching abstracts...", flush=True)
    out_file = OUT_DIR / f"{conf.lower()}{year}.json"
    partial_file = out_file.with_name(out_file.name + ".partial")
    results = {}
    failed = []
    dead = []
    for i, p in enumerate(papers):
        try:
            detail = fetch_with_retries(p["path"])
        except urllib.error.HTTPError as e:
            print(f"  [{conf}{year} {i + 1}/{len(papers)}] FAILED: {e}", flush=True)
            (dead if e.code == 404 else failed).append(i)
            continue
        except Exception as e:
            print(f"  [{conf}{year} {i + 1}/{len(papers)}] FAILED: {e}", flush=True)
            failed.append(i)
            continue
        results[i] = {"conference": conf, "year": year, "path": p["path"], **detail}
        if (i + 1) % 200 == 0:
            _write(partial_file, [results[k] for k in sorted(results)])
            print(f"  [{conf}{year} {i + 1}/{len(papers)}] progress saved", flush=True)
        time.sleep(0.15)

    if failed:
        print(f"{conf}{year}: retrying {len(failed)} failed papers once more", flush=True)
        time.sleep(30)
        for i in failed:
            try:
                detail = fetch_with_retries(papers[i]["path"])
            except Exception as e:
                print(f"  still failing: {papers[i]['path']}: {e}", flush=True)
                continue
            results[i] = {"conference": conf, "year": year, "path": papers[i]["path"], **detail}
            time.sleep(0.15)

    ordered = [results[k] for k in sorted(results)]
    n_existing = existing_count(out_file)
    if not should_write(len(ordered), len(papers), len(dead), n_existing):
        _write(partial_file, ordered)
        print(f"{conf}{year}: only {len(ordered)} of {len(papers)} listed papers fetched "
              f"({len(dead)} dead links, existing file has {n_existing}); NOT overwriting "
              f"{out_file.name}, partial result left in {partial_file.name}. If the listing "
              f"really shrank (withdrawn papers), delete the old file and rerun.", flush=True)
        return False
    _write(out_file, ordered)
    if partial_file.exists():
        partial_file.unlink()
    print(f"{conf}{year}: done, {len(ordered)} papers with detail ({len(dead)} dead links skipped)", flush=True)
    return True


def main():
    if len(sys.argv) < 4:
        raise SystemExit("Usage: python fetch_cvf_history.py <CONF> <START_YEAR> <END_YEAR>")
    conf, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    incomplete = [year for year in range(start, end + 1) if not fetch_year(conf, year)]
    if incomplete:
        print(f"INCOMPLETE: {conf} {', '.join(map(str, incomplete))} -- rerun those years", flush=True)
        sys.exit(1)
    print(f"ALL DONE: {conf} {start}-{end}", flush=True)


if __name__ == "__main__":
    main()
