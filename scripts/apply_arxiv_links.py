#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Folds fetch_arxiv_links.py's side file (data/arxiv_ids.json) into
papers_full.json's arxiv_url field -- the single writer for it, same
pattern as apply_citation_sources.py / apply_affiliations_arxiv.py. Safe to
run anytime, including while fetch_arxiv_links.py is still running in the
background (only reads its side file, never blocks on it finishing).

Never overwrites an arxiv_url a paper already has (from
apply_affiliations_arxiv.py, or a previous run of this script).

Usage: python apply_arxiv_links.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
IDS_FILE = BASE / "data" / "arxiv_ids.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    ids = json.loads(IDS_FILE.read_text(encoding="utf-8")) if IDS_FILE.exists() else {}

    n_applied = 0
    for p in papers:
        if p.get("arxiv_url"):
            continue
        arxiv_id = ids.get(normalize_title(p.get("title")))
        if not arxiv_id:
            continue
        p["arxiv_url"] = f"https://arxiv.org/abs/{arxiv_id}"
        n_applied += 1

    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Applied arxiv_url to {n_applied} papers")


if __name__ == "__main__":
    main()
