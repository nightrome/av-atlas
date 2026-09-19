#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches full paper listings (title, authors, abstract) from
proceedings.neurips.cc for a given year. Static HTML, no auth, no rate
limit, roughly one request per paper (~0.3-0.6s each).

Writes av-atlas/data/venues/neurips<year>.json.

Usage:
  python fetch_neurips.py 2024            # full year
  python fetch_neurips.py 2024 --limit 15 # bounded test batch
"""
import json
import re
import sys

from fetch_common import BASE, OUT_DIR, fetch

NEURIPS_BASE = "https://proceedings.neurips.cc"


def list_papers(year):
    html = fetch(f"{NEURIPS_BASE}/paper_files/paper/{year}")
    # Older years (pre-2020ish) use "...-Abstract.html" instead of "...-Abstract-Conference.html".
    entries = re.findall(
        r'<a title="paper title" href="(/paper_files/paper/\d+/hash/[^"]*Abstract(?:-Conference)?\.html)">([^<]*)</a>',
        html,
    )
    return [{"path": path, "title": title.strip()} for path, title in entries]


def fetch_paper(path):
    html = fetch(f"{NEURIPS_BASE}{path}")
    title_m = re.search(r"<title>(.*?)</title>", html, re.S)
    abstract_m = re.search(r'<p class="paper-abstract">\s*(?:<p>)?\s*(.*?)\s*(?:</p>)?\s*</p>', html, re.S)
    authors = re.findall(r'citation_author" content="([^"]*)"', html)
    return {
        "title": re.sub(r"\s+", " ", title_m.group(1)).strip() if title_m else None,
        "abstract": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", abstract_m.group(1))).strip() if abstract_m else None,
        "authors": ", ".join(authors) if authors else None,
    }


def main():
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python fetch_neurips.py <YEAR> [--limit N]")
    year = sys.argv[1]
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    papers = list_papers(year)
    print(f"Found {len(papers)} papers in NeurIPS {year} listing")
    if limit:
        papers = papers[:limit]
        print(f"Limiting to first {limit} for this run")

    # Windows' console defaults to cp1252, which can't encode every
    # character a paper title might contain (confirmed real: a Greek letter
    # in a NeurIPS 2012 title crashed this loop's progress print outright --
    # with results only written to disk after the full loop finishes below,
    # that took the whole run's output down with it, not just that one
    # paper). safe_print degrades to a replacement character instead of
    # raising, and results are now saved incrementally so a still-possible
    # crash elsewhere loses only what's left to fetch, not everything so far.
    def safe_print(s):
        print(s.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8", errors="replace"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"neurips{year}.json"

    results = []
    for i, p in enumerate(papers, 1):
        try:
            detail = fetch_paper(p["path"])
        except Exception as e:
            safe_print(f"  [{i}/{len(papers)}] FAILED {p['path']}: {e}")
            continue
        results.append({"conference": "NeurIPS", "year": int(year), "path": p["path"], **detail})
        safe_print(f"  [{i}/{len(papers)}] {detail['title'][:70] if detail['title'] else p['path']}")
        if i % 25 == 0:
            out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")

    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    n_with_abstract = sum(1 for r in results if r.get("abstract"))
    print(f"\nWrote {len(results)} papers to {out_file} ({n_with_abstract} with an abstract)")


if __name__ == "__main__":
    main()
