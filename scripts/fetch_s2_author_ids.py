#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Resolves a Semantic Scholar author ID for as many corpus authors as
possible -- the prerequisite for fetch_orcids.py, which needs an author ID
to look up ORCID (paper-level data has no author ORCID field at all).

Deliberately does NOT resolve authors by searching S2's author-name index
directly (a bare name search is genuinely risky: common names collide, and
an author ID is a unique, identity-defining ID -- getting the WRONG person's
ID here is worse than the namesake-collision risk already flagged for
Scholar photo lookups in DECISIONS.md, since a wrong author ID would later
attach a real stranger's ORCID to this person). Instead: resolve one of an
author's own KNOWN papers via the same exact-title paper/search/match this
whole pipeline already trusts, and take the ID Semantic Scholar itself
attaches to that specific paper's specific co-author slot -- tied to a
paper we already know they wrote, not a guess from a name alone.

One paper lookup resolves EVERY co-author on it at once (S2's paper search
returns the full author list with IDs), so this iterates over papers, not
authors, skipping a paper once every one of its authors already has an ID
-- far fewer requests than one-per-author.

Matches an API-returned author to our own author list by normalized name
(exact match only, same standard as everywhere else in this pipeline) --
a paper where the counts/names don't line up 1:1 is skipped entirely rather
than guessed at.

Writes av-atlas/data/s2_author_ids.json: {normalizedAuthorName:
s2AuthorId}. Never touches papers_full.json -- see fetch_orcids.py, which
reads this file to know which authors to look up an ORCID for.

Usage: python fetch_s2_author_ids.py
"""
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from fetch_common import BASE, HEADERS

PAPERS_FILE = BASE / "data" / "papers_full.json"
IDS_FILE = BASE / "data" / "s2_author_ids.json"
CHECKED_FILE = BASE / "data" / "s2_author_ids_checked_papers.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
REQUEST_DELAY = 1.1
BATCH_SIZE = 20
MAX_CONSECUTIVE_FAILURES = 20


def load_api_key():
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


API_KEY = load_api_key()


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def normalize_name(n):
    return re.sub(r"[^a-z]", "", (n or "").lower())


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


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    av = [p for p in papers if p.get("av_relevance") == "AV" and p.get("authors")]
    # Shuffled, not sorted by author-list length -- the previous "most
    # papers first" ordering systematically resolved large-team-paper
    # authors before anyone else on every partial/interrupted run, leaving
    # ORCID/S2-ID coverage skewed toward big collaborations rather than a
    # representative slice of the corpus (user-requested, applied to every
    # incremental crawler in this pipeline).
    random.shuffle(av)

    ids = load_json(IDS_FILE, {})
    checked = set(load_json(CHECKED_FILE, []))

    def unresolved_authors(p):
        return [a for a in p["authors"] if normalize_name(a) not in ids]

    pending = [p for p in av if normalize_title(p["title"]) not in checked and unresolved_authors(p)]
    print(f"{len(pending)} AV papers left to check (of {len(av)} AV papers with an author list, "
          f"{len(ids)} authors already resolved)", flush=True)

    processed = 0
    consecutive_failures = 0

    for p in pending:
        title_key = normalize_title(p["title"])
        if not unresolved_authors(p):
            checked.add(title_key)
            continue
        try:
            data = s2_get("/paper/search/match", {"query": p["title"], "fields": "title,authors"})
            results = data.get("data") or []
            match = results[0] if results and normalize_title(results[0].get("title")) == title_key else None
            if match:
                api_authors = match.get("authors") or []
                # Only trust a 1:1 name-for-name lineup -- if S2's author
                # count/order doesn't match ours exactly, there's no safe
                # way to know which API author corresponds to which of ours.
                if len(api_authors) == len(p["authors"]):
                    for our_name, api_author in zip(p["authors"], api_authors):
                        if normalize_name(our_name) == normalize_name(api_author.get("name")) and api_author.get("authorId"):
                            ids[normalize_name(our_name)] = api_author["authorId"]
            checked.add(title_key)
            consecutive_failures = 0
        except Exception as e:
            print(f"  failed on {p['title'][:60]!r}: {e}", flush=True)
            consecutive_failures += 1
        processed += 1

        if processed % BATCH_SIZE == 0 or processed == len(pending):
            save_json(IDS_FILE, ids)
            save_json(CHECKED_FILE, sorted(checked))
            print(f"  [{processed}/{len(pending)}] progress saved ({len(ids)} authors resolved so far)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive failures. Resume later by rerunning.",
                  flush=True)
            break

    save_json(IDS_FILE, ids)
    save_json(CHECKED_FILE, sorted(checked))
    print(f"\nDone this run: {len(ids)} authors have a resolved Semantic Scholar ID.", flush=True)


if __name__ == "__main__":
    main()
