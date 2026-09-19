#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PROTOTYPE, not part of the normal pipeline: converts the corpus into a
queryable SQLite file, so the Authors page can pull "top 50 by citations"
straight out of the database (via sql.js-httpvfs's HTTP-range-request
virtual filesystem, see dev/authors_sql_prototype.html) instead of shipping
the full all_papers array to every visitor just to compute a leaderboard
that's mostly thrown away. Local-only spike, not wired into the site.

Deliberately does NOT import aggregate.py's own author/institution/country
extraction (those are private closures inside its main(), and refactoring
that well-tested production pipeline just to share a few lines with an
exploratory prototype isn't worth the risk) -- reimplements just the
author-name extraction this prototype actually needs, reusing the two
underlying pure helpers (clean_author_name/is_full_name) that ARE already
shared, top-level functions.

Usage: python av-atlas/scripts/build_sqlite.py
"""
import json
import sqlite3
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import aggregate as agg  # noqa: E402  (reuses clean_author_name/is_full_name/citation_count/is_fully_processed)

BASE = SCRIPTS_DIR.parent
IN_FILE = BASE / "data" / "papers_full.json"
OUT_FILE = BASE / "data" / "av_research_prototype.db"


def paper_authors(entry):
    details = entry.get("authors_detail")
    if details:
        names = [agg.clean_author_name(a["name"]) for a in details if a.get("name")]
    else:
        names = [agg.clean_author_name(a) for a in (entry.get("authors") or "").split(",") if a.strip()]
    return [n for n in names if agg.is_full_name(n)]


def main():
    entries = json.loads(IN_FILE.read_text(encoding="utf-8"))
    entries = [e for e in entries if agg.is_fully_processed(e)]
    print(f"{len(entries)} fully-processed papers")

    if OUT_FILE.exists():
        OUT_FILE.unlink()
    conn = sqlite3.connect(str(OUT_FILE))
    cur = conn.cursor()
    # No title/doi/arxiv_url here -- this prototype only serves the Authors
    # leaderboard query (group-by-author aggregate), which never needs a
    # per-paper title. Skipping them isn't just smaller: SQLite stores TEXT
    # uncompressed (unlike stats.json's gzip-in-transit), so ~230k full
    # titles would have dwarfed everything actually needed for this query.
    #
    # authors is a lookup table (id -> name), and paper_authors stores only
    # the small integer author_id -- interned, not the name repeated at
    # every one of that author's ~950k (paper, author) pairs. Chen Lv alone
    # has 104 papers; storing "Chen Lv" as text 104 times instead of once
    # plus 104 integers is exactly the kind of redundancy a real database
    # (unlike a flat JSON array) is supposed to eliminate.
    cur.executescript("""
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY,
            year INTEGER, venue TEXT, category TEXT,
            av_relevance TEXT, citations INTEGER
        );
        CREATE TABLE authors (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE
        );
        CREATE TABLE paper_authors (
            paper_id INTEGER REFERENCES papers(id),
            author_id INTEGER REFERENCES authors(id)
        );
        CREATE INDEX idx_papers_relevance ON papers(av_relevance);
        CREATE INDEX idx_papers_category ON papers(category);
        CREATE INDEX idx_papers_venue ON papers(venue);
        CREATE INDEX idx_papers_year ON papers(year);
        CREATE INDEX idx_authors_name ON authors(name);
        CREATE INDEX idx_pa_author ON paper_authors(author_id);
        CREATE INDEX idx_pa_paper ON paper_authors(paper_id);
    """)

    paper_rows = []
    author_id_by_name = {}
    pa_rows = []
    for i, e in enumerate(entries):
        paper_rows.append((
            i, e.get("year"), e.get("venue"), e.get("category"),
            e.get("av_relevance"), agg.citation_count(e),
        ))
        for name in paper_authors(e):
            author_id = author_id_by_name.get(name)
            if author_id is None:
                author_id = len(author_id_by_name)
                author_id_by_name[name] = author_id
            pa_rows.append((i, author_id))

    cur.executemany("INSERT INTO papers VALUES (?,?,?,?,?,?)", paper_rows)
    cur.executemany("INSERT INTO authors VALUES (?,?)", [(v, k) for k, v in author_id_by_name.items()])
    cur.executemany("INSERT INTO paper_authors VALUES (?,?)", pa_rows)

    # Precomputed per-(author, relevance) aggregate -- SQLite's query planner
    # has no good plan for "GROUP BY author, ORDER BY an unrelated sum,
    # HAVING a count" over a 3-way join (confirmed empirically: it drives the
    # scan from the largest table, authors, touching most of the file
    # regardless of indexes). That's fine for a narrow query (one author's
    # papers) but the Authors page's DEFAULT view -- no filters, just the
    # relevance toggle -- IS this exact expensive shape, and it's the most
    # common case by far. Materializing it turns the common case into a
    # single small indexed table scan; a category/venue/year filter still
    # falls back to the live join below, which is fine since those are both
    # rarer and much more selective.
    cur.execute("""
        CREATE TABLE author_stats (
            author_id INTEGER, av_relevance TEXT,
            papers INTEGER, citations INTEGER, cited_papers INTEGER
        )
    """)
    cur.execute("""
        INSERT INTO author_stats
        SELECT au.id, p.av_relevance,
               COUNT(DISTINCT pa.paper_id),
               COALESCE(SUM(p.citations), 0),
               SUM(CASE WHEN p.citations IS NOT NULL THEN 1 ELSE 0 END)
        FROM paper_authors pa
        JOIN papers p ON p.id = pa.paper_id
        JOIN authors au ON au.id = pa.author_id
        GROUP BY au.id, p.av_relevance
    """)
    # `papers` is part of the index (not just av_relevance/citations) so the
    # "how many total match" count query never has to fall back to the
    # table itself -- confirmed empirically this matters: the same query
    # without `papers` in the index touched ~15MB (scattered lookups back to
    # the table row for each of ~7k matching authors to check papers>N),
    # vs. this covering version answering entirely from the index.
    cur.execute("CREATE INDEX idx_author_stats ON author_stats(av_relevance, papers, citations DESC)")
    conn.commit()
    n_author_stats = cur.execute("SELECT COUNT(*) FROM author_stats").fetchone()[0]
    # SQLite pages default to 4096 bytes, matching sql.js-httpvfs's own
    # default requestChunkSize -- ANALYZE lets the query planner actually use
    # the indexes above instead of falling back to full scans, which matters
    # a lot here since a full scan means fetching every page of the file
    # over the network, defeating the entire point of this prototype.
    cur.execute("ANALYZE")
    conn.execute("VACUUM")
    conn.close()

    size_mb = OUT_FILE.stat().st_size / 1e6
    print(f"Wrote {OUT_FILE} ({size_mb:.1f} MB): {len(paper_rows)} papers, "
          f"{len(author_id_by_name)} distinct authors, {len(pa_rows)} paper-author rows, "
          f"{n_author_stats} author_stats rows")


if __name__ == "__main__":
    main()
