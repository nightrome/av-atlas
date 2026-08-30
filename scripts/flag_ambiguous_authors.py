#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Flags authors in the top-N-by-citations list whose single name-keyed record
most likely conflates two or more different real people.

The corpus has no author disambiguation: authors are matched by cleaned name
string alone (see aggregate.py's clean_author_name / KNOWN_NAME_FIXES). For a
distinctive name that is almost always fine. For a common romanized name
("Bo Li", "Wei Wang", "Yang Liu", ...) it silently merges several
researchers into one leaderboard row, so that row's citation total, paper
count, institution history and co-author network are a blend of people.

This script does not try to SPLIT anyone. It produces a review queue: a
ranked list of names, each with the evidence that triggered it, for a human
to check on Google Scholar. Confirmed cases get an {"ambiguous": true} entry
in data/scholar_profiles.json, which author.html already renders as a
warning banner.

Signals (each contributes to a score; none is decisive alone):

  collab_clusters   Build a graph over the author's own papers, linking two
                    papers that share at least one OTHER co-author. One
                    person's output almost always chains into 1-2 connected
                    components through repeat collaborators; 3+ disjoint
                    clusters means 3+ collaboration circles that never touch,
                    the strongest structural sign of distinct people.

  lone_institutions Count affiliations that appear on exactly one paper and
                    never share a year with another of the author's
                    affiliations. A real career revisits institutions and
                    overlaps them at transitions; a pile of one-off,
                    non-overlapping affiliations is several short records
                    stacked together.

  countries         Distinct affiliation countries. 3+ is rare for one
                    person within this corpus's ~8-year window.

  common_name       Two-token name whose surname (and, more strongly, whose
                    full given+surname pair) is on a curated high-collision
                    list. This is a prior, not evidence on its own: it only
                    amplifies a score that already has structural support.

  productivity      In-corpus papers per active year. Above ~18 is
                    implausible for a single first/second author in this
                    field and usually means merged records.

  category_spread   Number of distinct paper categories. Weak: prolific
                    single authors do span topics, so this is capped low.

Usage:
  python flag_ambiguous_authors.py [top_n]        # default 1000
  python flag_ambiguous_authors.py 1000 --json    # machine-readable dump
  python flag_ambiguous_authors.py --min-score 4  # only show score >= 4
"""
import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATS_FILE = BASE / "data" / "stats.json"
PROFILES_FILE = BASE / "data" / "scholar_profiles.json"

# Romanized family names that map to a very large number of distinct people
# in Chinese / Korean / Vietnamese naming. A two-token corpus name ending in
# one of these is a collision risk; it does not by itself mean the record is
# merged.
HIGH_COLLISION_SURNAMES = {
    "wang", "li", "zhang", "liu", "chen", "yang", "huang", "zhao", "wu",
    "zhou", "xu", "sun", "ma", "zhu", "hu", "guo", "he", "gao", "lin", "luo",
    "zheng", "liang", "xie", "tang", "han", "feng", "deng", "cao", "peng",
    "song", "kim", "lee", "park", "choi", "jung", "kang", "cho", "yoon",
    "nguyen", "tran", "pham",
}

# Full given+surname pairs already seen (or highly likely) to be several
# prominent AV researchers at once. Adds extra weight beyond the surname
# prior. Lower-cased, whitespace-collapsed.
KNOWN_COLLIDING_NAMES = {
    "bo li", "wei wang", "yang liu", "lei zhang", "jun li", "jun wang",
    "wei li", "tao wang", "yu zhang", "hao chen", "rui li", "xin wang",
    "jian sun", "yi yang", "wei zhang", "qian zhang", "peng wang",
    "chao zhang", "yang li", "kai zhang", "yue wang", "jun ma", "hao li",
    "xiang li", "bin yang", "ziwei liu",
}


def norm(s):
    return " ".join((s or "").split()).lower()


def build_author_index(stats):
    papers = stats.get("all_papers", [])
    by_author = defaultdict(list)
    for p in papers:
        for a in (p.get("authors") or []):
            by_author[a].append(p)
    return by_author


def collab_cluster_count(author, author_papers):
    """Connected components of the graph whose nodes are the author's papers
    and whose edges join two papers sharing at least one other co-author."""
    n = len(author_papers)
    if n <= 1:
        return n
    coauthor_to_papers = defaultdict(list)
    for i, p in enumerate(author_papers):
        for a in (p.get("authors") or []):
            if a != author:
                coauthor_to_papers[a].append(i)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for idxs in coauthor_to_papers.values():
        for j in idxs[1:]:
            union(idxs[0], j)
    return len({find(i) for i in range(n)})


def lone_institution_count(detail):
    """Affiliations on exactly one paper that never share a year with any
    other affiliation of this author."""
    insts = detail.get("institutions") or []
    if len(insts) <= 1:
        return 0
    spans = [(i["name"], i.get("first_year"), i.get("last_year"), i.get("papers", 0)) for i in insts]
    lone = 0
    for name, f1, l1, papers in spans:
        if papers != 1 or f1 is None:
            continue
        overlaps = False
        for oname, f2, l2, _ in spans:
            if oname == name or f2 is None:
                continue
            if f1 <= l2 and f2 <= l1:
                overlaps = True
                break
        if not overlaps:
            lone += 1
    return lone


def score_author(name, author_papers, detail):
    """Return (score, tier, clusters, reasons).

    A name prior is a GATE, not just a bonus: a distinctive name
    ("Luc Van Gool", "Marc Pollefeys") that happens to have a wide,
    loosely-connected collaboration network is one prolific person, not
    several, so its structural signals are discounted hard and it never
    reaches the review threshold on structure alone. Structural evidence
    only counts at full weight once the name itself is collision-prone.
    """
    reasons = []
    tokens = norm(name).split()
    nm = norm(name)
    if len(tokens) == 2 and nm in KNOWN_COLLIDING_NAMES:
        name_prior = 2
        reasons.append("name on known multi-person list")
    elif len(tokens) == 2 and tokens[-1] in HIGH_COLLISION_SURNAMES:
        name_prior = 1
        reasons.append(f"common romanized name (surname '{tokens[-1]}')")
    else:
        name_prior = 0

    clusters = collab_cluster_count(name, author_papers)
    lone = lone_institution_count(detail)
    countries = detail.get("countries") or []
    years = [p["year"] for p in author_papers if p.get("year")]
    span = (max(years) - min(years) + 1) if years else 1
    ppy = len(author_papers) / span
    cats = {p.get("category") for p in author_papers if p.get("category")}

    structural = 0.0
    if clusters >= 6:
        structural += 3
        reasons.append(f"{clusters} disjoint co-author clusters")
    elif clusters >= 4:
        structural += 2
        reasons.append(f"{clusters} disjoint co-author clusters")
    elif clusters == 3:
        structural += 1
        reasons.append("3 disjoint co-author clusters")
    if lone >= 4:
        structural += 2
        reasons.append(f"{lone} one-off non-overlapping affiliations")
    elif lone >= 2:
        structural += 1
        reasons.append(f"{lone} one-off non-overlapping affiliations")
    if len(countries) >= 3:
        structural += 1.5
        reasons.append(f"{len(countries)} countries ({', '.join(countries)})")
    elif len(countries) == 2:
        structural += 0.5
        reasons.append(f"2 countries ({', '.join(countries)})")
    if ppy > 18:
        structural += 2
        reasons.append(f"{ppy:.0f} papers/active-year")
    elif ppy > 12:
        structural += 1
        reasons.append(f"{ppy:.0f} papers/active-year")
    if len(cats) >= 10:
        structural += 0.5
        reasons.append(f"{len(cats)} distinct categories")

    if name_prior == 0:
        # No collision-prone name: heavily discount, cannot reach threshold.
        score = round(structural * 0.35, 1)
        tier = "prolific-single-likely"
    else:
        score = round(name_prior + structural, 1)
        if name_prior == 2 and clusters >= 4 and structural >= 3:
            tier = "high-confidence"
        elif clusters >= 3 and structural >= 2:
            tier = "review"
        else:
            tier = "weak"

    return score, tier, clusters, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("top_n", nargs="?", type=int, default=1000)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--min-score", type=float, default=3.0)
    args = ap.parse_args()

    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    detail_by_name = stats.get("author_detail", {})
    by_author = build_author_index(stats)

    cites = defaultdict(int)
    for name, papers in by_author.items():
        cites[name] = sum(p.get("citations") or 0 for p in papers)
    ranked = sorted(cites, key=lambda n: -cites[n])[: args.top_n]

    existing = json.loads(PROFILES_FILE.read_text(encoding="utf-8")) if PROFILES_FILE.exists() else {}

    rows = []
    for rank, name in enumerate(ranked, 1):
        papers = by_author[name]
        detail = detail_by_name.get(name, {})
        score, tier, clusters, reasons = score_author(name, papers, detail)
        if score < args.min_score:
            continue
        rows.append({
            "rank_by_citations": rank,
            "name": name,
            "score": score,
            "tier": tier,
            "citations": cites[name],
            "papers": len(papers),
            "already_flagged": bool((existing.get(name) or {}).get("ambiguous")),
            "reasons": reasons,
        })

    TIER_ORDER = {"high-confidence": 0, "review": 1, "weak": 2, "prolific-single-likely": 3}
    rows.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 9), -r["score"]))

    if args.json:
        json.dump(rows, sys.stdout, indent=2, ensure_ascii=False)
        print()
        return

    from collections import Counter
    tally = Counter(r["tier"] for r in rows)
    print(f"{len(rows)} of top {args.top_n} authors flagged (score >= {args.min_score})")
    print("  " + ", ".join(f"{t}: {n}" for t, n in tally.most_common()) + "\n")
    cur = None
    for r in rows:
        if r["tier"] != cur:
            cur = r["tier"]
            print(f"\n=== {cur} ===")
            print(f"{'score':>5}  {'cite#':>6}  {'pap':>4}  {'seen':>4}  name")
            print("-" * 78)
        seen = "yes" if r["already_flagged"] else ""
        print(f"{r['score']:>5}  {r['citations']:>6}  {r['papers']:>4}  {seen:>4}  {r['name']}")
        print(f"         -> {'; '.join(r['reasons'])}")


if __name__ == "__main__":
    main()
