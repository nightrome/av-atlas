#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-off repair for a real, already-shipped data bug: apply_cvf_affiliations.py
used to do `for name in authors` where `authors` was papers_full.json's raw
comma-separated STRING field, not a list -- iterating it character by
character. 1107 papers ended up with authors_detail like
[{"name": "A", ...}, {"name": "l", ...}, {"name": "i", ...}, ...] instead of
one entry per real author (confirmed on nuScenes: reduced to single letters,
which zeroed out its author list everywhere downstream -- institution
credit, self-citation detection, and real authors showing "no affiliation"
on the Researchers page). The root cause is fixed in apply_cvf_affiliations.py; this
repairs the data already written to disk, which that script won't touch
again (it skips any paper that already has authors_detail).

Detection: every authors_detail entry's name is <=2 characters after
stripping. Repair: since this source credits every author with the SAME
affiliations list (apply_cvf_affiliations.py's whole design -- no per-author
mapping from a PDF's page 1), the (correct, never-garbled) affiliations/
countries from the first entry are recovered and re-applied to one entry per
real author, split from the paper's own "authors" string.

Usage: python repair_garbled_authors_detail.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"


def is_garbled(detail):
    return bool(detail) and all(len((a.get("name") or "").strip()) <= 2 for a in detail)


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    n_repaired = 0
    for p in papers:
        detail = p.get("authors_detail")
        if not is_garbled(detail):
            continue
        affs = detail[0].get("affiliations") or []
        countries = detail[0].get("countries") or []
        author_names = [a.strip() for a in (p.get("authors") or "").split(",") if a.strip()]
        if not author_names:
            continue
        p["authors_detail"] = [
            {"name": name, "affiliations": affs, "countries": countries} for name in author_names
        ]
        n_repaired += 1

    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Repaired {n_repaired} papers' garbled authors_detail")


if __name__ == "__main__":
    main()
