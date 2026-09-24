#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stops build_public_site.py from publishing a corpus that shrank.

A handful of numbers are read off data/stats.json before aggregate.py
overwrites it (the baseline) and compared with the new stats.json after:
AV papers, AV papers with at least one institution, AV papers with an
abstract, total in-corpus citations, and venues covered. If any of them
drops by more than 3%, the build stops before anything is published. Growth
is always fine.

This is there for the failures that don't crash anything: an interrupted
crawler save that truncates papers_full.json, a venue file that no longer
parses, a build on a machine missing the big gitignored inputs. Each of
those used to produce a smaller but perfectly valid stats.json that went
out as if nothing had happened.

The baseline is kept in data/publish_gate_baseline.json until a build
passes, so a failed build can't make its own shrunk stats.json the next
build's baseline. When there's no local stats.json (a fresh clone, a CI
runner) the baseline comes from the live site's stats.json instead; when
that can't be fetched either, the build warns and carries on.

A real, intended drop (a matcher fix that removes false matches, say) goes
through with --allow-shrink on build_public_site.py or deploy.py.

Usage (normally called from build_public_site.py, not by hand):
    python publish_gate.py            # compare data/stats.json with the saved baseline
"""
import json
import sys
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATS_FILE = BASE / "data" / "stats.json"
BASELINE_FILE = BASE / "data" / "publish_gate_baseline.json"
PROD_STATS_URL = "https://nightrome.github.io/av-atlas/stats.json"
MAX_DROP = 0.03

METRICS = [
    ("av_papers", "AV papers"),
    ("av_with_institution", "AV papers with an institution"),
    ("av_with_abstract", "AV papers with an abstract"),
    ("in_corpus_citations", "in-corpus citations"),
    ("venues", "venues covered"),
]


def corpus_metrics(stats):
    """The gated numbers, from a parsed stats.json. A number the file
    doesn't carry (an older stats.json) is left out rather than set to 0."""
    papers = stats.get("all_papers") or []
    corpus = stats.get("corpus_stats") or {}
    metrics = {
        "av_papers": len(papers),
        "av_with_institution": sum(1 for p in papers if p.get("institutions")),
        "in_corpus_citations": sum(p.get("citations") or 0 for p in papers),
    }
    abstracts = (corpus.get("pipeline_stages") or {}).get("3_abstract")
    if abstracts is not None:
        metrics["av_with_abstract"] = abstracts
    if corpus.get("venue_coverage") is not None:
        metrics["venues"] = len(corpus["venue_coverage"])
    return metrics


def compare(old, new, max_drop=MAX_DROP):
    """One row per metric both sides have: (label, old, new, change, too_big)."""
    rows = []
    for key, label in METRICS:
        if key not in old or key not in new:
            continue
        before, after = old[key], new[key]
        change = (after - before) / before if before else 0.0
        rows.append((label, before, after, change, change < -max_drop))
    return rows


def format_rows(rows):
    return "\n".join(f"  {label:<32} {before:>9,} -> {after:>9,}  ({change:+.1%})"
                     f"{'  <-- dropped more than ' + format(MAX_DROP, '.0%') if bad else ''}"
                     for label, before, after, change, bad in rows)


def fetch_production_metrics(url=PROD_STATS_URL, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "av-atlas build (mailto:holger@it-caesar.com)"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return corpus_metrics(json.loads(resp.read().decode("utf-8")))


def save_baseline(stats_file=STATS_FILE, baseline_file=BASELINE_FILE, fetch=fetch_production_metrics):
    """Called before aggregate.py runs. Keeps an existing baseline: that one
    belongs to the last build that got through, and the stats.json on disk
    may be the shrunk output of a build that was stopped."""
    if baseline_file.exists():
        print(f"  keeping the baseline from an earlier build that didn't pass ({baseline_file.name})")
        return True
    if stats_file.exists():
        metrics, source = corpus_metrics(json.loads(stats_file.read_text(encoding="utf-8"))), str(stats_file.name)
    else:
        try:
            metrics, source = fetch(), PROD_STATS_URL
        except Exception as exc:
            print(f"  WARNING: no local stats.json and the live one couldn't be fetched ({exc}); "
                  "this build won't be checked for shrinking.")
            return False
    baseline_file.write_text(json.dumps({"source": source, "metrics": metrics}, indent=2) + "\n",
                             encoding="utf-8", newline="\n")
    print(f"  baseline saved from {source}")
    return True


def check(stats_file=STATS_FILE, baseline_file=BASELINE_FILE, allow_shrink=False):
    """Called after aggregate.py. Raises SystemExit if a number dropped too
    far (unless allow_shrink); clears the baseline once the build passes."""
    if not baseline_file.exists():
        print("  no baseline, nothing to compare against -- skipped")
        return
    baseline = json.loads(baseline_file.read_text(encoding="utf-8"))
    new = corpus_metrics(json.loads(stats_file.read_text(encoding="utf-8")))
    rows = compare(baseline["metrics"], new)
    print(f"  compared with {baseline.get('source')}:\n{format_rows(rows)}")
    if any(bad for *_, bad in rows):
        if not allow_shrink:
            raise SystemExit(
                "The new stats.json is smaller than the last one (see above), so nothing was published. "
                "If the drop is expected, rerun with --allow-shrink "
                "(python scripts/build_public_site.py --allow-shrink, or python scripts/deploy.py --allow-shrink).")
        print("  --allow-shrink given, publishing anyway")
    baseline_file.unlink()


if __name__ == "__main__":
    check(allow_shrink="--allow-shrink" in sys.argv[1:])
