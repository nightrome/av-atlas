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
import hashlib
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT_DIR = BASE / "data" / "venues"
HEADERS = {"User-Agent": "av-atlas (mailto:holger@it-caesar.com)"}

# arXiv's two machine interfaces. The plain-http API address now answers
# with a 301 to https, so every fetcher uses the https one directly.
#
# Since September 2026 the search API refuses Python's urllib with HTTP 406
# on any query its CDN hasn't cached, while curl sending the identical URL
# and identical headers (User-Agent, Accept-Encoding: identity, Connection:
# close, HTTP/1.1) gets 200. An Accept header of */* or application/atom+xml
# doesn't change that. So the refusal follows the client library itself, and
# we don't work around it. fetch_arxiv.py, mine_abstracts.py and
# fetch_affiliations_arxiv.py still use the API and will get 406 until arXiv
# changes this. The monthly intake (fetch_arxiv_monthly.py) uses OAI-PMH,
# arXiv's documented harvesting interface, which answers urllib normally.
ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_OAI_URL = "https://oaipmh.arxiv.org/oai"


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


# ---------------------------------------------------------------- errors

# Response headers worth printing when a request fails. The x-amzn ones are
# how Semantic Scholar's API gateway says *why* it refused (a bad key comes
# back as ForbiddenException, a throttle as TooManyRequestsException).
_ERROR_HEADERS = ("content-type", "retry-after", "x-amzn-errortype", "x-ratelimit-limit",
                  "x-ratelimit-remaining", "x-ratelimit-reset", "x-rate-limit-limit",
                  "x-rate-limit-interval", "server")


def http_error_body(e, limit=300):
    """First `limit` characters of an HTTPError's body. The body can only be
    read once, so it's kept on the exception for later callers."""
    if not hasattr(e, "_av_body"):
        try:
            raw = e.read() or b""
        except Exception:
            raw = b""
        e._av_body = raw.decode("utf-8", errors="replace")
    return " ".join(e._av_body.split())[:limit]


def describe_http_error(e, limit=300):
    """One line saying what a 4xx/5xx actually was: status, URL (without the
    query string), the start of the body and the headers that explain it."""
    url = (getattr(e, "url", None) or getattr(e, "filename", None) or "").split("?")[0]
    headers = []
    if e.headers:
        for name in _ERROR_HEADERS:
            value = e.headers.get(name)
            if value:
                headers.append(f"{name}={value}")
    parts = [f"HTTP {e.code} {e.reason or ''}".rstrip()]
    if url:
        parts.append(f"from {url}")
    body = http_error_body(e, limit)
    if body:
        parts.append(f"body: {body}")
    if headers:
        parts.append(f"headers: {', '.join(headers)}")
    return "; ".join(parts)


# ---------------------------------------------------------------- Semantic Scholar /paper/batch

_DOI_URL_PREFIX = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ARXIV_NEW_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
_ARXIV_OLD_RE = re.compile(r"^[a-z][a-z-]*(\.[A-Z]{2})?/\d{7}(v\d+)?$")


def clean_doi(raw):
    """A DOI without surrounding whitespace or a doi.org URL in front."""
    return _DOI_URL_PREFIX.sub("", (raw or "").strip()).strip()


def looks_like_s2_id(ext_id):
    """False for a DOI: or ARXIV: id that can't be right, which S2 would
    answer with a 400 for the whole batch. Other id kinds pass."""
    kind, _, value = ext_id.partition(":")
    kind = kind.upper()
    if kind == "DOI":
        return bool(_DOI_RE.match(clean_doi(value)))
    if kind == "ARXIV":
        return bool(_ARXIV_NEW_RE.match(value) or _ARXIV_OLD_RE.match(value))
    return True


def new_s2_batch_counts():
    return {"skipped": 0, "unknown": 0, "rejected": 0}


