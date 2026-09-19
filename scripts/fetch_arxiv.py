#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pulls arXiv preprints for this corpus's top AV researchers, via arXiv's free
public Atom API (export.arxiv.org/api/query -- no auth, no paid tier, unlike
OpenAlex; see DECISIONS.md for why this project stopped depending on
OpenAlex).

Earlier approach (kept here in history, not in the file) tried one broad
AV-keyword search across all of arXiv -- a single huge query, immediately
rate-limited (429) by arXiv's API, compounded by fetch_affiliations_arxiv.py
already hitting the same domain concurrently. This is the opposite shape:
~100 small, targeted per-author queries (one arXiv author-search call each),
for the top AV researchers already ranked in this corpus by paper count
(data/stats.json's all_papers, AV-relevant only) -- a researcher who
already has several AV papers in the corpus is likely to have more on arXiv
that haven't reached a tracked venue yet (or won't -- a preprint under
review, an industry technical report). 100 requests at arXiv's own
requested 3-second spacing is ~5 minutes total, comfortably inside any
reasonable rate limit, vs. one query complex enough to time out or get
throttled outright.

Like every per-author or per-keyword pull (as opposed to a venue's complete
proceedings), this is a recall net, not a precision filter: classify.py's
real relevance check still runs on every fetched paper during
merge_corpus.py, on its usual terms, so a top author's non-AV papers (they
all have some) end up "non-AV" like any other non-AV paper, not silently
forced "AV" just for showing up here.

Writes av-atlas/data/venues/arxiv_authors.json, same schema as every
other venue file ("conference": "arXiv preprint", so it renders with the
site's existing arXiv icon -- see index.html's sourceCell()). merge_corpus.py
treats arxiv*.json as lowest-priority fill: a paper that's both an arXiv
preprint and published at a real venue keeps the real venue's name, never
gets relabeled "arXiv preprint" -- see that script for the ordering
guarantee.

Usage: python fetch_arxiv.py             # top 100 authors from stats.json
       python fetch_arxiv.py --top 50    # fewer authors, faster test run
"""
import json
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter

from fetch_common import BASE, HEADERS, fetch as _fetch

STATS_FILE = BASE / "data" / "stats.json"
OUT_FILE = BASE / "data" / "venues" / "arxiv_authors.json"
API_URL = "http://export.arxiv.org/api/query"
PAGE_SIZE = 100
# arXiv's own etiquette guidance: no more than one request per 3 seconds.
REQUEST_DELAY = 3.0

ATOM_NS = "{http://www.w3.org/2005/Atom}"


def top_authors(n):
    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    counts = Counter()
    for p in stats.get("all_papers", []):
        for a in (p.get("authors") or []):
            counts[a] += 1
    return [name for name, _ in counts.most_common(n)]


def fetch_page(query, start):
    params = urllib.parse.urlencode({
        "search_query": query, "start": start, "max_results": PAGE_SIZE,
        "sortBy": "submittedDate", "sortOrder": "descending",
    })
    return _fetch(f"{API_URL}?{params}", timeout=30, max_retries=4, retry_status=(429,), backoff=15, headers=HEADERS)


def parse_entries(xml_bytes):
    root = ET.fromstring(xml_bytes)
    total = int(root.findtext(f"{{http://a9.com/-/spec/opensearch/1.1/}}totalResults") or 0)
    entries = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        title = (entry.findtext(f"{ATOM_NS}title") or "").strip().replace("\n", " ")
        title = " ".join(title.split())
        summary = (entry.findtext(f"{ATOM_NS}summary") or "").strip()
        summary = " ".join(summary.split())
        authors = [a.findtext(f"{ATOM_NS}name") for a in entry.findall(f"{ATOM_NS}author")]
        published = entry.findtext(f"{ATOM_NS}published") or ""
        arxiv_url = entry.findtext(f"{ATOM_NS}id") or ""
        if not title or not published:
            continue
        entries.append({
            "conference": "arXiv preprint",
            "year": int(published[:4]),
            "title": title,
            "authors": ", ".join(a for a in authors if a),
            "abstract": summary or None,
            "doi": arxiv_url or None,
        })
    return entries, total


def fetch_author(name, cap=200):
    # au: is a phrase match against arXiv's normalized author-name field --
    # not perfect for common names (a "J Wang" search could pull in a
    # different J Wang's papers), but harmless here: classify.py's relevance
    # check runs on every result regardless of whose name attached it, so a
    # wrong-person false positive just has to also independently look
    # AV-relevant to survive -- vanishingly unlikely by chance.
    query = f'au:"{name}"'
    papers = []
    start = 0
    while True:
        xml_bytes = fetch_page(query, start)
        entries, total = parse_entries(xml_bytes)
        if not entries:
            break
        papers.extend(entries)
        start += PAGE_SIZE
        if len(papers) >= cap or start >= total:
            break
        time.sleep(REQUEST_DELAY)
    return papers


def main():
    n = 100
    if "--top" in sys.argv:
        n = int(sys.argv[sys.argv.index("--top") + 1])
    authors = top_authors(n)
    print(f"Fetching arXiv papers for the top {len(authors)} authors by corpus paper count...", flush=True)

    all_papers = {}  # normalized title -> record, de-duped across authors
    for i, name in enumerate(authors, 1):
        try:
            papers = fetch_author(name)
        except Exception as e:
            print(f"  [{i}/{len(authors)}] {name}: failed ({e}), skipping", flush=True)
            time.sleep(REQUEST_DELAY)
            continue
        for p in papers:
            key = p["title"].lower()
            all_papers.setdefault(key, p)
        print(f"  [{i}/{len(authors)}] {name}: {len(papers)} papers ({len(all_papers)} unique so far)", flush=True)
        time.sleep(REQUEST_DELAY)

    result = list(all_papers.values())
    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote {len(result)} unique papers to {OUT_FILE}")


if __name__ == "__main__":
    main()
