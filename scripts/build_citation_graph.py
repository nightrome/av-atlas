#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds an in-corpus citation graph -- "how much do AV papers in this corpus
cite each other", a signal OpenAlex/Semantic Scholar can't give us -- from
two raw-reference sources:

  1. This script fetches PDFs for av_relevance=="AV" papers hosted on CVF
     (CVPR/ICCV/WACV -- predictable URL, no rate limit), extracts just the
     references section (pdfplumber, stopping once past it, never touching
     figures), and splits it into raw entries.
  2. fetch_affiliations_arxiv.py separately fetches ar5iv's full-text HTML
     for author affiliations -- since that page is already downloaded, it
     extracts ar5iv's cleanly-structured bibliography (one <li class=
     "ltx_bibitem"> per reference, no PDF-layout noise) at zero extra
     network cost, and saves it to its own file.

Fetching (network-bound, slow) and matching (local string comparison,
fast) are deliberately two separate phases, not one pass that fetches-and-
matches-then-forgets:

  - Phase 1 (fetch): download each AV CVF paper's PDF once, extract and
    save its RAW reference entries to data/reference_lists_cvf.json --
    every entry, whether or not it currently matches anything in the
    corpus. Skips papers already fetched (successfully or not -- failures
    retry automatically, see fetch_with_retries below).
  - Phase 2 (match): runs on every invocation of this script, even if phase
    1 fetched nothing new. Reads BOTH data/reference_lists_cvf.json and
    data/reference_lists_arxiv.json (read-only -- that file belongs to
    fetch_affiliations_arxiv.py) and rematches every saved reference list
    against the CURRENT corpus, overwriting data/citation_graph.json fresh.

This split is the direct answer to "papers not yet indexed might be indexed
later": a reference to a paper that isn't in the corpus yet just doesn't
match today. Nothing is thrown away -- next time the corpus grows (a new
venue pulled, a gap filled) and this script reruns, phase 2 alone picks up
the newly-matchable edges, for every paper ever scanned, with no new network
requests at all.

Matching: a naive "does this corpus title appear in the reference text"
check across all ~66k titles per reference would be O(refs x corpus) and far
too slow. Instead builds an inverted index (distinctive title word -> paper
keys) once per run, and for each reference entry only checks the small
candidate set whose distinctive words actually appear in that entry's text.

data/reference_lists_cvf.json is this script's own file, in the same
succeeded/failed/side-file pattern as fetch_citations_openalex.py /
fetch_affiliations_arxiv.py -- never written by anything else, so it's safe
to run this alongside those. data/citation_graph.json is likewise only ever
written by this script.

