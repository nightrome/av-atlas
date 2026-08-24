#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-year, with-abstracts pull for a CVF Open Access conference (CVPR,
ICCV, WACV). Loops the given year range, skips years with no listing
(404), and for each year fetches every paper's title+authors+abstract
(one request per paper -- this is the slow, thorough version, meant to run
unattended in the background for a long time).

Saves progress per-year as it goes (av-atlas/data/venues/<conf><year>.json)
so a partial/interrupted run still leaves usable data.

Usage: python fetch_cvf_history.py CVPR 2013 2026
"""
import json
import sys
import time
import urllib.error
from pathlib import Path

import fetch_cvf as fc

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "venues"


def main():
    if len(sys.argv) < 4:
        raise SystemExit("Usage: python fetch_cvf_history.py <CONF> <START_YEAR> <END_YEAR>")
    conf, start, end = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])

    for year in range(start, end + 1):
        try:
            papers = fc.list_papers(conf, year)
        except urllib.error.HTTPError as e:
            print(f"{conf}{year}: no listing ({e.code}), skipping", flush=True)
            continue
        except Exception as e:
            print(f"{conf}{year}: listing failed ({e}), skipping", flush=True)
            continue
        if not papers:
            print(f"{conf}{year}: empty listing, skipping", flush=True)
            continue

        print(f"{conf}{year}: {len(papers)} papers found, fetching abstracts...", flush=True)
        out_file = OUT_DIR / f"{conf.lower()}{year}.json"
        results = []
        for i, p in enumerate(papers, 1):
            try:
                detail = fc.fetch_paper(p["path"])
            except Exception as e:
                print(f"  [{conf}{year} {i}/{len(papers)}] FAILED: {e}", flush=True)
                continue
            results.append({"conference": conf, "year": year, "path": p["path"], **detail})
            if i % 200 == 0 or i == len(papers):
                out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
                print(f"  [{conf}{year} {i}/{len(papers)}] progress saved", flush=True)
            time.sleep(0.15)
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"{conf}{year}: done, {len(results)} papers with detail", flush=True)

    print(f"ALL DONE: {conf} {start}-{end}", flush=True)


if __name__ == "__main__":
    main()
