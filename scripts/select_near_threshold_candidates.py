#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Picks a citation-prioritized sample of "adjacent" papers plausibly worth a
second, human look for AV relevance -- a targeted recall-improvement pass,
not a general classifier-training sample (select_labeling_candidates.py
already does that, and predates the current model-based classify_
relevance(); see its stale "av_weight" references). This is a triage tool:
spend a small hand-labeling budget where a miss is most likely real AND
most likely to be visible to someone (an author whose highly-cited paper
reads "not AV-relevant" on their own author page).

WHY NOT JUST "closest to the model's decision threshold"
----------------------------------------------------------
The first version of this script ranked every non-hard-scope-excluded
"adjacent" paper by classify.relevance_model_score() distance below
data/relevance_model.json's threshold. Checked against the real corpus,
that ranking is degenerate: threshold (-0.660) sits only ~0.022 above the
model's bare intercept (-0.683), so EVERY paper matching zero of the
model's ~70 vocab phrases scores exactly at the intercept and ties for
"closest" -- and, separately, matching even one of those phrases is
usually already enough to cross the gap and be called "core", so almost no
"adjacent" paper matches any of them at all (confirmed: 0 matches across
165,923 candidates on the real corpus). train_relevance_classifier.py's
threshold search deliberately picks the LOWEST threshold that holds target
precision, i.e. it already spends all the recall its known vocabulary can
safely buy -- so distance-to-threshold within that vocabulary mostly
measures nothing. What actually predicts a real miss, per the radar story
in classify.py's AV_RELEVANCE_TERMS comment (~60+ 4D-radar papers were
wrongly "adjacent" until "automotive radar" etc. were added), is AV
vocabulary the model doesn't have at all -- which by definition its own
score can't see.

SELECTION (see near_threshold_pool)
------------------------------------
1. av_relevance == "adjacent", title present, and not something classify.
   py's hard scope filters would exclude on their own (mechanical/
   hardware-only, non-road-platform) -- those are correctly adjacent per
   RUBRIC_relevance.md's "Hard adjacents"; relabeling them just re-confirms
   the classifier already got it right.
2. category in DRIVING_SPECIFIC_CATEGORIES -- categories.json's taxonomy
   is scored independently of av_relevance (classify.py's own docstring:
   "category membership alone is NOT used to decide relevance"), so a
   paper can win a category by keyword overlap alone. Restricting to the
   categories whose NAME already asserts a driving subject (end-to-end-
   driving, llm-vlm-driving, v2x-cooperative, platooning-cruise-control,
   driver-behavior-hmi, traffic-flow-management, vehicle-dynamics-
   powertrain) -- rather than every category, or none -- is the same
   "plausible-looking to a labeler" logic select_labeling_candidates.py's
   hard-negative pool used, aimed at the specific slice of "adjacent"
   where a real miss is most likely to live.
