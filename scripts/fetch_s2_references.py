#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetches Semantic Scholar's reference list for every paper in the corpus, so
in-corpus citation counts can come from all ~235k papers instead of only the
~32k whose reference list we could read from a CVF PDF or an ar5iv page.

Two steps, each resumable and each writing its own gitignored side file:

  1. ids -- map each corpus paper (data/papers_full.json) to a Semantic
     Scholar CorpusId, in three passes from cheapest to most expensive:
       a. /paper/batch by ARXIV:<id> and DOI:<doi>, 500 ids per call. The
          arXiv id comes from arxiv_url, from an arxiv.org "doi", or from
          data/venues/arxiv_s2_citing.json (every paper in that file carries
          one, but merge_corpus.py can drop it when a venue listing wins the
          dedupe).
       b. /paper/search/bulk once per venue over the venue's year range,
          following the continuation token, and matched by exact normalized
          title with the year allowed to be off by one (S2 files an ICRA 2023
          paper under 2022 when its preprint came out in 2022).
       c. /paper/search/match per title for whatever is still unmapped,
          accepted only when the returned title normalizes to the same key.
     Writes data/s2_paper_ids.json.
  2. refs -- /paper/batch with fields=references.corpusId for every mapped
     paper, most-cited-first. Stores every reference's CorpusId, not just the
     ones that are in the corpus today, so build_citation_graph.py can pick up
     new edges when the corpus grows without refetching anything (the same
     fetch/match split as reference_lists_cvf.json). Writes
     data/reference_lists_s2.json.
  3. elided -- optional, never part of "all". The batch endpoint withholds
     the references of about half the papers ("elided by the publisher":
     referenceCount says 40, the list is empty). The one-paper endpoint
     /paper/{id}/references still returns them for some of those, mostly
     arXiv-only preprints and almost never IEEE papers (probed 2026-09-25:
     6 of 6 arXiv-only, 2 of 7 CVPR, 0 of 14 from ICLR, NeurIPS, ICRA,
     T-ITS and ITSC). It costs one request per paper, so it's meant to be
     run with --venue, e.g. for the arXiv venues.

build_citation_graph.py's match phase turns these into edges: a reference
CorpusId that maps back to a corpus paper through s2_paper_ids.json is an
exact citation, with no title matching involved.

Keys are the same plain lowercase-and-digits title key build_citation_graph.py
writes citation_graph.json with, so the S2 edges and the text-matched edges
land in one key space.

Both side files are written compactly (no indentation) and atomically: the
reference file alone is tens of MB, and a half-written file after a laptop
goes to sleep would lose the whole crawl.

Usage:
  python scripts/fetch_s2_references.py                    # both steps, whole corpus
  python scripts/fetch_s2_references.py --step ids         # only the id mapping
  python scripts/fetch_s2_references.py --step refs        # only the reference lists
  python scripts/fetch_s2_references.py --step elided --venue arXiv.org
  python scripts/fetch_s2_references.py --venue ICRA --year 2022
  python scripts/fetch_s2_references.py --venue CVPR --limit 200 \\
      --ids-file /tmp/ids.json --refs-file /tmp/refs.json  # a sample run

--venue and --year can be repeated. --limit caps how many papers are taken
(most-cited-first) after the venue/year filter. --no-title-match skips pass c,
which is the slow one (one request per paper).

Needs SEMANTIC_SCHOLAR_API_KEY in .env. Paced at one request per 1.1 s, backs
off on 429 and stops after repeated 429/403 rather than pushing through.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path

from fetch_common import BASE, HEADERS, by_citations

