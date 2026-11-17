#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Asks Claude Haiku whether the papers that are AV only through one passing
mention in the abstract really are about driving (see classify.py's
rests_on_one_abstract_mention). Those papers are demoted to non-AV unless
someone vouches for them; the verdicts written here are that vouching:
data/relevance_labels_llm_v3.json, same shape as the earlier local-LLM label
files, read by classify.load_llm_av_titles.

Needs ANTHROPIC_API_KEY (environment or av-atlas/.env). Resumable: titles
already in the file are skipped. Rate limits and overloads (429, 5xx) wait for
Retry-After or back off 20 s doubling to 10 min; a run stops after 8 failures
in a row so a dead key does not burn time.

Without an API key, use a machine where Claude Code is logged in: --export the
pending papers, run run_claude_cli_labels.py there (it calls `claude -p`), then
--import its results here.

Usage:
  python label_single_mention_llm.py             # label everything pending
  python label_single_mention_llm.py --limit 50  # try it on 50 papers
  python label_single_mention_llm.py --export pending.json
  python label_single_mention_llm.py --import results.json
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import classify as cl
from atomic_write import write_json_atomic
from fetch_common import BASE

OUT_FILE = cl.LLM_LABELS_V3_FILE
PAPERS_FILE = BASE / "data" / "papers_full.json"
MODEL = "claude-haiku-5-5"
BATCH = 20
URL = "https://api.anthropic.com/v1/messages"

PROMPT = """You label research papers. For each paper below decide:
"AV" if it is about autonomous or automated ROAD vehicles / driving: driving perception (BEV, LiDAR or camera
3D detection, occupancy for cars), HD maps, motion or trajectory prediction for driving, planning or control
for cars, traffic and ITS for automated driving, simulation or datasets for driving, driver assistance.
"non-AV" otherwise, including papers that only mention autonomous driving as one motivating application,
generic vision, robotics or ML methods, indoor robots, drones, ships, trains, medical work, crowds without vehicles.
Answer with one JSON object mapping each id to "AV" or "non-AV" and nothing else.

"""


def api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    env = BASE / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    return key


def ask(key, papers, sleep=time.sleep):
    body = PROMPT + "\n".join(
        f'[{i}] {p["title"]}\n{(p.get("abstract") or "")[:1500]}\n' for i, p in enumerate(papers))
    data = json.dumps({"model": MODEL, "max_tokens": 600,
                       "messages": [{"role": "user", "content": body}]}).encode()
    req = urllib.request.Request(URL, data=data, headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    backoff, fails = 20, 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                text = json.loads(resp.read())["content"][0]["text"]
            verdicts = json.loads(text[text.index("{"):text.rindex("}") + 1])
            return {int(k): v for k, v in verdicts.items() if v in ("AV", "non-AV")}
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"The API refused the key (HTTP {e.code}); check ANTHROPIC_API_KEY.")
            if e.code not in (429, 500, 502, 503, 504, 529):
                raise SystemExit(f"HTTP {e.code}: {e.read()[:300]!r}")
            try:
                wait = int(e.headers.get("Retry-After") or 0)
            except ValueError:
                wait = 0
            problem = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, KeyError, IndexError) as e:
            wait, problem = 0, type(e).__name__
        fails += 1
        if fails >= 8:
            raise SystemExit(f"Giving up after {fails} failures in a row ({problem}); run again to resume.")
        wait = min(max(wait, backoff), 600)
        print(f"  {problem}; waiting {wait}s", flush=True)
        sleep(wait)
        backoff = min(backoff * 2, 600)


def pending(papers, done):
    seen, out = set(), []
    for p in papers:
        key = cl.normalize_title(p.get("title"))
        # The stored av_relevance is not used: once demoted, these papers read non-AV.
        if (p.get("venue") not in cl.AV_FOCUSED_VENUES and key not in done and key not in seen
                and (p.get("abstract") or "").strip()
                and cl.rests_on_one_abstract_mention(p["title"], p["abstract"])
                and cl.classify_relevance(p["title"], p["abstract"]) == "AV"):
            seen.add(key)
            out.append(p)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--export", metavar="FILE", help="write the pending papers to FILE and stop")
    ap.add_argument("--import", dest="import_file", metavar="FILE",
                    help="merge verdicts ({normalizedTitle: 'AV'|'non-AV'}) from FILE and stop")
    args = ap.parse_args(argv)
    if args.export or args.import_file:
        labels = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
        papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
        if args.export:
            todo = pending(papers, set(labels))[:args.limit]
            write_json_atomic(args.export, [{"key": cl.normalize_title(p["title"]), "title": p["title"],
                                             "abstract": (p.get("abstract") or "")[:1500],
                                             "venue": p.get("venue")} for p in todo])
            print(f"Exported {len(todo)} papers to {args.export}")
            return 0
        titles = {cl.normalize_title(p.get("title")): p for p in papers}
        verdicts = json.loads(open(args.import_file, encoding="utf-8").read())
        n = 0
        for k, v in verdicts.items():
            if v in ("AV", "non-AV") and k in titles:
                labels[k] = {"title": titles[k]["title"], "pool": "single_abstract_mention", "label": v,
                             "venue": titles[k].get("venue"), "model": "claude-haiku via claude -p",
                             "labeled_at": datetime.now(timezone.utc).isoformat()}
                n += 1
        write_json_atomic(OUT_FILE, labels, indent=1)
        print(f"Imported {n} verdicts; {sum(1 for v in labels.values() if v['label'] == 'AV')} AV in total.")
        return 0
    key = api_key()
    if not key:
        raise SystemExit("No ANTHROPIC_API_KEY in the environment or av-atlas/.env.")
    labels = json.loads(OUT_FILE.read_text(encoding="utf-8")) if OUT_FILE.exists() else {}
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    todo = pending(papers, set(labels))[:args.limit]
    print(f"{len(todo)} papers to label", flush=True)
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        verdicts = ask(key, chunk)
        for j, p in enumerate(chunk):
            if j in verdicts:
                labels[cl.normalize_title(p["title"])] = {
                    "title": p["title"], "pool": "single_abstract_mention", "label": verdicts[j],
                    "venue": p.get("venue"), "model": MODEL,
                    "labeled_at": datetime.now(timezone.utc).isoformat()}
        write_json_atomic(OUT_FILE, labels, indent=1)
        print(f"  {min(i + BATCH, len(todo))}/{len(todo)} labelled, "
              f"{sum(1 for v in labels.values() if v['label'] == 'AV')} AV so far", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
