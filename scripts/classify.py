#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assigns a category (from the living taxonomy in av-atlas/data/categories.json)
and an av_relevance tag to each paper in av-atlas/data/enriched.json, writing
the result back in place.

This is a keyword-matching first pass, not real semantic classification --
title+abstract text is scored against each category's keyword list and the
best match wins. It's meant as a fast, free, rerunnable baseline; category
assignments should be spot-checked (and the taxonomy in categories.json
extended) as real content comes in, rather than trusted blindly. Papers that
don't match any category's keywords are left "uncategorized" rather than
forced into a catch-all bucket -- that's a signal the taxonomy needs a new
category, not that the paper doesn't have one.

av_relevance is judged independently of category: category keywords are
broad CV/robotics topic terms (e.g. "object detection", "segmentation")
that also match huge numbers of non-AV papers (medical imaging, generic
robotics, etc.) once the corpus is a full, unfiltered venue proceeding
rather than a pre-filtered AV citation crawl -- so category membership
alone is NOT used to decide relevance. Instead av_relevance requires an
explicit AV-specific phrase (below) to appear in the title/abstract.
Papers can be "core" and "uncategorized" (topic didn't match any category
but the paper is clearly about AVs) or "adjacent" with a category (a
general CV method paper that happens to be about e.g. segmentation but
isn't about driving).

Usage: python classify.py
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ENRICHED_FILE = BASE / "data" / "enriched.json"
CATEGORIES_FILE = BASE / "data" / "categories.json"
LLM_LABELS_FILE = BASE / "data" / "relevance_labels_llm.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_llm_core_titles():
    """Normalized titles the local LLM (see fetch_llm_relevance_labels.py)
    graded 'core'. Read-only second opinion, not a replacement for the
    keyword pass -- see av_weight_and_relevance for how it's combined."""
    if not LLM_LABELS_FILE.exists():
        return set()
    labels = json.loads(LLM_LABELS_FILE.read_text(encoding="utf-8"))
    return {key for key, v in labels.items() if v.get("label") == "core"}

# Phrases specific enough to AVs that their presence is real signal, unlike
# generic CV terms (object detection, segmentation, etc.) which also match
# plenty of non-AV papers once the corpus is full venue proceedings.
AV_RELEVANCE_TERMS = [
    "autonomous driving", "autonomous vehicle", "self-driving", "self driving",
    "driverless", "ego vehicle", "ego-vehicle", "driving scene", "adas",
    "advanced driver assistance", "on-road driving", "road scene understanding",
    "vehicle trajectory", "traffic scene", "driving policy", "driving behavior",
    "driving dataset", "v2x", "vehicle-to-vehicle", "vehicle-to-infrastructure",
    "roadside perception", "cooperative perception", "lane detection", "lane change",
    "nuscenes", "kitti", "waymo open dataset", "argoverse", "bdd100k", "carla simulator",
]

# Word-boundary matched, not plain substring -- caught in practice once
# ICLR (an optimizer-heavy ML venue) was added: "adas" as a bare substring
# matched "AdaShift" and "Adasum" (both optimizer names with nothing to do
# with Advanced Driver Assistance Systems), wrongly marking them "core".
# Multi-word phrases ("autonomous driving", "carla simulator", ...) were
# never at real risk of this -- only "adas" is short enough to collide -- but
# matching everything the same way is one rule to reason about instead of a
# per-term judgment call on which ones are "safe" as bare substrings.
#
# A trailing optional "s" before the closing \b -- without it, a term ending
# in a noun (most of them) only ever matched its exact singular form: "autonomous
# vehicle" never matched "autonomous vehicles" (a following word-character
# "s" means \b isn't a boundary there at all), silently missing any paper
# titled e.g. "Can Autonomous Vehicles Identify, Recover From, and Adapt to
# Distribution Shifts" -- confirmed on real data (ICML, ~980 titles newly
# caught once this shipped). Harmless no-op for terms with no natural plural
# (acronyms, hyphenated compounds, proper nouns like "nuscenes") since \b
# still requires a non-word character right after the optional "s".
AV_RELEVANCE_PATTERNS = [re.compile(r"\b" + re.escape(term) + r"s?\b") for term in AV_RELEVANCE_TERMS]

# A paper's own title choosing to foreground driving (e.g. "DriveLM",
# "DriveGPT4", "Driving with Graph VQA") is on its own as strong a signal as
# any AV_RELEVANCE_TERMS phrase -- these are single/short words too generic
# to trust in an *abstract* (see av_weight_and_relevance below), but a title
# is a much higher-precision place to find them: a CVPR/ICRA/etc. paper that
# puts "drive"/"driving" in its own title is essentially never about
# something else. Word-boundary matched so "data-driven" (a completely
# different, extremely common ML phrase) never matches "driven".
TITLE_STRONG_PATTERNS = [re.compile(p) for p in (
    r"\bdrive\b", r"\bdrives\b", r"\bdriving\b", r"\bdriver\b", r"\bdrivers\b",
)]

# Scope decision: this corpus is about learning-based approaches and
# software for autonomous vehicles (broadly interpreted), not automotive
# mechanical/hardware engineering -- chassis dynamics, suspension, actuator
# and powertrain hardware design belong to a different field even when the
# paper is nominally "about" a vehicle. Confirmed on real data this was
# actually happening: "Handling and Stability Integrated Control of AFS and
# DYC for Distributed Drive Electric Vehicle" was marked "core" purely
# because TITLE_STRONG_PATTERNS matched "Drive" inside "Distributed Drive"
# (a drivetrain configuration, nothing to do with driving a car).
#
# A paper matching one of these hardware/mechanical phrases is excluded
# UNLESS it also shows a learning-based or software signal (LEARNING_SIGNAL_
# PATTERNS, interpreted broadly on purpose) -- a paper that applies RL/deep
# learning to chassis control, for instance, still belongs in this corpus;
# one that's pure classical control-theory hardware design does not.
MECHANICAL_EXCLUSION_TERMS = [
    "chassis control", "vehicle dynamics control", "distributed drive electric vehicle",
    "active front steering", "direct yaw moment control", "afs and dyc", "suspension control",
    "in-wheel motor", "electro-hydraulic", "vehicle handling and stability", "tire force",
    "steering system design", "braking system design", "powertrain design", "torque vectoring",
    "anti-lock braking system", "traction control system", "yaw moment control",
    "hydraulic actuator", "vehicle stability control", "wheel torque distribution",
]
MECHANICAL_EXCLUSION_PATTERNS = [re.compile(r"\b" + re.escape(t) + r"\b") for t in MECHANICAL_EXCLUSION_TERMS]

LEARNING_SIGNAL_TERMS = [
    "learning", "neural network", "neural net", "deep learning", "reinforcement learning",
    "machine learning", "data-driven", "transformer", "policy learning", "imitation learning",
    "neural", "supervised", "unsupervised",
]
LEARNING_SIGNAL_PATTERNS = [re.compile(r"\b" + re.escape(t) + r"\b") for t in LEARNING_SIGNAL_TERMS]


def is_mechanical_hardware_only(title_l, abstract_l):
    text = f"{title_l} {abstract_l}"
    if not any(p.search(text) for p in MECHANICAL_EXCLUSION_PATTERNS):
        return False
    return not any(p.search(text) for p in LEARNING_SIGNAL_PATTERNS)


def score_category(text, keywords):
    return sum(text.count(kw) for kw in keywords)


# A local LLM (qwen2.5:7b-instruct, see fetch_llm_relevance_labels.py) graded
# a sample of papers "core"/"adjacent" against the same core-vs-adjacent
# definition used here, and scored 100% precision / 67% recall against 65
# hand-labeled papers: when it says core, it's right, but it's conservative
# and misses about a third of true core papers. That precision/recall shape
# is exactly what makes it useful as a *promotion* signal on top of the
# keyword pass (catch keyword misses) rather than a replacement for it
# (its recall gap would silently drop papers the keywords already catch).


def classify_relevance(title, abstract, llm_says_core=False):
    """Binary: is this paper about autonomous vehicles, yes or no. A paper is
    either "core" (its own title foregrounds AVs, or an AV-specific phrase --
    see AV_RELEVANCE_TERMS -- appears anywhere in title/abstract, or the LLM
    second-opinion agrees) or "adjacent" (no such evidence). There is no
    partial credit: a paper that mentions "autonomous driving" once in a list
    of applications counts exactly the same as one whose title is "... for
    Autonomous Driving" -- both get to say what they're about, and grading
    "how AV-focused" a paper really is is not something a keyword match can
    do reliably. This is still a keyword heuristic, not semantic
    understanding -- see Methodology for its limits.
    """
    title_l = (title or "").lower()
    abstract_l = (abstract or "").lower()

    if is_mechanical_hardware_only(title_l, abstract_l):
        return "adjacent"
    if any(p.search(title_l) for p in AV_RELEVANCE_PATTERNS):
        return "core"
    if any(p.search(title_l) for p in TITLE_STRONG_PATTERNS):
        return "core"
    if any(p.search(abstract_l) for p in AV_RELEVANCE_PATTERNS):
        return "core"
    if llm_says_core:
        return "core"
    return "adjacent"


def classify_paper(title, abstract, categories, llm_core_titles=frozenset(), known_dataset_titles=frozenset()):
    # A dataset/benchmark paper's own abstract is dominated by the TASKS its
    # data supports (detection, tracking, ...), not by "we introduce a
    # dataset" phrasing repeated often enough to outscore those -- confirmed
    # on real data: nuScenes, Argoverse, and the Waymo Open Dataset paper
    # were all landing in "tracking"/"object-detection" under pure keyword
    # scoring. These are the SAME papers already trusted as dataset-seed
    # papers on the Datasets page (categories.json's known_dataset_papers,
    # kept in sync with aggregate.py's DATASET_SEED_TITLES) -- forcing them
    # here keeps that page and this category consistent by construction,
    # not just by keyword luck.
    if normalize_title(title) in known_dataset_titles:
        return "dataset-benchmark-paper", classify_relevance(title, abstract, normalize_title(title) in llm_core_titles)

    text = f"{title} {abstract or ''}".lower()
    scores = [(score_category(text, [k.lower() for k in cat["keywords"]]), cat["id"])
              for cat in categories]
    scores.sort(reverse=True)
    category = "uncategorized"
    for best_score, best_id in scores:
        if best_score <= 0:
            break
        # A method paper that derives/releases a dataset as a byproduct of
        # its evaluation (e.g. "Sparsity Invariant CNNs", which derives a
        # depth dataset from KITTI to evaluate its own sparse-conv layer)
        # still trips "novel dataset" in the abstract, even though the
        # paper's own subject is the method, not the data -- confirmed on
        # real data (user-flagged). A genuine dataset paper says so in its
        # own title (nuScenes, KITTI-360, ...); gate the category on that
        # so an abstract-only mention falls through to the next-best
        # category instead of stealing the classification.
        if best_id == "dataset-benchmark-paper" and not re.search(r"\b(dataset|benchmark|corpus)\b", title.lower()):
            continue
        category = best_id
        break
    llm_says_core = normalize_title(title) in llm_core_titles
    relevance = classify_relevance(title, abstract, llm_says_core)
    return category, relevance


def main():
    taxonomy = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    categories = taxonomy["categories"]
    known_dataset_titles = frozenset(normalize_title(t) for t in taxonomy.get("known_dataset_papers", []))
    entries = json.loads(ENRICHED_FILE.read_text(encoding="utf-8"))
    llm_core_titles = load_llm_core_titles()

    counts = {}
    for e in entries:
        category, relevance = classify_paper(
            e.get("title", ""), e.get("abstract"), categories, llm_core_titles, known_dataset_titles)
        e["category"] = category
        e["av_relevance"] = relevance
        counts[category] = counts.get(category, 0) + 1

    ENRICHED_FILE.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Classified {len(entries)} papers")
    for cat_id, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:>3}  {cat_id}")


if __name__ == "__main__":
    main()
