#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds an in-corpus citation graph -- "who cites whom within this corpus",
a signal OpenAlex/Semantic Scholar can't give us -- from two raw-reference
sources:

  1. This script fetches PDFs for every paper hosted on CVF (CVPR/ICCV/WACV
     -- predictable URL, no rate limit; not just av_relevance=="AV" papers,
     see main()'s own comment), archives the raw PDF to data/pdfs_cvf/ (so
     it doesn't have to be re-downloaded to be reprocessed for something
     else later -- gitignored, this is a local cache, not a repo asset),
     extracts just the references section (PyMuPDF, stopping once past it,
     never touching figures), and splits it into raw entries.
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
too slow. Instead builds an index of title prefixes once per run and looks
each position of the reference entry up in it (see CorpusMatchIndex).

data/reference_lists_cvf.json is this script's own file, in the same
succeeded/failed/side-file pattern as fetch_citations_openalex.py /
fetch_affiliations_arxiv.py -- never written by anything else, so it's safe
to run this alongside those. data/citation_graph.json is likewise only ever
written by this script.

Usage: python build_citation_graph.py
   or: python build_citation_graph.py --match-only   # phase 2 alone, no network

build_public_site.py runs the --match-only form on every build, so a
corpus change always reaches the graph (and from there stats.json) without
anyone remembering to rerun this script. It needs only the saved reference
lists, never the PDFs or the network.
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fetch_common import by_citations

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
VENUES_DIR = BASE / "data" / "venues"
REFS_CVF_FILE = BASE / "data" / "reference_lists_cvf.json"
REFS_ARXIV_FILE = BASE / "data" / "reference_lists_arxiv.json"
GRAPH_FILE = BASE / "data" / "citation_graph.json"
PDF_DIR = BASE / "data" / "pdfs_cvf"
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
    pdf_path = path.replace("/html/", "/papers/").replace(".html", ".pdf")
    # ICCV 2017 specifically directories its PDFs under "content_ICCV_2017"
    # even though its own html/ listing pages live under lowercase
    # "content_iccv_2017" -- every other year keeps html/ and papers/ under
    # the same-cased directory. Confirmed live: the stored "content_iccv_2017/
    # html/<slug>.html" path 200s, but naively lowercasing papers/ 404s,
    # while the html page's own outgoing PDF link is "../../content_ICCV_2017/
    # papers/<slug>.pdf" -- caught because every one of that year's fetches
    # in fetch_cvf_affiliations.py/build_citation_graph.py was 404ing.
    pdf_path = pdf_path.replace("content_iccv_2017/papers/", "content_ICCV_2017/papers/")
    return f"{CVF_BASE}/{pdf_path.lstrip('/')}"


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


# Length of the normalized-title prefix the match index is keyed on (see
# CorpusMatchIndex). Long enough that a prefix is nearly unique, short enough
# that almost every title is at least this long.
PREFIX_LEN = 12


class CorpusMatchIndex:
    """What match_references needs to know about the corpus.

    A reference entry matches a corpus title when the normalized title is a
    substring of the normalized entry and the two share at least one
    distinctive word. The first version looked up every title sharing any
    word with the entry and substring-checked each one, which is fine for a
    few reference lists but took well over 20 minutes for the full set
    against the ~235k-title corpus, too slow to run on every build. Titles
    are now found by their first PREFIX_LEN characters at each position of
    the entry instead, which gives the same matches. Titles shorter than
    that (rare) still go through the word lookup.
    """

    def __init__(self):
        self.words = defaultdict(set)          # key -> its distinctive words
        self.by_prefix = defaultdict(set)      # key[:PREFIX_LEN] -> long keys
        self.short_by_word = defaultdict(set)  # word -> keys shorter than PREFIX_LEN

    def add(self, key, words):
        self.words[key] |= words
        if len(key) >= PREFIX_LEN:
            self.by_prefix[key[:PREFIX_LEN]].add(key)
        else:
            for w in words:
                self.short_by_word[w].add(key)


def build_corpus_match_index(papers):
    """The match index (see CorpusMatchIndex) for every paper in the corpus."""
    index = CorpusMatchIndex()
    for p in papers:
        key = normalize_title(p.get("title"))
        words = significant_words(p.get("title"))
        if not key or not words:
            continue
        index.add(key, words)
    return index


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


def fetch_pdf_text(url, key):
    raw = fetch_with_retries(url)
    # Archived before parsing, so even a PDF that fails to parse (corrupt,
    # scanned image, whatever) still leaves the raw file on disk for later
    # reprocessing -- user-requested, this is meant as a general local PDF
    # cache for this corpus, not just a reference-extraction input.
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    (PDF_DIR / f"{key}.pdf").write_bytes(raw)
    # PyMuPDF instead of pdfplumber: pdfplumber's extract_text() does its own
    # from-scratch character-layout reconstruction and was measured at
    # ~23 sec/paper on this corpus's PDFs -- almost entirely spent on pages
    # we then throw away (this function's own early-stop check ran
    # extract_text() on every page up to and including the references
    # section, not just up to it, since the "stop" was never actually early).
    # PyMuPDF's get_text() is backed by MuPDF's C parser and is the standard
    # go-to for bulk text extraction where layout fidelity doesn't matter --
    # confirmed already installed locally (1.28.2) before switching.
    # Imported here rather than at the top so the match phase, which the
    # build runs every time, works on a machine without PyMuPDF.
    import pymupdf
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        pages_text = []
        started = False
        for page in pdf:
            text = page.get_text() or ""
            if not started and re.search(r"\bReferences\b", text):
                started = True
            if started:
                pages_text.append(text)
        if not pages_text and pdf.page_count:
            # No explicit "References" heading found (rare, some templates use
            # "Bibliography" or a different heading) -- fall back to the last
            # two pages, where the reference list almost always lives.
            pages_text = [pdf[i].get_text() or "" for i in range(max(0, pdf.page_count - 2), pdf.page_count)]
        return "\n".join(pages_text)


