#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Folds mine_abstracts.py's side file (data/abstracts_arxiv.json) into
papers_full.json's abstract/abstract_search_exhausted fields -- same
fetch/apply-with-side-file shape as fetch_affiliations_arxiv.py/
apply_affiliations_arxiv.py. Only fills gaps: a paper that already has an
abstract (from any source) or is already marked exhausted keeps that,
never overwritten.

Like apply_affiliations_arxiv.py, this is the single writer of these two
fields onto papers_full.json -- mine_abstracts.py only ever writes its own
side file, so this is safe to run anytime, including while that script is
still running in the background.

Usage: python apply_abstracts_arxiv.py
"""
import json
from pathlib import Path

import mine_abstracts as ma

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    cache = ma.load_cache()

    n_abstracts = 0
    n_exhausted = 0
    for p in papers:
        if p.get("abstract") or p.get("abstract_search_exhausted"):
            continue
        entry = cache.get(ma.normalize_title(p.get("title")))
        if not entry:
            continue
        if entry.get("abstract"):
            p["abstract"] = entry["abstract"]
            n_abstracts += 1
        elif entry.get("exhausted"):
            p["abstract_search_exhausted"] = True
            n_exhausted += 1

    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Applied {n_abstracts} abstracts and {n_exhausted} confirmed-no-match markers to papers_full.json")


if __name__ == "__main__":
    main()
