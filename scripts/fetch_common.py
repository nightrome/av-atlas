#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared HTTP-fetch plumbing for every av-atlas fetch_*.py script -- BASE/
OUT_DIR paths, the User-Agent header, and a retry-with-backoff GET. Every
fetcher used to hand-roll its own copy of this (confirmed on real data: DBLP
got retry-backoff after a real 503 incident, but CVF/NeurIPS never did,
purely because whoever touched DBLP that day didn't also touch the others).
Pulling it into one module means a fix here reaches every fetcher instead of
whichever one happened to get edited.

This does NOT try to unify the actual scraping/parsing logic -- CVF's HTML,
DBLP's HTML, NeurIPS' HTML, OpenAlex's JSON, and arXiv's Atom XML are
genuinely different formats with genuinely different pagination and rate
limits, and forcing them through one shape would trade real per-source
correctness for a uniformity that doesn't otherwise buy anything.
"""
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "venues"
HEADERS = {"User-Agent": "av-atlas (mailto:holger@it-caesar.com)"}


def _in_corpus_citations(p):
    # NOT p.get("citations") -- that top-level field is dead (confirmed on
    # real data: 0 of 25,639 AV papers have it set, including nuScenes,
    # this corpus's single most-cited paper). The real, actively-maintained
    # count is citations_by_source.in_corpus.count, written by
    # build_citation_graph.py/backfill_citing_venues.py -- the same number
    # every leaderboard and "most cited" list on the site itself ranks by.
    return ((p.get("citations_by_source") or {}).get("in_corpus") or {}).get("count") or 0


def by_citations(papers):
    """Sorts paper dicts by in-corpus citation count, descending --
    highest-impact papers first, ties broken by title for a deterministic
    order across runs. Missing/zero citations sorts to the back rather than
    crashing the comparison or being treated as "unknown, skip".

    User-requested: every external source this pipeline crawls is
    rate-limited or budget-capped (see PIPELINE.md's "OpenAlex and arXiv
    rate limits" -- OpenAlex's daily budget alone has cut a full
    enrich_av_authors.py run off after ~200 papers of a 15,000+ backlog).
    A crawl that gets cut off partway through should already have enriched
    the papers readers actually encounter first -- the ones on every
    top-cited list, comparison page, and venue/author leaderboard -- not
    whichever paper happened to load first from its source venue file.
    Replaces this pipeline's earlier random-shuffle-the-queue convention
    (still correct for ML-training-data sampling scripts, which need an
    unbiased draw, not a priority order -- see e.g. select_labeling_
    candidates.py/fetch_llm_relevance_labels.py, deliberately NOT switched
    to this) -- and matches enrich_av_authors.py's own load_pending(),
    which already did this same most-cited-first sort by user request
    before this helper existed.
    """
    return sorted(papers, key=lambda p: (-_in_corpus_citations(p), p.get("title") or ""))


def fetch(url, timeout=30, max_retries=1, retry_status=(503,), backoff=10, headers=None):
    """GET url and return the decoded response body.

    max_retries=1 (the default) means "try once, no retry" -- matching every
    fetcher's original behavior before this module existed. Pass a higher
    max_retries for sources known to throttle under sustained use (DBLP: 503,
    OpenAlex/arXiv: 429) -- retry_status/backoff let each caller keep its own
    tuning (arXiv backs off harder than DBLP does) instead of forcing one
    schedule on every source.

    Also retries on a bare connection drop (http.client.RemoteDisconnected,
    a ConnectionError subclass) regardless of retry_status/max_retries=1 --
    confirmed on real data: a multi-hour DBLP fetch (ICML, 15 years) died on
    year 14 of 15 with an uncaught RemoteDisconnected, a transient network
    blip unrelated to any HTTP status code (there's no response to have a
    status), losing all progress on that invocation. A single retry after a
    short pause is enough for a one-off drop; a sustained outage still
    surfaces as an error after that, not silently retried forever.
    """
    req = urllib.request.Request(url, headers=headers or HEADERS)
    # At least 2 attempts for a bare connection drop even when the caller
    # left max_retries at its 1-attempt default -- a RemoteDisconnected has
    # no HTTP status to check against retry_status, so it needs its own
    # floor rather than inheriting a budget callers set with only HTTP
    # error codes in mind.
    connection_error_attempts = max(2, max_retries)
    for attempt in range(connection_error_attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code in retry_status and attempt < max_retries - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise
        except ConnectionError:
            if attempt < connection_error_attempts - 1:
                time.sleep(backoff)
                continue
            raise
