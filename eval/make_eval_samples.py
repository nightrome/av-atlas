#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generates the two hand-annotation sheets used to measure the pipeline:

  eval/relevance_eval_sample.csv  -- 50 random papers the classifier calls
     "core" + 50 it calls "adjacent". Annotator fills `human_label`
     (core / adjacent / unsure) against RUBRIC_relevance.md. Gives classifier
     precision directly (from the core half), false-omission rate directly
     (from the adjacent half); recall is backed out with the corpus base rate.

  eval/citation_eval_sample.csv   -- 50 random matched (citing -> cited) edges
     for matcher PRECISION + 50 random *unmatched* reference strings (each
     with the 3 best fuzzy corpus candidates) for matcher RECALL. Annotator
     fills `edge_correct` (yes/no) on the first block and `real_target`
     (candidate number, or 0 for none) on the second.

Fixed seed. Draws from a fresh random sample, NOT data/labeling_candidates.json
(that sampler is keyword-based and would bias the test toward the classifier's
own blind spots). Re-run any time; overwrites the CSVs.

Usage: python eval/make_eval_samples.py
"""
import csv
import json
import random
import re
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SEED = 20260830
random.seed(SEED)

papers = json.loads((BASE / "data" / "papers_full.json").read_text(encoding="utf-8"))


def nt(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


by_norm = {nt(p.get("title")): p for p in papers}


def clip(s, n=600):
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:n] + ("..." if len(s) > n else "")


# ---------------- relevance sheet ----------------
core = [p for p in papers if p.get("av_relevance") == "core"]
adj = [p for p in papers if p.get("av_relevance") == "adjacent"]
sample = random.sample(core, 50) + random.sample(adj, 50)
random.shuffle(sample)

rel_path = BASE / "eval" / "relevance_eval_sample.csv"
with rel_path.open("w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["row", "human_label (core/adjacent/unsure)", "title", "venue", "year",
                "has_abstract", "abstract", "classifier_says", "norm_id"])
    for i, p in enumerate(sample, 1):
        w.writerow([i, "", p.get("title"), p.get("venue"), p.get("year"),
                    "yes" if p.get("abstract") else "no", clip(p.get("abstract")),
                    p.get("av_relevance"), nt(p.get("title"))])
print(f"wrote {rel_path}  ({len(sample)} rows: 50 classifier-core + 50 classifier-adjacent, shuffled)")

# ---------------- citation sheet ----------------
graph = json.loads((BASE / "data" / "citation_graph.json").read_text(encoding="utf-8"))
edges = graph["edges"]  # {citing_norm: [cited_norm, ...]}
refs = json.loads((BASE / "data" / "reference_lists_arxiv.json").read_text(encoding="utf-8"))  # {citing_norm: [raw_str,...]}

# precision block: 50 random matched edges
flat_edges = [(c, d) for c, ds in edges.items() for d in ds if d in by_norm and c in by_norm]
prec_edges = random.sample(flat_edges, 50)


def best_raw_for(citing_norm, cited_title):
    """Find the raw reference string in the citing paper's list that most
    likely produced this edge (highest token overlap with the cited title)."""
    cand = refs.get(citing_norm, [])
    if not cand:
        return ""
    ct = set(re.findall(r"[a-z]{4,}", (cited_title or "").lower()))
    best, bs = "", 0.0
    for r in cand:
        rt = set(re.findall(r"[a-z]{4,}", r.lower()))
        s = len(ct & rt) / (len(ct) + 1)
        if s > bs:
            bs, best = s, r
    return best


# recall block: 50 random raw refs that did NOT yield an edge, each with 3 fuzzy
# candidates. Inverted index (token -> title rows) so each ref is scored only
# against titles that share a token, not all ~236k.
title_rows = [(nrm, p.get("title")) for nrm, p in by_norm.items() if p.get("title")]
title_tok = [set(re.findall(r"[a-z]{4,}", t.lower())) for _, t in title_rows]
inv = {}
for idx, toks in enumerate(title_tok):
    for tk in toks:
        inv.setdefault(tk, []).append(idx)

recall_rows = []
citing_pool = [c for c in refs if c in by_norm]
random.shuffle(citing_pool)
for c in citing_pool:
    matched_norms = set(edges.get(c, []))
    unmatched = [r for r in refs[c] if len(r) > 40]
    random.shuffle(unmatched)
    if not unmatched:
        continue
    r = unmatched[0]
    rtok = set(re.findall(r"[a-z]{4,}", r.lower()))
    cand_idx = set()
    for tk in rtok:
        cand_idx.update(inv.get(tk, ())[:400])
    scored = sorted(
        ((len(rtok & title_tok[i]) / (len(rtok) + 1), title_rows[i][0], title_rows[i][1])
         for i in cand_idx),
        reverse=True)[:3]
    if scored and scored[0][1] in matched_norms:
        continue
    recall_rows.append((c, r, scored))
    if len(recall_rows) >= 50:
        break

cit_path = BASE / "eval" / "citation_eval_sample.csv"
with cit_path.open("w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(["block", "row", "answer", "citing_paper", "reference_text / cited_paper",
                "candidate_1", "candidate_2", "candidate_3"])
    for i, (c, d) in enumerate(prec_edges, 1):
        w.writerow(["PRECISION", i, "  (edge_correct: yes/no)",
                    by_norm[c].get("title"),
                    f"MATCHED TO: {by_norm[d].get('title')}   | raw ref: {clip(best_raw_for(c, by_norm[d].get('title')), 300)}",
                    "", "", ""])
    for i, (c, r, scored) in enumerate(recall_rows, 1):
        cands = [t for _, _, t in scored] + ["", "", ""]
        w.writerow(["RECALL", i, "  (real_target: 1/2/3/0=none)",
                    by_norm[c].get("title"), clip(r, 400), cands[0], cands[1], cands[2]])
print(f"wrote {cit_path}  ({len(prec_edges)} precision rows + {len(recall_rows)} recall rows)")
