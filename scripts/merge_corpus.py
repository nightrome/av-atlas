#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merges the venue-listing pulls into one corpus:
  - av-atlas/data/venues/*.json (CVPR/ICCV/WACV/ECCV/NeurIPS/CoRL/ICRA/IROS/
    RSS/ICLR/AAAI, complete populations, no keyword filtering) plus
    arxiv*.json (fetch_arxiv.py -- NOT a complete population, an AV-specific
    keyword search against arXiv itself; see that script's docstring).
    arXiv entries are only ever used to fill a title the other venues don't
    already have -- see the venue_files/arxiv_files split below.

An earlier citation-crawl pilot (seeded from nuScenes/KITTI/Waymo via Google
Scholar's "Cited by" lists, data/seeds.json -> data/raw/*.json ->
enrich.py -> data/enriched.json) was deliberately never folded in here and
has since been removed entirely. It used a different, non-uniform sampling
method -- a paper's presence depended on whether it happened to cite one of
~100 seed dataset papers, not on having been published at one of the venues
everything else is drawn from -- which would have made "how was this paper
found" an invisible, unstated variable across the whole corpus.

Dedupes by normalized title within the venue pulls.

Classifies every paper (category + av_relevance) via classify.py's
keyword-taxonomy approach, applied uniformly to the whole corpus -- this
labels papers, it does not decide which papers are included.

Writes av-atlas/data/papers_full.json.

Usage: python merge_corpus.py
"""
import json
import re
from pathlib import Path

import classify as cl

BASE = Path(__file__).resolve().parent.parent
VENUES_DIR = BASE / "data" / "venues"
OUT_FILE = BASE / "data" / "papers_full.json"
CATEGORIES_FILE = BASE / "data" / "categories.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def discovery_source(filename):
    # A paper's "source" field records HOW it entered the corpus, not just
    # that it did -- lets a reader tell a venue's own official proceedings
    # listing apart from a discovery path with different reliability.
    #
    # A prior version of this also tagged an "arxiv_citing_discovery" path
    # (fetch_arxiv_citing.py, an abstract-mention text match -- arXiv itself
    # has no citation graph to search) -- removed, along with its output and
    # the script itself, once fetch_semanticscholar_citing.py's verified
    # citation edges made it both redundant and the weaker of the two.
    if filename.startswith("arxiv_s2_citing"):
        return "arxiv_s2_citing_discovery"  # verified citation edge (Semantic Scholar)
    if filename.startswith("arxiv"):
        return "arxiv_author_pull"
    return "venue_listing"


def main():
    taxonomy_full = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    taxonomy = taxonomy_full["categories"]
    known_dataset_titles = frozenset(cl.normalize_title(t) for t in taxonomy_full.get("known_dataset_papers", []))
    llm_core_titles = cl.load_llm_core_titles()

    # This script used to be safe to re-run at any time, but several other
    # scripts (enrich_core_authors.py, fetch_citations_openalex.py, the
    # in-corpus citation-graph builder) patch enrichment directly onto the
    # *output* of this script (papers_full.json) rather than onto
    # venues/*.json -- so a naive rebuild-from-sources silently discards that
    # enrichment (this actually happened once, for authors_detail). Fix:
    # carry forward any of these fields found on the previous papers_full.json,
    # keyed by normalized title, to matching records that don't already have
    # them from this rebuild. Self-healing reruns instead of a trap.
    CARRY_OVER_FIELDS = ("authors_detail", "citations_by_source", "arxiv_url", "abstract")
    prior_by_field = {field: {} for field in CARRY_OVER_FIELDS}
    if OUT_FILE.exists():
        try:
            prior_papers = json.loads(OUT_FILE.read_text(encoding="utf-8"))
            for p in prior_papers:
                key = normalize_title(p.get("title"))
                if not key:
                    continue
                for field in CARRY_OVER_FIELDS:
                    if p.get(field):
                        prior_by_field[field][key] = p[field]
        except Exception as e:
            print(f"  could not read prior {OUT_FILE.name} for carry-over: {e}")

    merged = {}  # normalized title -> record

    # arxiv*.json files are processed LAST, and only ever fill a gap, never
    # win a collision -- a paper that's both an arXiv preprint and published
    # at a real venue (the common case: most conference papers have an
    # arXiv version too) must keep the real venue name, not get relabeled
    # "arXiv preprint" just because that file happened to sort first
    # alphabetically. Without this, "arxiv2023.json" (a<c) would process
    # before "cvpr2023.json" and silently steal every dual-listed paper's
    # venue attribution under naive first-wins dedup.
    venue_files = sorted(f for f in VENUES_DIR.glob("*.json") if not f.name.startswith("arxiv"))
    arxiv_files = sorted(VENUES_DIR.glob("arxiv*.json"))

    for f in venue_files + arxiv_files:
        try:
            papers = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  skip {f.name}: {e}")
            continue
        source = discovery_source(f.name)
        is_arxiv_file = f.name.startswith("arxiv")
        for p in papers:
            key = normalize_title(p.get("title"))
            if not key:
                continue
            # arxiv*.json's own schema (fetch_arxiv.py, fetch_semanticscholar_
            # citing.py) predates arxiv_url existing as its own field and
            # puts the abstract URL in "doi" instead -- lift it into
            # arxiv_url here so every paper's arXiv link lives in one
            # consistent field regardless of which file it came from.
            arxiv_url = p.get("doi") if is_arxiv_file else None
            if key not in merged:
                merged[key] = {
                    "title": p.get("title"),
                    "authors": p.get("authors"),
                    "abstract": p.get("abstract"),
                    "venue": p.get("conference"),
                    "year": p.get("year"),
                    "citations": p.get("citations"),
                    "citations_updated": p.get("citations_updated"),
                    "doi": p.get("doi"),
                    "source": source,
                    "arxiv_url": arxiv_url,
                    # Exactly which page this paper's data was pulled from,
                    # when the fetcher recorded one (user-requested) -- most
                    # fetchers pull from one predictable per-venue URL
                    # pattern already documented in PIPELINE.md, but a
                    # community-maintained GitHub list (fetch_github_paper_
                    # lists.py) is a different repo per year/venue, so the
                    # exact source needs recording per paper, not just once
                    # in a docstring.
                    "source_url": p.get("source_url"),
                }
            elif arxiv_url and not merged[key].get("arxiv_url"):
                # A dual-listed paper: already merged from a real venue's
                # own listing (which wins for title/venue/etc, see the
                # ordering note above), but this arXiv-sourced record still
                # has a real arXiv link worth keeping, not discarding.
                merged[key]["arxiv_url"] = arxiv_url

    n_carried_over = 0
    for key, p in merged.items():
        for field in CARRY_OVER_FIELDS:
            if not p.get(field) and key in prior_by_field[field]:
                p[field] = prior_by_field[field][key]
                n_carried_over += 1

    papers = list(merged.values())
    for p in papers:
        category, relevance = cl.classify_paper(
            p.get("title", ""), p.get("abstract"), taxonomy, llm_core_titles, known_dataset_titles)
        p["category"] = category
        p["av_relevance"] = relevance

    OUT_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote {len(papers)} unique papers to {OUT_FILE}")
    n_core = sum(1 for p in papers if p["av_relevance"] == "core")
    print(f"  core={n_core} adjacent={len(papers) - n_core}")
    if n_carried_over:
        print(f"  carried over {n_carried_over} enrichment fields from the previous run")


if __name__ == "__main__":
    main()
