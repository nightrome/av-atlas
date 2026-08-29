#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM code-link classifier -- the source of truth for `has_code_link`.

Replaces fetch_affiliations_arxiv.py's regex `detect_code_link`, which
over-flagged: an audit (audit_code_links_llm.py) put its false-positive
rate around 40% -- dataset/leaderboard links on HuggingFace, third-party
baseline repos named in the body, and a loose "code ... available" text
match that also fires on "code is NOT available".

For every core, arXiv-sourced paper this re-fetches the ar5iv page, pulls
out the title, abstract, every candidate code URL with its surrounding
sentence, and any code-availability sentence, and asks a local Ollama model
the narrow question: does THIS paper release ITS OWN source code? The
verdict (yes / no / unclear + the URL + a short quote) is written to
data/code_links_llm.json, keyed by normalized title.

aggregate.py prefers this file's verdict over the regex whenever an entry
exists; the regex stays only as the fallback for papers not yet classified
here. "unclear" counts as not-yet-checked (excluded from the Insights
open-source percentages), same as a paper with no verdict at all.

Slow: ~10k papers x (one ar5iv fetch + one local inference). Meant to run
in periodic batches -- fully resumable, rerun to continue. Circuit-breaks
after too many consecutive failures (Ollama down, network out).

Usage:
  python classify_code_links_llm.py [--model qwen2.5:7b-instruct]
                                    [--limit N] [--redo-unclear]

  --limit         stop after N newly-classified papers this run (0 = all)
  --redo-unclear  re-ask papers previously marked "unclear" (e.g. after a
                  model upgrade); by default those are left as-is
"""
import argparse
import copy
import json
import re
import time
import urllib.request

from fetch_common import BASE
from fetch_affiliations_arxiv import (
    CODE_HOST_RE,
    CODE_AVAILABILITY_TEXT_RE,
    fetch_ar5iv_page,
)

PAPERS_FILE = BASE / "data" / "papers_full.json"
AFFIL_FILE = BASE / "data" / "affiliations_arxiv.json"
OUT_FILE = BASE / "data" / "code_links_llm.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
MAX_CONSECUTIVE_FAILURES = 15

PROMPT_TEMPLATE = """You are deciding whether a research paper releases ITS OWN source code.

"Its own code" means an implementation of THIS paper's method/experiments that the authors published. It does NOT count if:
- the link is to a dataset, a model checkpoint page, or a benchmark/leaderboard
- the link is to a third-party or baseline method's repository the paper merely uses or compares against
- the paper only says code is "not available" / "will be released" (nothing actually released yet)
- the only "code host" link is unrelated site chrome or documentation

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
    sentences -- the same page regions the regex detector looked at, minus
    the bibliography and ar5iv's own chrome."""
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
    links, seen = [], set()
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

    avail = sentences_around(work.get_text(" "), CODE_AVAILABILITY_TEXT_RE)
    return title, abstract, links, avail


def classify_one(model, arxiv_id, fallback_title):
    soup = fetch_ar5iv_page(arxiv_id)
    title, abstract, links, avail = extract_features(soup)
    prompt = PROMPT_TEMPLATE.format(
        title=title or fallback_title,
        abstract=abstract or "(no abstract found)",
        links="\n".join(f"- {u} -- {c}" for u, c in links) or "(none)",
        sentences="\n".join(f"- {s}" for s in avail) or "(none)",
    )
    raw = call_ollama(model, prompt)
    verdict, url, evidence = parse_verdict(raw)
    if verdict is None:
        raise ValueError(f"unparseable model output: {raw[:160]!r}")
    return {"verdict": verdict, "url": url, "evidence": evidence,
            "n_candidate_links": len(links), "n_avail_sentences": len(avail)}


def load_targets():
    """Core, arXiv-sourced papers with a known arXiv id -- the same set the
    regex detector runs over, so this is a like-for-like replacement."""
    affil = json.loads(AFFIL_FILE.read_text(encoding="utf-8"))
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    core = {norm_title(p.get("title")): p.get("title")
            for p in papers if p.get("av_relevance") == "core"}
    out = []
    for key, v in affil.items():
        if not isinstance(v, dict) or key not in core:
            continue
        aid = v.get("arxiv_id")
        if aid:
            out.append((key, aid, core[key]))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--redo-unclear", action="store_true")
    args = ap.parse_args()

    done = {}
    if OUT_FILE.exists():
        done = json.loads(OUT_FILE.read_text(encoding="utf-8"))

    targets = load_targets()
    skip = {k for k, r in done.items()
            if not (args.redo_unclear and r.get("verdict") == "unclear")}
    pending = [t for t in targets if t[0] not in skip]
    print(f"{len(targets)} core arXiv papers; {len(done)} already classified; "
          f"{len(pending)} to go this run (model={args.model})", flush=True)

    fails = 0
    n_new = 0
    for i, (key, aid, title) in enumerate(pending, 1):
        try:
            rec = classify_one(args.model, aid, title)
            rec["model"] = args.model
            rec["checked_at"] = time.strftime("%Y-%m-%d")
            done[key] = rec
            fails = 0
            n_new += 1
            if n_new % 25 == 0:
                _save(done)
            print(f"  [{i}/{len(pending)}] {rec['verdict']:7} {title[:64]}", flush=True)
        except Exception as e:
            fails += 1
            print(f"  [{i}/{len(pending)}] ERROR {title[:64]}: {e}", flush=True)
            if fails >= MAX_CONSECUTIVE_FAILURES:
                print("Too many consecutive failures -- stopping. Rerun to resume.", flush=True)
                break
            time.sleep(3)
            continue
        if args.limit and n_new >= args.limit:
            print(f"Hit --limit {args.limit}.", flush=True)
            break
        time.sleep(0.3)

    _save(done)
    counts = {}
    for r in done.values():
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    yes, no = counts.get("yes", 0), counts.get("no", 0)
    decided = yes + no
    print(f"\nTotal classified: {len(done)}  ({counts})")
    if decided:
        print(f"Share with own code (of decided): {yes / decided * 100:.1f}%  "
              f"({yes}/{decided}); {len(load_targets()) - len(done)} still unclassified")


def _save(done):
    # Atomic write (temp + replace): aggregate.py may read this file at any
    # moment during a concurrent build, and a half-written file would fail
    # its json.loads and abort the deploy.
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(done, ensure_ascii=False, indent=2),
                   encoding="utf-8", newline="\n")
    tmp.replace(OUT_FILE)


if __name__ == "__main__":
    main()
