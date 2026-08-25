#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Folds fetch_affiliations_arxiv.py's side file (data/affiliations_arxiv.json)
into papers_full.json's authors_detail -- the same field enrich_core_authors.py
(OpenAlex) writes, so both sources feed the Institutions/Countries pages
uniformly. Only applied to papers that still have no authors_detail at all;
a paper OpenAlex already enriched keeps that richer data (OpenAlex includes
country codes directly, arXiv-sourced data doesn't).

Also folds arxiv_id (when found) into arxiv_url on EVERY matching paper,
independent of whether affiliations were found -- fetch_arxiv_links.py
reuses this same side file so a paper this script already resolved an
arXiv ID for never needs a second, separate arXiv search. has_code_link is
applied the same unconditional way, on every paper with a resolved
arxiv_id, regardless of whether affiliations were found OR whether the
paper already has richer OpenAlex-sourced authors_detail -- it isn't an
"affiliation richness" question the same way authors_detail is, so it
shouldn't be skipped just because a richer source got there first.

Two on-disk shapes coexist in affiliations_arxiv.json: older entries are a
plain author list (from before arxiv_id was tracked alongside them), newer
entries are {"authors": [...], "arxiv_id": ...}. Both are handled here
rather than migrating the file -- it's a derived cache, safe to be mixed,
and a migration would just be extra risk for no real benefit.

Country codes: ar5iv gives institution NAMES, not ISO codes the way OpenAlex
does. data/institution_countries.json is a small hand-curated map (built via
real web lookups, not guessed -- same standard as the Scholar profile
lookups, see DECISIONS.md) from institution name to country code. An
affiliation that isn't in the map still gets recorded (so it shows up on the
Institutions page) but contributes no country -- better than a wrong guess.

Like apply_citation_sources.py, this is the single writer for this data onto
papers_full.json -- fetch_affiliations_arxiv.py only ever writes its own
side file, so this is safe to run anytime, including while that script is
still running in the background.

Usage: python apply_affiliations_arxiv.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
ARXIV_AFFS_FILE = BASE / "data" / "affiliations_arxiv.json"
COUNTRY_MAP_FILE = BASE / "data" / "institution_countries.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    arxiv_affs = load_json(ARXIV_AFFS_FILE, {})
    country_map = load_json(COUNTRY_MAP_FILE, {})
    country_map = {k: v for k, v in country_map.items() if not k.startswith("_")}

    n_applied = 0
    n_linked = 0
    n_code_link = 0
    for p in papers:
        key = normalize_title(p.get("title"))
        entry = arxiv_affs.get(key)
        if entry is None:
            continue
        # Old shape: entry IS the author list. New shape: entry is
        # {"authors": [...], "arxiv_id": ..., "has_code_link": ...}.
        authors = entry if isinstance(entry, list) else entry.get("authors")
        arxiv_id = None if isinstance(entry, list) else entry.get("arxiv_id")
        has_code_link = None if isinstance(entry, list) else entry.get("has_code_link")

        if arxiv_id and not p.get("arxiv_url"):
            p["arxiv_url"] = f"https://arxiv.org/abs/{arxiv_id}"
            n_linked += 1

        # Unconditional -- see this script's own docstring for why
        # has_code_link isn't gated by authors_detail richness the way the
        # block below is. Only ever set when a page was actually checked
        # (has_code_link is None, not False, when arxiv_id never resolved).
        if has_code_link is not None and p.get("has_code_link") is None:
            p["has_code_link"] = has_code_link
            if has_code_link:
                n_code_link += 1

        if p.get("authors_detail") or not authors or not any(a.get("affiliations") for a in authors):
            continue  # OpenAlex data already there is richer -- don't overwrite it
        detail = []
        for a in authors:
            affs = a.get("affiliations") or []
            countries = sorted({country_map[aff] for aff in affs if aff in country_map})
            detail.append({"name": a["name"], "affiliations": affs, "countries": countries})
        p["authors_detail"] = detail
        p["authors_detail_source"] = "arxiv-html"
        n_applied += 1

    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Applied arXiv-sourced authors_detail to {n_applied} papers, arxiv_url to {n_linked} papers, "
          f"has_code_link=true to {n_code_link} papers")


if __name__ == "__main__":
    main()