def split_reference_entries(text):
    # Reference lists are numbered, either "[12] ..." or "12. ..." at the
    # start of an entry -- split on that marker rather than on newlines,
    # since a single reference often wraps across several PDF text lines.
    parts = re.split(r"(?:^|\n)\s*\[?\d{1,3}\]?[.\)]\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def match_references(entries, index):
    """Corpus title keys cited by these reference entries. index is a
    CorpusMatchIndex (build_corpus_match_index)."""
    matched = set()
    by_prefix, words = index.by_prefix, index.words
    for raw_entry in entries:
        norm_entry = re.sub(r"[^a-z0-9]", "", raw_entry.lower())
        entry_words = significant_words(raw_entry)
        # The match criterion is the normalized title appearing verbatim as a
        # substring of the normalized reference text, plus at least one
        # distinctive word in common (the old candidate filter, kept so the
        # matches don't change).
        for i in range(len(norm_entry) - PREFIX_LEN + 1):
            for key in by_prefix.get(norm_entry[i:i + PREFIX_LEN], ()):
                if key not in matched and norm_entry.startswith(key, i) and not words[key].isdisjoint(entry_words):
                    matched.add(key)
        for w in entry_words:
            for key in index.short_by_word.get(w, ()):
                if key in norm_entry:
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


def fetch_phase(cvf_titles, pdf_url_index):
    refs = load_refs_cvf()
    succeeded = set(refs["succeeded"])
    # Most-cited-first, not corpus order (which clusters by venue/year) --
    # an interrupted or session-limited run should have already extracted
    # reference lists for the papers readers actually visit (feeds each
    # paper's own self-citation/CD-index numbers and its detail page),
    # not whichever ones happen to sort first (user-requested, applied to
    # every incremental crawler in this pipeline, not just this one; see
    # fetch_common.by_citations). cvf_titles already carries this order
    # from main() below -- filtered here, not re-sorted, since it's a list
    # of title keys with no citation count left to sort by.
    pending = [k for k in cvf_titles if k in pdf_url_index and k not in succeeded]
    n_retrying = sum(1 for k in pending if k in refs["failed"])
    print(f"{len(pending)} CVF papers left to fetch (of {len(cvf_titles)} CVF total, "
          f"AV and non-AV), including {n_retrying} retrying a previous failure", flush=True)

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
                text = fetch_pdf_text(url, key)
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


def match_phase(match_index):
    # Always runs, fetch or no fetch -- pure local computation over whatever
    # raw reference text has been saved so far by either source, against
    # whatever the corpus looks like right now. This is what lets a later
    # corpus expansion surface new edges for a paper scanned long ago,
    # without re-fetching anything.
    #
    # With no saved reference lists at all (a fresh clone, or a CI runner
    # that wasn't given them), rematching would overwrite a good graph with
    # an empty one and wipe every in-corpus count on the next build. Leave
    # the existing graph alone instead.
    if not REFS_CVF_FILE.exists() and not REFS_ARXIV_FILE.exists():
        print("Match phase skipped: no saved reference lists "
              f"({REFS_CVF_FILE.name}, {REFS_ARXIV_FILE.name}); keeping the existing graph.", flush=True)
        return False
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
        matches = sorted(match_references(entries, match_index) - {source})
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
    return True


def main():
    parser = argparse.ArgumentParser(description="Build the in-corpus citation graph.")
    parser.add_argument("--match-only", action="store_true",
                        help="Skip the PDF fetch phase and only rematch the saved reference lists "
                             "against the current corpus (offline).")
    args = parser.parse_args()

    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    if args.match_only:
        match_phase(build_corpus_match_index(papers))
        return
    # Whole corpus, not just av_relevance=="AV" -- user-requested: a paper's
    # citation count should reflect who cites it anywhere in this corpus,
    # not just its AV-relevant slice (see aggregate.py's citations_by_
    # source.in_corpus_all and DECISIONS.md's "Whole-corpus citations" entry
    # for the two-metric design this feeds). Most-cited-first still applies
    # (see fetch_common.by_citations) -- for a non-AV paper this naturally
    # prioritizes ones already known to be cited by AV papers (foundational
    # CV/robotics work our own corpus already points at) over an arbitrary
    # non-AV paper nothing here has any reason to care about yet.
    cvf_titles = [
        normalize_title(p["title"]) for p in by_citations(
            p for p in papers if (p.get("venue") or "") in CVF_VENUES
        )
    ]
    pdf_url_index = build_cvf_pdf_url_index()
    match_index = build_corpus_match_index(papers)

    fetch_phase(cvf_titles, pdf_url_index)
    match_phase(match_index)


if __name__ == "__main__":
    main()
