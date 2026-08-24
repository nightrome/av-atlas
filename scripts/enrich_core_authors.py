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

Usage: python enrich_core_authors.py
"""
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
IN_FILE = BASE / "data" / "papers_full.json"
CONTACT_EMAIL = "holger@it-caesar.com"
BATCH_SIZE = 20
MAX_CONSECUTIVE_FAILURES = 30


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


def openalex_authors(title):
    params = urllib.parse.urlencode({"search": title, "per-page": 1, "mailto": CONTACT_EMAIL})
    data = fetch_json(f"https://api.openalex.org/works?{params}")
    if not data or not data.get("results"):
        return None
    work = data["results"][0]
    authors_detail = []
    for a in (work.get("authorships") or []):
        name = (a.get("author") or {}).get("display_name")
        if not name:
            continue
        insts = a.get("institutions") or []
        authors_detail.append({
            "name": name,
            "affiliations": [i.get("display_name") for i in insts if i.get("display_name")],
            "countries": [i.get("country_code") for i in insts if i.get("country_code")],
        })
    return authors_detail


def load_pending():
    """Re-reads the file fresh and returns (all_papers, titles still needing lookup)."""
    papers = json.loads(IN_FILE.read_text(encoding="utf-8"))
    pending_titles = [p["title"] for p in papers
                       if p.get("av_relevance") == "core" and not p.get("authors_detail")]
    # Shuffled, not corpus order (clusters by venue/year) -- an interrupted
    # run should still leave affiliation coverage a representative slice of
    # the corpus, not just whichever venues happen to sort first
    # (user-requested, applied to every incremental crawler in this
    # pipeline).
    random.shuffle(pending_titles)
    core_total = sum(1 for p in papers if p.get("av_relevance") == "core")
    return papers, pending_titles, core_total


def main():
    _, pending, core_total = load_pending()
    total_pending = len(pending)
    print(f"{total_pending} core papers need author-affiliation lookup (of {core_total} core total)", flush=True)

    done = 0
    processed = 0
    consecutive_failures = 0

    while True:
        papers, pending_titles, _ = load_pending()
        if not pending_titles:
            break
        batch = pending_titles[:BATCH_SIZE]
        by_title = {p["title"]: p for p in papers if p.get("title") in batch}

        for title in batch:
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
