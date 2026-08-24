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
