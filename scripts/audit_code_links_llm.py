#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM-based audit of the `has_code_link` heuristic.

The current detector (fetch_affiliations_arxiv.py's detect_code_link) flags a
paper as "links its own code" if ANY non-bibliography link on its arXiv HTML
points at a known code host, OR a loose "code ... available" sentence
matches. That over-counts in predictable ways: a HuggingFace / Papers With
Code link that's a dataset or leaderboard rather than own code; a
third-party GitHub repo named in the body ("we use the official
implementation ..."); the text regex matching a negated claim ("code is not
yet available"). A ~37% "has code" rate looked high, so this script gets a
second opinion.

For a stratified subsample (half currently flagged True, half False, spread
across years) it re-fetches the ar5iv page, pulls out the title, abstract,
every candidate code URL with its surrounding sentence, and any
code-availability sentence, and asks a local Ollama model the narrow
question: does THIS paper release ITS OWN source code? The answer
(yes / no / unclear, plus the URL and a short evidence quote) is compared
to the heuristic's verdict and written to data/code_link_audit.json.

Nothing here feeds the live site -- it's an evaluation of the existing
heuristic, run before deciding whether to replace it. Resumable: rerun to
pick up where a network/Ollama hiccup left off (already-audited titles are
skipped).

Usage:
  python audit_code_links_llm.py [--n 75] [--model qwen2.5:7b-instruct] [--seed 20260828]

  --n      papers per stratum (True / False); total sample is 2*n
  --model  Ollama model tag (must already be pulled: `ollama list`);
           gemma4:26b judges better but is far slower on CPU
"""
import argparse
import copy
import json
import random
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict

from fetch_common import BASE

# Reuse the exact fetch + patterns the heuristic itself uses, so the audit
# looks at the same page content the detector saw.
from fetch_affiliations_arxiv import (
    CODE_HOST_RE,
    CODE_AVAILABILITY_TEXT_RE,
    fetch_ar5iv_page,
)

PAPERS_FILE = BASE / "data" / "papers_full.json"
AFFIL_FILE = BASE / "data" / "affiliations_arxiv.json"
OUT_FILE = BASE / "data" / "code_link_audit.json"
OLLAMA_URL = "http://localhost:11434/api/generate"

PROMPT_TEMPLATE = """You are auditing whether a research paper releases ITS OWN source code.

"Its own code" means an implementation of THIS paper's method/experiments that the authors published. It does NOT count if:
- the link is to a dataset, a model checkpoint page, or a benchmark/leaderboard
- the link is to a third-party or baseline method's repository the paper merely uses or compares against
- the paper only says code is "not available" / "will be released" (nothing actually released yet)
- the only "code host" link is unrelated site chrome

Paper title: {title}

Abstract:
{abstract}

Candidate code links found on the page (URL -- surrounding sentence):
{links}

Code-availability sentences found on the page:
{sentences}

Answer with ONLY a compact JSON object, no other text:
{{"has_own_code": "yes" | "no" | "unclear", "url": "<the paper's own code URL, or null>", "evidence": "<short quote or reason, <=200 chars>"}}"""


def call_ollama(model, prompt, timeout=180):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "format": "json", "options": {"temperature": 0}}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


def parse_verdict(raw):
    try:
        obj = json.loads(raw)
        v = str(obj.get("has_own_code", "")).strip().lower()
        if v in ("yes", "no", "unclear"):
            return v, obj.get("url"), str(obj.get("evidence", ""))[:300]
    except Exception:
        pass
    m = re.search(r"\b(yes|no|unclear)\b", raw.lower())
    return (m.group(1) if m else None), None, raw[:200]


def norm_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return None


def sentences_around(text, pattern, limit=6):
    out = []
    for m in pattern.finditer(text):
        start = text.rfind(".", 0, m.start()) + 1
        end = text.find(".", m.end())
        if end == -1:
            end = m.end() + 200
        snippet = " ".join(text[start:end + 1].split())
        if snippet and snippet not in out:
            out.append(snippet[:300])
        if len(out) >= limit:
            break
    return out


def extract_features(soup):
    """Title, abstract, candidate code links (with context), availability
    sentences -- the same page regions detect_code_link looks at, minus the
    bibliography and ar5iv's own chrome."""
    work = copy.deepcopy(soup)
    for el in work.select("li.ltx_bibitem, .ar5iv-footer"):
        el.decompose()

    title_el = work.select_one("h1.ltx_title, h1.ltx_title_document")
    title = " ".join(title_el.get_text(" ").split()) if title_el else ""

    abs_el = work.select_one(".ltx_abstract")
    abstract = " ".join(abs_el.get_text(" ").split())[:2000] if abs_el else ""

    bib_hrefs = {a.get("href") for item in soup.select("li.ltx_bibitem")
                 for a in item.select("a[href]")}
    chrome_hrefs = {a.get("href") for a in soup.select(".ar5iv-footer a[href], a[class*='ar5iv'][href]")}
    links = []
    seen = set()
    for a in work.select("a[href]"):
        href = a.get("href") or ""
        if href in bib_hrefs or href in chrome_hrefs or href in seen:
            continue
        if CODE_HOST_RE.search(href):
            seen.add(href)
            parent = a.find_parent(["p", "div", "span", "li", "td"])
            ctx = " ".join((parent.get_text(" ") if parent else a.get_text(" ")).split())
            links.append((href, ctx[:300]))
        if len(links) >= 12:
            break

    full_text = work.get_text(" ")
    avail = sentences_around(full_text, CODE_AVAILABILITY_TEXT_RE)
    return title, abstract, links, avail


def build_sample(n, seed):
    affil = json.loads(AFFIL_FILE.read_text(encoding="utf-8"))
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    year_by_title = {norm_title(p.get("title")): p.get("year")
                     for p in papers if p.get("av_relevance") == "core"}
    core_titles = set(year_by_title)

    pool = []
    for key, v in affil.items():
        if not isinstance(v, dict):
            continue
        aid = v.get("arxiv_id")
        hcl = as_bool(v.get("has_code_link"))
        if not aid or hcl is None or key not in core_titles:
            continue
        pool.append({"key": key, "arxiv_id": aid, "heuristic": hcl,
                     "year": year_by_title.get(key)})

    rng = random.Random(seed)
    rng.shuffle(pool)
    by_flag = defaultdict(list)
    for row in pool:
        by_flag[row["heuristic"]].append(row)
    # Spread each stratum across years rather than taking the first n after a
    # plain shuffle (which can clump on whatever year dominates the corpus).
    sample = []
    for flag in (True, False):
        rows = sorted(by_flag[flag], key=lambda r: (r["year"] or 0))
        if len(rows) <= n:
            sample.extend(rows)
        else:
            step = len(rows) / n
            sample.extend(rows[int(i * step)] for i in range(n))
    rng.shuffle(sample)
    return sample


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=75, help="papers per stratum (True/False)")
    ap.add_argument("--model", default="qwen2.5:7b-instruct")
    ap.add_argument("--seed", type=int, default=20260828)
    args = ap.parse_args()

    sample = build_sample(args.n, args.seed)
    print(f"Sample: {len(sample)} papers "
          f"({sum(r['heuristic'] for r in sample)} flagged has-code, "
          f"{sum(not r['heuristic'] for r in sample)} not), model={args.model}")

    results = {}
    if OUT_FILE.exists():
        results = json.loads(OUT_FILE.read_text(encoding="utf-8")).get("papers", {})
        print(f"Resuming: {len(results)} already audited")

    fails = 0
    for i, row in enumerate(sample, 1):
        key = row["key"]
        if key in results:
            continue
        try:
            soup = fetch_ar5iv_page(row["arxiv_id"])
            title, abstract, links, avail = extract_features(soup)
            prompt = PROMPT_TEMPLATE.format(
                title=title or key,
                abstract=abstract or "(no abstract found)",
                links="\n".join(f"- {u} -- {c}" for u, c in links) or "(none)",
                sentences="\n".join(f"- {s}" for s in avail) or "(none)",
            )
            raw = call_ollama(args.model, prompt)
            verdict, url, evidence = parse_verdict(raw)
            if verdict is None:
                raise ValueError(f"unparseable model output: {raw[:160]!r}")
            results[key] = {
                "arxiv_id": row["arxiv_id"], "year": row["year"],
                "heuristic": row["heuristic"], "llm": verdict,
                "llm_url": url, "llm_evidence": evidence,
                "n_candidate_links": len(links), "n_avail_sentences": len(avail),
            }
            fails = 0
            mark = "  " if (verdict == "yes") == row["heuristic"] else "DIFF"
            print(f"  [{i}/{len(sample)}] {mark} heuristic={row['heuristic']!s:5} "
                  f"llm={verdict:7} {key[:60]}", flush=True)
        except Exception as e:
            fails += 1
            print(f"  [{i}/{len(sample)}] ERROR {key[:60]}: {e}", flush=True)
            if fails >= 12:
                print("Too many consecutive failures -- stopping; rerun to resume.", flush=True)
                break
            time.sleep(3)
            continue

        if i % 10 == 0:
            _write(results)
        time.sleep(0.5)

    _write(results)
    _report(results)


def _write(results):
    OUT_FILE.write_text(json.dumps({"papers": results}, ensure_ascii=False, indent=2),
                        encoding="utf-8", newline="\n")


def _report(results):
    rows = list(results.values())
    if not rows:
        print("No results yet.")
        return
    # LLM "unclear" is set aside from the precision/recall math -- reported
    # separately so it isn't silently folded into either "agrees" or "wrong".
    decided = [r for r in rows if r["llm"] in ("yes", "no")]
    unclear = len(rows) - len(decided)
    h_true = [r for r in decided if r["heuristic"]]
    h_false = [r for r in decided if not r["heuristic"]]
    fp = sum(1 for r in h_true if r["llm"] == "no")
    tp = sum(1 for r in h_true if r["llm"] == "yes")
    fn = sum(1 for r in h_false if r["llm"] == "yes")
    tn = sum(1 for r in h_false if r["llm"] == "no")
    print(f"\n=== Audit ({len(rows)} audited, {unclear} LLM-unclear set aside) ===")
    print(f"Heuristic flagged has-code, LLM agrees (yes):   {tp}")
    print(f"Heuristic flagged has-code, LLM disagrees (no): {fp}"
          + (f"   -> ~{fp / (tp + fp) * 100:.0f}% false-positive rate" if tp + fp else ""))
    print(f"Heuristic said no, LLM says yes (missed):       {fn}"
          + (f"   -> ~{fn / (fn + tn) * 100:.0f}% false-negative rate" if fn + tn else ""))
    print(f"Heuristic said no, LLM agrees (no):             {tn}")
    print(f"\nDisagreements written to {OUT_FILE} (filter for heuristic != (llm=='yes')).")


if __name__ == "__main__":
    main()
