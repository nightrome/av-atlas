"""Papers that exist twice under different titles, mostly a preprint and its published version.

The list lives in data/duplicate_papers.json, a small file a person reviews and commits:
    [{"primary": "<published title>", "duplicate": "<preprint title>", "similarity": 0.99}, ...]
It is produced by find_duplicate_papers.py (which needs the embedding vectors, so it
cannot run in the monthly build); the build only reads the list.

Merging means: the duplicate record disappears from the corpus, its citations count
toward the primary (the citation graph's keys are folded), and the primary keeps a note of
where else it appeared (`also_published_as`: title, venue, year, url) so the old title
can still be shown and found.
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DUPLICATES_FILE = BASE / "data" / "duplicate_papers.json"


def plain_key(title):
    """The key space of data/citation_graph.json: lowercase letters and digits only."""
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def load_pairs(path=DUPLICATES_FILE):
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def alias_map(pairs):
    """{duplicate key: primary key} in the citation graph's key space."""
    aliases = {}
    for p in pairs:
        dup, primary = plain_key(p["duplicate"]), plain_key(p["primary"])
        if dup and primary and dup != primary:
            aliases[dup] = primary
    return aliases


def fold_graph(graph, aliases):
    """The citation graph with every duplicate key replaced by its primary's key,
    as citer and as cited. Edges that become identical collapse; self-edges are dropped."""
    if not aliases:
        return graph
    edges = {}
    for citer, cited in (graph.get("edges") or {}).items():
        citer = aliases.get(citer, citer)
        targets = {aliases.get(t, t) for t in cited}
        targets.discard(citer)
        edges.setdefault(citer, set()).update(targets)
    return {**graph, "edges": {k: sorted(v) for k, v in edges.items()}}


def merge_entries(entries, pairs):
    """Drops each duplicate record and notes it on its primary. Returns the kept entries.

    A pair is skipped when either side is missing from `entries` (e.g. after a title
    changed), so a stale list never breaks the build."""
    by_key = {plain_key(e.get("title")): e for e in entries}
    drop = set()
    for pair in pairs:
        primary = by_key.get(plain_key(pair["primary"]))
        dup = by_key.get(plain_key(pair["duplicate"]))
        if primary is None or dup is None or primary is dup or id(dup) in drop:
            continue
        note = {k: v for k, v in {
            "title": dup.get("title"), "venue": dup.get("venue"), "year": dup.get("year"),
            "url": dup.get("arxiv_url") or dup.get("source_url") or dup.get("doi"),
        }.items() if v}
        primary.setdefault("also_published_as", []).append(note)
        for field in ("abstract", "arxiv_url"):
            if not primary.get(field) and dup.get(field):
                primary[field] = dup[field]
        drop.add(id(dup))
    return [e for e in entries if id(e) not in drop]
