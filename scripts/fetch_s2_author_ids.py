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
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

from fetch_common import BASE, HEADERS, by_citations, clean_doi, looks_like_s2_id, normalize_s2_key, s2_request

PAPERS_FILE = BASE / "data" / "papers_full.json"
IDS_FILE = BASE / "data" / "s2_author_ids.json"
CHECKED_FILE = BASE / "data" / "s2_author_ids_checked_papers.json"
# {normalizedAuthorName: {S2 authorId: number of our papers it was seen on}}. Semantic Scholar often splits
# one person over several author profiles and only one of them carries the ORCID, so the name alone is
# not enough to find it: fetch_orcids.py uses these counts to pick the profile that most of the person's
# papers belong to.
COUNTS_FILE = BASE / "data" / "s2_author_id_counts.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
BATCH_SIZE = 20
MAX_CONSECUTIVE_FAILURES = 20
PAPER_BATCH = 400   # S2 takes up to 500 ids per /paper/batch call


@lru_cache(maxsize=None)
def load_api_key():
    # Lazy + memoized, not a module-level call -- this module is imported by
    # its own test suite (test_fetch_s2_author_ids.py), which mocks s2_get
    # and never needs a real key. Reading .env at import time made the mere
    # act of importing this module fail outright wherever .env doesn't
    # exist (correctly, by design, everywhere except a maintainer's own
    # checkout) -- CI included, where it broke `python -m unittest discover`
    # for the whole test run, not just this module's own tests.
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} -- add a line SEMANTIC_SCHOLAR_API_KEY=... (never commit this file)")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return normalize_s2_key(line)
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def normalize_name(n):
    return re.sub(r"[^a-z]", "", (n or "").lower())


def author_list(p):
    """papers_full.json keeps authors as one comma-separated string."""
    a = p.get("authors") or []
    return [x.strip() for x in a.split(",") if x.strip()] if isinstance(a, str) else list(a)


def same_person_name(ours, theirs):
    """Same surname and same first initial: S2 often shortens given names ("A. Lang" for "Alex H. Lang")."""
    a = re.sub(r"[^a-z ]", "", (ours or "").lower().replace("-", " ")).split()
    b = re.sub(r"[^a-z ]", "", (theirs or "").lower().replace("-", " ")).split()
    return bool(a and b) and a[-1] == b[-1] and a[0][0] == b[0][0]


def s2_get(path, params):
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={**HEADERS, "x-api-key": load_api_key()})
    return s2_request(req, timeout=20)


def s2_post_batch(ids):
    url = f"{API_BASE}/paper/batch?{urllib.parse.urlencode({'fields': 'title,authors.authorId,authors.name'})}"
    req = urllib.request.Request(
        url, data=json.dumps({"ids": ids}).encode("utf-8"),
        headers={**HEADERS, "x-api-key": load_api_key(), "Content-Type": "application/json"}, method="POST")
    return s2_request(req, timeout=60)


def external_id(p):
    """ARXIV:/DOI: id S2 understands for this paper, or None."""
    url = (p.get("arxiv_url") or "").rstrip("/")
    if "/abs/" in url:
        cand = "ARXIV:" + re.sub(r"v\d+$", "", url.split("/abs/", 1)[1])
        if looks_like_s2_id(cand):
            return cand
    if p.get("doi"):
        cand = "DOI:" + clean_doi(p["doi"])
        if looks_like_s2_id(cand):
            return cand
    return None


def match_authors(authors, api_authors):
    """{normalized name: authorId} when S2's author list lines up 1:1 with ours, else {}."""
    api_authors = api_authors or []
    if len(api_authors) != len(authors):
        return {}
    return {normalize_name(a): b["authorId"] for a, b in zip(authors, api_authors)
            if b and b.get("authorId") and same_person_name(a, b.get("name"))}


