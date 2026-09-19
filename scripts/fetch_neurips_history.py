#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-year NeurIPS pull (title+authors+abstract per paper). Loops the given
year range, skips years with no listing, saves progress per-year.

Usage: python fetch_neurips_history.py 2013 2023
"""
import json
import sys
import time
from pathlib import Path

import fetch_neurips as fn

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "venues"


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: python fetch_neurips_history.py <START_YEAR> <END_YEAR>")
    start, end = int(sys.argv[1]), int(sys.argv[2])

    for year in range(start, end + 1):
        try:
            papers = fn.list_papers(year)
        except Exception as e:
            print(f"NeurIPS{year}: listing failed ({e}), skipping", flush=True)
            continue
        if not papers:
            print(f"NeurIPS{year}: empty listing, skipping", flush=True)
            continue

        print(f"NeurIPS{year}: {len(papers)} papers found, fetching...", flush=True)
        out_file = OUT_DIR / f"neurips{year}.json"
        results = []
        for i, p in enumerate(papers, 1):
            try:
                detail = fn.fetch_paper(p["path"])
            except Exception as e:
                print(f"  [NeurIPS{year} {i}/{len(papers)}] FAILED: {e}", flush=True)
                continue
            results.append({"conference": "NeurIPS", "year": year, "path": p["path"], **detail})
            if i % 200 == 0 or i == len(papers):
                out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
                print(f"  [NeurIPS{year} {i}/{len(papers)}] progress saved", flush=True)
            time.sleep(0.2)
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"NeurIPS{year}: done, {len(results)} papers", flush=True)

    print(f"ALL DONE: NeurIPS {start}-{end}", flush=True)


if __name__ == "__main__":
    main()