PAPERS_FILE = BASE / "data" / "papers_full.json"
S2_CITING_FILE = BASE / "data" / "venues" / "arxiv_s2_citing.json"
IDS_FILE = BASE / "data" / "s2_paper_ids.json"
REFS_FILE = BASE / "data" / "reference_lists_s2.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
REQUEST_DELAY = 1.1
ID_BATCH_SIZE = 500
REFS_BATCH_SIZE = 250
MIN_REFS_BATCH_SIZE = 10
MAX_RATE_LIMIT_RETRIES = 8
MAX_CONSECUTIVE_FAILURES = 10
SAVE_EVERY = 20
# A paper S2 had no references for (or didn't know at all) is asked again
# after this many days. New preprints often get their reference list a few
# weeks after the paper itself shows up.
RETRY_EMPTY_AFTER_DAYS = 90
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# Corpus venue -> the names S2's bulk search accepts in its venue filter
# (comma-joined into one filter, which S2 treats as "any of"). Checked live
# on 2026-09-24. The short names resolve through S2's alternate names (CVPR ->
# "Computer Vision and Pattern Recognition"); the journals and RA-L don't
# have one and need the full name. Don't use "IV" or "Intelligent Vehicles
# Symposium" here: both resolve to the International Conference on
# Information Visualisation. A venue missing here just skips pass b; its
# papers still get passes a and c.
S2_VENUES = {
    "CVPR": ["CVPR"],
    "ICCV": ["ICCV"],
    "ECCV": ["ECCV"],
    "WACV": ["WACV"],
    "BMVC": ["BMVC"],
    "ACCV": ["ACCV"],
    "GCPR": ["GCPR"],
    "NeurIPS": ["NeurIPS"],
    "ICLR": ["ICLR"],
    "ICML": ["ICML"],
    "AAAI": ["AAAI"],
    "ICRA": ["ICRA"],
    "IROS": ["IROS"],
    "CoRL": ["CoRL"],
    "RSS": ["RSS"],
    "RA-L": ["IEEE Robotics and Automation Letters"],
    "T-RO": ["IEEE Transactions on robotics"],
    "IJRR": ["Int. J. Robotics Res.", "The international journal of robotics research"],
    "TPAMI": ["IEEE Transactions on Pattern Analysis and Machine Intelligence"],
    "IJCV": ["International Journal of Computer Vision"],
    "T-ITS": ["IEEE transactions on intelligent transportation systems (Print)"],
    "ITSC": ["International Conference on Intelligent Transportation Systems"],
    "IV": ["IEEE Intelligent Vehicles Symposium"],
}


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# S2 files many ITSC and IV papers under the raw name printed on that year's
# proceedings rather than under the venue, so those names are added per year.
# ITSC 2023 was the 26th edition.
EDITION_NAMES = {
    "ITSC": lambda y: f"{y} IEEE {_ordinal(y - 1997)} International Conference on Intelligent Transportation Systems (ITSC)",
    "IV": lambda y: f"{y} IEEE Intelligent Vehicles Symposium (IV)",
}


def s2_venue_filter(venue, years):
    names = list(S2_VENUES[venue])
    if venue in EDITION_NAMES:
        names += [EDITION_NAMES[venue](y) for y in years]
    return ",".join(names)


ELIDED = "elided"


class RateLimited(Exception):
    """S2 kept answering 429/403 after every backoff -- stop the run."""


@lru_cache(maxsize=None)
def load_api_key():
    # Lazy, for the same reason as fetch_s2_author_ids.py: the tests import
    # this module on machines (CI) that have no .env.
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} -- add a line SEMANTIC_SCHOLAR_API_KEY=... (never commit this file)")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


def normalize_title(t):
    # Same key as build_citation_graph.normalize_title -- the key space
    # citation_graph.json is written in.
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def arxiv_id(url):
    """'https://arxiv.org/abs/2203.17270v2' -> '2203.17270', else None."""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#\s]+?)(?:v\d+)?(?:\.pdf)?$", url or "")
    return m.group(1) if m else None


def doi_of(p):
    raw = (p.get("doi") or "").strip()
    if not raw or "arxiv.org" in raw:
        return None
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", raw) or None


