#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Labels the papers in a pending file by calling `claude -p --model haiku`, for a
machine where Claude Code is logged in and no API key exists. Standard library
only, so it runs anywhere with Python 3. See label_single_mention_llm.py,
which writes the pending file (--export) and reads the results (--import).

  python3 run_claude_cli_labels.py pending.json results.json

Resumable: keys already in results.json are skipped. A failed call (a usage
limit, an overload, a bad answer) backs off 60 s doubling up to 1 h and tries
the same batch again; after 12 failures in a row it stops, and a rerun resumes.
"""
import json
import subprocess
import sys
import time

BATCH = 20
PROMPT = """You label research papers. For each paper below decide:
"AV" if it is about autonomous or automated ROAD vehicles / driving: driving perception (BEV, LiDAR or camera
3D detection, occupancy for cars), HD maps, motion or trajectory prediction for driving, planning or control
for cars, traffic and ITS for automated driving, simulation or datasets for driving, driver assistance.
"non-AV" otherwise, including papers that only mention autonomous driving as one motivating application,
generic vision, robotics or ML methods, indoor robots, drones, ships, trains, medical work, crowds without vehicles.
Answer with one JSON object mapping each number to "AV" or "non-AV" and nothing else.

"""


def ask(batch):
    body = PROMPT + "\n".join(f'[{i}] {p["title"]}\n{p["abstract"]}\n' for i, p in enumerate(batch))
    r = subprocess.run(["claude", "-p", "--model", "haiku"], input=body, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200] or f"exit {r.returncode}")
    text = r.stdout
    verdicts = json.loads(text[text.index("{"):text.rindex("}") + 1])
    out = {int(k): v for k, v in verdicts.items() if v in ("AV", "non-AV")}
    if len(out) < len(batch):
        raise RuntimeError(f"only {len(out)} of {len(batch)} verdicts")
    return out


def main(pending_file, results_file):
    pending = json.load(open(pending_file, encoding="utf-8"))
    try:
        results = json.load(open(results_file, encoding="utf-8"))
    except FileNotFoundError:
        results = {}
    todo = [p for p in pending if p["key"] not in results]
    print(f"{len(todo)} papers to label", flush=True)
    backoff, fails = 60, 0
    i = 0
    while i < len(todo):
        batch = todo[i:i + BATCH]
        try:
            verdicts = ask(batch)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired) as e:
            fails += 1
            if fails >= 12:
                print(f"Stopping after {fails} failures in a row ({e}); rerun to resume.", flush=True)
                return 1
            print(f"  {e}; waiting {backoff}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 3600)
            continue
        fails, backoff = 0, 60
        for j, p in enumerate(batch):
            results[p["key"]] = verdicts[j]
        json.dump(results, open(results_file, "w", encoding="utf-8"))
        i += BATCH
        print(f"  {min(i, len(todo))}/{len(todo)} labelled", flush=True)
    print("Finished.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
