#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trains a small logistic regression on data/relevance_labels.json to replace
classify.py's hand-picked abstract-only tiers (0.08 / 0.2 / 0.5 by distinct
AV-term count) with weights learned from real labeled examples.

Deliberately kept low-dimensional: features are just "does this abstract
contain phrase X" for each of the existing AV_RELEVANCE_TERMS (the same
~28-phrase vocabulary classify.py already uses), not a full TF-IDF
vocabulary. Two reasons: with only a few dozen labeled examples, thousands
of TF-IDF features would badly overfit; and keeping the same fixed
vocabulary means the result is still a short, auditable list of per-term
weights someone can read in classify.py, not a black box. This only
replaces HOW the existing terms get combined into a score, not what counts
as a term.

Prints cross-validated accuracy (small-n, so this is a rough signal, not a
confident number) and the learned per-term coefficients, sorted by weight,
so you can sanity-check them against intuition before anything gets used.
Does NOT modify classify.py automatically -- that's a deliberate manual
step (see the printed instructions at the end) so a change to how every
paper on the site gets ranked is never silently auto-applied.

Usage: python train_relevance_classifier.py
"""
import json
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score, LeaveOneOut

import classify as cl

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
LABELS_FILE = BASE / "data" / "relevance_labels.json"


def featurize(abstract):
    text = (abstract or "").lower()
    return [1 if term in text else 0 for term in cl.AV_RELEVANCE_TERMS]


def main():
    labels = json.loads(LABELS_FILE.read_text(encoding="utf-8"))
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    from build_citation_graph import normalize_title  # reuse, don't reimplement a third copy
    by_norm = {normalize_title(p.get("title")): p for p in papers}

    X, y, missing = [], [], 0
    for row in labels:
        p = by_norm.get(row["id"])
        if not p or not p.get("abstract"):
            missing += 1
            continue
        X.append(featurize(p["abstract"]))
        y.append(1 if row["label"] == "core" else 0)

    X, y = np.array(X), np.array(y)
    print(f"{len(y)} labeled examples usable ({missing} missing abstract text), "
          f"{y.sum()} core / {len(y) - y.sum()} adjacent")

    if len(y) < 20 or len(set(y.tolist())) < 2:
        print("Not enough examples (or only one class) to train yet -- label more and rerun.")
        return

    model = LogisticRegression(class_weight="balanced", C=0.5, max_iter=1000)

    # Leave-one-out CV: with this few examples, a train/test split would be
    # too noisy to mean anything -- LOO uses every example as a test point
    # once, giving the most honest small-n estimate available.
    scores = cross_val_score(model, X, y, cv=LeaveOneOut())
    print(f"Leave-one-out cross-validated accuracy: {scores.mean():.1%} "
          f"(over {len(y)} examples -- treat as a rough signal, not a confident number)")

    model.fit(X, y)
    coefs = sorted(zip(cl.AV_RELEVANCE_TERMS, model.coef_[0]), key=lambda kv: -kv[1])
    print("\nLearned per-term weights (higher = stronger evidence of 'core'):")
    for term, coef in coefs:
        print(f"  {coef:+.2f}  {term}")

    print(f"\nintercept: {model.intercept_[0]:+.2f}")
    print("\nThese are NOT applied to classify.py automatically. If they look sane, the next step is")
    print("hand-editing av_weight_and_relevance's abstract-only branch to use these weights (e.g. a")
    print("sigmoid over the weighted sum) instead of the current 0.08/0.2/0.5 step function.")


if __name__ == "__main__":
    main()
