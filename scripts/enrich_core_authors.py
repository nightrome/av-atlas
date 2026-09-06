#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Institution/country data in stats.json was only ever available for the ~15
citation-crawl-pilot papers that had it from the start -- the ~66k papers
pulled from venue proceedings only ever captured a plain author-name string,
never affiliations. That's why the institution/country leaderboards looked
implausible (huge numbers from a tiny, non-representative sample).

This fixes the root cause: looks up each av_relevance=="core" paper in
papers_full.json against OpenAlex (same source already used for ICRA/IROS)
to fill in authors_detail (name + affiliations + country codes), then
overwrites papers_full.json in place. Only touches "core" papers -- the
other ~63.7k "adjacent" papers were never going to be ranked anyway, so
there's no reason to spend OpenAlex's rate budget on them.

Stamps authors_detail_source="openalex" alongside the detail, so every
paper's affiliation data can be traced back to which crawl produced it (see
also fetch_cvf_affiliations.py / apply_affiliations_arxiv.py, which stamp
their own sources) -- run this LAST, after the free PDF/arXiv-HTML sources
have had a chance to fill in what they can, since it only touches papers
that still have no authors_detail at all.

Runs in small batches (BATCH_SIZE papers at a time): re-reads papers_full.json
fresh at the start of every batch and writes it back immediately after, rather
than holding one in-memory snapshot for the whole run. This was a real bug,
not just hygiene -- an earlier version loaded the file once and saved its own
increasingly-stale copy every 100 papers, so a concurrent merge_corpus.py
rerun that updated the file mid-run got silently reverted on the next save.
Small batches keep this process's view of the file fresh and its footprint on
disk small enough to interrupt at any point.

Also exits early after MAX_CONSECUTIVE_FAILURES consecutive lookup failures,
since a long run of failures almost always means OpenAlex's rate/daily budget
is exhausted, not that this batch of papers is unusually hard to find -- no
point grinding through thousands more doomed requests.

Safe to stop and resume any time; skips papers that already have
authors_detail (from the citation-crawl pilot or a prior run of this script).

Also captures the per-author OpenAlex author id and ORCID (used downstream
for identity-resolved author ranking -- see aggregate.py). `--refetch-ids`
reprocesses OpenAlex-sourced papers that predate id capture (authors_detail
present but no openalex_id on its entries).

A title-similarity guard rejects the OpenAlex work when its title isn't
essentially this paper's -- the search takes result #1, which for affiliation
noise was tolerable but for author *identity* would attach the wrong
person's id.

Usage: python enrich_core_authors.py [--refetch-ids]
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
IN_FILE = BASE / "data" / "papers_full.json"
CONTACT_EMAIL = "holger@it-caesar.com"
BATCH_SIZE = 20
MAX_CONSECUTIVE_FAILURES = 30
TITLE_MATCH_MIN = 0.90


