#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Labels papers "core" (about AVs) vs "adjacent" (not) using a local LLM via
Ollama (http://localhost:11434), as a second opinion alongside the
keyword-heuristic tiers in classify.py -- see DECISIONS.md for why this
runs locally rather than a paid API (standing preference, no per-call cost)
and why it's scoped rather than run over the full ~66k-paper corpus (no
discrete GPU on this machine -- see the hardware check earlier this
session; realistic throughput is a few seconds per paper on CPU).

Processes four pools, in priority order, so a run that gets interrupted
partway still produced the most useful work first:
  1. The 65 hand-labeled ground-truth papers (data/relevance_labels.json) --
     always labeled first, regardless of budget, so there's always an
     apples-to-apples set to evaluate the LLM against (see
     evaluate_llm_relevance.py). Not for training -- for grading.
  2. The abstract-only tier pool (~2,340 papers, weight 0.08/0.2/0.5) -- the
     actual target of this whole effort: replacing classify.py's hand-picked
     tier weights.
  3. A bounded random sample of the zero-match pool (~63k papers, capped at
     --n-zero-match) -- checks for false negatives (an abstract that's
     genuinely about AVs but doesn't contain any of the fixed AV_RELEVANCE_TERMS
     phrases) and gives negative-calibration signal the tier pool alone can't.
  4. A bounded sample of the title-match pool (weight 1.0, capped at
     --n-title-match) -- these are already high-confidence by construction,
     lowest priority, just a sanity check.

CRITICAL: never mixed with human ground truth. Writes to its own file,
av-atlas/data/relevance_labels_llm.json, tagged per-record with which
pool it came from ("eval_groundtruth" / "abstract_tier" / "zero_match" /
"title_match") and the model name/timestamp -- data/relevance_labels.json
(human-labeled) is never read for anything except pulling the 65 ids/text
to prioritize in pool 1, and is never written by this script.

Small-batch, re-read-before-each-batch, resumable, same pattern as every
other backfill this session -- see build_citation_graph.py for why.

Usage: python fetch_llm_relevance_labels.py [--model qwen2.5:7b-instruct]
                                             [--n-zero-match 5000] [--n-title-match 500]
"""
import argparse
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import classify as cl
from build_citation_graph import normalize_title

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
GROUND_TRUTH_FILE = BASE / "data" / "relevance_labels.json"
OUT_FILE = BASE / "data" / "relevance_labels_llm.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 10
SEED = 20260815

PROMPT_TEMPLATE = """You are labeling computer vision / robotics research papers for a corpus specifically about autonomous vehicles (AVs).

A paper is CORE if its method or technology is DIRECTLY APPLIED to autonomous vehicles or self-driving cars -- not just generically applicable to them. For example, a paper about general-purpose object detection, segmentation, or tracking is ADJACENT even though such methods are USED BY autonomous driving systems, unless the paper itself is specifically about the autonomous-driving application.

Strong evidence of CORE: the paper uses an autonomous-vehicle-specific dataset (nuScenes, KITTI, Waymo Open Dataset, Argoverse, BDD100K, or the CARLA simulator), or its stated application/evaluation setting is driving/on-road/traffic scenes.

Weak evidence, NOT enough on its own: merely mentioning the phrase "autonomous vehicle" or "autonomous driving" once (e.g. as one item in a list of possible applications) without the paper's actual method or evaluation being about autonomous driving. Treat this as a hint, not a verdict.

Title: {title}

Abstract: {abstract}

