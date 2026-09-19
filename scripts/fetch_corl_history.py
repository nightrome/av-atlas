#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Full CoRL history (2017-2025) with abstracts. PMLR volume index pages have
title+authors inline (no extra request), but abstract needs one request per
paper. CoRL year -> PMLR volume mapping is hardcoded below (found via web
search, PMLR volume numbers aren't derivable from the year alone).

Usage: python fetch_corl_history.py
"""
import json
import re
import time

from fetch_common import BASE, OUT_DIR, fetch

CORL_VOLUMES = {
    2017: 78, 2018: 87, 2019: 100, 2020: 155, 2021: 164,
    2022: 205, 2023: 229, 2024: 270, 2025: 305,
}


def list_papers(volume):
    html = fetch(f"https://proceedings.mlr.press/v{volume}/")
    entries = re.findall(
        r'<p class="title">(.*?)</p>\s*<p class="details">\s*<span class="authors">(.*?)</span>.*?'
        r'<a href="(https://proceedings\.mlr\.press/v\d+/[a-z0-9]+\.html)">abs</a>',
        html, re.S,
    )
    papers = []
    for title, authors_raw, url in entries:
        authors = re.sub(r"&nbsp;", " ", authors_raw)
        authors = re.sub(r"<[^>]+>", "", authors).strip()
        title = re.sub(r"\s+", " ", title).strip()
        papers.append({"title": title, "authors": authors, "url": url})
    return papers


def fetch_abstract(url):
    html = fetch(url)
    m = re.search(r'<div id="abstract" class="abstract">\s*(.*?)\s*</div>', html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else None


def main():
    for year, volume in sorted(CORL_VOLUMES.items()):
        try:
            papers = list_papers(volume)
        except Exception as e:
            print(f"CoRL{year} (v{volume}): listing failed ({e}), skipping", flush=True)
            continue
        print(f"CoRL{year} (v{volume}): {len(papers)} papers found, fetching abstracts...", flush=True)

        out_file = OUT_DIR / f"corl{year}.json"
        results = []
        for i, p in enumerate(papers, 1):
            try:
                abstract = fetch_abstract(p["url"])
            except Exception as e:
                print(f"  [CoRL{year} {i}/{len(papers)}] FAILED: {e}", flush=True)
                abstract = None
            results.append({"conference": "CoRL", "year": year, "title": p["title"],
                             "authors": p["authors"], "abstract": abstract})
            if i % 50 == 0 or i == len(papers):
                out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
            time.sleep(0.15)
        out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"CoRL{year}: done, {len(results)} papers", flush=True)

    print("ALL DONE: CoRL history", flush=True)


if __name__ == "__main__":
    main()