def _nt(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def title_close(a, b):
    na, nb = _nt(a), _nt(b)
    if not na or not nb:
        return False
    if na == nb or (len(na) > 20 and (na in nb or nb in na)):
        return True
    return SequenceMatcher(None, na, nb).ratio() >= TITLE_MATCH_MIN


def fetch_json(url, max_retries=4):
    req = urllib.request.Request(url, headers={"User-Agent": f"av-atlas (mailto:{CONTACT_EMAIL})"})
    delay = 5.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        except Exception:
            return None


def _short_id(url):
    return url.rsplit("/", 1)[-1] if url else None


def openalex_authors(title):
    params = urllib.parse.urlencode({"search": title, "per-page": 1, "mailto": CONTACT_EMAIL})
    data = fetch_json(f"https://api.openalex.org/works?{params}")
    if not data or not data.get("results"):
        return None
    work = data["results"][0]
    if not title_close(title, work.get("display_name") or work.get("title")):
        return None  # search returned a different paper -- don't attach its authors
    authors_detail = []
    for a in (work.get("authorships") or []):
        au = a.get("author") or {}
        name = au.get("display_name")
        if not name:
            continue
        insts = a.get("institutions") or []
        authors_detail.append({
            "name": name,
            "affiliations": [i.get("display_name") for i in insts if i.get("display_name")],
            "countries": [i.get("country_code") for i in insts if i.get("country_code")],
            "openalex_id": _short_id(au.get("id")),
            "orcid": _short_id(au.get("orcid")),
        })
    return authors_detail


def _needs_ids(p):
    ad = p.get("authors_detail") or []
    return (p.get("authors_detail_source") == "openalex" and ad
            and not any("openalex_id" in a for a in ad))


def _in_corpus_citations(p):
    return ((p.get("citations_by_source") or {}).get("in_corpus") or {}).get("count") or 0


def load_pending(refetch_ids):
    """Re-reads the file fresh and returns (all_papers, titles needing lookup, core_total)."""
    papers = json.loads(IN_FILE.read_text(encoding="utf-8"))
    core = [p for p in papers if p.get("av_relevance") == "core"]
    # Most-cited-first: OpenAlex's rate ceiling means coverage plateaus well
    # short of 100%, so the papers it does reach should be the ones that
    # anchor the Institutions/Countries leaderboards -- the top-cited ones --
    # not a random slice (user-requested, supersedes the earlier
    # random-order call now that the ceiling is the binding constraint).
    core.sort(key=_in_corpus_citations, reverse=True)
    if refetch_ids:
        pending_titles = [p["title"] for p in core if _needs_ids(p)]
    else:
        pending_titles = [p["title"] for p in core if not p.get("authors_detail")]
    return papers, pending_titles, len(core)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refetch-ids", action="store_true",
                    help="reprocess OpenAlex-sourced papers whose authors_detail predates openalex_id capture")
    args = ap.parse_args()
    refetch_ids = args.refetch_ids

    _, pending, core_total = load_pending(refetch_ids)
    total_pending = len(pending)
    what = "re-fetch for author ids" if refetch_ids else "author-affiliation lookup"
    print(f"{total_pending} core papers need {what} (of {core_total} core total)", flush=True)

    done = 0
    processed = 0
    consecutive_failures = 0
    # load_pending() rebuilds pending_titles purely from "still missing
    # authors_detail" every iteration -- a genuine OpenAlex non-match never
    # sets that field, so without this a paper OpenAlex simply doesn't have
    # gets re-queried in every subsequent batch for the rest of THIS run.
    # Beyond the wasted requests, it also corrupts the one signal
    # consecutive_failures exists to give: a real cluster of "not on
    # OpenAlex" papers would look identical to "budget exhausted" and could
    # trip the early-stop even when the budget is fine (same bug class as
    # build_citation_graph.py's; see that file for the confirmed-in-practice
    # version of this).
    attempted_this_run = set()

    while True:
        papers, pending_titles, _ = load_pending(refetch_ids)
        pending_titles = [t for t in pending_titles if t not in attempted_this_run]
        if not pending_titles:
            break
        batch = pending_titles[:BATCH_SIZE]
        by_title = {p["title"]: p for p in papers if p.get("title") in batch}

        for title in batch:
            attempted_this_run.add(title)
            detail = openalex_authors(title)
            time.sleep(0.15)
            processed += 1
            if detail:
                by_title[title]["authors_detail"] = detail
                by_title[title]["authors_detail_source"] = "openalex"
                done += 1
                consecutive_failures = 0
            else:
                consecutive_failures += 1

        IN_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"  [{processed}/{total_pending}] progress saved ({done} enriched so far)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive lookup failures "
                  f"(likely OpenAlex rate/daily budget exhausted). Resume later by rerunning this script.",
                  flush=True)
            return

    print(f"Done: enriched {done}/{total_pending} core papers with author affiliations", flush=True)


if __name__ == "__main__":
    main()
