#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extracts author affiliations from page 1 of CVF-hosted PDFs (CVPR/ICCV/WACV
-- predictable URL, no rate limit), as a free alternative to OpenAlex for
core papers that don't have an arXiv preprint (fetch_affiliations_arxiv.py
already covers the ones that do -- see that script's docstring for why
arXiv's ar5iv HTML is preferred over raw PDF text when both exist: cleaner
markup, no PDF-layout noise).

Unlike ar5iv's HTML, a CVF PDF's author/affiliation block has no structural
markup at all -- no per-author tags, no linkage from a name to its
affiliation footnote/superscript once the text is flattened by pdfplumber.
Reliably parsing "author N works at affiliation M" from that would need
font/position data this script doesn't attempt to use. Instead: extract
every institution-shaped phrase from the block between the title and the
"Abstract" heading, and credit ALL of the paper's already-known authors
(from papers_full.json's "authors" list, not re-parsed from the PDF) with
the FULL set found. This is coarser than OpenAlex/arXiv's real per-author
mapping -- a 3-institution paper's first author "gains" institutions they
may not actually be at -- but it's still correct at the PAPER level (this
paper really does involve these institutions), which is what the
Institutions/Countries pages aggregate on. Documented here so nobody mistakes
this for the same precision as the other two sources.

Writes its own side file, data/affiliations_cvf.json:
  {normalizedTitle: {"affiliations": [...]}}
in the same succeeded/failed/side-file pattern as fetch_affiliations_arxiv.py
-- never touches papers_full.json directly; see apply_cvf_affiliations.py for
the single-writer step that folds this in (stamps
authors_detail_source="cvf-pdf").

