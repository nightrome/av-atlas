#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches full paper listings (title, authors, abstract) from CVF Open Access
(openaccess.thecvf.com) for a given conference+year -- covers CVPR, ICCV,
WACV (not ECCV/NeurIPS/ICRA/etc., which are hosted elsewhere). Static HTML,
no auth, no rate limit, ~0.3-0.6s per paper page.

Two-step: fetch the listing page for all paper.html links + titles, then
fetch each paper page for its abstract. Writes
av-atlas/data/venues/<conf><year>.json.

Usage:
  python fetch_cvf.py CVPR 2024            # full conference
  python fetch_cvf.py CVPR 2024 --limit 15 # bounded test batch
"""
import html
import json
import re
import sys
import time

from fetch_common import BASE, OUT_DIR, fetch

CVF_BASE = "https://openaccess.thecvf.com"


def _extract_paper_links(html):
    # Older years (pre ~2019) use relative hrefs like "content_cvpr_2015/html/..."
    # instead of "/content/CVPR2015/html/...", and some years have a stray <br>
    # between <dt class="ptitle"> and the <a> -- match loosely on href alone.
    return re.findall(r'<dt class="ptitle">.*?href="([^"]*paper\.html)"', html, re.S)


def list_papers(conf, year):
    html = fetch(f"{CVF_BASE}/{conf}{year}?day=all")
    if "Error 1525" in html or "Incorrect DATE value" in html:
        # ?day=all is broken server-side for some years (confirmed CVPR
        # 2018-2020) -- the per-day pages still work, so discover each day's
        # URL from the conference's day-selector page and union their listings.
        index_html = fetch(f"{CVF_BASE}/{conf}{year}")
        days = sorted(set(re.findall(r'day=([0-9-]+)"', index_html)))
        if not days:
            raise RuntimeError(f"{conf}{year}: ?day=all broken and no per-day links found")
        links = []
        seen = set()
        for day in days:
            day_html = fetch(f"{CVF_BASE}/{conf}{year}?day={day}")
            for link in _extract_paper_links(day_html):
                if link not in seen:
                    seen.add(link)
                    links.append(link)
    else:
        links = _extract_paper_links(html)
    return [{"path": link, "title": None} for link in links]


def clean_text(s):
    # CVF's title/abstract/author HTML carries entities un-decoded (e.g.
    # "Bird&#x27;s-Eye-View" instead of "Bird's-Eye-View") -- unescape() turns
    # those into the real characters they represent; confirmed safe to run
    # on every field since none of them are expected to contain literal
    # HTML markup of their own.
    return html.unescape(re.sub(r"\s+", " ", s)).strip()


def fetch_paper(path):
    url = path if path.startswith("http") else f"{CVF_BASE}/{path.lstrip('/')}"
    page_html = fetch(url)
    title_m = re.search(r'<div id="papertitle">\s*(.*?)\s*<', page_html, re.S)
    abstract_m = re.search(r'<div id="abstract"[^>]*>(.*?)</div>', page_html, re.S)
    authors_m = re.search(r'<div id="authors">.*?<b><i>(.*?)</i></b>', page_html, re.S)
    return {
        "title": clean_text(title_m.group(1)) if title_m else None,
        "abstract": clean_text(abstract_m.group(1)) if abstract_m else None,
        "authors": clean_text(authors_m.group(1)) if authors_m else None,
    }


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Usage: python fetch_cvf.py <CONF> <YEAR> [--limit N]")
    conf, year = sys.argv[1], sys.argv[2]
    limit = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])

    papers = list_papers(conf, year)
    print(f"Found {len(papers)} papers in {conf}{year} listing")
    if limit:
        papers = papers[:limit]
        print(f"Limiting to first {limit} for this run")

    results = []
    for i, p in enumerate(papers, 1):
        try:
            detail = fetch_paper(p["path"])
        except Exception as e:
            print(f"  [{i}/{len(papers)}] FAILED {p['path']}: {e}")
            continue
        results.append({"conference": conf, "year": int(year), "path": p["path"], **detail})
        print(f"  [{i}/{len(papers)}] {detail['title'][:70] if detail['title'] else p['path']}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = OUT_DIR / f"{conf.lower()}{year}.json"
    out_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    n_with_abstract = sum(1 for r in results if r.get("abstract"))
    print(f"\nWrote {len(results)} papers to {out_file} ({n_with_abstract} with an abstract)")


if __name__ == "__main__":
    main()