def s2_request(path, params, body=None):
    """GET (or POST when body is given) against the Graph API.

    Sleeps REQUEST_DELAY after every call, backs off on 429 (and on 403,
    which S2 has been seen to send instead while throttling) and raises
    RateLimited when that doesn't clear it. Other HTTP errors are raised as
    they are for the caller to decide."""
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    headers = {**HEADERS, "x-api-key": load_api_key()}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    delay = 5.0
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code not in (429, 403):
                raise
            if attempt == MAX_RATE_LIMIT_RETRIES - 1:
                raise RateLimited(f"HTTP {e.code} on {path} after {MAX_RATE_LIMIT_RETRIES} tries")
            retry_after = (e.headers or {}).get("Retry-After") if e.headers else None
            time.sleep(float(retry_after) if (retry_after or "").isdigit() else delay)
            delay = min(delay * 2, 120.0)
        except ConnectionError:
            if attempt == MAX_RATE_LIMIT_RETRIES - 1:
                raise
            time.sleep(delay)
        finally:
            time.sleep(REQUEST_DELAY)


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def load_ids(path):
    state = load_json(path, {})
    state.setdefault("ids", {})             # title key -> S2 CorpusId (int)
    state.setdefault("via", {})             # title key -> "arxiv" | "doi" | "venue" | "match"
    state.setdefault("venue_years_scanned", [])  # "CVPR|2022", done by pass b
    state.setdefault("no_match", {})        # title key -> date pass c found nothing
    return state


def load_refs(path):
    state = load_json(path, {})
    state.setdefault("references", {})      # title key -> [CorpusId, ...], non-empty only
    state.setdefault("empty", {})           # title key -> date S2 returned no references
    state.setdefault("elided", {})          # title key -> date S2 withheld them for the publisher
    state.setdefault("not_found", {})       # title key -> date S2 didn't know the CorpusId
    state.setdefault("elided_checked", {})  # title key -> date step 3 found nothing either
    return state


def days_since(day):
    try:
        return (date.fromisoformat(TODAY) - date.fromisoformat(day)).days
    except (TypeError, ValueError):
        return RETRY_EMPTY_AFTER_DAYS


# ---------------------------------------------------------------- step 1: ids

def s2_citing_arxiv_ids():
    """title key -> arXiv id from arxiv_s2_citing.json, which stores the
    arXiv abstract URL in its "doi" field for every paper."""
    out = {}
    for p in load_json(S2_CITING_FILE, []):
        aid = arxiv_id(p.get("doi"))
        key = normalize_title(p.get("title"))
        if aid and key:
            out.setdefault(key, aid)
    return out


def external_ids(p, citing_arxiv):
    """The ids /paper/batch can look this paper up by, best first."""
    key = normalize_title(p.get("title"))
    aid = arxiv_id(p.get("arxiv_url")) or arxiv_id(p.get("doi")) or citing_arxiv.get(key)
    out = []
    if aid:
        out.append(("arxiv", f"ARXIV:{aid}"))
    doi = doi_of(p)
    if doi:
        out.append(("doi", f"DOI:{doi}"))
    return out


def corpus_id_of(record):
    cid = (record or {}).get("corpusId")
    if cid is None:
        cid = ((record or {}).get("externalIds") or {}).get("CorpusId")
    return int(cid) if cid is not None else None


def batch_lookup(ext_ids):
    """/paper/batch for corpusId, one record (or None) per id. S2 answers 400
    for the whole batch when a single id is malformed (a DOI with stray
    characters, say), so a 400 splits the batch until the bad id is alone
    and gets None."""
    try:
        return s2_request("/paper/batch", {"fields": "corpusId"}, {"ids": ext_ids})
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
        if len(ext_ids) == 1:
            return [None]
        mid = len(ext_ids) // 2
        return batch_lookup(ext_ids[:mid]) + batch_lookup(ext_ids[mid:])


def map_by_external_ids(papers, state, citing_arxiv):
    """Pass a. Returns how many papers got an id."""
    wanted = []  # (key, via, external id)
    for p in papers:
        key = normalize_title(p.get("title"))
        for via, ext in external_ids(p, citing_arxiv):
            wanted.append((key, via, ext))
    found = 0
    for i in range(0, len(wanted), ID_BATCH_SIZE):
        chunk = [w for w in wanted[i:i + ID_BATCH_SIZE] if w[0] not in state["ids"]]
        if not chunk:
            continue
        results = batch_lookup([w[2] for w in chunk])
        for (key, via, _), rec in zip(chunk, results):
            cid = corpus_id_of(rec)
            if cid is not None and key not in state["ids"]:
                state["ids"][key] = cid
                state["via"][key] = via
                found += 1
    return found