Usage: python build_citation_graph.py
"""
import io
import json
import random
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
VENUES_DIR = BASE / "data" / "venues"
REFS_CVF_FILE = BASE / "data" / "reference_lists_cvf.json"
REFS_ARXIV_FILE = BASE / "data" / "reference_lists_arxiv.json"
GRAPH_FILE = BASE / "data" / "citation_graph.json"
CVF_BASE = "https://openaccess.thecvf.com"
CVF_VENUES = {"CVPR", "ICCV", "WACV"}
HEADERS = {"User-Agent": "av-atlas (mailto:holger@it-caesar.com)"}
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 15
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

STOPWORDS = {
    "the", "and", "for", "with", "from", "using", "based", "into", "over", "via",
    "toward", "towards", "learning", "network", "networks", "model", "models",
    "approach", "method", "methods", "detection", "estimation", "driving",
    "autonomous", "vision", "image", "images", "data", "deep", "neural",
}


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def significant_words(title):
    words = re.findall(r"[a-zA-Z]{5,}", (title or "").lower())
    return {w for w in words if w not in STOPWORDS}


def pdf_url_from_path(path):
    # CVF's convention: the listing links to .../html/<slug>.html; the PDF is
    # at the same slug under .../papers/<slug>.pdf. Confirmed against a live
    # paper before building this script (200 OK, not a guess). Older years
    # (pre ~2019) use relative paths with no leading slash, e.g.
    # "content_cvpr_2015/html/..." instead of "/content/CVPR2015/html/..."
    # (same quirk fetch_cvf.py works around) -- always join with exactly one
    # slash rather than relying on the path already having one.
    return f"{CVF_BASE}/{path.lstrip('/')}".replace("/html/", "/papers/").replace(".html", ".pdf")


def build_cvf_pdf_url_index():
    """normalized title -> pdf_url, for every CVPR/ICCV/WACV paper we have a listing for."""
    index = {}
    for f in sorted(VENUES_DIR.glob("*.json")):
        name = f.stem
        venue = re.match(r"[a-z]+", name).group(0).upper()
        if venue not in CVF_VENUES:
            continue
        try:
            papers = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in papers:
            key = normalize_title(p.get("title"))
            path = p.get("path")
            if key and path:
                index[key] = pdf_url_from_path(path)
    return index


def build_corpus_match_index(papers):
    """distinctive title word -> candidate normalized titles, for every paper in the corpus."""
    word_index = defaultdict(list)
    for p in papers:
        key = normalize_title(p.get("title"))
        words = significant_words(p.get("title"))
        if not key or not words:
            continue
        for w in words:
            word_index[w].append(key)
    return word_index


def fetch_with_retries(url, max_retries=3):
    # Most failures seen in practice are transient local-network blips (DNS
    # resolution failing, "network unreachable" -- classic symptoms of Wi-Fi
    # dropping for a moment during a long-running process), not CVF blocking
    # us or anything wrong with that specific paper. A short retry with
    # backoff recovers the large majority of these without waiting for the
    # next full script run.
    delay = 3.0
    last_error = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise  # a real 404 won't fix itself on retry -- fail immediately
            last_error = e
        except Exception as e:
            last_error = e
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
    raise last_error


def fetch_pdf_text(url):
    raw = fetch_with_retries(url)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        pages_text = []
        started = False
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not started and re.search(r"\bReferences\b", text):
                started = True
            if started:
                pages_text.append(text)
        if not pages_text and pdf.pages:
            # No explicit "References" heading found (rare, some templates use
            # "Bibliography" or a different heading) -- fall back to the last
            # two pages, where the reference list almost always lives.
            pages_text = [p.extract_text() or "" for p in pdf.pages[-2:]]
        return "\n".join(pages_text)


def split_reference_entries(text):
    # Reference lists are numbered, either "[12] ..." or "12. ..." at the
    # start of an entry -- split on that marker rather than on newlines,
    # since a single reference often wraps across several PDF text lines.
    parts = re.split(r"(?:^|\n)\s*\[?\d{1,3}\]?[.\)]\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def match_references(entries, word_index):
    matched = set()
    for raw_entry in entries:
        norm_entry = re.sub(r"[^a-z0-9]", "", raw_entry.lower())
        entry_words = significant_words(raw_entry)
        # Candidate generation is just an efficiency narrowing step (checking
        # all ~66k titles against every entry would be too slow) -- the
        # actual match criterion is the normalized title appearing verbatim
        # as a substring of the normalized reference text.
        candidates = set()
        for w in entry_words:
            candidates.update(word_index.get(w, ()))
        for key in candidates:
            if key and key in norm_entry:
                matched.add(key)
    return matched


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def load_refs_cvf():
    refs = load_json(REFS_CVF_FILE, {"succeeded": [], "failed": {}, "references": {}})
    refs.setdefault("succeeded", [])
    refs.setdefault("failed", {})
    refs.setdefault("references", {})
    return refs


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def fetch_phase(av_cvf_titles, pdf_url_index):
    refs = load_refs_cvf()
    succeeded = set(refs["succeeded"])
    pending = [k for k in av_cvf_titles if k in pdf_url_index and k not in succeeded]
    # Shuffled, not left in corpus order (which clusters by venue/year) --
    # an interrupted or session-limited run should still leave the
    # extracted reference-list coverage a representative slice of every
    # venue/year, not just whichever ones happen to sort first
    # (user-requested, applied to every incremental crawler in this
    # pipeline, not just this one).
    random.shuffle(pending)
    n_retrying = sum(1 for k in pending if k in refs["failed"])
    print(f"{len(pending)} AV CVF papers left to fetch (of {len(av_cvf_titles)} AV CVF total), "
          f"including {n_retrying} retrying a previous failure", flush=True)

    processed = 0
    consecutive_failures = 0
    # A key only ever leaves `pending` (the static list built above) by
    # entering `succeeded` -- a failure, permanent or transient, never did,
    # so without this a paper that fails once got re-selected into every
    # subsequent batch for the rest of THIS run, forever, until fetch_with_
    # retries' 404 (which never changes) or a real outage inflated
    # consecutive_failures past the threshold. Confirmed in practice: a
    # cluster of permanently-404 ICCV 2017 papers got retried every single
    # batch, tripping the "likely blocked or network down" early-stop after
    # burning through several batches on a handful of already-known-dead
    # URLs instead of ever reaching the rest of the queue. Cross-run retry
    # (next invocation) is unaffected -- `pending` is rebuilt from `failed`
    # again at the top of the next run.
    attempted_this_run = set()

    while pending:
        refs = load_refs_cvf()
        succeeded = set(refs["succeeded"])
        batch = [k for k in pending if k not in succeeded and k not in attempted_this_run][:BATCH_SIZE]
        if not batch:
            break

        for key in batch:
            attempted_this_run.add(key)
            url = pdf_url_index[key]
            try:
                text = fetch_pdf_text(url)
                refs["references"][key] = split_reference_entries(text)
                refs["succeeded"].append(key)
                refs["failed"].pop(key, None)
                consecutive_failures = 0
            except Exception as e:
                print(f"  failed on {url}: {e}", flush=True)
                # A 404 is permanent (fetch_with_retries never retries one) --
                # the paper simply isn't there, not "not yet fetched". Tagged
                # explicitly so match_phase/aggregate.py can count it as done
                # (nothing more this script could ever do) rather than pending.
                # Doesn't count toward consecutive_failures either -- a 404 is
                # a conclusive, expected outcome, not evidence the connection
                # or this script is broken (which is what that counter exists
                # to detect).
                permanent = isinstance(e, urllib.error.HTTPError) and e.code == 404
                refs["failed"][key] = {"error": str(e), "last_attempt": TODAY, "permanent": permanent}
                consecutive_failures = 0 if permanent else consecutive_failures + 1
            processed += 1
            time.sleep(0.3)

        save_json(REFS_CVF_FILE, refs)
        print(f"  [{processed}/{len(pending)}] fetched ({len(refs['failed'])} papers currently failing)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping fetch early: {consecutive_failures} consecutive PDF fetch/parse failures "
                  f"(likely blocked or network down). Failed papers retry automatically next run.", flush=True)
            return False

    return True


def match_phase(word_index):
    # Always runs, fetch or no fetch -- pure local computation over whatever
    # raw reference text has been saved so far by either source, against
    # whatever the corpus looks like right now. This is what lets a later
    # corpus expansion surface new edges for a paper scanned long ago,
    # without re-fetching anything.
    cvf_all_refs = load_refs_cvf()
    cvf_refs = cvf_all_refs["references"]
    arxiv_refs = load_json(REFS_ARXIV_FILE, {})
    # Confirmed-404 papers can never be fetched no matter how many times this
    # reruns -- counted as "done" (nothing left to do), not "still pending",
    # by aggregate.py's coverage stat.
    cvf_permanent_failures = sum(1 for v in cvf_all_refs["failed"].values() if v.get("permanent"))

    edges = {}
    total_edges = 0
    for source, entries in {**cvf_refs, **arxiv_refs}.items():
        matches = sorted(match_references(entries, word_index) - {source})
        if matches:
            edges[source] = matches
            total_edges += len(matches)

    graph = {
        "generated_at": TODAY,
        "sources_scanned": {"cvf": len(cvf_refs), "arxiv": len(arxiv_refs)},
        "cvf_permanent_failures": cvf_permanent_failures,
        "edges": edges,
    }
    save_json(GRAPH_FILE, graph)
    print(f"Match phase: {len(cvf_refs)} CVF + {len(arxiv_refs)} arXiv reference lists rematched "
          f"against the current corpus -- {total_edges} in-corpus citation edges found "
          f"across {len(edges)} papers.", flush=True)


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    av_cvf_titles = {
        normalize_title(p["title"]) for p in papers
        if p.get("av_relevance") == "AV" and (p.get("venue") or "") in CVF_VENUES
    }
    pdf_url_index = build_cvf_pdf_url_index()
    word_index = build_corpus_match_index(papers)

    fetch_phase(av_cvf_titles, pdf_url_index)
    match_phase(word_index)


if __name__ == "__main__":
    main()