3. A soft vehicle/traffic word (WEAK_AV_TERMS -- "vehicle", "road",
   "driver", "pedestrian", "lane", ... deliberately excluded from the
   trained model corpus-wide for being too ambiguous, see train_relevance_
   classifier.py's WEAK_VOCAB comment) in title or abstract. Checked on
   real data: category alone still let through plenty of generically-
   categorized noise with no vehicle framing at all (a 3D human pose
   paper, a museum-exhibit VQA paper) that happened to win a driving-named
   category anyway. This cuts a ~74k pool to ~2.7k without needing the
   strict, corpus-wide-tuned model vocabulary.
4. With --use-openalex: look up each survivor's citation count via the
   OpenAlex API (free, no key needed) -- deliberately an EXTERNAL,
   broader-population count used ONLY to prioritize this one-off
   hand-labeling queue. Never written to papers_full.json, never
   displayed: the site's own citation_count() (aggregate.py) counts only
   the in-corpus reference-list graph (see DECISIONS.md's "Citations are
   counted only between papers in the corpus"), and fetch_ieee_openalex.py
   deliberately does NOT capture OpenAlex's cited_by_count for exactly
   that reason -- mixing an external count into anything the site ranks or
   shows caused a real user-reported bug before. Nothing here touches that
   path: this script's only output is its own throwaway candidates file,
   read by nothing else in the pipeline. Building the real in-corpus graph
   instead (build_citation_graph.py) means fetching and parsing thousands
   of PDFs -- far too slow for a one-off triage pass, and an outside
   signal is if anything more apt here anyway: a paper's real-world
   prominence is exactly what should catch a labeler's attention.
   OFF BY DEFAULT: api.openalex.org is denied by this environment's egress
   policy (confirmed via the agent-proxy status endpoint -- a hard
   "connect_rejected" on every request, not a rate limit or cert issue),
   so a session running under that policy should not turn this on; it
   exists for a checkout that can actually reach the network.
5. Without --use-openalex (the default): a citation-free fallback --
   stratified_select spreads --n-out evenly across whichever
   DRIVING_SPECIFIC_CATEGORIES survived filtering (a fair-share water-fill:
   every category gets an equal slice, and a category too small to fill
   its slice frees the remainder to the others) so one oversized category
   doesn't crowd out the rest (traffic-flow-management alone is ~40% of
   the raw pool), sorted newest-first within each category as the
   closest available proxy for "most likely to still be an open gap."
   With --use-openalex, instead keeps the most-cited survivors (confident
   title-match lookups first, then everything else by year).

classify.relevance_model_score() is still computed for every candidate and
shipped in the output (model_score/matched_terms/distance_to_threshold) --
not as a filter, just context: on the rare candidate where it did match
something, that's worth a labeler seeing.

Writes data/near_threshold_candidates.json:
  [{"id", "title", "abstract", "venue", "year", "category", "model_score",
    "threshold", "distance_to_threshold", "matched_terms",
    "external_citations"}]

Usage: python select_near_threshold_candidates.py [--n-out 200] [--pool-size 3000] [--use-openalex]
"""
import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import classify as cl
from fetch_common import fetch as _fetch, HEADERS

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
OUT_FILE = BASE / "data" / "near_threshold_candidates.json"
CONTACT_EMAIL = "holger@it-caesar.com"
SEED = 20260908  # fixed, so a pool-size cap truncates the same way on a rerun

# Categories (data/categories.json) whose NAME already asserts a driving
# subject, as opposed to a generic CV/ML method category (object-detection,
# segmentation, control, ...) that scores just as high on a paper about any
# other robot or domain. See the module docstring's point 2.
DRIVING_SPECIFIC_CATEGORIES = {
    "end-to-end-driving", "llm-vlm-driving", "v2x-cooperative",
    "platooning-cruise-control", "driver-behavior-hmi",
    "traffic-flow-management", "vehicle-dynamics-powertrain",
}

# Deliberately the ambiguous, corpus-wide-risky words train_relevance_
# classifier.py's WEAK_VOCAB comment keeps OUT of the trained model (they'd
# flood "core" applied blindly across the whole corpus). Fine, even useful,
# as a soft filter INSIDE the already-narrow DRIVING_SPECIFIC_CATEGORIES
# pool: here they separate genuine vehicle/traffic framing from a paper
# that just won a driving-named category on unrelated keyword overlap.
#
# "driving"/"drive"/"driven"/"drives" are deliberately NOT here, despite
# being the obvious first choice for a "driving" filter. Checked on real
# data: they were the single biggest source of noise in this pool by far
# (63/200 candidates matched "driven" ALONE, more than "vehicle" and
# "traffic" combined) -- almost entirely generic ML/robotics papers using
# "driven" as a suffix ("affordance-driven", "depth-vision-driven") or
# "drive"/"drives" as a plain verb ("this drives improved performance"),
# nothing to do with road vehicles. classify.py hits the same trap with
# TITLE_STRONG_PATTERNS and guards it there (_DATA_DRIVEN_RE) for a fixed
# handful of compounds; the compounds found here weren't on that fixed
# list, and this filter runs over the whole abstract besides the title, so
# dropping the word family entirely -- rather than special-casing every
# compound -- is the more robust fix for this use.
WEAK_AV_TERMS = [
    "vehicle", "vehicles", "traffic", "road", "roads", "roadway", "driver", "drivers",
    "pedestrian", "pedestrians", "cyclist", "cyclists", "lane", "lanes",
    "intersection", "intersections", "roundabout", "motorway",
    "freeway", "highway", "highways", "parking", "junction", "car", "cars",
    "truck", "trucks", "bus", "buses", "ego-car", "ego car",
]
# "collision" was here too and is deliberately gone: checked on real data,
# it was pure noise -- "collision-free path planning"/"collision
# detection"/"collision avoidance" are generic motion-planning phrases any
# robot paper uses (soft robots, manipulators, ...), not vehicle-specific
# (9/200 candidates in one run had NO other weak-term match, and every one
# was a non-vehicle robotics paper). Real collision-relevant AV phrases
# ("forward collision warning", "collision warning system") are already in
# classify.py's AV_RELEVANCE_TERMS, which is specific enough to be a
# keyword-floor phrase outright, unlike bare "collision" here.
WEAK_AV_PATTERNS = [re.compile(r"\b" + re.escape(t) + r"\b", re.I) for t in WEAK_AV_TERMS]


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def is_hard_scope_adjacent(title, abstract):
    title_l = (title or "").lower()
    abstract_l = (abstract or "").lower()
    if cl.is_mechanical_hardware_only(title_l, abstract_l):
        return True
    return any(p.search(title_l) for p in cl.OFF_SCOPE_TITLE_PATTERNS)


def has_weak_av_signal(title):
    """TITLE only, not the abstract. Checked on real data: an abstract-wide
    check let through plenty of papers whose actual subject is a drone, a
    ship, or a robot arm and which mention "vehicle" only in passing (an
    aerial-VLN paper's own abstract saying "unmanned aerial vehicles", a
    marine-craft paper saying "unmanned surface vehicle") -- exactly the
    comparison-domain mentions classify.py's own OFF_SCOPE_TITLE_PATTERNS
    comment already flags as routine and deliberately does NOT title-gate
    on. Same fix in the same spirit here: a paper's own title states its
    subject; its abstract mentions everything it touches (the same
    title-states-it philosophy classify_paper's category tiebreak comment
    uses)."""
    return any(p.search(title or "") for p in WEAK_AV_PATTERNS)


def score_and_match(title, abstract):
    """classify.relevance_model_score(), plus which vocab phrases actually
    fired -- context for the labeling UI, not a filter (see module
    docstring for why distance-to-threshold alone doesn't separate real
    near-misses from zero-evidence papers on this corpus)."""
    m = cl.RELEVANCE_MODEL
    t, a = title or "", abstract or ""
    score = m["intercept"]
    title_terms, abstract_terms = [], []
    for term, pat in m["_patterns"]:
        if m["title_coef"].get(term) and pat.search(t):
            score += m["title_coef"][term]
            title_terms.append(term)
        if m["abstract_coef"].get(term) and pat.search(a):
            score += m["abstract_coef"][term]
            abstract_terms.append(term)
    title_strong = False
    if m.get("title_strong_coef"):
        clean = cl._DATA_DRIVEN_RE.sub(" ", t.lower())
        if any(p.search(clean) for p in cl.TITLE_STRONG_PATTERNS):
            score += m["title_strong_coef"]
            title_strong = True
    return score, sorted(set(title_terms) | set(abstract_terms))


def near_threshold_pool(papers, pool_size):
    if cl.RELEVANCE_MODEL is None:
        sys.exit("data/relevance_model.json is missing -- run train_relevance_classifier.py first.")

    pool = []
    for p in papers:
        if p.get("av_relevance") != "adjacent":
            continue
        if p.get("category") not in DRIVING_SPECIFIC_CATEGORIES:
            continue
        title, abstract = p.get("title"), p.get("abstract")
        if not title or is_hard_scope_adjacent(title, abstract):
            continue
        if not has_weak_av_signal(title):
            continue
        pool.append(p)

    # A prefix of pool (file/venue order) would silently favor whichever
    # venue happens to be processed first -- shuffle before capping so a
    # --pool-size below the natural pool size is still a fair sample.
    rng = random.Random(SEED)
    rng.shuffle(pool)
    return pool[:pool_size]


def fetch_json(url, max_retries=4):
    # Same exponential-backoff-on-429 tuning as fetch_ieee_openalex.py --
    # OpenAlex rate-limits sustained use. A proxy policy denial (a "Tunnel
    # connection failed: 403/407" URLError -- confirmed the actual failure
    # mode for api.openalex.org in this environment via
    # $HTTPS_PROXY/__agentproxy/status) is NOT retried: /root/.ccr/README.md
    # is explicit that a 403/407 from the proxy is an organization egress
    # decision, not a transient fault -- "do not retry or route around it."
    # Retrying it anyway was the real reason the first version of this
    # lookup projected to over an hour: every one of ~2600 calls burned a
    # full 5+10+20s backoff before failing regardless.
    delay = 5.0
    for attempt in range(max_retries):
        try:
            return json.loads(_fetch(url, timeout=20, headers=HEADERS))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        except urllib.error.URLError as e:
            if "403" in str(e.reason) or "407" in str(e.reason):
                raise
            if attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise


def openalex_citations(title):
    """(count, matched) -- best-effort: None/False on anything short of a
    confident normalized-title match, never a guess."""
    params = urllib.parse.urlencode({
        "filter": f"title.search:{title}", "per-page": 1, "mailto": CONTACT_EMAIL,
    })
    try:
        data = fetch_json(f"https://api.openalex.org/works?{params}")
    except Exception:
        return None, False
    results = data.get("results") or []
    if not results:
        return None, False
    hit = results[0]
    if normalize_title(hit.get("title")) != normalize_title(title):
        return None, False
    return hit.get("cited_by_count") or 0, True


def build_row(p, citations=None, matched=False):
    score, matched_terms = score_and_match(p.get("title"), p.get("abstract"))
    return matched, {
        "id": normalize_title(p["title"]),
        "title": p.get("title"),
        "abstract": p.get("abstract"),
        "venue": p.get("venue"),
        "year": p.get("year"),
        "category": p.get("category"),
        "model_score": round(score, 4),
        "threshold": cl.RELEVANCE_MODEL["threshold"],
        "distance_to_threshold": round(cl.RELEVANCE_MODEL["threshold"] - score, 4),
        "matched_terms": matched_terms,
        "external_citations": citations,
    }


def stratified_select(candidates, n_out):
    """Citation-free fallback ranking (see module docstring point 5): an
    equal-share water-fill across DRIVING_SPECIFIC_CATEGORIES so the
    ~40%-of-the-pool traffic-flow-management category can't crowd out the
    other six, newest-first within each as the closest available proxy for
    "an open gap worth relabeling" without a citation signal."""
    by_cat = {}
    for c in candidates:
        by_cat.setdefault(c["category"], []).append(c)
    for rows in by_cat.values():
        rows.sort(key=lambda c: -(c["year"] or 0))

    quota = {cat: 0 for cat in by_cat}
    active = set(by_cat)
    remaining = n_out
    while remaining > 0 and active:
        share = max(1, remaining // len(active))
        progressed = False
        for cat in list(active):
            avail = len(by_cat[cat]) - quota[cat]
            if avail <= 0:
                active.discard(cat)
                continue
            take = min(share, avail, remaining)
            quota[cat] += take
            remaining -= take
            progressed = True
            if remaining <= 0:
                break
        if not progressed:
            break

    selected = []
    for cat, q in quota.items():
        selected.extend(by_cat[cat][:q])
    return selected, quota


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-out", type=int, default=200)
    ap.add_argument("--pool-size", type=int, default=3000,
                     help="cap on how many plausible candidates to consider")
    ap.add_argument("--use-openalex", action="store_true",
                     help="rank by OpenAlex citation count instead of the stratified/recency fallback -- "
                          "only for a checkout that isn't behind an egress policy blocking api.openalex.org")
    args = ap.parse_args()

    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    pool = near_threshold_pool(papers, args.pool_size)
    print(f"candidate pool: {len(pool)} adjacent papers in a driving-named category, "
          f"with vehicle/traffic framing, not hard-scope-excluded")

    if args.use_openalex:
        # Independent, I/O-bound requests -- a thread pool, not a smaller
        # sample, is the fix for OpenAlex's per-call latency (fetch_json's
        # own backoff still handles real 429s per call). ~16x concurrency
        # is still one identified (mailto=) polite-pool client.
        candidates, matched, unmatched = [], 0, 0
        with ThreadPoolExecutor(max_workers=16) as pool_exec:
            def lookup(p):
                citations, ok = openalex_citations(p["title"])
                return build_row(p, citations, ok)
            for i, (ok, row) in enumerate(pool_exec.map(lookup, pool)):
                candidates.append(row)
                matched += ok
                unmatched += not ok
                if (i + 1) % 200 == 0:
                    print(f"  looked up {i + 1}/{len(pool)} ({matched} matched, {unmatched} unmatched)")
        # Unmatched papers (lookup failed or ambiguous) sort after every
        # matched one, by year as a weak fallback.
        candidates.sort(key=lambda c: (
            c["external_citations"] is None,
            -(c["external_citations"] or 0),
            -(c["year"] or 0),
        ))
        kept = candidates[:args.n_out]
    else:
        candidates = [build_row(p)[1] for p in pool]
        kept, quota = stratified_select(candidates, args.n_out)
        print("stratified allocation: " + ", ".join(f"{cat}={q}" for cat, q in sorted(quota.items())))

    OUT_FILE.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"\nwrote {len(kept)} candidates to {OUT_FILE}")
    if args.use_openalex:
        print(f"  ({matched} OpenAlex matches, {unmatched} unmatched of {len(pool)} looked up)")
    if kept:
        cited = [c["external_citations"] for c in kept if c["external_citations"] is not None]
        if cited:
            print(f"  external citations in kept set: max={max(cited)}, min={min(cited)}")
        with_model_signal = sum(1 for c in kept if c["matched_terms"])
        print(f"  {with_model_signal}/{len(kept)} also matched a relevance-model phrase (shown, not used to rank)")


if __name__ == "__main__":
    main()