def pick_by_year(candidates, year):
    """Of the S2 records sharing a title, the one closest in year, if it is
    within one year of ours."""
    best = None
    for rec in candidates:
        y = rec.get("year")
        dy = abs(y - year) if isinstance(y, int) and isinstance(year, int) else 0
        if dy <= 1 and (best is None or dy < best[0]):
            best = (dy, rec)
    return best[1] if best else None


def map_by_venue_scan(papers, state):
    """Pass b: one bulk-search scan per venue over all of its unscanned
    years. Returns how many papers got an id."""
    todo = defaultdict(list)
    for p in papers:
        venue = p.get("venue")
        key = normalize_title(p.get("title"))
        if venue in S2_VENUES and key not in state["ids"] and f"{venue}|{p.get('year')}" not in state["venue_years_scanned"]:
            todo[venue].append(p)
    found = 0
    for venue, ps in sorted(todo.items()):
        years = sorted({p["year"] for p in ps if isinstance(p.get("year"), int)})
        if not years:
            continue
        by_key = defaultdict(list)
        params = {"venue": s2_venue_filter(venue, years), "year": f"{years[0] - 1}-{years[-1] + 1}",
                  "fields": "title,year,externalIds"}
        pages = 0
        while True:
            data = s2_request("/paper/search/bulk", params)
            pages += 1
            for rec in data.get("data") or []:
                by_key[normalize_title(rec.get("title"))].append(rec)
            token = data.get("token")
            if not token:
                break
            params = {**params, "token": token}
        hit = 0
        for p in ps:
            key = normalize_title(p.get("title"))
            rec = pick_by_year(by_key.get(key, []), p.get("year"))
            cid = corpus_id_of(rec)
            if cid is not None and key not in state["ids"]:
                state["ids"][key] = cid
                state["via"][key] = "venue"
                hit += 1
        found += hit
        state["venue_years_scanned"] = sorted(set(state["venue_years_scanned"]) |
                                              {f"{venue}|{p.get('year')}" for p in ps})
        print(f"  {venue} {years[0]}-{years[-1]}: {pages} pages, {sum(len(v) for v in by_key.values())} S2 records, "
              f"{hit} of {len(ps)} papers mapped", flush=True)
    return found


def match_title(title):
    """Pass c for one paper: CorpusId, or None when S2 has no paper whose
    title normalizes to ours."""
    try:
        data = s2_request("/paper/search/match", {"query": title, "fields": "title,corpusId"})
    except urllib.error.HTTPError as e:
        if e.code == 404:  # search/match's way of saying "no match"
            return None
        raise
    results = data.get("data") or []
    if results and normalize_title(results[0].get("title")) == normalize_title(title):
        return corpus_id_of(results[0])
    return None


def map_by_title_match(papers, state, save):
    pending = [p for p in papers if normalize_title(p.get("title")) not in state["ids"]
               and normalize_title(p.get("title")) not in state["no_match"]]
    print(f"  title match: {len(pending)} papers to look up one by one", flush=True)
    found = 0
    failures = 0
    for n, p in enumerate(pending, 1):
        key = normalize_title(p.get("title"))
        try:
            cid = match_title(p["title"])
            failures = 0
        except RateLimited:
            raise
        except Exception as e:
            print(f"  failed on {p['title'][:60]!r}: {e}", flush=True)
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print("  stopping title match after repeated failures; rerun to resume", flush=True)
                break
            continue
        if cid is None:
            state["no_match"][key] = TODAY
        else:
            state["ids"][key] = cid
            state["via"][key] = "match"
            found += 1
        if n % 100 == 0:
            save()
            print(f"  [{n}/{len(pending)}] {found} matched so far", flush=True)
    return found


