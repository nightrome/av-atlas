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
import time
import urllib.error
import urllib.request

from fetch_common import BASE, HEADERS

IDS_FILE = BASE / "data" / "s2_author_ids.json"
OUT_FILE = BASE / "data" / "orcids.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
REQUEST_DELAY = 1.1
CHUNK_SIZE = 1000  # /author/batch's own documented max


def load_api_key():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


API_KEY = load_api_key()


def s2_post(path, params, body):
    from urllib.parse import urlencode
    url = f"{API_BASE}{path}?{urlencode(params)}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={**HEADERS, "x-api-key": API_KEY, "Content-Type": "application/json"}, method="POST",
    )
    delay = REQUEST_DELAY
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        finally:
            time.sleep(REQUEST_DELAY)


def main():
    author_ids = json.loads(IDS_FILE.read_text(encoding="utf-8")) if IDS_FILE.exists() else {}
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
