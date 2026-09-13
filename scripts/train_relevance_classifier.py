#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trains the AV-relevance scorer that classify.py uses at classification time.

WHY THIS EXISTS
---------------
classify.py's relevance decision used to be pure keyword counting: a fixed
list of ~30 AV phrases, every one weighted the same, and *any* single match
-> "AV". That has two failure modes on a full, unfiltered venue corpus:
  * recall: obvious synonyms were simply missing from the list
    ("automated vehicle", "connected vehicle", "adaptive cruise control",
    "platooning", "car-following", ...), so ~thousands of DBLP title-only
    IV/ITSC/T-ITS papers with no abstract fell through to "non-AV";
  * precision: a term that is *usually* but not *always* AV ("vehicle",
    "traffic", "trajectory", "routing") counted exactly as hard as
    "self-driving", so generic transportation / vehicular-network /
    surveillance papers got pulled in whenever the list grew.

This model keeps the "features are keyword presence" property (so the
result is still a short, auditable list of per-phrase weights, not a
black box) but *learns the weights* from labeled examples and adds a
decision threshold -- "more than just counting". Two more things counting
couldn't do:
  * a phrase in the TITLE and the same phrase in the ABSTRACT are separate
    features (title mentions are stronger evidence);
  * ambiguous phrases ("vehicle", "traffic", "routing", "charging", ...)
    are in the vocabulary too, so the model can give them ~0 or negative
    weight instead of the list-membership binary of "counts as much as
    'self-driving'".

Hard rules stay OUT of the model and in classify.py as pre-filters
(mechanical/hardware-only exclusion; non-road-vehicle "off-scope" title
guard for aerial/underwater/legged/manipulation/spacecraft) -- those are
scope definitions, not things to learn from noisy labels.

LABELS
------
  data/relevance_labels.json       -- 65 hand labels (ground truth, weight 3)
  data/relevance_labels_llm.json   -- local-LLM pass 1 (abstracts only)
  data/relevance_labels_llm_v2.json -- local-LLM pass 2 (incl. title-only)
The two LLM files are weak supervision (weight 1). The 65 hand labels are
held out of training and used only to report precision/recall, and to pick
the threshold.

OUTPUT
------
  data/relevance_model.json  -- {vocab, title_coef, abstract_coef,
                                 title_strong_coef, intercept, threshold}
classify.py loads this at import; if the file is absent it falls back to
the old keyword-any behavior, so the pipeline still runs without it.

Usage: python train_relevance_classifier.py [--target-precision 0.93]
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
HAND_FILE = BASE / "data" / "relevance_labels.json"
LLM1_FILE = BASE / "data" / "relevance_labels_llm.json"
LLM2_FILE = BASE / "data" / "relevance_labels_llm_v2.json"
MODEL_FILE = BASE / "data" / "relevance_model.json"