def step_ids(papers, ids_path, title_match=True):
    state = load_ids(ids_path)
    save = lambda: save_json(ids_path, {**state, "updated": TODAY})  # noqa: E731
    before = len(state["ids"])
    try:
        n = map_by_external_ids(papers, state, s2_citing_arxiv_ids())
        print(f"Pass a (arXiv/DOI batch): {n} papers mapped", flush=True)
        save()
        n = map_by_venue_scan(papers, state)
        print(f"Pass b (venue scan): {n} papers mapped", flush=True)
        save()
        if title_match:
            n = map_by_title_match(papers, state, save)
            print(f"Pass c (title match): {n} papers mapped", flush=True)
    finally:
        save()
    keys = {normalize_title(p.get("title")) for p in papers}
    print(f"Ids: {len(keys & set(state['ids']))} of {len(keys)} selected papers mapped "
          f"({len(state['ids']) - before} new this run)", flush=True)
    return state


# --------------------------------------------------------------- step 2: refs

def refs_pending(papers, ids_state, refs_state):
    out = []
    for p in papers:
        key = normalize_title(p.get("title"))
        if key not in ids_state["ids"] or key in refs_state["references"]:
            continue
        seen = (refs_state["empty"].get(key) or refs_state["elided"].get(key)
                or refs_state["not_found"].get(key))
        if seen and days_since(seen) < RETRY_EMPTY_AFTER_DAYS:
            continue
        out.append(key)
    return out


def fetch_references_batch(keys, ids):
    """{key: [CorpusId, ...]}, or None when S2 didn't know the paper, or
    ELIDED when S2 knows the paper has references but won't hand them out.
    That last case is common: some publishers (IEEE above all) have S2 elide
    the reference list from the API, so referenceCount is 40 and references
    is empty."""
    results = s2_request("/paper/batch", {"fields": "referenceCount,references.corpusId"},
                         {"ids": [f"CorpusId:{ids[k]}" for k in keys]})
    out = {}
    for key, rec in zip(keys, results):
        if rec is None:
            out[key] = None
            continue
        cids = set()
        for ref in rec.get("references") or []:
            cid = (ref or {}).get("corpusId")
            if cid is not None:
                cids.add(int(cid))
        out[key] = sorted(cids) if cids or not rec.get("referenceCount") else ELIDED
    return out