def resolve_by_id(papers, ids, checked, save, post=s2_post_batch, counts=None):
    """One request per PAPER_BATCH papers that carry an arXiv ID or DOI. Papers S2 does not know come back
    as null and count as checked. Returns the papers without any such id (for the title fallback)."""
    with_id = [(external_id(p), p) for p in papers]
    rest = [p for e, p in with_id if not e]
    todo = [(e, p) for e, p in with_id if e]
    for i in range(0, len(todo), PAPER_BATCH):
        chunk = todo[i:i + PAPER_BATCH]
        try:
            answer = post([e for e, _ in chunk])
        except Exception as e:  # noqa: BLE001 -- left unchecked, tried again next run
            print(f"  batch {i // PAPER_BATCH} failed: {e}", flush=True)
            continue
        for (_, p), got in zip(chunk, answer):
            if got:
                matched = match_authors(author_list(p), got.get("authors"))
                ids.update(matched)
                if counts is not None:
                    for name, author_id in matched.items():
                        per_name = counts.setdefault(name, {})
                        per_name[author_id] = per_name.get(author_id, 0) + 1
            checked.add(normalize_title(p["title"]))
        save()
        print(f"  [{min(i + PAPER_BATCH, len(todo))}/{len(todo)}] by id, {len(ids)} authors resolved so far", flush=True)
    return rest


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def resolve_author_ids(title, authors):
    """Looks up `title` on Semantic Scholar and returns {normalized_name:
    authorId} for whichever of `authors` it could confidently match --
    empty (not an error) when nothing matched or the paper isn't on S2 at
    all. Raises on a real failure (network, rate limit after retries);
    callers must treat that as "try again later", never silently as "no
    author ids for this paper" the way an empty return here means.
    """
    try:
        data = s2_get("/paper/search/match", {"query": title, "fields": "title,authors"})
    except urllib.error.HTTPError as e:
        # search/match responds 404 (not 200 + an empty data array) when
        # NOTHING matches the query at all -- same confirmed behavior as
        # fetch_abstracts_semanticscholar.py's identical guard on this same
        # endpoint. A clean "no match", not a failure: without this, EVERY
        # unmatched title (most of them, in practice) counted as a failure
        # and never got marked checked -- tripping the consecutive-failures
        # stop-early almost immediately, and re-querying the exact same
        # doomed titles again on every future run since they never got
        # recorded as tried.
        if e.code == 404:
            return {}
        raise
    results = data.get("data") or []
    title_key = normalize_title(title)
    match = results[0] if results and normalize_title(results[0].get("title")) == title_key else None
    if not match:
        return {}
    api_authors = match.get("authors") or []
    # Only trust a 1:1 name-for-name lineup -- if S2's author count/order
    # doesn't match ours exactly, there's no safe way to know which API
    # author corresponds to which of ours.
    if len(api_authors) != len(authors):
        return {}
    resolved = {}
    for our_name, api_author in zip(authors, api_authors):
        if same_person_name(our_name, api_author.get("name")) and api_author.get("authorId"):
            resolved[normalize_name(our_name)] = api_author["authorId"]
    return resolved


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # titles hold non-ASCII; the Windows console cannot print them
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    av = [p for p in papers if p.get("av_relevance") == "AV" and p.get("authors")]
    # Most-cited-first (requested, applied to every crawler in this
    # pipeline -- see fetch_common.by_citations). This knowingly reopens
    # the same shape of skew a prior "sort by author-list length" ordering
    # was reverted for (resolves big-collaboration-paper authors first,
    # since a highly-cited paper often has a large author list too) --
    # accepted deliberately this time: the goal here is the same as every
    # other crawler's now (enrich what readers actually encounter first),
    # not corpus-wide author-ID representativeness.
    av = by_citations(av)

    ids = load_json(IDS_FILE, {})
    checked = set(load_json(CHECKED_FILE, []))
    recount = "--recount" in sys.argv
    counts = {} if recount else load_json(COUNTS_FILE, {})
    if recount:
        # Revisit every paper once to count which S2 profile each author name appears under.
        checked = set()

    def unresolved_authors(p):
        return [a for a in author_list(p) if normalize_name(a) not in ids]

    pending = [p for p in av if normalize_title(p["title"]) not in checked and (recount or unresolved_authors(p))]
    print(f"{len(pending)} AV papers left to check (of {len(av)} AV papers with an author list, "
          f"{len(ids)} authors already resolved)", flush=True)

    def save():
        save_json(IDS_FILE, ids)
        save_json(CHECKED_FILE, sorted(checked))
        save_json(COUNTS_FILE, counts)

    pending = resolve_by_id(pending, ids, checked, save, counts=counts)
    if recount:
        # The profile most of an author's papers sit under is the one to use.
        for name, per_id in counts.items():
            ids[name] = max(per_id, key=lambda i: per_id[i])
    save()
    if "--title-fallback" not in sys.argv:
        print(f"{len(pending)} papers have no arXiv ID or DOI; rerun with --title-fallback to look them up by "
              f"title (slow).", flush=True)
        return
    processed = 0
    consecutive_failures = 0

    for p in pending:
        title_key = normalize_title(p["title"])
        if not unresolved_authors(p):
            checked.add(title_key)
            continue
        try:
            ids.update(resolve_author_ids(p["title"], author_list(p)))
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
