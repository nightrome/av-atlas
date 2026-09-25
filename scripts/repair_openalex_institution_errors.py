#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-off repair for a real, already-shipped data bug from OpenAlex itself
(not our own extraction code): OpenAlex's own per-work `authorships[].
institutions` field -- the exact field enrich_av_authors.py reads verbatim,
see openalex_authors() -- returns "TH Bingen University of Applied
Sciences" for several papers by the University of Tübingen / MPI-IS
Autonomous Vision group (Andreas Geiger, Kashyap Chitta, Bernhard Jaeger,
Aditya Prakash, Katrin Renz, Yiyi Liao, Zehao Yu -- KITTI-360, TransFuser,
DepthSplat, "End-to-End Autonomous Driving: Challenges and Frontiers").
Confirmed on real data: every single occurrence of this institution string
anywhere in the corpus belongs to one of these seven names -- likely
OpenAlex's own affiliation-to-ROR matching confusing "Tübingen"/"Tuebingen"
with the similarly-spelled but unrelated town of Bingen, since a small
teaching-focused Fachhochschule has no plausible reason to co-author seven
CV/robotics papers with the same well-documented Tübingen research group
and no one else. Discarded rather than replaced with a guessed correct
institution: we don't have reliable per-paper affiliation history for every
one of these authors at every one of these paper's publication years, and
an author left with no known institution on one paper is the same, honest,
already-handled case as any other under-enriched paper -- better than
asserting a specific one we can't actually verify.

Not folded into aggregate.py's INVALID_INSTITUTIONS: that set is for
extraction-artifact fragments ("Beijing", "and", a stray building name) that
were never a real institution to begin with and are filtered at display
time only, leaving the underlying authors_detail on disk untouched (see its
own comment). This is different -- a real, correctly-spelled institution
name that is simply the wrong one for these specific authors -- so, like
repair_garbled_authors_detail.py and repair_glued_institution_strings.py, it
belongs in papers_full.json itself, following DECISIONS.md's "By-hand data
corrections are scripts, not one-off edits".

Usage: python repair_openalex_institution_errors.py
"""
import json
from pathlib import Path

from atomic_write import write_json_atomic

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"

# Exact strings confirmed wrong on real data (see module docstring) --
# dropped unconditionally wherever found, not replaced.
WRONG_INSTITUTIONS = {
    "TH Bingen University of Applied Sciences",
}


def repair_affiliations(affs):
    """Pure function: given one author's affiliations list, drops any known-
    wrong entry. Returns (new_list, changed)."""
    if not affs or not any(a in WRONG_INSTITUTIONS for a in affs):
        return affs, False
    return [a for a in affs if a not in WRONG_INSTITUTIONS], True


def main():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    n_papers = 0
    n_authors = 0
    for p in papers:
        paper_changed = False
        for a in p.get("authors_detail") or []:
            new_affs, changed = repair_affiliations(a.get("affiliations") or [])
            if changed:
                a["affiliations"] = new_affs
                n_authors += 1
                paper_changed = True
        if paper_changed:
            n_papers += 1

    if n_papers:
        write_json_atomic(PAPERS_FILE, papers, indent=2)
    print(f"Repaired {n_authors} author-records across {n_papers} papers with a wrong OpenAlex institution")


if __name__ == "__main__":
    main()
