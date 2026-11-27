#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds data/author_name_merges.json (read by aggregate.py) and a report of
what was merged and what was left alone.

Reads data/papers_full.json (the AV papers only, the same ones aggregate.py
uses), collects per name the OpenAlex ids, ORCIDs, co-authors and
institution years, and asks author_name_variants.find_merges which spellings
belong together. Names listed as shared or ambiguous are never merged:
data/author_identities.json, the "ambiguous" Scholar profiles, "Yue Wang" and,
when data/stats.json exists, identity_conflicted_authors.

Run it after merge_corpus.py and before aggregate.py when the corpus has
changed a lot; the committed JSON is used as is otherwise.

Usage: py -3 scripts/merge_author_name_variants.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import aggregate  # noqa: E402
import author_name_variants as anv  # noqa: E402

BASE = SCRIPTS_DIR.parent
OUT_FILE = BASE / "data" / "author_name_merges.json"
REPORT_FILE = BASE / "data" / "author_name_merges_report.md"
N_EXAMPLES = 30
N_NEAR_MISSES = 60


def paper_names(e):
    """Same author list aggregate.paper_authors builds, but without the
    merge step (we are deciding that merge)."""
    details = e.get("authors_detail")
    if details:
        raw = [a["name"] for a in details if a.get("name")]
    else:
        raw = [a for a in (e.get("authors") or "").split(",") if a.strip()]
    names = []
    for r in raw:
        for part in aggregate.split_joined_authors(r):
            c = aggregate.clean_author_name(part)
            if c:
                # the identity step, but never the merge map
                names.append(_identity_only(c, e.get("title")))
    return [n for n in names if aggregate.is_full_name(n)]


def _identity_only(name, title):
    saved = aggregate.AUTHOR_NAME_MERGES
    aggregate.AUTHOR_NAME_MERGES = {}
    try:
        return aggregate.resolve_ambiguous_author_identity(name, title)
    finally:
        aggregate.AUTHOR_NAME_MERGES = saved


def collect(entries):
    people = defaultdict(lambda: {"papers": 0, "ids": set(), "orcids": set(),
                                  "coauthors": set(), "inst_years": {}, "together": set()})
    for e in entries:
        names = paper_names(e)
        year = e.get("year")
        details = {}
        for a in e.get("authors_detail") or []:
            for part in aggregate.split_joined_authors(a.get("name") or ""):
                c = aggregate.clean_author_name(part)
                if c:
                    details[_identity_only(c, e.get("title"))] = a
        for n in set(names):
            p = people[n]
            p["papers"] += 1
            for other in names:
                if other != n:
                    p["together"].add(other)
                    k = anv.coauthor_key(other)
                    if k:
                        p["coauthors"].add(k)
            a = details.get(n)
            if not a:
                continue
            if a.get("openalex_id"):
                p["ids"].add(aggregate.canonical_openalex_id(a["openalex_id"]))
            if aggregate.ORCID_RE.fullmatch(a.get("orcid") or ""):
                p["orcids"].add(a["orcid"])
            affs = aggregate.author_affiliations(a)
            if len(affs) > 3 or year is None:  # a glued affiliation block says nothing
                continue
            for aff in affs:
                lo, hi = p["inst_years"].get(aff, (year, year))
                p["inst_years"][aff] = (min(lo, year), max(hi, year))
    return people


def excluded_names():
    out = set(aggregate.AUTHOR_IDENTITIES) | {"Yue Wang"}
    prof = BASE / "data" / "scholar_profiles.json"
    if prof.exists():
        for raw, p in json.loads(prof.read_text(encoding="utf-8")).items():
            if p.get("ambiguous"):
                out.add(aggregate.clean_author_name(raw))
    stats = BASE / "data" / "stats.json"
    if stats.exists():
        out |= set(json.loads(stats.read_text(encoding="utf-8")).get("identity_conflicted_authors") or [])
    return out


def main():
    all_entries = json.loads((BASE / "data" / "papers_full.json").read_text(encoding="utf-8"))
    entries = [e for e in all_entries
               if e.get("av_relevance") == "AV" and aggregate.is_fully_processed(e)
               and e.get("author_verification") != "mismatch"]
    del all_entries
    people = collect(entries)
    excluded = excluded_names()
    merges, near, groups = anv.find_merges(people, excluded, aggregate.COMMON_NAME_SURNAMES)

    # Merging removes a name only when a *different* spelling exists, so the
    # distinct-author count falls by exactly len(merges).
    before = len(people)
    after = before - len(merges)

    # near misses: skip pairs that ended up merged anyway through another member
    near = [n for n in near if merges.get(n["a"], n["a"]) != merges.get(n["b"], n["b"])]
    seen, uniq = set(), []
    for n in near:
        k = (n["a"], n["b"])
        if k not in seen:
            seen.add(k)
            uniq.append(n)
    near = uniq

    OUT_FILE.write_text(json.dumps({
        "_readme": "Spelling variants folded into one author name; generated by "
                   "scripts/merge_author_name_variants.py, read by aggregate.py.",
        "merges": dict(sorted(merges.items())),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    groups.sort(key=lambda g: -sum(people[n]["papers"] for n in [g["name"]] + g["variants"]))
    lines = ["# Author name variants",
             "",
             "Generated by scripts/merge_author_name_variants.py.",
             "",
             "- distinct author names in the AV corpus before: %d" % before,
             "- after merging: %d" % after,
             "- groups merged: %d (%d variant names folded away)" % (len(groups), len(merges)),
             "- compatible pairs left alone: %d" % len(near),
             "",
             "## Examples of merges (largest first)", ""]
    for g in groups[:N_EXAMPLES]:
        lines.append("- **%s** <- %s (%s)" % (
            g["name"], ", ".join(g["variants"]), "; ".join(g["evidence"])))
    lines += ["", "## Near misses that were NOT merged", "",
              "Same surname and compatible given names, but no independent evidence, "
              "or the group was inconsistent. First %d of %d." % (min(N_NEAR_MISSES, len(near)), len(near)), ""]
    near.sort(key=lambda n: -(people[n["a"]]["papers"] + people[n["b"]]["papers"]))
    for n in near[:N_NEAR_MISSES]:
        lines.append("- %s / %s: %s" % (n["a"], n["b"], n["reason"]))
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("names before %d, after %d; %d groups; %d near misses" % (
        before, after, len(groups), len(near)))


if __name__ == "__main__":
    main()
