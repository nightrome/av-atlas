#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
One-off repair for a real, already-shipped data bug: some raw affiliation
text from arXiv/ar5iv has no delimiter at all between two or more
institutions -- not a comma-split failure (nothing to split on), a genuinely
run-on string, e.g. "UC Berkeley Stanford UCL Virginia Tech Nvidia" as ONE
affiliations[] entry. normalize_institution() has no way to tell this apart
from a real (if unusual) institution name, so it survived as-is and showed
up as one nonsense "institution" wherever affiliations are displayed --
user-flagged: Boyi Li's and Marco Pavone's author pages both carried this
exact string from their shared "AccidentBench" paper.

Found by scanning data/institution_registry.json for entries containing 2+
well-known standalone institution/company names with no separator between
them, then re-extracting each one through institution_extraction_llm.py
(the same registry-anchored LLM extractor built earlier this session) --
verified it correctly splits a run-on string into separate institutions
even with zero punctuation to key off. Two of the fourteen didn't split
cleanly on the first pass ("Alibaba Group Baidu Research" -- the model
returned it unchanged; "Human Interactive Driving Toyota Research Institute
Cambridge" -- a lab-name prefix glued to a real institute, not two
institutions) and were resolved by hand; GLUED_INSTITUTION_SPLITS below is
the final, human-reviewed table, not raw LLM output.

Root cause is NOT separately fixed anywhere -- this exact shape of run-on
text could recur on a future re-fetch of the same or a similar paper.
Re-run this script (idempotent -- a paper with no glued string left is a
no-op) after any bulk re-crawl of arXiv affiliation data, the same way
repair_garbled_authors_detail.py is meant to be re-run after that source's
own bug class resurfaces.

Usage: python repair_glued_institution_strings.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
REGISTRY_FILE = BASE / "data" / "institution_registry.json"

# {glued raw string: [split, clean, institution, names]} -- every value was
# either matched_existing=True against the registry by institution_extraction_
# llm.py, or (the two exceptions noted above) chosen by hand against the
# same registry. Keys are exact strings as they appeared in a real
# affiliations[] list -- an exact-match table, not a pattern, on purpose:
# guessing at a general "split on capital letters" rule would mangle real
# multi-word institution names (see aggregate.py's own LEADING_FOOTNOTE_
# NUMBER_RE comment for a past example of exactly that kind of regression).
GLUED_INSTITUTION_SPLITS = {
    "Alibaba Group Baidu Research": ["Alibaba Group", "Baidu Research"],
    "Cornell University. Work done during internship at Huawei Technologies Canada": [
        "Cornell University", "Huawei Technologies Canada",
    ],
    "ETH Zurich TU Munich Technion NVIDIA": ["ETH Zurich", "TU Munich", "Technion", "NVIDIA"],
    "Georgia Institute of Technology Amazon AWS Toyota Research Institute Amazon Style": [
        "Georgia Institute of Technology", "Amazon AWS", "Toyota Research Institute", "Amazon",
    ],
    "Google Research Waymo LLC Robotics at Google Google Research": ["Google Research", "Waymo LLC"],
    "Human Interactive Driving Toyota Research Institute Cambridge": ["Toyota Research Institute"],
    "NVIDIA Research University of Wisconsin -- Madison Stanford": [
        "NVIDIA Research", "University of Wisconsin -- Madison", "Stanford",
    ],
    "NVIDIA UCLA Stanford University": ["NVIDIA", "UCLA", "Stanford University"],
    "Purdue University UC Berkeley Toyota InfoTech Labs": [
        "Purdue University", "UC Berkeley", "Toyota InfoTech Labs",
    ],
    "Tsinghua University Huawei Noah’s Ark": ["Tsinghua University", "Huawei Noah’s Ark"],
    "UC Berkeley MIT UT Austin": ["UC Berkeley", "MIT", "UT Austin"],
    "UC Berkeley Stanford UCL Virginia Tech Nvidia": [
        "UC Berkeley", "Stanford", "UCL", "Virginia Tech", "Nvidia",
    ],
    "UCLA UW-Madison NCSU Purdue University UC Berkeley UT Austin": [
        "UCLA", "UW-Madison", "NCSU", "Purdue University", "UC Berkeley", "UT Austin",
    ],
    "University of Cambridge UIUC EPFL": ["University of Cambridge", "UIUC", "EPFL"],
}


def repair_affiliations(affs):
    """Pure function: given one author's affiliations list, splices in the
    split form of any glued string found. Returns (new_list, changed) --
    de-duplicated (dict.fromkeys, order-preserving) since splicing two
    glued strings that share an institution shouldn't double it."""
    if not affs or not any(a in GLUED_INSTITUTION_SPLITS for a in affs):
        return affs, False
    new_affs = []
    for aff in affs:
        new_affs.extend(GLUED_INSTITUTION_SPLITS.get(aff, [aff]))
    return list(dict.fromkeys(new_affs)), True


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
        PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Repaired {n_authors} author-records across {n_papers} papers with a glued institution string")

    # The now-obsolete glued entries shouldn't linger in the registry --
    # institution_extraction_llm.py would otherwise keep offering them as a
    # shortlist candidate on a future extraction call. Removes only entries
    # this table knows about; safe to re-run even if the registry has
    # already been cleaned (a no-op set difference).
    if REGISTRY_FILE.exists():
        registry = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
        before = len(registry.get("institutions", []))
        registry["institutions"] = [
            n for n in registry.get("institutions", []) if n not in GLUED_INSTITUTION_SPLITS
        ]
        removed = before - len(registry["institutions"])
        if removed:
            REGISTRY_FILE.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"Removed {removed} obsolete glued entries from institution_registry.json")


if __name__ == "__main__":
    main()
