#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Folds av-atlas/data/citation_graph.json (written by build_citation_graph.py,
in-corpus counts derived from its edges) into papers_full.json's
citations_by_source field.

This is the ONLY script that writes citation data onto papers_full.json --
build_citation_graph.py deliberately never touches it directly (see its
docstring) so it can run concurrently without racing this script over the
same multi-MB file; this script does the single-writer merge, and is safe to
run anytime, including while the backfill is still running in the
background -- it only reads the side file, never blocks on it finishing.

Citations used to also merge in an OpenAlex-sourced count
(data/citations_openalex.json, via the now-deleted fetch_citations_openalex.py)
but that data was never actually shown anywhere -- the UI has only ever read
citations_by_source.in_corpus since the citation-source picker was removed
(see DECISIONS.md). Dropped rather than kept as unused dead weight.

Usage: python apply_citation_sources.py
"""
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
GRAPH_FILE = BASE / "data" / "citation_graph.json"
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def in_corpus_counts(graph):
    # Self-citations (citer and cited paper share at least one author) count
    # toward the total here -- they used to be excluded outright, but with a
    # separate per-author Self-citation % metric now shown alongside every
    # citation count (see aggregate.py's self_citations field), there's no
    # need to also suppress them from the total; a reader can see both the
    # full count and how much of it is self-citation, rather than only ever
    # seeing a total with self-citations silently removed (user-requested).
    incoming = defaultdict(int)
    for citer_key, targets in graph.get("edges", {}).items():
        for t in targets:
            incoming[t] += 1
    return incoming


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    graph = load_json(GRAPH_FILE, {"edges": {}})
    incoming = in_corpus_counts(graph)

    n_in_corpus = 0
    for p in papers:
        key = normalize_title(p.get("title"))
        by_source = p.setdefault("citations_by_source", {})

        count = incoming.get(key)
        if count:
            entry = {"count": count, "updated": graph.get("generated_at") or TODAY}
            if by_source.get("in_corpus") != entry:
                by_source["in_corpus"] = entry
                n_in_corpus += 1

        if not by_source:
            del p["citations_by_source"]

    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Applied {n_in_corpus} in-corpus counts to {PAPERS_FILE}")


if __name__ == "__main__":
    main()
