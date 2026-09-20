#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Repairs venue names that Semantic Scholar's `venue` field got wrong for the
arXiv-discovered papers in data/venues/arxiv_s2_citing.json.

S2 sometimes resolves a venue acronym to an unrelated name (or truncates it),
e.g. "Machine-mediated learning" for the Springer journal Machine Learning,
"Conference on Algebraic Informatics" for the IEEE Conference on Artificial
Intelligence (CAI), "Most" for the IEEE MOST conference. Each RENAME below was
checked against the paper's own S2 journal name / DOI, not guessed.

Some short names could not be tied to any real venue (the same "Delta" or
"Make" is attached to arXiv-only papers, MDPI papers and book chapters
alike). Those are recorded as *missing*, not renamed and not left in place:

    conference    "arXiv preprint"  (all we actually know: it is on arXiv)
    venue_status  "missing"         (looked up; no usable venue exists)
    venue_raw     the discarded string, kept for auditing

"Missing" is deliberately different from "not yet parsed", which is an
"arXiv preprint" record with NO venue_status: nobody has looked up its venue
yet. backfill_citing_venues.py sets the same "missing" status when S2 itself
returns no venue, and skips records that already have a status.

Idempotent: a record already fixed no longer matches. Runs first in
build_public_site.py so a fresh S2 crawl can't bring the bad names back.

Usage: python fix_suspect_venues.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA_FILE = BASE / "data" / "venues" / "arxiv_s2_citing.json"

PLACEHOLDER_VENUE = "arXiv preprint"
STATUS_MISSING = "missing"

RENAMES = {
    "Machine-mediated learning": "Machine Learning",
    "Conference on Algebraic Informatics": "CAI",
    "Most": "MOST",
    "Eras": "ERAS",
    "Cyber ..": "CYBER",
    "International Congress of Mathematicans": "ICM",
    "Micro": "MICRO",
    "UI": "International Conference on Automotive User Interfaces and Interactive Vehicular Applications",
}

# Short names with no verifiable real venue behind them.
MISSING_VENUES = {"Delta", "Make", "Engineering", "Proceedings", "Inf.", "De Computis", "ROBOT"}


def fix_entry(entry):
    """Apply the repair to one record in place; True if it changed."""
    venue = entry.get("conference")
    if venue in RENAMES:
        entry["venue_raw"] = venue
        entry["conference"] = RENAMES[venue]
        return True
    if venue in MISSING_VENUES:
        entry["venue_raw"] = venue
        entry["conference"] = PLACEHOLDER_VENUE
        entry["venue_status"] = STATUS_MISSING
        return True
    return False


def main():
    entries = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    changed = sum(fix_entry(e) for e in entries)
    if changed:
        DATA_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"  fixed {changed} suspect venue names in {DATA_FILE.name}")


if __name__ == "__main__":
    main()
