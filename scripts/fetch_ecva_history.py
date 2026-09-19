#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-year ECCV pull with abstracts. ecva.net only has 2018/2020/2022/2024
(biennial, and only these editions are mirrored there). Parses title+authors
from the listing page, then fetches each paper's own page for its abstract.

Usage: python fetch_ecva_history.py
"""
import json
import re
import time

from fetch_common import BASE, OUT_DIR, fetch

YEARS = [2018, 2020, 2022, 2024]


def fetch_abstract(path):
    url = f"https://www.ecva.net/{path}"
    html = fetch(url)
    m = re.search(r'<div id="abstract">(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def main():
    html = fetch("https://www.ecva.net/papers.php")
    for year in YEARS:
        section_m = re.search(rf'ECCV {year} Papers.*?(?=ECCV \d{{4}} Papers|\Z)', html, re.S)
        if not section_m:
            print(f"ECCV{year}: no section found, skipping", flush=True)
            continue
        section = section_m.group(0)
        entries = re.findall(
            r'<dt class="ptitle">.*?<a href=([^>]+)>\s*(.*?)\s*</a>\s*</dt>\s*<dd>\s*(.*?)\s*</dd>',
            section, re.S,
        )
        print(f"ECCV{year}: {len(entries)} papers found, fetching abstracts...", flush=True)
        out_file = OUT_DIR / f"eccv{year}.json"
        results = []
        for i, (path, title, authors_raw) in enumerate(entries, 1):
            path = path.strip().strip("'\"")
            title = re.sub(r"\s+", " ", title).strip()
            authors = re.sub(r"\*", "", authors_raw)
            authors = re.sub(r"\s+", " ", authors).strip().strip(",")
            try:
                abstract = fetch_abstract(path)
            except Exception as e:
                print(f"  [ECCV{year} {i}/{len(entries)}] FAILED: {e}", flush=True)
                abstract = None
            results.append({"conference": "ECCV", "year": year, "path": path, "title": title,
                             "authors": authors, "abstract": abstract})
            if i % 200 == 0 or i == len(entries):
                out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
                print(f"  [ECCV{year} {i}/{len(entries)}] progress saved", flush=True)
            time.sleep(0.15)
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"ECCV{year}: done, {len(results)} papers", flush=True)

    print("ALL DONE: ECCV history", flush=True)


if __name__ == "__main__":
    main()
