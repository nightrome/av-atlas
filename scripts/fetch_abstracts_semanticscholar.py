#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfills missing abstracts from Semantic Scholar, for AV papers that have
none -- mostly the DBLP-sourced venues (T-ITS, ITSC, IV account for ~83% of
the gap: DBLP has never carried abstracts, see DECISIONS.md's "DBLP-sourced
venues have no abstracts") plus smaller gaps in ICRA/IROS/RA-L/T-RO and a
handful of other venues' DBLP-fallback years.

One API call per paper: /paper/search/match?query=<title>&fields=title,abstract
resolves a title straight to Semantic Scholar's best-match record AND its
abstract in the same request (no separate /paper/{id} lookup needed) --
confirmed against fetch_semanticscholar_citing.py's existing use of the same
endpoint (that script already requests `abstract` in its OWN /paper/{id}/citations
call, just for a different purpose: discovering new citing papers, not
backfilling this corpus's own). Same API key, same documented 1 req/sec
rate limit, same exact-title-match verification standard as every other
title-resolution step in this pipeline.

Writes its own side file, data/abstracts_semanticscholar.json:
  {"succeeded": [...normalizedTitle...], "failed": {...}, "abstracts": {normalizedTitle: "..."}}
in the same succeeded/failed/side-file pattern as fetch_cvf_affiliations.py --
never touches papers_full.json directly; see apply_abstracts_semanticscholar.py
for the single-writer step that folds this in.

Safe to stop and resume any time; skips titles already in "succeeded".
Exits early after MAX_CONSECUTIVE_FAILURES consecutive lookup failures,
since a long run of failures almost always means the API key's rate/daily
budget is exhausted, not that this batch of papers is unusually hard to find.

Usage: python fetch_abstracts_semanticscholar.py
"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fetch_common import BASE, HEADERS

PAPERS_FILE = BASE / "data" / "papers_full.json"
OUT_FILE = BASE / "data" / "abstracts_semanticscholar.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
REQUEST_DELAY = 1.1  # documented 1 req/sec, cumulative -- same tuning as fetch_semanticscholar_citing.py
BATCH_SIZE = 20
MAX_CONSECUTIVE_FAILURES = 30


def load_api_key():
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} -- add a line SEMANTIC_SCHOLAR_API_KEY=... (never commit this file)")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


API_KEY = load_api_key()


def normalize_title(t):
    import re
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def s2_get(path, params):
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={**HEADERS, "x-api-key": API_KEY})
    delay = REQUEST_DELAY
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        finally:
            time.sleep(REQUEST_DELAY)


def fetch_abstract(title):
    """Returns the abstract string, or None if S2 has no exact-title match
    or no abstract on file for it."""
    data = s2_get("/paper/search/match", {"query": title, "fields": "title,abstract"})
    results = data.get("data") or []
    if not results:
        return None
    # Same exact-title verification standard as resolve_paper_id in
    # fetch_semanticscholar_citing.py -- a fuzzy same-topic match must never
    # be treated as a real hit.
    if normalize_title(results[0].get("title")) != normalize_title(title):
        return None
    return results[0].get("abstract") or None


def load_out():
    data = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    data.setdefault("succeeded", [])
    data.setdefault("failed", {})
    data.setdefault("abstracts", {})
    return data


def save_out(data):
    OUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    pending_titles = [
        p["title"] for p in papers
        if p.get("av_relevance") == "AV" and not (p.get("abstract") or "").strip() and p.get("title")
    ]
    # Shuffled, not corpus order -- see build_citation_graph.py's matching
    # fix for why (a long-tail venue must not always sort last across
    # interrupted/resumed runs). Applied to every incremental crawler.
    random.shuffle(pending_titles)

    data = load_out()
    succeeded = set(data["succeeded"])
    pending = [t for t in pending_titles if normalize_title(t) not in succeeded]
    print(f"{len(pending)} AV papers without an abstract to try via Semantic Scholar "
          f"(of {len(pending_titles)} missing total)", flush=True)

    processed = 0
    got_abstracts = 0
    consecutive_failures = 0
    # Same fix as build_citation_graph.py's identical loop shape -- without
    # this, a paper that fails once (a permanent no-match in particular,
    # which will never succeed on retry) gets re-selected into every
    # subsequent batch for the rest of THIS run.
    attempted_this_run = set()

    while pending:
        data = load_out()
        succeeded = set(data["succeeded"])
        batch = [t for t in pending if normalize_title(t) not in succeeded and t not in attempted_this_run][:BATCH_SIZE]
        if not batch:
            break

        for title in batch:
            attempted_this_run.add(title)
            key = normalize_title(title)
            try:
                abstract = fetch_abstract(title)
                if abstract:
                    data["abstracts"][key] = abstract
                    got_abstracts += 1
                data["succeeded"].append(key)
                data["failed"].pop(key, None)
                consecutive_failures = 0
            except Exception as e:
                print(f"  failed on {title[:70]!r}: {e}", flush=True)
                data["failed"][key] = {"error": str(e)}
                consecutive_failures += 1
            processed += 1

        save_out(data)
        print(f"  [{processed}/{len(pending)}] checked ({got_abstracts} abstracts found, "
              f"{len(data['failed'])} currently failing)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive lookup failures. "
                  f"Failed papers retry automatically next run.", flush=True)
            return

    print(f"\nDone: {got_abstracts} abstracts found this run.", flush=True)


if __name__ == "__main__":
    main()