def s2_batch_split(fetch, ext_ids, counts, log=print):
    """Looks ext_ids up with fetch (one /paper/batch call, returning a list
    aligned with the ids it was given) and returns a list aligned with
    ext_ids, None where there's no paper.

    Malformed ids aren't sent at all. S2 answers a batch with 400 "No valid
    paper ids given" when it knows none of the ids (checked live on
    2026-09-30: a batch with one unknown or garbled id next to a known one
    gets 200 and a null for the bad one), so that 400 just means "all None"
    and is not retried. Any other 400 splits the batch in half until the
    id S2 objects to is on its own. None of these 400s are logged (fetch
    shouldn't log them either); each id rejected on its own gets one line,
    and counts keeps the totals for a summary at the end of the run."""
    out = [None] * len(ext_ids)
    good = []
    for i, ext in enumerate(ext_ids):
        if looks_like_s2_id(ext):
            good.append(i)
        else:
            counts["skipped"] += 1
            log(f"  Skipping malformed id {ext}")

    def lookup(idx):
        try:
            return fetch([ext_ids[i] for i in idx])
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
            if "no valid paper ids" in http_error_body(e).lower():
                counts["unknown"] += len(idx)
                return [None] * len(idx)
            if len(idx) == 1:
                counts["rejected"] += 1
                log(f"  Semantic Scholar rejected id {ext_ids[idx[0]]}")
                return [None]
            mid = len(idx) // 2
            return lookup(idx[:mid]) + lookup(idx[mid:])

    if good:
        for i, rec in zip(good, lookup(good)):
            out[i] = rec
    return out


def describe_s2_batch_counts(counts):
    return (f"Semantic Scholar batch: {counts['skipped']} malformed ids skipped, "
            f"{counts['unknown']} in batches it had no match for, "
            f"{counts['rejected']} rejected")


# ---------------------------------------------------------------- Semantic Scholar key

S2_KEY_NAME = "SEMANTIC_SCHOLAR_API_KEY"
S2_KEYED_DELAY = 1.1      # 1 request/sec with a key, per S2's docs
S2_ANONYMOUS_DELAY = 3.0  # the keyless pool is shared by everyone; go slower


def normalize_s2_key(raw):
    """The key as S2 expects it: no surrounding whitespace or quotes, and no
    "SEMANTIC_SCHOLAR_API_KEY=" in front (all easy to paste into a GitHub
    secret or .env by accident). None when nothing is left."""
    if raw is None:
        return None
    key = raw.strip()
    if key.startswith(S2_KEY_NAME + "="):
        key = key[len(S2_KEY_NAME) + 1:].strip()
    while len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()
    return key or None


def read_s2_key(env_file, environ=None):
    """The normalized key from the environment, else from env_file, else None."""
    environ = os.environ if environ is None else environ
    key = normalize_s2_key(environ.get(S2_KEY_NAME))
    if key:
        return key
    if env_file and Path(env_file).exists():
        for line in Path(env_file).read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(S2_KEY_NAME + "="):
                key = normalize_s2_key(line)
                if key:
                    return key
    return None


def s2_key_fingerprint(key):
    """Length and a short hash, so a log can show which key was used (to
    compare with the one that works locally) without showing the key."""
    if not key:
        return "no key"
    return f"{len(key)} chars, sha256 {hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}"


class S2Auth:
    """The Semantic Scholar key a script sends, and the switch to go without.

    If S2 answers 403 while the key is being sent, the key is the problem
    (S2's gateway returns 403 Forbidden for a key it doesn't know, and for
    one with quotes or spaces around it). Rather than failing the step, the
    script drops the key for the rest of the run and carries on at the
    anonymous rate, saying so in the log."""

    def __init__(self, key, log=print):
        self.key = key
        self.dropped = False
        self.log = log

    @classmethod
    def from_env(cls, env_file, log=print):
        return cls.start(read_s2_key(env_file), log=log)

    @classmethod
    def start(cls, key, log=print):
        """An S2Auth for `key` (None for none), after saying in the log
        which one it is."""
        auth = cls(normalize_s2_key(key), log=log)
        if auth.key:
            log(f"Semantic Scholar: using the API key ({s2_key_fingerprint(auth.key)})")
        else:
            log("Semantic Scholar: no API key found, using anonymous requests "
                f"(one every {S2_ANONYMOUS_DELAY:g} s)")
        return auth

    @property
    def keyed(self):
        return bool(self.key) and not self.dropped

    def headers(self):
        return {"x-api-key": self.key} if self.keyed else {}

    @property
    def delay(self):
        return S2_KEYED_DELAY if self.keyed else S2_ANONYMOUS_DELAY

    def handle_forbidden(self, e):
        """Call on a 403. Returns True when the key was dropped and the
        request should be retried without it."""
        if not self.keyed:
            return False
        self.dropped = True
        self.log(f"Semantic Scholar rejected the API key ({s2_key_fingerprint(self.key)}): "
                 f"{describe_http_error(e)}")
        self.log("  Falling back to anonymous requests for the rest of this run "
                 f"(one every {S2_ANONYMOUS_DELAY:g} s). Check the {S2_KEY_NAME} value: "
                 "it should be the bare key, no quotes or spaces.")
        return True
