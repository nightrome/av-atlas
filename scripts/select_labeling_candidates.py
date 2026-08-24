#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Picks a diverse, hand-labelable sample of papers for improving classify.py's
weakest rule: the abstract-only tiered weighting (0.08 / 0.2 / 0.5 by
distinct AV-term count -- see av_weight_and_relevance in classify.py).
Papers whose TITLE matches an AV term are already high-confidence
(weight 1.0) and aren't included.

Draws from four pools, not just one:
  - positive tiers (weight 0.08 / 0.2 / 0.5): the abstract-only matches the
    current tier logic actually scores.
  - hard negatives (weight 0.0, but matched a generic CV/robotics category
    keyword -- object detection, segmentation, tracking, ...): zero AV-term
    match, but plausible-*looking* to a labeler skimming the abstract. This
    was missing from the first version of this script and it showed: every
    label came back "core", because every candidate already had at least
    one AV-specific phrase in its abstract by construction. Real negative
    examples only exist in the zero-match pool, and random zero-match
    papers (usually NLP, medical imaging, totally unrelated fields) are too
    easy to be useful training signal -- the hard-negative pool specifically
    targets papers that share vocabulary with AV research without being
    about it, which is the actual decision boundary a classifier needs to
    learn.
  - random negatives (weight 0.0, any category): a smaller pool, just to
    confirm the "obviously unrelated" case is still labeled as expected.

Already-labeled papers (data/relevance_labels.json, if present) are
excluded, so re-running this after a labeling round doesn't show the same
papers again.

Writes av-atlas/data/labeling_candidates.json:
  [{"id": "<normalized title>", "title": "...", "abstract": "...",
    "venue": "...", "year": ..., "current_weight": 0.08,
    "matched_terms": ["autonomous driving", ...]}]

Usage: python select_labeling_candidates.py [--n-positive 90] [--n-hard-negative 60] [--n-random-negative 30]
"""
import argparse
import json
import random
import re
from pathlib import Path

import classify as cl

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
OUT_FILE = BASE / "data" / "labeling_candidates.json"
LABELS_FILE = BASE / "data" / "relevance_labels.json"
SEED = 20260814  # fixed, so rerunning without new data reproduces the same sample


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def matched_terms(abstract):
    text = (abstract or "").lower()
    return [t for t in cl.AV_RELEVANCE_TERMS if t in text]


def to_candidate(p):
    return {
        "id": normalize_title(p.get("title")),
        "title": p.get("title"),
        "abstract": p.get("abstract"),
        "venue": p.get("venue"),
        "year": p.get("year"),
        "current_weight": p.get("av_weight"),
        "matched_terms": matched_terms(p.get("abstract")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-positive", type=int, default=90, help="from the three abstract-match tiers")
    parser.add_argument("--n-hard-negative", type=int, default=60, help="zero-match but categorized (looks plausible)")
    parser.add_argument("--n-random-negative", type=int, default=30, help="zero-match, any category")
    args = parser.parse_args()

    already_labeled = set()
    if LABELS_FILE.exists():
        already_labeled = {row["id"] for row in json.loads(LABELS_FILE.read_text(encoding="utf-8"))}

    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    papers = [p for p in papers if p.get("abstract") and normalize_title(p.get("title")) not in already_labeled]

    tiers = {0.08: [], 0.2: [], 0.5: []}
    hard_negatives, random_negatives = [], []
    for p in papers:
        w = p.get("av_weight")
        if w in tiers:
            tiers[w].append(p)
        elif w == 0.0:
            (hard_negatives if p.get("category") and p["category"] != "uncategorized" else random_negatives).append(p)

    rng = random.Random(SEED)
    sample = []

    per_tier = args.n_positive // len(tiers)
    for pool in tiers.values():
        rng.shuffle(pool)
        sample.extend(pool[:per_tier])

    rng.shuffle(hard_negatives)
    sample.extend(hard_negatives[:args.n_hard_negative])

    rng.shuffle(random_negatives)
    sample.extend(random_negatives[:args.n_random_negative])

    candidates = [to_candidate(p) for p in sample]
    rng.shuffle(candidates)  # don't present them grouped by pool -- that would bias a labeler's judgment

    OUT_FILE.write_text(json.dumps(candidates, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote {len(candidates)} candidates to {OUT_FILE}: "
          f"{sum(len(v[:per_tier]) for v in tiers.values())} positive (tiers), "
          f"{len(hard_negatives[:args.n_hard_negative])} hard negative, "
          f"{len(random_negatives[:args.n_random_negative])} random negative "
          f"({len(already_labeled)} already-labeled papers excluded)")


if __name__ == "__main__":
    main()