Respond with ONLY a compact JSON object, no other text: {{"label": "core", "reason": "<one short sentence>"}} or {{"label": "adjacent", "reason": "<one short sentence>"}}"""


def call_ollama(model, prompt, timeout=120):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


def parse_label(raw_response):
    try:
        obj = json.loads(raw_response)
        label = str(obj.get("label", "")).strip().lower()
        if label in ("core", "adjacent"):
            return label, obj.get("reason", "")
    except Exception:
        pass
    # Fallback for a model that didn't respect format=json strictly enough --
    # look for the bare word rather than discarding a usable answer.
    m = re.search(r"\b(core|adjacent)\b", raw_response.lower())
    return (m.group(1), "") if m else (None, raw_response[:200])


def label_paper(model, title, abstract):
    prompt = PROMPT_TEMPLATE.format(title=title, abstract=abstract or "(no abstract)")
    raw = call_ollama(model, prompt)
    return parse_label(raw)


def load_results():
    if OUT_FILE.exists():
        return json.loads(OUT_FILE.read_text(encoding="utf-8"))
    return {}


def save_results(results):
    OUT_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def build_pools(args):
    import random
    ground_truth = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8")) if GROUND_TRUTH_FILE.exists() else []
    gt_ids = {row["id"] for row in ground_truth}

    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    by_norm = {normalize_title(p.get("title")): p for p in papers if p.get("abstract")}

    rng = random.Random(SEED)

    pool_eval = [(pid, "eval_groundtruth") for pid in gt_ids if pid in by_norm]

    abstract_tier = [normalize_title(p.get("title")) for p in papers
                      if p.get("abstract") and p.get("av_weight") and 0 < p["av_weight"] < 1.0]
    pool_abstract = [(pid, "abstract_tier") for pid in abstract_tier if pid not in gt_ids]

    zero_match = [normalize_title(p.get("title")) for p in papers
                  if p.get("abstract") and p.get("av_weight") == 0.0]
    rng.shuffle(zero_match)
    pool_zero = [(pid, "zero_match") for pid in zero_match[:args.n_zero_match] if pid not in gt_ids]

    title_match = [normalize_title(p.get("title")) for p in papers
                   if p.get("abstract") and p.get("av_weight") == 1.0]
    rng.shuffle(title_match)
    pool_title = [(pid, "title_match") for pid in title_match[:args.n_title_match] if pid not in gt_ids]

    return by_norm, pool_eval + pool_abstract + pool_zero + pool_title


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="qwen2.5:7b-instruct")
    parser.add_argument("--n-zero-match", type=int, default=5000)
    parser.add_argument("--n-title-match", type=int, default=500)
    args = parser.parse_args()

    by_norm, ordered_pool = build_pools(args)
    results = load_results()
    pending = [(pid, pool) for pid, pool in ordered_pool if pid not in results]
    print(f"{len(pending)} papers to label with {args.model} "
          f"(of {len(ordered_pool)} total across all pools; {len(results)} already done)", flush=True)

    processed = 0
    consecutive_failures = 0
    now = lambda: datetime.now(timezone.utc).isoformat()

    while pending:
        results = load_results()
        batch = [(pid, pool) for pid, pool in pending if pid not in results][:BATCH_SIZE]
        if not batch:
            break

        for pid, pool in batch:
            p = by_norm[pid]
            try:
                label, reason = label_paper(args.model, p["title"], p.get("abstract"))
                if label is None:
                    raise ValueError(f"could not parse a label from model output: {reason!r}")
                results[pid] = {
                    "title": p["title"], "pool": pool, "label": label, "reason": reason,
                    "model": args.model, "labeled_at": now(),
                }
                consecutive_failures = 0
            except Exception as e:
                print(f"  failed on {p['title'][:60]!r}: {e}", flush=True)
                consecutive_failures += 1
            processed += 1

        save_results(results)
        by_pool = {}
        for row in results.values():
            by_pool[row["pool"]] = by_pool.get(row["pool"], 0) + 1
        print(f"  [{processed}/{len(pending)}] progress saved — " +
              ", ".join(f"{k}={v}" for k, v in by_pool.items()), flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive failures "
                  f"(is 'ollama serve' running? is the model pulled?). Resume by rerunning.", flush=True)
            return

    print(f"Done: {len(results)} papers labeled across all pools.", flush=True)


if __name__ == "__main__":
    main()