# Strong, unambiguous AV phrases (old list + the synonyms it was missing).
STRONG_VOCAB = [
    "autonomous driving", "autonomous vehicle", "self-driving", "self driving",
    "driverless", "ego vehicle", "ego-vehicle", "driving scene", "adas",
    "advanced driver assistance", "advanced driver-assistance", "driver assistance system",
    "on-road driving", "road scene understanding", "vehicle trajectory", "traffic scene",
    "driving policy", "driving behavior", "driving dataset", "naturalistic driving",
    "v2x", "vehicle-to-vehicle", "vehicle-to-infrastructure", "vehicle to everything",
    "roadside perception", "cooperative perception", "collective perception",
    "lane detection", "lane change", "lane changing", "lane-changing", "lane keeping",
    "lane-keeping", "lane departure", "nuscenes", "kitti", "waymo open dataset",
    "argoverse", "bdd100k", "carla simulator", "traffic light", "traffic sign",
    "automated driving", "automated vehicle", "automated driving system",
    "highly automated driving", "connected vehicle", "connected and automated vehicle",
    "connected automated vehicle", "connected and autonomous vehicle",
    "adaptive cruise control", "cooperative adaptive cruise control",
    "automated valet parking", "car-following", "car following", "vehicle platoon",
    "platooning", "platoon control", "truck platoon", "on-ramp merging", "ramp merging",
    "highway merging", "autonomous racing", "autonomous race car", "forward collision warning",
    "collision warning system", "autonomous emergency braking", "automatic emergency braking",
    "driving automation", "end-to-end driving", "end-to-end autonomous driving",
    "drivable area", "drivable region", "free-space detection", "freespace detection",
    "vulnerable road user", "pedestrian intention", "pedestrian crossing intention",
    "intelligent vehicle", "driving simulator", "takeover request",
    "hd map", "high-definition map", "scenario-based testing", "safety of the intended functionality",
    "sotif", "operational design domain", "eco-driving", "cut-in", "car following model",
    "traffic participant", "road user", "onboard", "driving scenario", "highway driving",
    "urban driving", "cooperative driving", "mixed traffic", "mixed autonomy",
    # generic CV/robotics terms ("point cloud", "odometry", "sensor fusion",
    # "3d object detection", "occupancy grid", "bev", "slam") are NOT here on
    # purpose -- same flood risk as the WEAK terms below.
]
# Ambiguous / weak terms ("vehicle", "traffic", "slam", "camera", ...) are
# deliberately NOT in the vocabulary. They appear on huge numbers of non-AV
# papers corpus-wide, but the training labels come from keyword-gated pools
# where they *do* correlate with AV -- so a model given them as features
# learns a spuriously large positive weight and floods AV when applied to
# the whole corpus (confirmed: at a recall-competitive threshold, ~72% of
# "vehicle"/"slam"/"camera"-driven promotions were false positives -- edge
# detection, orthopedic robots, NLP OOD detection). Only phrases specific
# enough that their presence is real AV signal are features here. Recall on
# generically-titled AV papers is recovered instead via the local-LLM
# "AV" verdict, which classify.py ORs on top of this model.
WEAK_VOCAB = []
VOCAB = list(STRONG_VOCAB)
PATTERNS = [re.compile(r"\b" + re.escape(t) + r"s?\b", re.I) for t in VOCAB]
TITLE_STRONG_RE = re.compile(r"\bdrive\b|\bdrives\b|\bdriving\b|\bdriver\b|\bdrivers\b", re.I)
DATA_DRIVEN_RE = re.compile(r"data[- ]driven|goal[- ]driven|model[- ]driven|event[- ]driven", re.I)


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def featurize(title, abstract):
    t = (title or "")
    a = (abstract or "")
    tf = [1 if p.search(t) else 0 for p in PATTERNS]
    af = [1 if p.search(a) else 0 for p in PATTERNS]
    # strong driving word standalone in the title, but not as part of the
    # very common "*-driven" ML phrase (data-driven, goal-driven, ...)
    ts = 1 if TITLE_STRONG_RE.search(DATA_DRIVEN_RE.sub(" ", t)) else 0
    return tf + af + [ts]


def load_labels():
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    by_norm = {}
    for p in papers:
        k = normalize_title(p.get("title"))
        if k and k not in by_norm:
            by_norm[k] = p

    hand = {row["id"]: row["label"] for row in json.loads(HAND_FILE.read_text(encoding="utf-8"))}

    # Per-pool sample weight. The v2 "neg_random" / "core_audit" pools are
    # drawn uniformly from the real corpus, so they -- not the keyword-gated
    # "gap_*" pools -- are what stops the model from over-learning that a bare
    # "vehicle"/"car" in a title means AV (true inside the gated pool,
    # false corpus-wide). Upweight them.
    POOL_W = {"neg_random": 4.0, "core_audit": 2.5, "zero_match": 3.0,
              "gap_noabs": 1.0, "gap_abs": 1.0, "abstract_tier": 1.0,
              "title_match": 0.6, "eval_groundtruth": 0.0}  # eval_groundtruth == hand, held out

    weak = {}
    for f in (LLM1_FILE, LLM2_FILE):
        if not f.exists():
            continue
        for k, v in json.loads(f.read_text(encoding="utf-8")).items():
            if v.get("label") in ("AV", "non-AV"):
                weak[k] = (v["label"], v.get("pool", "gap_noabs"))

    train, hold = [], []
    for k, (lab, pool) in weak.items():
        if k in hand or k not in by_norm:
            continue
        w = POOL_W.get(pool, 1.0)
        if w <= 0:
            continue
        p = by_norm[k]
        train.append((featurize(p.get("title"), p.get("abstract")), 1 if lab == "AV" else 0, w))
    for k, lab in hand.items():
        if k not in by_norm:
            continue
        p = by_norm[k]
        hold.append((featurize(p.get("title"), p.get("abstract")), 1 if lab == "AV" else 0))
    return by_norm, train, hold


