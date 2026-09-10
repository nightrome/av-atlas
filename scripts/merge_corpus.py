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

# Every entry in a per-venue-year file (e.g. cvpr2024.json) repeated the same
# "conference"/"year" values 265k times over -- 11.1MB of pure redundancy
# across data/venues/*.json, already fully determined by the filename
# (repo-size cleanup, user-requested; see DECISIONS.md). scripts/strip_
# redundant_venue_fields.py removes them from the tracked files; this dict
# (derived from the real, once-verified 1:1 filename-prefix -> conference
# mapping, not guessed) is how they're reconstructed on read.
VENUE_PREFIX_TO_CONFERENCE = {
    "aaai": "AAAI", "accv": "ACCV", "bmvc": "BMVC", "corl": "CoRL", "cvpr": "CVPR",
    "eccv": "ECCV", "gcpr": "GCPR", "iccv": "ICCV", "iclr": "ICLR", "icml": "ICML",
    "icra": "ICRA", "ijcv": "IJCV", "ijrr": "IJRR", "iros": "IROS", "itsc": "ITSC",
    "iv": "IV", "neurips": "NeurIPS", "ral": "RA-L", "rss": "RSS", "tits": "T-ITS",
    "tpami": "TPAMI", "tro": "T-RO", "wacv": "WACV",
}


def conference_and_year_for_file(filename):
    """(conference, year) implied by a venue filename, or (None, None) for a
    prefix this table doesn't recognize (e.g. an arxiv*.json file, which
    keeps its own real per-entry conference/year and is never looked up
    here -- see the venue_files/arxiv_files split below). Only a fallback:
    callers still prefer a per-entry "conference"/"year" field when the
    source (currently only arxiv_s2_citing.json, and any not-yet-migrated
    venue file) actually carries one -- see the "or" in the record-
    construction loop below. year is None for a "_all" journal file (e.g.
    ijcv_all.json -- continuous publication, not one proceedings per file,
    so the real year still has to come from each entry, not the filename)."""
    stem = filename[:-5] if filename.endswith(".json") else filename
    m = re.match(r"^([a-z]+)", stem)
    conference = VENUE_PREFIX_TO_CONFERENCE.get(m.group(1)) if m else None
    year_match = re.search(r"(\d{4})", stem)
    year = int(year_match.group(1)) if year_match else None
    return conference, year


# A community paper-list sometimes stores a title as a raw markdown link,
# "[Real Title](https://arxiv.org/abs/....)" (confirmed: ~600 IROS 2024
# entries via fetch_github_paper_lists.py) -- or wraps the whole title in
# literal quotes (several ECCV listings). Either leaves the paper un-deduped
# against its clean copy from another source, and shows the raw markup on
# the site. clean_title() strips both; it feeds the STORED display title as
# well as normalize_title() below.
_MARKDOWN_LINK_TITLE_RE = re.compile(r"^\s*\[([^\]]+)\]\((?:https?|ftp)://[^)]*\)\s*$")
_SUPERSCRIPT_DIGITS = str.maketrans(
    "¹²³⁴⁵⁶⁷⁸⁹⁰"
    "₀₁₂₃₄₅₆₇₈₉",
    "12345678900123456789")
# A decorative emoji dropped into a title ("🏘️ ProcTHOR: ...", "PooDLe🐩:",
# "⚡FLARES⚡: ...") -- always author whimsy, never part of the name, and it
# left the paper un-deduped against its plain copy.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿️]")
# LaTeX math glue plus any whitespace it sat next to, so "R $^2$ Former" ->
# "R2Former" and "VIENA ^2 :" -> "VIENA2:".
_TITLE_MATH_RE = re.compile(r"\s*[\^\$\{\}\\]+\s*")
# A trailing plural "s" on the flattened key -- the citing-paper metadata
# routinely drops or adds it on the last word ("... Multi-View Image[s]",
# "... Spiking Neural Network[s]"). Folded so singular/plural of one paper
# collapse; "...ss" (address, progress) is left alone.
_TRAILING_PLURAL_S_RE = re.compile(r"(?<=[a-z]{4})s$")


def clean_title(t):
    t = (t or "").strip()
    m = _MARKDOWN_LINK_TITLE_RE.match(t)
    if m:
        t = m.group(1).strip()
    for q in ('"', "'", "“", "”"):
        t = t.strip(q).strip()
    return re.sub(r"\s{2,}", " ", t).strip()


