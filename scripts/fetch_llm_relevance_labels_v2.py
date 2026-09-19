#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Round 2 of local-LLM relevance labeling (see fetch_llm_relevance_labels.py
for the original). That first pass only ever looked at papers that HAVE an
abstract (`by_norm = {... for p in papers if p.get("abstract")}`), so it
never saw the ~16k currently-"non-AV" DBLP/title-only papers from
IV/ITSC/T-ITS/IROS/... that are exactly where AV recall is being lost --
a paper with no abstract could not be labeled at all.

This pass labels from the TITLE ALONE when there's no abstract (the same
thing a human does skimming a proceedings listing), across a stratified
sample that is NOT venue-conditioned -- every venue is drawn from the same
way, the strata are about how much weak keyword signal the title carries
and what the current classifier already decided:

  1. weak-signal + currently non-AV + NO abstract   (the recall gap)
  2. weak-signal + currently non-AV + has abstract
  3. currently AV (any)                              (precision audit)
  4. random no-keyword non-AV                        (negative calibration)

"weak signal" = the title contains at least one of a broad vehicle/driving/
traffic vocabulary (see WEAK_TITLE_RE) -- deliberately loose, this is the
candidate net, not the classifier.

Writes data/relevance_labels_llm_v2.json (its own file, never mixes with
the human ground truth in relevance_labels.json). Small-batch, re-read
before each batch, resumable -- same pattern as every other backfill.

Usage: python fetch_llm_relevance_labels_v2.py [--model qwen2.5:7b-instruct]
         [--n-gap 3800] [--n-gap-abs 1200] [--n-av 800] [--n-neg 500]
"""
import argparse
import json
import random
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
GROUND_TRUTH_FILE = BASE / "data" / "relevance_labels.json"
V1_FILE = BASE / "data" / "relevance_labels_llm.json"
OUT_FILE = BASE / "data" / "relevance_labels_llm_v2.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 12
SEED = 20260830

WEAK_TITLE_RE = re.compile(
    r"\b(vehicle|vehicular|driv|traffic|lane|automated|autonomous|self-driving|"
    r"pedestrian|road|highway|intersection|platoon|adas|lidar|ego|car|cars|truck|"
    r"cyclist|collision|steering|overtak|merging|roundabout|v2x|v2v|v2i|"
    r"cruise control|odometry|slam|occupancy|nuscenes|kitti|waymo|argoverse)\w*", re.I)

PROMPT_TEMPLATE = """You are labeling computer vision / robotics research papers for a corpus specifically about autonomous vehicles (AVs) -- self-driving road cars, trucks, and buses, and the ADAS / connected-vehicle / automated-driving research around them.

Label the paper CORE if it is specifically about the autonomous / automated / connected road-vehicle application: perception, prediction, planning, control, mapping/localization, V2X, driver assistance, ACC / lane-keeping / platooning, automated-driving safety & validation, driving datasets/scenarios, etc. -- where the paper's own subject is the road vehicle or the driving task.

Label it ADJACENT if it is:
- a general-purpose CV/ML method (detection, segmentation, tracking, SLAM, ...) not itself about driving, even if driving is one listed application;
- about a NON-road-vehicle platform: aerial / UAV / drone / quadrotor, underwater / marine / surface vessel, spacecraft / planetary rover, legged robot / quadruped / humanoid, robot manipulation / grasping;
- about intelligent-transportation topics that are not the vehicle: traffic-signal timing, travel-demand / mode-choice modeling, public-transit / rail / freight logistics scheduling, toll / congestion pricing, EV charging-infrastructure siting, crash-frequency / injury-severity statistics, vehicular-network communication protocols, roadside traffic surveillance / speed enforcement.

Consider a paper with no abstract on its title alone -- proceedings titles are usually specific enough.

Title: {title}

Abstract: {abstract}

