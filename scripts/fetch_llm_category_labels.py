#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Local-LLM category labeling for title-only AV papers stuck in "misc" --
the same population fetch_llm_relevance_labels_v2.py's "gap" pool targets
for relevance, but for the OTHER axis: classify.py's keyword-matching
already knows these papers ARE about AVs (that's why they're "misc", not
"non-AV"), it just can't tell WHICH of the 32 categories from a title alone,
the same way a human skimming a proceedings listing sometimes can. A
DBLP-sourced venue (T-ITS, ITSC, IV, ...) never carried an abstract in the
first place (see DECISIONS.md's "DBLP-sourced venues have no abstracts"),
so for this population the title is ALL there ever will be to go on --
unlike the "misc" residue from papers that DO have an abstract, where the
gap is a taxonomy-coverage question, not a data-availability one.

Reads every one of the 30 real categories' {id, label} straight from
categories.json at runtime (not hardcoded here), so this stays in sync
automatically as the taxonomy grows -- the LLM picks from the same list a
maintainer extending categories.json would recognize.

Writes data/category_labels_llm.json:
  {normalizedTitle: {"category": "<id>" or null, "reason": "...", ...}}
`category: null` is a real, useful answer (the LLM's own judgment that
nothing fits, i.e. genuinely misc), not a failure -- distinct from a
missing key (not yet labeled) or an entry absent because the call itself
errored (retried next run). See classify.py's load_llm_category_labels for
how this is consumed: a LAST-resort signal, weaker than any real category
match and even weaker than the explainability last-resort tier, since it's
a single model's single-title guess, not a verified keyword.

Small-batch, re-read before each batch, resumable -- same pattern as
fetch_llm_relevance_labels_v2.py and every other local-LLM backfill.

Usage: python fetch_llm_category_labels.py [--model qwen2.5:7b-instruct] [--n N]
"""
import argparse
import json
import random
import re
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
CATEGORIES_FILE = BASE / "data" / "categories.json"
OUT_FILE = BASE / "data" / "category_labels_llm.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 12
SEED = 20260913

PROMPT_TEMPLATE = """You are assigning ONE topic category to an autonomous-vehicle (AV) research paper, from its title alone (no abstract is available for this paper -- its source venue never published one). The paper has already been confirmed to be genuinely about autonomous/automated vehicles; your only job is picking which of these categories its own topic best fits.

Categories (id: label):
{category_list}

Title: {title}

If the title gives a genuine, specific signal for exactly one category, respond with that category's id. If the title is too generic, or the paper's topic doesn't fit any of these (e.g. it's a general survey, a policy/economics paper, or a topic none of these categories cover), respond with null -- do not force a weak guess.

Respond with ONLY a compact JSON object, no other text: {{"category": "<id or null>", "reason": "<=8 words"}}"""


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_categories():
    data = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    return [(c["id"], c["label"]) for c in data["categories"]]


def call_ollama(model, prompt, timeout=180):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


def parse_label(raw, valid_ids):
    try:
        obj = json.loads(raw)
        cat = obj.get("category")
        cat = str(cat).strip() if cat not in (None, "null", "") else None
        if cat is None or cat in valid_ids:
            return True, cat, str(obj.get("reason", ""))[:120]
        if cat is not None:
            # The model sometimes paraphrases an id instead of copying it
            # verbatim (confirmed live: "simulation" for
            # "simulation-benchmarking") -- a real category signal, not
            # worth discarding just because the string isn't an exact
            # match. Accept it if it's an unambiguous prefix of exactly one
            # valid id; anything less certain still falls through to a
            # genuine parse failure below rather than guessing.
            prefix_matches = [v for v in valid_ids if v.startswith(cat) or cat.startswith(v)]
            if len(prefix_matches) == 1:
                return True, prefix_matches[0], str(obj.get("reason", ""))[:120]
    except Exception:
        pass
    return False, None, raw[:160]


def load_results():
    return json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}


def save_results(r):
    OUT_FILE.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def build_pool(n):
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    by_norm = {}
    for p in papers:
        # Title-only AND still misc -- see module docstring for why this is
        # the population an LLM helps with (vs. an abstract-having misc
        # paper, which is a taxonomy-coverage question, not a data one).
        if p.get("av_relevance") != "AV" or p.get("category") != "misc" or (p.get("abstract") or "").strip():
            continue
        k = normalize_title(p.get("title"))
        if k and k not in by_norm:
            by_norm[k] = p
    keys = list(by_norm.keys())
    random.Random(SEED).shuffle(keys)
    return by_norm, keys[:n] if n else keys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--n", type=int, default=None, help="cap the pool size (default: every eligible paper)")
    args = ap.parse_args()

    categories = load_categories()
    valid_ids = {cid for cid, _ in categories}
    category_list = "\n".join(f"{cid}: {label}" for cid, label in categories)

    by_norm, pool = build_pool(args.n)
    results = load_results()
    pending = [k for k in pool if k not in results]
    print(f"{len(pending)} title-only misc papers to label with {args.model} "
          f"({len(pool)} in pool, {len(results)} already done)", flush=True)

    processed = 0
    fails = 0
    # Same fix as build_citation_graph.py's identical loop shape -- without
    # this, a paper whose response never parses (confirmed live: the model
    # can keep making the same unrecoverable mistake, e.g. inventing a
    # category id not in valid_ids and not a clean prefix match either)
    # gets re-selected into every subsequent batch for the rest of THIS
    # run, burning inference time on a call already known to fail.
    attempted_this_run = set()
    while pending:
        results = load_results()
        batch = [k for k in pending if k not in results and k not in attempted_this_run][:BATCH_SIZE]
        if not batch:
            break
        for k in batch:
            attempted_this_run.add(k)
            p = by_norm[k]
            try:
                prompt = PROMPT_TEMPLATE.format(category_list=category_list, title=p["title"])
                ok, category, reason = parse_label(call_ollama(args.model, prompt), valid_ids)
                if not ok:
                    raise ValueError(f"unparseable: {reason!r}")
                results[k] = {
                    "title": p["title"], "category": category, "reason": reason,
                    "venue": p.get("venue"), "model": args.model,
                }
                fails = 0
            except Exception as e:
                print(f"  fail {p['title'][:60]!r}: {e}", flush=True)
                fails += 1
            processed += 1
        save_results(results)
        n_assigned = sum(1 for r in results.values() if r["category"])
        print(f"  [{processed}/{len(pending)}] saved {len(results)} (assigned={n_assigned}, "
              f"null={len(results) - n_assigned})", flush=True)
        if fails >= MAX_CONSECUTIVE_FAILURES:
            print("Stopping: too many consecutive failures. Rerun to resume.", flush=True)
            return
    print(f"Done: {len(results)} labels in {OUT_FILE.name}", flush=True)


if __name__ == "__main__":
    main()