def pr_at(y, scores, thr):
    pred = (scores >= thr).astype(int)
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def pr_at_weighted(y, scores, w, thr):
    pred = (scores >= thr).astype(int)
    tp = float(w[(pred == 1) & (y == 1)].sum())
    fp = float(w[(pred == 1) & (y == 0)].sum())
    fn = float(w[(pred == 0) & (y == 1)].sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return prec, rec, f1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-precision", type=float, default=0.92)
    ap.add_argument("--C", type=float, default=0.5, help="inverse L1 strength (smaller = sparser)")
    ap.add_argument("--penalty", default="l1", choices=["l1", "l2"])
    args = ap.parse_args()

    by_norm, train, hold = load_labels()
    if len(train) < 200:
        print(f"Only {len(train)} weak-labeled training rows -- run the LLM labelers first.")
        return

    Xtr = np.array([f for f, _, _ in train]); ytr = np.array([y for _, y, _ in train])
    wtr = np.array([w for _, _, w in train])
    print(f"train: {len(ytr)} rows ({int(ytr.sum())} AV / {int(len(ytr)-ytr.sum())} non-AV), "
          f"weighted AV frac { 100*wtr[ytr==1].sum()/wtr.sum():.0f}%   "
          f"features: {Xtr.shape[1]} ({len(VOCAB)} title + {len(VOCAB)} abstract + 1 title-strong)")
    print(f"holdout (hand labels): {len(hold)} ({sum(y for _,y in hold)} AV / {sum(1-y for _,y in hold)} non-AV)")

    # sklearn 1.8+ deprecated penalty=; L1 is now l1_ratio=1 with the saga
    # solver. L1 keeps the model sparse == auditable (a short weight list).
    l1_ratio = 1.0 if args.penalty == "l1" else 0.0
    model = LogisticRegression(l1_ratio=l1_ratio, C=args.C, class_weight="balanced",
                               solver="saga", max_iter=8000, tol=1e-3)
    model.fit(Xtr, ytr, sample_weight=wtr)

    coef = model.coef_[0]
    n = len(VOCAB)
    title_coef = {k: float(round(v, 4)) for k, v in zip(VOCAB, coef[:n]) if abs(v) > 1e-4}
    abs_coef = {k: float(round(v, 4)) for k, v in zip(VOCAB, coef[n:2 * n]) if abs(v) > 1e-4}
    ts_coef = float(round(coef[2 * n], 4))
    intercept = float(round(model.intercept_[0], 4))
    print(f"nonzero features: {len(title_coef)} title + {len(abs_coef)} abstract "
          f"(+ title-strong {ts_coef:+.2f}), intercept {intercept:+.2f}")

    Xho = np.array([f for f, _ in hold]); yho = np.array([y for _, y in hold])
    sc_ho = model.decision_function(Xho)
    sc_tr = model.decision_function(Xtr)

    # Honest threshold sweep. Want the LOWEST threshold (=> best recall) whose
    # precision still clears target on BOTH the hand holdout and the
    # (larger, noisier) weak-train set. Grid over the observed score range.
    lo, hi = float(min(sc_ho.min(), sc_tr.min())), float(max(sc_ho.max(), sc_tr.max()))
    grid = np.linspace(lo, hi, 400)
    print("\n  thr    hand(P/R)      weak(P/R)")
    rows = []
    for thr in grid:
        hp, hr, _ = pr_at(yho, sc_ho, thr)
        wp, wr, _ = pr_at_weighted(ytr, sc_tr, wtr, thr)
        rows.append((thr, hp, hr, wp, wr))
    for thr, hp, hr, wp, wr in rows[::40]:
        print(f"  {thr:+5.2f}  {hp:.2f}/{hr:.2f}    {wp:.2f}/{wr:.2f}")

    # Ground truth (hand holdout) drives the threshold: lowest thr (=> best
    # recall) with hand precision >= target. weak-train precision is only a
    # sanity floor (its absolute level is depressed by label noise + the
    # keyword-gated pool mix, so it can't be a hard target).
    ok = [(thr, hr) for thr, hp, hr, wp, wr in rows
          if hp >= args.target_precision and wp >= 0.45 and hr >= 0.5]
    if ok:
        chosen = float(min(ok, key=lambda x: x[0])[0])
    else:
        relaxed = [(thr, hr) for thr, hp, hr, wp, wr in rows if hp >= args.target_precision and hr > 0]
        chosen = float(min(relaxed, key=lambda x: x[0])[0]) if relaxed else \
            float(max(rows, key=lambda r: (2*r[1]*r[2]/(r[1]+r[2]) if r[1]+r[2] else 0))[0])
        print("  (primary criterion unmet -- relaxed to hand-precision only)")

    hp, hr, hf = pr_at(yho, sc_ho, chosen)
    wp, wr, wf = pr_at_weighted(ytr, sc_tr, wtr, chosen)
    print(f"\nchosen threshold = {chosen:+.3f}")
    print(f"  hand-holdout : precision={hp:.3f} recall={hr:.3f} f1={hf:.3f}  (n={len(yho)})")
    print(f"  weak-train   : precision={wp:.3f} recall={wr:.3f} f1={wf:.3f}")

    print("\nTITLE phrase weights (nonzero):")
    for k, v in sorted(title_coef.items(), key=lambda kv: -kv[1]):
        print(f"  {v:+.2f}  {k}")
    print("\nABSTRACT phrase weights (nonzero):")
    for k, v in sorted(abs_coef.items(), key=lambda kv: -kv[1]):
        print(f"  {v:+.2f}  {k}")

    kept_vocab = [t for t in VOCAB if t in title_coef or t in abs_coef]
    MODEL_FILE.write_text(json.dumps({
        "_comment": "Trained by train_relevance_classifier.py. Loaded by classify.py. "
                    "score = intercept + sum(title_coef[t] for phrase t in the title) "
                    "+ sum(abstract_coef[t] for phrase t in the abstract) "
                    "+ title_strong_coef if a standalone driving word is in the title; "
                    "AV iff score >= threshold (after classify.py's hard pre-filters).",
        "vocab": kept_vocab,
        "title_coef": title_coef,
        "abstract_coef": abs_coef,
        "title_strong_coef": ts_coef,
        "intercept": intercept,
        "threshold": chosen,
        "penalty": args.penalty, "C": args.C,
        "target_precision": args.target_precision,
        "n_train": int(len(ytr)), "n_hand_holdout": int(len(yho)),
    }, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"\nwrote {MODEL_FILE}  ({len(kept_vocab)} phrases)")

    import random
    papers = list(by_norm.values())
    rng = random.Random(0)
    sample = papers if len(papers) <= 60000 else rng.sample(papers, 60000)
    feats = np.array([featurize(p.get("title"), p.get("abstract")) for p in sample])
    allsc = model.decision_function(feats)
    frac = float((allsc >= chosen).mean())
    print(f"corpus projection (sample n={len(sample)}): ~{100*frac:.1f}% score AV by the model alone "
          f"=> ~{int(frac*len(papers))} / {len(papers)}  (before LLM-AV OR-promotion)")
    hits = [sample[i] for i in np.argsort(-allsc)[: (allsc >= chosen).sum()]]
    rng.shuffle(hits)
    with open(BASE / "scripts" / "_relevance_projection_sample.txt", "w", encoding="utf-8") as fh:
        for p in hits[:250]:
            fh.write(f"[{p.get('venue')}] ({p.get('year')}) {p.get('title')}\n")
    print("  wrote scripts/_relevance_projection_sample.txt (250 random model-AV titles to eyeball)")


if __name__ == "__main__":
    main()
