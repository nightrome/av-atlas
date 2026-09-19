#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-time backfill: fetch_semanticscholar_citing.py used to hardcode every
discovered paper's venue as "arXiv preprint" regardless of whether it was
actually published somewhere real (confirmed on real data: "Argoverse 2" is
a NeurIPS 2021 Datasets & Benchmarks paper, shown on the site as arXiv-only)
-- fixed going forward in that script, but the ~72k papers it already
discovered still carry the wrong label. This re-queries Semantic Scholar's
own `venue` field for all of them via the batch endpoint (500 papers/request,
not one request per paper) and patches data/venues/arxiv_s2_citing.json in
place wherever S2 actually knows a real venue.

Idempotent and resumable: writes back to the same file after every batch, so
a killed/restarted run just picks up wherever it left off (nothing to
re-fetch for entries already updated to a real venue).

Usage: python backfill_citing_venues.py
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_FILE = BASE / "data" / "venues" / "arxiv_s2_citing.json"
API_BASE = "https://api.semanticscholar.org/graph/v1"
BATCH_SIZE = 500
REQUEST_DELAY = 3.0
ARXIV_ID_RE = re.compile(r"arxiv\.org/abs/([\w.\-]+)")


def s2_post_batch(ids):
    url = f"{API_BASE}/paper/batch?fields=venue"
    body = json.dumps({"ids": ids}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"Content-Type": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f"  429 rate limited, waiting {wait}s...", flush=True)
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("gave up after 5 rate-limit retries")


def main():
    entries = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    print(f"{len(entries)} total entries")

    # Only entries still carrying the placeholder AND with an arXiv id to
    # look up -- already-corrected entries (a real venue, from a re-run or a
    # previous partial pass of this same script) are skipped for free.
    todo = []
    for e in entries:
        if e.get("conference") != "arXiv preprint":
            continue
        m = ARXIV_ID_RE.search(e.get("doi") or "")
        if m:
            todo.append((e, m.group(1)))
    print(f"{len(todo)} entries still labeled 'arXiv preprint' with a lookup-able arXiv id")

    updated = 0
    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        ids = [f"ARXIV:{arxiv_id}" for _, arxiv_id in batch]
        try:
            results = s2_post_batch(ids)
        except Exception as e:
            print(f"  batch {i // BATCH_SIZE} failed: {e}", flush=True)
            time.sleep(REQUEST_DELAY)
            continue
        for (entry, _arxiv_id), result in zip(batch, results):
            venue = (result or {}).get("venue")
            if venue and venue.strip():
                entry["conference"] = venue.strip()
                updated += 1
        DATA_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"  batch {i // BATCH_SIZE + 1}/{(len(todo) - 1) // BATCH_SIZE + 1}: "
              f"{updated} updated so far", flush=True)
        time.sleep(REQUEST_DELAY)

    print(f"Done. {updated} entries updated with a real venue.")


if __name__ == "__main__":
    main()
