#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finds an arXiv link for every AV paper, even ones already published at a
real venue -- most conference papers have an arXiv preprint too, but until
now this corpus only ever showed an arXiv icon for papers whose ONLY source
was an arxiv*.json venue file (see index.html's sourceCell(), which gated
the arXiv icon on `p.venue === 'arXiv preprint'`). A CVPR paper with an
arXiv version got no arXiv link at all.

Reuses fetch_affiliations_arxiv.py's find_arxiv_id() (exact-title-match
arXiv search, same safety standard as everywhere else in this pipeline: a
fuzzy same-topic match is rejected, only a normalized-title match counts)
rather than a fresh implementation. Also reads data/affiliations_arxiv.json
first (read-only) -- that script has already resolved an arxiv_id for
~4700+ papers as a side effect of its own affiliation search, so those are
free here, no new network request needed.

Writes its own side file, data/arxiv_ids.json: {normalizedTitle: arxiv_id
or null} ("tried, no match" recorded so a rerun doesn't retry it forever).
Never touches papers_full.json directly -- see apply_arxiv_links.py for the
single-writer step that folds arxiv_url in.

Usage: python fetch_arxiv_links.py
"""
import json
import re
import time
from pathlib import Path

from fetch_affiliations_arxiv import find_arxiv_id

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
AFFS_FILE = BASE / "data" / "affiliations_arxiv.json"
OUT_FILE = BASE / "data" / "arxiv_ids.json"
BATCH_SIZE = 15
MAX_CONSECUTIVE_FAILURES = 20
REQUEST_DELAY = 3.0  # arXiv's own etiquette guidance


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    av = [p for p in papers if p.get("av_relevance") == "AV" and not p.get("arxiv_url")]

    ids = load_json(OUT_FILE, {})

    # Free head start: fetch_affiliations_arxiv.py has already resolved an
    # arxiv_id for many papers as a side effect of its own work -- copy
    # those in before spending a single new network request.
    affs = load_json(AFFS_FILE, {})
    n_reused = 0
    for key, entry in affs.items():
        if key in ids or isinstance(entry, list):
            continue  # old-shape entries never recorded an arxiv_id at all
        if "arxiv_id" in entry:
            ids[key] = entry["arxiv_id"]
            n_reused += 1
    if n_reused:
        save_json(OUT_FILE, ids)
        print(f"Reused {n_reused} arxiv_id lookups already done by fetch_affiliations_arxiv.py (no network cost)",
              flush=True)

    pending = [p for p in av if normalize_title(p["title"]) not in ids]
    print(f"{len(pending)} AV papers left to search on arXiv (of {len(av)} still missing a link)", flush=True)

    processed = 0
    found = 0
    consecutive_failures = 0

    for p in pending:
        key = normalize_title(p["title"])
        try:
            arxiv_id = find_arxiv_id(p["title"])
            ids[key] = arxiv_id
            if arxiv_id:
                found += 1
            consecutive_failures = 0
        except Exception as e:
            print(f"  failed on {p['title'][:60]!r}: {e}", flush=True)
            consecutive_failures += 1
        processed += 1
        time.sleep(REQUEST_DELAY)

        if processed % BATCH_SIZE == 0 or processed == len(pending):
            save_json(OUT_FILE, ids)
            print(f"  [{processed}/{len(pending)}] progress saved ({found} arXiv links found so far)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive failures. Resume later by rerunning.",
                  flush=True)
            break

    print(f"\nDone this run: {found} new arXiv links found.", flush=True)


if __name__ == "__main__":
    main()
