#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Grades the local LLM's labels (data/relevance_labels_llm.json) against the
hand-labeled ground truth (data/relevance_labels.json) -- restricted to the
"eval_groundtruth" pool, i.e. exactly the papers a human already labeled, so
this is a real apples-to-apples comparison, not the LLM grading itself on
its own broader labeling run.

Never writes to either input file -- this is read-only, print-a-report.

Usage: python evaluate_llm_relevance.py
"""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
GROUND_TRUTH_FILE = BASE / "data" / "relevance_labels.json"
LLM_FILE = BASE / "data" / "relevance_labels_llm.json"


def main():
    ground_truth = {row["id"]: row["label"] for row in json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8"))}
    llm = json.loads(LLM_FILE.read_text(encoding="utf-8")) if LLM_FILE.exists() else {}

    llm_eval = {pid: row for pid, row in llm.items() if row.get("pool") == "eval_groundtruth"}
    overlap = [pid for pid in ground_truth if pid in llm_eval]

    print(f"{len(ground_truth)} ground-truth labels, {len(llm_eval)} LLM eval-pool labels, "
          f"{len(overlap)} overlapping (graded below)")
    if not overlap:
        print("No overlap yet -- run fetch_llm_relevance_labels.py first (it labels the eval pool before anything else).")
        return

    tp = fp = tn = fn = 0
    disagreements = []
    for pid in overlap:
        truth = ground_truth[pid]
        pred = llm_eval[pid]["label"]
        if truth == "AV" and pred == "AV":
            tp += 1
        elif truth == "non-AV" and pred == "non-AV":
            tn += 1
        elif truth == "non-AV" and pred == "AV":
            fp += 1
        else:
            fn += 1
        if truth != pred:
            disagreements.append((llm_eval[pid]["title"], truth, pred, llm_eval[pid].get("reason", "")))

    n = len(overlap)
    accuracy = (tp + tn) / n
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")

    print(f"\nAccuracy:  {accuracy:.1%}")
    print(f"Precision: {precision:.1%}  (of papers the LLM called 'AV', how many really are)")
    print(f"Recall:    {recall:.1%}  (of papers that really are 'AV', how many the LLM caught)")
    print(f"F1:        {f1:.1%}")
    print(f"\nConfusion matrix:")
    print(f"                  predicted AV   predicted non-AV")
    print(f"  actually AV   {tp:>13}   {fn:>18}")
    print(f"  actually adj.   {fp:>13}   {tn:>18}")

    if disagreements:
        print(f"\n{len(disagreements)} disagreements:")
        for title, truth, pred, reason in disagreements:
            print(f"  [{truth} -> LLM said {pred}] {title[:70]}")
            if reason:
                print(f"      LLM reason: {reason}")


if __name__ == "__main__":
    main()
