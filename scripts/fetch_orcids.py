#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches ORCID IDs for every author fetch_s2_author_ids.py resolved a
Semantic Scholar author ID for. Uses /author/batch (POST, up to 1000 IDs
per call, confirmed live) rather than one request per author -- resolving
thousands of authors' IDs costs one request each in fetch_s2_author_ids.py,
but their ORCIDs cost only a handful of batched requests total.

Not every author has an ORCID linked on their Semantic Scholar profile --
that's expected and recorded as such (this author has NO orcid key at all
in the output), not an error.

Writes av-atlas/data/orcids.json: {normalizedAuthorName: orcid or
absent}. Per-author enrichment, not paper-level -- consumed directly by
aggregate.py the same way it already reads data/scholar_profiles.json,
not folded into papers_full.json first.

Usage: python fetch_orcids.py
"""
import json
import sys
import time
import urllib.error
import urllib.request
from functools import lru_cache

from fetch_common import BASE, HEADERS, normalize_s2_key, s2_request

IDS_FILE = BASE / "data" / "s2_author_ids.json"
COUNTS_FILE = BASE / "data" / "s2_author_id_counts.json"
OUT_FILE = BASE / "data" / "orcids.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
CHUNK_SIZE = 1000  # /author/batch's own documented max


@lru_cache(maxsize=None)
def load_api_key():
    # Lazy + memoized, not a module-level call -- importing this module
    # (e.g. from a future test file, same class of bug fixed in
    # fetch_s2_author_ids.py) must not require .env to exist.
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} -- add a line SEMANTIC_SCHOLAR_API_KEY=... (never commit this file)")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return normalize_s2_key(line)
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


def s2_post(path, params, body):
    from urllib.parse import urlencode
    url = f"{API_BASE}{path}?{urlencode(params)}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={**HEADERS, "x-api-key": load_api_key(), "Content-Type": "application/json"}, method="POST",
    )
    return s2_request(req, timeout=30)


def dominant_id(per_id, min_share=0.5):
    """The S2 profile most of a name's papers sit under, or None when no profile has at least
    `min_share` of them (the name then probably stands for several people)."""
    total = sum(per_id.values())
    best = max(per_id, key=lambda i: per_id[i])
    return best if total and per_id[best] / total >= min_share else None


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # titles hold non-ASCII; the Windows console cannot print them
    author_ids = json.loads(IDS_FILE.read_text(encoding="utf-8")) if IDS_FILE.exists() else {}
    counts = json.loads(COUNTS_FILE.read_text(encoding="utf-8")) if COUNTS_FILE.exists() else {}
    # With per-profile counts, a name uses the profile most of its papers belong to; without them
    # (an older run) it keeps the single profile that was stored.
    for name, per_id in counts.items():
        chosen = dominant_id(per_id)
        if chosen:
            author_ids[name] = chosen
        else:
            author_ids.pop(name, None)
    name_by_id = {}
    for name, aid in author_ids.items():
        name_by_id.setdefault(aid, []).append(name)

    all_ids = list(name_by_id.keys())
    print(f"{len(all_ids)} distinct Semantic Scholar author IDs to check for an ORCID", flush=True)

    orcids = {}
    for i in range(0, len(all_ids), CHUNK_SIZE):
        chunk = all_ids[i:i + CHUNK_SIZE]
        try:
            results = s2_post("/author/batch", {"fields": "externalIds"}, {"ids": chunk})
        except Exception as e:
            print(f"  batch {i // CHUNK_SIZE + 1} failed: {e}", flush=True)
            continue
        for author in results or []:
            if not author:
                continue
            orcid = (author.get("externalIds") or {}).get("ORCID")
            if not orcid:
                continue
            for name in name_by_id.get(author["authorId"], []):
                orcids[name] = orcid
        print(f"  [{min(i + CHUNK_SIZE, len(all_ids))}/{len(all_ids)}] checked ({len(orcids)} ORCIDs found so far)",
              flush=True)

    OUT_FILE.write_text(json.dumps(orcids, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"\nWrote {OUT_FILE}: {len(orcids)} authors have a confirmed ORCID", flush=True)


if __name__ == "__main__":
    main()