def step_refs(papers, ids_state, refs_path):
    refs = load_refs(refs_path)
    pending = refs_pending(papers, ids_state, refs)
    print(f"References: {len(pending)} mapped papers left to fetch", flush=True)
    batch_size = REFS_BATCH_SIZE
    i = 0
    calls = 0
    failures = 0
    try:
        while i < len(pending):
            keys = pending[i:i + batch_size]
            try:
                got = fetch_references_batch(keys, ids_state["ids"])
            except RateLimited:
                raise
            except urllib.error.HTTPError as e:
                # S2 refuses a batch whose response would be too large; a
                # smaller batch of the same papers goes through.
                if e.code in (400, 413, 500, 502, 504) and batch_size > MIN_REFS_BATCH_SIZE:
                    batch_size = max(MIN_REFS_BATCH_SIZE, batch_size // 2)
                    print(f"  HTTP {e.code}, retrying with batches of {batch_size}", flush=True)
                    continue
                failures += 1
                print(f"  batch at {i} failed: {e}", flush=True)
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    print("  stopping after repeated failures; rerun to resume", flush=True)
                    break
                i += len(keys)
                continue
            failures = 0
            for key, cids in got.items():
                for bucket in ("empty", "elided", "not_found"):
                    refs[bucket].pop(key, None)
                if cids is None:
                    refs["not_found"][key] = TODAY
                elif cids == ELIDED:
                    refs["elided"][key] = TODAY
                elif cids:
                    refs["references"][key] = cids
                else:
                    refs["empty"][key] = TODAY
            i += len(keys)
            calls += 1
            if calls % SAVE_EVERY == 0:
                save_json(refs_path, {**refs, "updated": TODAY})
                print(f"  [{i}/{len(pending)}] saved, {len(refs['references'])} reference lists so far", flush=True)
    finally:
        save_json(refs_path, {**refs, "updated": TODAY})
    print(f"References: {len(refs['references'])} papers with a list, {len(refs['elided'])} withheld "
          f"by the publisher, {len(refs['empty'])} empty, {len(refs['not_found'])} not found", flush=True)
    return refs


# ------------------------------------------------------------- step 3: elided

def fetch_references_single(cid):
    """Every reference CorpusId from the one-paper endpoint, following its
    pagination."""
    cids = set()
    offset = 0
    while offset is not None:
        data = s2_request(f"/paper/CorpusId:{cid}/references",
                          {"fields": "corpusId", "limit": 1000, "offset": offset})
        for row in data.get("data") or []:
            ref = (row or {}).get("citedPaper") or {}
            if ref.get("corpusId") is not None:
                cids.add(int(ref["corpusId"]))
        offset = data.get("next")
    return sorted(cids)


def step_elided(papers, ids_state, refs_path):
    refs = load_refs(refs_path)
    pending = []
    for p in papers:
        key = normalize_title(p.get("title"))
        checked = refs["elided_checked"].get(key)
        if key in refs["elided"] and key in ids_state["ids"] and not (
                checked and days_since(checked) < RETRY_EMPTY_AFTER_DAYS):
            pending.append(key)
    print(f"Elided: {len(pending)} papers to ask one by one", flush=True)
    found = 0
    failures = 0
    try:
        for n, key in enumerate(pending, 1):
            try:
                cids = fetch_references_single(ids_state["ids"][key])
                failures = 0
            except RateLimited:
                raise
            except Exception as e:
                print(f"  failed on {key[:60]!r}: {e}", flush=True)
                failures += 1
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    print("  stopping after repeated failures; rerun to resume", flush=True)
                    break
                continue
            if cids:
                refs["references"][key] = cids
                refs["elided"].pop(key, None)
                refs["elided_checked"].pop(key, None)
                found += 1
            else:
                refs["elided_checked"][key] = TODAY
            if n % 100 == 0:
                save_json(refs_path, {**refs, "updated": TODAY})
                print(f"  [{n}/{len(pending)}] {found} reference lists recovered so far", flush=True)
    finally:
        save_json(refs_path, {**refs, "updated": TODAY})
    print(f"Elided: {found} of {len(pending)} papers had their references on the one-paper endpoint", flush=True)
    return refs


# ---------------------------------------------------------------------- main

def select_papers(papers, venues=None, years=None, limit=None):
    if venues:
        papers = [p for p in papers if p.get("venue") in venues]
    if years:
        papers = [p for p in papers if p.get("year") in years]
    papers = [p for p in by_citations(papers) if normalize_title(p.get("title"))]
    return papers[:limit] if limit else papers


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--step", choices=("ids", "refs", "all", "elided"), default="all")
    ap.add_argument("--venue", action="append", help="corpus venue, e.g. CVPR (repeatable)")
    ap.add_argument("--year", action="append", type=int, help="year, e.g. 2022 (repeatable)")
    ap.add_argument("--limit", type=int, help="take at most this many papers, most-cited-first")
    ap.add_argument("--no-title-match", action="store_true", help="skip the per-title search/match pass")
    ap.add_argument("--ids-file", default=str(IDS_FILE))
    ap.add_argument("--refs-file", default=str(REFS_FILE))
    args = ap.parse_args(argv)

    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    papers = select_papers(papers, args.venue, args.year, args.limit)
    print(f"{len(papers)} papers selected", flush=True)
    ids_path, refs_path = Path(args.ids_file), Path(args.refs_file)
    try:
        if args.step in ("ids", "all"):
            ids_state = step_ids(papers, ids_path, title_match=not args.no_title_match)
        else:
            ids_state = load_ids(ids_path)
        if args.step in ("refs", "all"):
            step_refs(papers, ids_state, refs_path)
        if args.step == "elided":
            step_elided(papers, ids_state, refs_path)
    except RateLimited as e:
        print(f"Stopping: {e}. Progress is saved; rerun later to resume.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