Respond with ONLY a compact JSON object, no other text: {{"label": "AV", "reason": "<=8 words"}} or {{"label": "non-AV", "reason": "<=8 words"}}"""


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def call_ollama(model, prompt, timeout=180):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


def parse_label(raw):
    try:
        obj = json.loads(raw)
        label = str(obj.get("label", "")).strip().lower()
        if label in ("AV", "non-AV"):
            return label, str(obj.get("reason", ""))[:120]
    except Exception:
        pass
    m = re.search(r"(non-?av|av)", raw.lower())
    return (m.group(1), "") if m else (None, raw[:160])


def load_results():
    return json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}


def save_results(r):
    OUT_FILE.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def build_pools(args):
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    gt = json.loads(GROUND_TRUTH_FILE.read_text(encoding="utf-8")) if GROUND_TRUTH_FILE.exists() else []
    already = {row["id"] for row in gt}
    if V1_FILE.exists():
        already |= set(json.loads(V1_FILE.read_text(encoding="utf-8")).keys())

    by_norm = {}
    for p in papers:
        k = normalize_title(p.get("title"))
        if k and k not in by_norm:
            by_norm[k] = p

    rng = random.Random(SEED)
    weak = lambda p: bool(WEAK_TITLE_RE.search(p.get("title") or ""))

    gap, gap_abs, cur_av, neg = [], [], [], []
    for k, p in by_norm.items():
        if k in already:
            continue
        rel = p.get("av_relevance")
        has_abs = bool(p.get("abstract"))
        if rel == "AV":
            cur_av.append(k)
        elif rel == "non-AV":
            if weak(p) and not has_abs:
                gap.append(k)
            elif weak(p) and has_abs:
                gap_abs.append(k)
            else:
                neg.append(k)

    for lst in (gap, gap_abs, cur_av, neg):
        rng.shuffle(lst)

    pool = ([(k, "gap_noabs") for k in gap[:args.n_gap]]
            + [(k, "gap_abs") for k in gap_abs[:args.n_gap_abs]]
            + [(k, "core_audit") for k in cur_av[:args.n_av]]
            + [(k, "neg_random") for k in neg[:args.n_neg]])
    rng.shuffle(pool)
    return by_norm, pool


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--n-gap", type=int, default=3800)
    ap.add_argument("--n-gap-abs", type=int, default=1200)
    ap.add_argument("--n-av", type=int, default=800)
    ap.add_argument("--n-neg", type=int, default=500)
    args = ap.parse_args()

    by_norm, pool = build_pools(args)
    results = load_results()
    pending = [(k, tag) for k, tag in pool if k not in results]
    print(f"{len(pending)} to label with {args.model} "
          f"({len(pool)} in sample, {len(results)} already done)", flush=True)

    now = lambda: datetime.now(timezone.utc).isoformat()
    processed = 0
    fails = 0
    while pending:
        results = load_results()
        batch = [(k, tag) for k, tag in pending if k not in results][:BATCH_SIZE]
        if not batch:
            break
        for k, tag in batch:
            p = by_norm[k]
            try:
                prompt = PROMPT_TEMPLATE.format(title=p["title"], abstract=p.get("abstract") or "(no abstract available)")
                label, reason = parse_label(call_ollama(args.model, prompt))
                if label is None:
                    raise ValueError(f"unparseable: {reason!r}")
                results[k] = {
                    "title": p["title"], "pool": tag, "label": label, "reason": reason,
                    "has_abstract": bool(p.get("abstract")), "venue": p.get("venue"),
                    "model": args.model, "labeled_at": now(),
                }
                fails = 0
            except Exception as e:
                print(f"  fail {p['title'][:60]!r}: {e}", flush=True)
                fails += 1
            processed += 1
        save_results(results)
        by_pool = {}
        for r in results.values():
            by_pool[r["pool"]] = by_pool.get(r["pool"], 0) + 1
        n_av = sum(1 for r in results.values() if r["label"] == "AV")
        print(f"  [{processed}/{len(pending)}] saved {len(results)} (AV={n_av}) — "
              + ", ".join(f"{a}={b}" for a, b in sorted(by_pool.items())), flush=True)
        if fails >= MAX_CONSECUTIVE_FAILURES:
            print("Stopping: too many consecutive failures. Rerun to resume.", flush=True)
            return
    print(f"Done: {len(results)} labels in {OUT_FILE.name}", flush=True)


if __name__ == "__main__":
    main()
