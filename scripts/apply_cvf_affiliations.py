#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Folds fetch_cvf_affiliations.py's side file (data/affiliations_cvf.json)
into papers_full.json's authors_detail, the same field OpenAlex and the
arXiv-HTML path write -- so all three sources feed the Institutions/
Countries pages uniformly. Only applied to papers that still have no
authors_detail at all; whichever source got there first (arXiv-HTML, being
more precise -- see fetch_cvf_affiliations.py's docstring -- normally runs
first) keeps its data.

Every author on the paper (from papers_full.json's own "authors" list) is
credited with the SAME full set of affiliations found on the PDF's page 1 --
this source doesn't have real per-author affiliation mapping the way
OpenAlex/arXiv-HTML do. countries is left empty here (same reasoning as
apply_affiliations_arxiv.py: PDF text gives institution NAMES, not ISO
codes) but reuses the same data/institution_countries.json map where an
extracted name happens to match one already curated there.

Stamps authors_detail_source="cvf-pdf" so this data's lower precision is
traceable on the paper itself, not just in this script's docstring.

Usage: python apply_cvf_affiliations.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
CVF_AFFS_FILE = BASE / "data" / "affiliations_cvf.json"
COUNTRY_MAP_FILE = BASE / "data" / "institution_countries.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    cvf_affs = load_json(CVF_AFFS_FILE, {}).get("affiliations", {})
    country_map = load_json(COUNTRY_MAP_FILE, {})
    country_map = {k: v for k, v in country_map.items() if not k.startswith("_")}

    n_applied = 0
    for p in papers:
        if p.get("authors_detail"):
            continue  # a more precise per-author source already filled this in
        key = normalize_title(p.get("title"))
        entry = cvf_affs.get(key)
        affs = (entry or {}).get("affiliations") or []
        authors = p.get("authors") or ""
        if not affs or not authors:
            continue
        countries = sorted({country_map[aff] for aff in affs if aff in country_map})
        # "authors" on papers_full.json is a single comma-separated STRING,
        # not a list -- `for name in authors` here iterated it character by
        # character (a real bug, shipped and caught on real data: 1107
        # papers' authors_detail ended up as one garbled entry per letter,
        # e.g. nuScenes' co-authors reduced to "H", "o", "l", "g", "e", "r",
        # ...). Split it into actual names first.
        author_names = [a.strip() for a in authors.split(",") if a.strip()]
        detail = [{"name": name, "affiliations": affs, "countries": countries} for name in author_names]
        p["authors_detail"] = detail
        p["authors_detail_source"] = "cvf-pdf"
        n_applied += 1

    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Applied CVF-PDF-sourced authors_detail to {n_applied} papers")


if __name__ == "__main__":
    main()
