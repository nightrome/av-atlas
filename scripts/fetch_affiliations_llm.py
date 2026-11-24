#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read author institutions off the first page of an arXiv PDF with a small LLM.

For AV papers that still have no institution but have an arXiv ID. Three
stages, so the slow part can run on another machine (night-desktop):

  --targets FILE   write the papers to look at (key, arXiv id, title, authors)
  --run FILE       for each target: download the PDF (one request per 3.5 s),
                   take the text of page 1 with pdftotext, ask `claude -p
                   --model haiku` for authors and institutions, check the
                   answer, store it. Needs only the standard library, pdftotext
                   and the claude CLI. Resumable: every paper that got an
                   answer is recorded as tried, so none is asked twice. A
                   download or CLI failure records nothing.
  --apply FILE     fold the checked answers into papers_full.json

An answer is kept only when it parses as JSON, names at least one author whose
last name is on the paper's known author list, and has institution strings of a
sensible length. Anything else is recorded as tried with the reason, and not
used. Results: data/affiliations_llm.json (--out).

Usage:
  python fetch_affiliations_llm.py --targets data/affiliations_llm_targets.json
  python fetch_affiliations_llm.py --run targets.json --out results.json --max-hours 6
  python fetch_affiliations_llm.py --apply data/affiliations_llm.json
Do not run --apply while another script is writing papers_full.json.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.request

PROMPT = ("Below is the first page of a research paper. List each author with their institution(s) as printed on "
          'the page. Answer with JSON only: [{"name": "...", "institutions": ["..."]}]. '
          "Use [] for institutions if none are printed.\n\n")
GAP = 3.5
MAX_CHARS = 6000
MIN_TEXT = 300


def norm_title(t):
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "", t)


def last_names(authors):
    if isinstance(authors, str):
        authors = re.split(r",|;| and ", authors)
    out = set()
    for a in authors or []:
        parts = re.findall(r"[^\W\d_]+", unicodedata.normalize("NFKD", str(a)).encode("ascii", "ignore").decode().lower())
        if parts:
            out.add(parts[-1])
    return out


def parse_answer(text):
    text = re.sub(r"^```(?:json)?|```$", "", (text or "").strip(), flags=re.M).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.S)
        try:
            data = json.loads(m.group(0)) if m else None
        except ValueError:
            data = None
    return data if isinstance(data, list) else None


def check_answer(data, known_authors):
    """(cleaned list or None, reason). Cleaned: [{"name", "affiliations"}]."""
    if data is None:
        return None, "not json"
    out = []
    for d in data:
        if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"].strip():
            continue
        insts = [i.strip() for i in (d.get("institutions") or []) if isinstance(i, str) and 3 <= len(i.strip()) <= 250]
        out.append({"name": d["name"].strip(), "affiliations": insts})
    if not out:
        return None, "no authors"
    known = last_names(known_authors)
    if known and not (last_names([d["name"] for d in out]) & known):
        return None, "authors do not match"
    if not any(d["affiliations"] for d in out):
        return None, "no institutions"
    return out, "ok"


def page_text(pdf_bytes):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.pdf")
        with open(path, "wb") as f:
            f.write(pdf_bytes)
        r = subprocess.run(["pdftotext", "-f", "1", "-l", "1", path, "-"], capture_output=True, text=True, timeout=60)
    return r.stdout[:MAX_CHARS]


def download(arxiv_id):
    req = urllib.request.Request(f"https://arxiv.org/pdf/{arxiv_id}", headers={"User-Agent": "research-script"})
    return urllib.request.urlopen(req, timeout=60).read()


def ask(text):
    r = subprocess.run(["claude", "-p", "--model", "haiku", PROMPT + text], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError(f"claude exited {r.returncode}: {r.stderr[:150]}")
    return r.stdout


def save(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def run(targets, state, out, max_seconds, sleep=time.sleep, clock=time.monotonic,
        download=download, ask=ask, page_text=page_text):
    started, fails = clock(), 0
    todo = [t for t in targets if t["key"] not in state["tried"]]
    print(f"{len(todo)} papers to look at, {len(state['tried'])} already tried", flush=True)
    for i, t in enumerate(todo, 1):
        if max_seconds and clock() - started > max_seconds:
            print("Time budget used up; stopping.", flush=True)
            break
        t0 = clock()
        try:
            text = page_text(download(t["arxiv_id"]))
            if len(text.strip()) < MIN_TEXT:
                state["tried"][t["key"]] = "no text"
            else:
                data, why = check_answer(parse_answer(ask(text)), t.get("authors"))
                state["tried"][t["key"]] = why
                if data:
                    state["results"][t["key"]] = data
            fails = 0
        except Exception as e:  # noqa: BLE001 -- nothing is recorded, the paper comes up again
            fails += 1
            print(f"  {t['arxiv_id']}: {str(e)[:100]}", flush=True)
            sleep(min(30 * 2 ** (fails - 1), 1800))
            if fails >= 8:
                print("Too many failures in a row; stopping.", flush=True)
                break
            continue
        if i % 20 == 0:
            save(out, state)
            ok = sum(v == "ok" for v in state["tried"].values())
            print(f"  {i}/{len(todo)} done, {ok} of {len(state['tried'])} tried gave institutions", flush=True)
        sleep(max(0, GAP - (clock() - t0)))
    save(out, state)
    return state


def make_targets(path):
    from fetch_abstracts_web import PAPERS_FILE
    from fetch_affiliations_openalex import has_affiliation
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    out = []
    for p in papers:
        if p.get("av_relevance") != "AV" or has_affiliation(p):
            continue
        m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", p.get("arxiv_url") or "", re.I)
        if m:
            authors = p.get("authors")
            out.append({"key": norm_title(p.get("title")), "arxiv_id": m.group(1), "title": p.get("title"),
                        "authors": authors if isinstance(authors, str) else list(authors or [])})
    out.sort(key=lambda t: t["arxiv_id"], reverse=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"{len(out)} targets written to {path}")


def apply(path):
    from atomic_write import write_json_atomic
    from fetch_abstracts_web import PAPERS_FILE
    from fetch_affiliations_openalex import has_affiliation, merge_details
    state = json.loads(open(path, encoding="utf-8").read())
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    n = 0
    for p in papers:
        if p.get("av_relevance") != "AV" or has_affiliation(p):
            continue
        found = state["results"].get(norm_title(p.get("title")))
        if not found:
            continue
        found = [{"name": d["name"], "affiliations": d["affiliations"], "countries": []} for d in found]
        detail, changed = merge_details(p.get("authors_detail"), found)
        if changed:
            p["authors_detail"] = detail
            p["authors_detail_source"] = p.get("authors_detail_source") or "pdf-llm"
            n += 1
    write_json_atomic(PAPERS_FILE, papers)
    print(f"Added institutions to {n} papers in papers_full.json.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--targets")
    ap.add_argument("--run")
    ap.add_argument("--apply")
    ap.add_argument("--out", default="affiliations_llm.json")
    ap.add_argument("--max-hours", type=float, default=None)
    args = ap.parse_args(argv)
    if args.targets:
        make_targets(args.targets)
    elif args.apply:
        apply(args.apply)
    elif args.run:
        targets = json.load(open(args.run, encoding="utf-8"))
        state = json.load(open(args.out, encoding="utf-8")) if os.path.exists(args.out) else {}
        state.setdefault("results", {})
        state.setdefault("tried", {})
        run(targets, state, args.out, (args.max_hours or 0) * 3600 or None)
    else:
        ap.error("one of --targets, --run, --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