Usage: python fetch_cvf_affiliations.py
"""
import io
import json
import random
import re
import time
import urllib.error
from pathlib import Path

import pdfplumber

from build_citation_graph import (
    CVF_VENUES, build_cvf_pdf_url_index, fetch_with_retries, normalize_title,
)

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
OUT_FILE = BASE / "data" / "affiliations_cvf.json"
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 15

# Phrases that mark a line/segment as an institution rather than an author
# name, a footnote, or boilerplate ("Equal contribution", "Work done at...").
# Deliberately broad (over-matching a stray sentence is harmless -- it just
# doesn't get deduped away) rather than narrow (missing a real institution
# because its name doesn't contain "University").
INSTITUTION_HINTS = re.compile(
    r"\b(University|Institute|College|Laborator(?:y|ies)|Lab\b|Center|Centre|"
    r"Corp(?:oration)?\b|Inc\.?\b|Ltd\.?\b|GmbH|LLC|Academy|School of|"
    r"Technolog(?:y|ies)|Research\b|Company|Foundation|CNRS|Fraunhofer|"
    r"Zentrum|Institut\b)",
    re.I,
)
EMAIL_DOMAIN_RE = re.compile(r"@[\w.-]+\.\w+")
LEADING_MARKER_RE = re.compile(r"^\s*[\d*†‡§¶,{}\s]{1,4}")
# Multiple affiliations on one line are often joined by a superscript index
# glued directly onto the next institution's name with no space or comma
# (e.g. "University of X 2Nvidia Corporation", "1Institute for Y" at the very
# start) -- a plain comma-split misses these entirely. Insert a split point
# before any digit that's immediately followed by an uppercase letter, which
# is what a flattened superscript index looks like once spacing collapses.
SUPERSCRIPT_SPLIT_RE = re.compile(r"(?<=[a-zA-Z])\s*\d+\s*(?=[A-Z])")
TRAILING_JUNK_RE = re.compile(r"[{}*†‡§¶]+$")


def normalize_line(line):
    line = EMAIL_DOMAIN_RE.sub("", line)
    line = LEADING_MARKER_RE.sub("", line).strip(" ,;")
    return line


def extract_affiliations(page1_text):
    # The author/affiliation block sits between the title (first ~1-3 lines)
    # and the "Abstract" heading -- skip the first line (title) and stop at
    # Abstract, rather than trying to detect where authors end and
    # affiliations begin (that boundary isn't consistent across templates).
    idx = re.search(r"\bAbstract\b", page1_text)
    block = page1_text[:idx.start()] if idx else page1_text[:800]
    lines = [normalize_line(l) for l in block.split("\n")]
    lines = lines[1:]  # drop the title line

    found = []
    seen = set()
    for line in lines:
        if not line or len(line) > 160:
            continue
        if not INSTITUTION_HINTS.search(line):
            continue
        # A line can list several affiliations separated by commas/semicolons
        # ("MIT, Stanford University") or by a glued-on superscript index
        # ("University of X 2Nvidia Corporation") -- split on both, but only
        # keep segments that themselves look institution-shaped, so a stray
        # "MIT" fragment next to an unrelated clause doesn't get split into
        # two bad entries.
        line = SUPERSCRIPT_SPLIT_RE.sub(",", line)
        for segment in re.split(r"[,;]", line):
            segment = LEADING_MARKER_RE.sub("", segment).strip()
            segment = TRAILING_JUNK_RE.sub("", segment).strip()
            if 4 < len(segment) <= 90 and INSTITUTION_HINTS.search(segment):
                key = segment.lower()
                if key not in seen:
                    seen.add(key)
                    found.append(segment)
    return found[:8]  # cap -- beyond this it's almost always PDF-parsing noise, not real affiliations


def fetch_page1_text(url):
    raw = fetch_with_retries(url)
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        if not pdf.pages:
            return ""
        # pdfplumber's default x_tolerance=3 merges adjacent words with no
        # space in many CVF papers' two-column author blocks (confirmed on
        # real PDFs: "University of California at Merced" -> "Universityof
        # CaliforniaatMerced"), which breaks every institution-keyword match
        # downstream. x_tolerance=1 preserves real word boundaries -- also
        # confirmed against the same PDFs, not a guess.
        return pdf.pages[0].extract_text(x_tolerance=1) or ""


def load_out():
    data = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    data.setdefault("succeeded", [])
    data.setdefault("failed", {})
    data.setdefault("affiliations", {})
    return data


def save_out(data):
    OUT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    pending_keys = {
        normalize_title(p["title"]) for p in papers
        if p.get("av_relevance") == "core" and (p.get("venue") or "") in CVF_VENUES
        and not p.get("authors_detail")
    }
    pdf_url_index = build_cvf_pdf_url_index()

    data = load_out()
    succeeded = set(data["succeeded"])
    pending = [k for k in pending_keys if k in pdf_url_index and k not in succeeded]
    # Shuffled, not corpus order -- see build_citation_graph.py's matching
    # fix for why (user-requested, applied to every incremental crawler).
    random.shuffle(pending)
    print(f"{len(pending)} core CVF papers left to check for affiliations "
          f"(of {len(pending_keys)} core CVF papers still missing authors_detail)", flush=True)

    processed = 0
    got_affiliations = 0
    consecutive_failures = 0
    # Same fix as build_citation_graph.py's identical loop shape -- without
    # this, a paper that fails once (a permanent 404 in particular, which
    # will never succeed on retry) gets re-selected into every subsequent
    # batch for the rest of THIS run, wasting most of the run re-hammering
    # the same already-known-dead URLs instead of reaching new papers.
    attempted_this_run = set()

    while pending:
        data = load_out()
        succeeded = set(data["succeeded"])
        batch = [k for k in pending if k not in succeeded and k not in attempted_this_run][:BATCH_SIZE]
        if not batch:
            break

        for key in batch:
            attempted_this_run.add(key)
            url = pdf_url_index[key]
            try:
                text = fetch_page1_text(url)
                affs = extract_affiliations(text)
                if affs:
                    data["affiliations"][key] = {"affiliations": affs}
                    got_affiliations += 1
                data["succeeded"].append(key)
                data["failed"].pop(key, None)
                consecutive_failures = 0
            except Exception as e:
                print(f"  failed on {url}: {e}", flush=True)
                # A 404 is permanent and won't count toward consecutive_failures
                # -- it's a conclusive, expected outcome, not evidence this
                # script or the connection is broken (see build_citation_graph.py).
                permanent = isinstance(e, urllib.error.HTTPError) and e.code == 404
                data["failed"][key] = {"error": str(e), "permanent": permanent}
                consecutive_failures = 0 if permanent else consecutive_failures + 1
            processed += 1
            time.sleep(0.3)

        save_out(data)
        print(f"  [{processed}/{len(pending)}] checked ({got_affiliations} with affiliations found, "
              f"{len(data['failed'])} currently failing)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive PDF fetch/parse failures. "
                  f"Failed papers retry automatically next run.", flush=True)
            return

    print(f"\nDone: {got_affiliations} papers got affiliation data this run.", flush=True)


if __name__ == "__main__":
    main()