# A leading "ACRONYM: " (a single, space-free token, then a colon or -- as a
# GitHub-list rendering artifact -- a spaced dash) is usually a paper's own
# coined short-name for itself, not part of what makes the paper distinct
# from its other listings: the same paper is routinely titled with the
# prefix by one source and without it by another (confirmed on real data:
# ECCV's own listing of "Generative End-to-End Autonomous Driving" carries
# no "GenAD:", a citation-graph-discovered copy does). Stripped only when
# the prefix is a single word, so a descriptive lead-in ("Learning to
# Drive: A Survey") is untouched -- a multi-word phrase is far likelier to
# coincidentally share a generic remainder with an unrelated paper than a
# coined name is, and two DIFFERENT papers reusing the same acronym stay
# distinct here regardless (their remainders still differ). The dash form
# ("MVX-Net - Multimodal VoxelNet ...", ICRA's GitHub lists, vs everyone
# else's "MVX-Net: ...") is only accepted for a clearly coined leading
# token (internal capital / digit / separator, not "2D"/"3D") AND a
# substantial remainder, so "2D - X" / "3D - X" can't collapse together.
_ACRONYM_PREFIX_RE = re.compile(
    r"^\s*(?P<w>[A-Za-z0-9][A-Za-z0-9+._-]{1,19})\s*"
    r"(?P<sep>:|(?<=\s)[\-–—])\s*(?=\S)")


def _acronym_prefix_ok(w, sep, rest):
    if sep == ":":
        return True
    coined = not re.fullmatch(r"\d+[Dd]", w) and any(
        c.isupper() or c.isdigit() or c in "-+_" for c in w[1:])
    return coined and len(re.sub(r"[^a-z0-9]", "", rest.lower())) >= 25


def normalize_title(t):
    t = _EMOJI_RE.sub("", clean_title(t).translate(_SUPERSCRIPT_DIGITS))
    t = _TITLE_MATH_RE.sub("", t)
    t = re.sub(r"\s+:", ":", t)  # "FG2 :" -> "FG2:"
    t = re.sub(r"^[^0-9A-Za-z(\"']+", "", t).strip()  # leading symbol/marker junk
    m = _ACRONYM_PREFIX_RE.match(t)
    if m and _acronym_prefix_ok(m.group("w"), m.group("sep"), t[m.end():]):
        t = t[m.end():]
    key = re.sub(r"[^a-z0-9]", "", t.lower())
    return key if key.endswith("ss") else _TRAILING_PLURAL_S_RE.sub("", key)


def discovery_source(filename):
    # A paper's "source" field records HOW it entered the corpus, not just
    # that it did -- lets a reader tell a venue's own official proceedings
    # listing apart from a discovery path with different reliability.
    if filename.startswith("arxiv_s2_citing"):
        return "arxiv_s2_citing_discovery"  # verified citation edge (Semantic Scholar)
    if filename.startswith("arxiv"):
        return "arxiv_author_pull"
    return "venue_listing"


def main():
    taxonomy_full = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    taxonomy = taxonomy_full["categories"]
    # Lowercased once here rather than inside classify_paper's per-category
    # rank() -- that used to rebuild this same list from scratch for every
    # paper (rank() runs once per category per paper), ~466 keywords x
    # 235k+ papers of pure repeated .lower() work for a value that never
    # changes across the run. Confirmed via profiling as a meaningful slice
    # of merge_corpus.py's ~6-minute runtime.
    for cat in taxonomy:
        cat["keywords"] = [k.lower() for k in cat["keywords"]]
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
    # authors_detail_source travels with authors_detail (it records which of
    # the three enrichment scripts -- openalex/arxiv/cvf-pdf -- produced it)
    # but was missing from this list, so it got silently dropped on every
    # rebuild even though authors_detail itself survived: confirmed on real
    # data, every paper with authors_detail currently shows
    # authors_detail_source=None. Once dropped there's no way to reconstruct
    # which source a given paper's data came from after the fact -- fixing
    # the carry-over only stops new data from losing it going forward.
    CARRY_OVER_FIELDS = ("authors_detail", "authors_detail_source", "citations_by_source",
                         "arxiv_url", "abstract", "abstract_search_exhausted", "has_code_link")
    prior_by_field = {field: {} for field in CARRY_OVER_FIELDS}
    if OUT_FILE.exists():
        try:
            prior_papers = json.loads(OUT_FILE.read_text(encoding="utf-8"))
            for p in prior_papers:
                key = normalize_title(p.get("title"))
                if not key:
                    continue
                for field in CARRY_OVER_FIELDS:
                    # "field in p and not None", not the truthy check this
                    # used to be -- has_code_link is a real 3-state field
                    # (True/False/never-checked), and a bare truthy check
                    # would silently drop every confirmed-False value (a
                    # paper actually checked and found to have no code
                    # link), making it indistinguishable from "never
                    # checked" on the very next rebuild.
                    if field in p and p[field] is not None:
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
        file_conference, file_year = conference_and_year_for_file(f.name)
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
                    "title": clean_title(p.get("title")),
                    "authors": p.get("authors"),
                    "abstract": p.get("abstract"),
                    "venue": p.get("conference") or file_conference,
                    "year": p.get("year") or file_year,
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
