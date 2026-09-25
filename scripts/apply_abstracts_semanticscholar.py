#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Folds fetch_abstracts_semanticscholar.py's side file
(data/abstracts_semanticscholar.json) into papers_full.json's "abstract"
field. Only applied to papers that still have no abstract at all -- never
overwrites a real, already-sourced abstract (CVF/NeurIPS/arXiv/etc.) with
this fallback.

Usage: python apply_abstracts_semanticscholar.py
"""
import json
import re
from pathlib import Path

from atomic_write import write_json_atomic

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
ABSTRACTS_FILE = BASE / "data" / "abstracts_semanticscholar.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def fill_missing_abstract(paper, abstract, source=None):
    """Sets paper["abstract"] only if it has none yet; returns True if it did.
    Also used by fetch_crossref.py for its venue-file records."""
    if (paper.get("abstract") or "").strip() or not abstract:
        return False  # a real abstract already exists from a better source
    paper["abstract"] = abstract
    if source:
        paper["abstract_source"] = source
    return True


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    abstracts = load_json(ABSTRACTS_FILE, {}).get("abstracts", {})

    n_applied = 0
    for p in papers:
        if fill_missing_abstract(p, abstracts.get(normalize_title(p.get("title"))), "semanticscholar"):
            n_applied += 1

    write_json_atomic(PAPERS_FILE, papers, indent=2)
    print(f"Applied Semantic-Scholar-sourced abstracts to {n_applied} papers")


if __name__ == "__main__":
    main()
