#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assigns a category (from the living taxonomy in av-atlas/data/categories.json)
and an av_relevance tag to a paper. A module, not a standalone script --
merge_corpus.py imports classify_paper() and applies it to every paper in
papers_full.json; nothing here reads or writes a file of its own.

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
"""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
CATEGORIES_FILE = BASE / "data" / "categories.json"
LLM_LABELS_FILE = BASE / "data" / "relevance_labels_llm.json"
LLM_LABELS_V2_FILE = BASE / "data" / "relevance_labels_llm_v2.json"
RELEVANCE_MODEL_FILE = BASE / "data" / "relevance_model.json"


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def load_llm_core_titles():
    """Normalized titles the local LLM (see fetch_llm_relevance_labels.py and
    ..._v2.py) graded 'core'. Read-only second opinion, folded in as a
    promotion signal -- see classify_relevance."""
    titles = set()
    for f in (LLM_LABELS_FILE, LLM_LABELS_V2_FILE):
        if f.exists():
            labels = json.loads(f.read_text(encoding="utf-8"))
            titles |= {key for key, v in labels.items() if v.get("label") == "core"}
    return titles

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
    # User-flagged real miss: "A VT-HMM-Based Framework for Countdown Timer
    # Traffic Light State Estimation" -- unambiguously about real-world
    # driving infrastructure (unlike generic CV terms, a paper about traffic
    # lights/signs is essentially never about anything else).
    "traffic light", "traffic sign",
    # Synonyms the original list simply didn't have. On a full venue corpus
    # (esp. the DBLP title-only IV/ITSC/T-ITS papers with no abstract) these
    # are where core recall was being lost -- every one is as AV-specific as
    # "self-driving". Kept as literal phrases, still word-boundary matched.
    "automated driving", "automated vehicle", "automated driving system",
    "highly automated driving", "connected vehicle", "connected and automated vehicle",
    "connected automated vehicle", "connected and autonomous vehicle",
    "adaptive cruise control", "cooperative adaptive cruise control",
    "automated valet parking", "car-following", "car following", "vehicle platoon",
    "platooning", "platoon control", "truck platoon", "on-ramp merging", "ramp merging",
    "highway merging", "autonomous racing", "autonomous race car",
    "forward collision warning", "collision warning system", "autonomous emergency braking",
    "automatic emergency braking", "driving automation", "end-to-end driving",
    "end-to-end autonomous driving", "drivable area", "drivable region",
    "free-space detection", "freespace detection", "vulnerable road user",
    "lane keeping", "lane-keeping", "lane changing", "lane-changing", "lane departure",
    "driver assistance system", "advanced driver-assistance", "naturalistic driving",
    # Radar-perception vocabulary. "automotive radar", "4D radar", "3+1D
    # radar" and radar-camera fusion are coined by and used near-exclusively
    # in the driving-perception literature -- as AV-specific as "adaptive
    # cruise control". Without them a signature AV subfield (4D-radar object
    # detection / odometry / scene flow, ~60+ papers here) fell through to
    # "adjacent" whenever the abstract didn't also happen to say "autonomous
    # driving" -- user-flagged via Andras Palffy's papers (RaDelft detector,
    # 4D-RaDiff, CLRNet were all wrongly "adjacent"). "4d radar" also matches
    # a handful of radar-based human-pose/gait papers; that's a small,
    # accepted cost for the recall gain (the aerial-nav ones are already
    # held out by OFF_SCOPE_TITLE_TERMS).
    "automotive radar", "4d radar", "4d imaging radar", "3+1d radar",
    "3d+1d radar", "radar odometry", "radar-camera fusion", "radar camera fusion",
    "radar scene flow",
    # Pedestrian-crossing behaviour/intention -- a "pedestrian crossing" or a
    # "crossing pedestrian" is a driving-scene concept (89 title matches in
    # this corpus, every one about crossing-intention prediction for
    # driving). Catches the older abstract-less IV/ITSC papers that only say
    # it in the title. "pedestrian intention" is already an AV_TITLE_ONLY
    # term; these two are specific enough to trust anywhere.
    "pedestrian crossing", "crossing pedestrian",
]

# Weaker on their own: high-precision only when they appear in the paper's
# own TITLE (a proceedings title that says "Intelligent Vehicle" is about
# one; an abstract that mentions it in passing is not). Fallback-path only.
AV_TITLE_ONLY_TERMS = [
    "intelligent vehicle", "pedestrian intention", "highway driving", "urban driving",
    "cooperative driving", "driving simulator", "takeover request",
]

# Non-road-vehicle platforms this corpus is explicitly NOT about. A promotion
# candidate whose own TITLE says it is about one of these is kept "adjacent"
# regardless of any AV phrase also present ("autonomous underwater vehicle"
# matches "autonomous ... vehicle" but is not this corpus). Title-only on
# purpose: abstracts routinely name drones/marine/etc. as comparison domains
# in papers that are themselves about driving.
OFF_SCOPE_TITLE_TERMS = [
    "unmanned aerial", "aerial vehicle", "aerial robot", "uav", "uas", "quadrotor",
    "quadcopter", "hexacopter", "multirotor", "drone", "fixed-wing", "evtol", "vtol",
    "micro air vehicle", "urban air mobility", "air traffic", "aircraft", "airborne",
    "underwater vehicle", "auv", "uuv", "rov", "surface vehicle", "usv", "marine vehicle",
    "marine robot", "maritime", "vessel", "unmanned ship", "spacecraft", "satellite",
    "planetary rover", "lunar", "martian", "legged robot", "quadruped", "quadrupedal",
    "hexapod", "bipedal", "humanoid", "exoskeleton", "prosthe", "manipulator",
    "robotic arm", "robot arm", "grasping", "gripper", "drone racing", "in-hand",
    # not road vehicles either: agricultural / field robots, and the
    # "self-driving laboratory" (materials-science lab automation) that
    # collides with "self-driving".
    "agricultur", "weed control", "weeding", "crop row", "orchard", "greenhouse",
    "harvesting robot", "livestock", "self-driving laboratory", "self-driving laboratories",
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

AV_TITLE_ONLY_PATTERNS = [re.compile(r"\b" + re.escape(t) + r"s?\b") for t in AV_TITLE_ONLY_TERMS]
OFF_SCOPE_TITLE_PATTERNS = [re.compile(r"\b" + re.escape(t) + r"s?\b") for t in OFF_SCOPE_TITLE_TERMS]


def _load_relevance_model():
    """The trained per-phrase weights + threshold from
    train_relevance_classifier.py, if present. Optional: when the file is
    absent, classify_relevance falls back to the expanded keyword-any pass
    below, so the pipeline still runs on a fresh checkout without a model."""
    if not RELEVANCE_MODEL_FILE.exists():
        return None
    try:
        m = json.loads(RELEVANCE_MODEL_FILE.read_text(encoding="utf-8"))
        m["_patterns"] = [(t, re.compile(r"\b" + re.escape(t) + r"s?\b", re.I)) for t in m["vocab"]]
        for req in ("title_coef", "abstract_coef", "intercept", "threshold"):
            if req not in m:
                return None
        return m
    except Exception:
        return None


RELEVANCE_MODEL = _load_relevance_model()
_DATA_DRIVEN_RE = re.compile(r"data[- ]driven|goal[- ]driven|model[- ]driven|event[- ]driven", re.I)


def relevance_model_score(title, abstract):
    """Linear score from RELEVANCE_MODEL: intercept + per-phrase weights for
    phrases present in the title and (separately) the abstract, + a bonus for
    a standalone driving word in the title. 'core' iff score >= threshold."""
    m = RELEVANCE_MODEL
    t = title or ""
    a = abstract or ""
    s = m["intercept"]
    tc = m["title_coef"]
    ac = m["abstract_coef"]
    for term, pat in m["_patterns"]:
        if tc.get(term) and pat.search(t):
            s += tc[term]
        if ac.get(term) and pat.search(a):
            s += ac[term]
    if m.get("title_strong_coef"):
        clean = _DATA_DRIVEN_RE.sub(" ", t.lower())  # TITLE_STRONG_PATTERNS are case-sensitive
        if any(p.search(clean) for p in TITLE_STRONG_PATTERNS):
            s += m["title_strong_coef"]
    return s

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
    """Binary: is this paper about autonomous *road* vehicles, yes or no.

    Layered, each layer only able to push one way:

    1. Hard scope pre-filters -> "adjacent" only: the mechanical/hardware-only
       exclusion, and the non-road-vehicle "off-scope" title guard (aerial /
       underwater / legged / manipulation / spacecraft). Scope definitions,
       not things to learn from noisy labels.
    2. Keyword floor -> "core": an explicit AV-specific phrase
       (AV_RELEVANCE_TERMS) anywhere in title/abstract, a standalone driving
       word in the title, or a title-only phrase in the title. This is the
       historical behavior and it is a *floor* -- the model below can add to
       it but never overrides it, so the obvious hits can't regress.
    3. Trained scorer (data/relevance_model.json) -> "core": learned
       per-phrase weights (separate weight for a phrase in the title vs the
       abstract) + a threshold, from the hand labels + the two local-LLM
       passes. This is what makes it "more than counting" -- it catches
       papers where several weak-ish signals add up, and it is trained with
       negative weights on phrases that over-fire ("driving dataset",
       "onboard", ...). Only consulted for papers the keyword floor didn't
       already call core, so a mis-weighting can't flood core corpus-wide.
    4. Local-LLM "core" verdict -> "core": individually-vetted promotions for
       the generically-titled AV papers (no AV phrase, no abstract) that no
       keyword or weight scheme can see. See fetch_llm_relevance_labels*.py.
    """
    title_l = (title or "").lower()
    abstract_l = (abstract or "").lower()

    if is_mechanical_hardware_only(title_l, abstract_l):
        return "adjacent"
    if any(p.search(title_l) for p in OFF_SCOPE_TITLE_PATTERNS):
        return "adjacent"

    # 2. keyword floor (never regressed by the model)
    if any(p.search(title_l) or p.search(abstract_l) for p in AV_RELEVANCE_PATTERNS):
        return "core"
    if any(p.search(title_l) for p in TITLE_STRONG_PATTERNS):
        return "core"
    if any(p.search(title_l) for p in AV_TITLE_ONLY_PATTERNS):
        return "core"

    # 3. trained scorer, for keyword-floor-negative papers only
    if RELEVANCE_MODEL is not None and \
            relevance_model_score(title, abstract) >= RELEVANCE_MODEL["threshold"]:
        return "core"

    # 4. LLM second opinion
    if llm_says_core:
        return "core"
    return "adjacent"


# Does this title say the paper PRESENTS a dataset/benchmark, as opposed to
# merely using or reviewing one?
#
# The Datasets category used to be gated the other way round: keyword scoring
# had to rank it first, and the title check could only veto. In practice the
# scoring almost never ranked it first, because a dataset paper's title and
# abstract are dominated by the tasks its data supports -- so the category
# held 31 papers out of a 25k corpus, while 755 core papers said "dataset",
# "benchmark" or "corpus" in their own titles. A field where dataset papers
# are among the most-cited work in it cannot plausibly have 31 of them, and
# the ones that did land there scored an implausible 377 citations/paper,
# because the bucket was effectively a hand-curated list of the famous ones.
#
# So the title check is now decisive rather than advisory: announcing a
# dataset in the title is a direct statement about what the paper IS, and a
# stronger signal than any count of task words. STRONG matches the shapes a
# dataset paper's title actually takes ("NAME: A ... Dataset for ...", "...:
# Benchmark and Baseline", a title ending in the word) and overrides the
# negatives; NEGATIVE catches the "we used one" and "we reviewed them"
# phrasings. Measured on the real corpus: 838 accepted, 40 rejected of the
# titles containing the word, with the rejects being surveys/reviews and
# papers evaluating on someone else's data.
_DS_WORD = r"(?:datasets?|benchmarks?|corpus|corpora)"
DATASET_TITLE_STRONG = [re.compile(p, re.I) for p in (
    rf":\s*(?:a|an|the)\s[^:]{{0,90}}\b{_DS_WORD}\b",
    rf":\s*{_DS_WORD}\b",
    rf"\b{_DS_WORD}\s*(?:\(.*\))?\s*$",
)]
# "We evaluated on someone else's data." Checked BEFORE
# DATASET_TITLE_STRONG, because a title like "Multi-Object Tracking on the
# KITTI Dataset" both ends in the word (which STRONG treats as presenting
# one) and is unambiguously a usage phrase -- the ending must not rescue it.
DATASET_TITLE_USES_ONE = re.compile(
    rf"\b(?:on|using|use\s+of|with|from|over|against|based\s+on|trained\s+on|evaluated\s+on"
    rf"|pre-?training\s+with)\s+(?:the\s+|a\s+|an\s+|its\s+|open\s+|public\s+)?"
    rf"(?:\S+\s+){{0,4}}{_DS_WORD}\b", re.I)

# Weaker signals that a title is about datasets without presenting one.
# STRONG can override these, since "NAME: A ... Dataset for ..." is
# presenting one whatever else the title also says.
DATASET_TITLE_NEGATIVE = [re.compile(p, re.I) for p in (
    rf"\bbenchmark(?:ing)?\s+(?:study|studies|analys[ie]s|evaluation|results?|comparison)\b",
    rf"\b{_DS_WORD}[- ]centric\b",
    rf"\b{_DS_WORD}\s+(?:distillation|pruning|selection|summariz\w+|augmentation"
    rf"|compression|cleaning|labell?ing|annotation)\b",
    rf"\b(?:extraction|selection|augmentation|distillation|pruning|generation|synthesis"
    rf"|compression|profiling)\s+(?:from|of)\s+.{{0,30}}\b{_DS_WORD}\b",
    r"\b(?:survey|review|overview|biases|toolsets?)\b",
)]


def presents_dataset(title):
    t = title or ""
    if not re.search(rf"\b{_DS_WORD}\b", t, re.I):
        return False
    if DATASET_TITLE_USES_ONE.search(t):
        return False
    if any(p.search(t) for p in DATASET_TITLE_STRONG):
        return True
    return not any(p.search(t) for p in DATASET_TITLE_NEGATIVE)


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
    if normalize_title(title) in known_dataset_titles or presents_dataset(title):
        return "dataset-benchmark-paper", classify_relevance(title, abstract, normalize_title(title) in llm_core_titles)

    text = f"{title} {abstract or ''}".lower()
    title_l = (title or "").lower()
    # Ties are common -- two or three categories matching twice each is the
    # normal case for a paper that touches several topics -- so how a tie is
    # broken decides a lot of the taxonomy.
    #
    # It used to be broken by sorting (score, id) descending, i.e. reverse
    # alphabetical order of the category slug, which is arbitrary and biased:
    # "simulation-benchmarking" beat "sensor-fusion" beat "end-to-end-driving"
    # every single time, whatever the paper was about. TransFuser -- a
    # sensor-fusion method for end-to-end driving, with none of those words
    # in its abstract more than the others -- came out as "Simulation".
    #
    # A title match breaks the tie instead. A paper's title states what it is
    # about; its abstract mentions everything it touches. Longest matching
    # keyword is the second tiebreak, preferring the more specific phrase,
    # with the slug last only so the result stays deterministic.
    # The title score leads, not just as a tiebreak. A title states what the
    # paper IS; an abstract mentions everything it touches, and the abstract
    # is several times longer, so raw counts over the combined text
    # systematically favour whatever a paper discusses most rather than what
    # it contributes. "PointPainting: Sequential Fusion for 3D Object
    # Detection" scored 4 on segmentation (its method paints segmentation
    # output onto points) against 2 on object detection, which its own title
    # names twice.
    #
    # Most titles match no category keyword at all, and those fall through to
    # the combined score exactly as before -- this only changes papers whose
    # title does name a topic, which is where the title should win.
    def rank(cat):
        keywords = [k.lower() for k in cat["keywords"]]
        return (
            score_category(title_l, keywords),
            score_category(text, keywords),
            max((len(k) for k in keywords if k in text), default=0),
            cat["id"],
        )

    ranked = sorted((rank(cat) for cat in categories), reverse=True)
    # Downstream only needs "did anything match at all" plus the id, and a
    # match anywhere counts -- so the combined score, not the title one.
    scores = [(r[1], r[3]) for r in ranked]
    category = "uncategorized"
    for best_score, best_id in scores:
        if best_score <= 0:
            break
        # A method paper that derives/releases a dataset as a byproduct of
        # its evaluation (e.g. "Sparsity Invariant CNNs", which derives a
        # depth dataset from KITTI to evaluate its own sparse-conv layer)
        # still trips "novel dataset" in the abstract, even though the
        # paper's own subject is the method, not the data -- confirmed on
        # real data (user-flagged). Anything whose own title presents a
        # dataset has already been claimed by presents_dataset() above, so
        # reaching here with this category means the evidence was in the
        # abstract only: fall through to the next-best category.
        if best_id == "dataset-benchmark-paper":
            continue
        category = best_id
        break
    llm_says_core = normalize_title(title) in llm_core_titles
    relevance = classify_relevance(title, abstract, llm_says_core)
    return category, relevance
