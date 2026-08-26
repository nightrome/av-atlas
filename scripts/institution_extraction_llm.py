#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Extracts clean institution name(s) from a raw affiliation text using a local
LLM (Ollama), anchored against a growing registry of already-known
institution names so the same real institution doesn't get re-invented as a
slightly different string every time a paper phrases it differently ("KTH"
vs "Royal Institute of Technology" vs "KTH Royal Institute of Technology").

Replaces clean_affiliations()'s naive comma-split (fetch_affiliations_arxiv.py)
as the primary extraction mechanism -- a real affiliation block is prose
("Authors are with the Division of Robotics, Perception, and Learning (RPL),
KTH Royal Institute of Technology, Stockholm, Sweden"), not a flat list of
independent comma-separated fields, and no amount of regex cleanup after a
blind split can fully recover what a naive split destroys (confirmed real
cost: "Perception" surviving as its own fake institution, see
aggregate.py's INVALID_INSTITUTIONS comment). User-requested: "extract them
in the right way" instead of extracting garbage and filtering it after.

Two-tier dedup, not a blind LLM call every time:
  1. Cheap candidate shortlist (build_candidate_shortlist): registry entries
     sharing a significant word with the raw text -- catches the common case
     where the raw text spells out enough of the real name to token-match.
  2. The LLM sees this shortlist alongside the raw text and is asked to
     either return one of them EXACTLY (including by acronym/abbreviation --
     the part token-overlap alone can't do, e.g. "KTH" matching "KTH Royal
     Institute of Technology") or propose a clean new name. User-requested:
     "tell me if this fits into these existing institutions or add a new
     one" -- this is that, with a cheap shortlist so the prompt doesn't have
     to carry the entire multi-thousand-entry registry every single call.

Registry: av-atlas/data/institution_registry.json, a flat list of canonical
institution names. Seeded once from the site's own already-cleaned
institution list; grows as genuinely new institutions are confirmed
(resolve_and_register), so later extractions -- in the same run and in
future runs -- have an ever-larger shortlist to match against instead of
re-proposing the same institution under a slightly different name.

Cache: av-atlas/data/affiliations_llm_extracted.json, raw affiliation text
-> extraction result. Keyed by the exact raw text (not a hash) so it doubles
as a human-readable audit trail of what the model was actually shown.

A module, not a standalone script -- fetch_affiliations_arxiv.py imports
extract_institutions() and calls it per author. No file I/O of its own
beyond the registry/cache helpers below, which callers use explicitly (same
"caller owns the read-modify-write cycle" shape as every other side-file in
this pipeline).
"""
import json
import re
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
REGISTRY_FILE = BASE / "data" / "institution_registry.json"
CACHE_FILE = BASE / "data" / "affiliations_llm_extracted.json"
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "qwen2.5:7b-instruct"
MAX_CANDIDATES = 40

# Generic words that appear in huge numbers of institution names and carry no
# disambiguating signal on their own -- excluded from the token-overlap
# shortlist so e.g. every "University of X" doesn't shortlist every other
# "University of Y", which would defeat the point of narrowing the candidate
# list down from thousands of entries.
GENERIC_WORDS = {
    "university", "institute", "institution", "college", "school", "of",
    "the", "and", "for", "at", "in", "de", "der", "van", "la", "le", "el",
    "technology", "technologies", "technical", "research", "center", "centre",
    "laboratory", "lab", "department", "faculty", "national", "state",
    "academy", "sciences", "science", "engineering", "corporation", "corp",
    "inc", "group", "co", "ltd",
}


def significant_words(text):
    words = re.findall(r"[A-Za-z][A-Za-z'\-]+", text or "")
    return {w.lower() for w in words if len(w) > 2 and w.lower() not in GENERIC_WORDS}


def build_candidate_shortlist(raw_text, registry, max_candidates=MAX_CANDIDATES):
    """Registry entries sharing at least one significant word with raw_text,
    most-overlapping first. Deliberately cheap (no LLM call) -- this is the
    filter that keeps the actual LLM prompt from having to carry the whole
    registry every time."""
    text_words = significant_words(raw_text)
    if not text_words:
        return []
    scored = []
    for name in registry:
        overlap = text_words & significant_words(name)
        if overlap:
            scored.append((len(overlap), name))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [name for _, name in scored[:max_candidates]]


PROMPT_TEMPLATE = """You are extracting the real institution name(s) (universities, companies, or research labs -- NOT departments/cities/countries/emails/footnotes) that an author is affiliated with, from raw text pulled out of a paper's author block. This text is often messy: it may be a full sentence, may bundle multiple people's affiliations together, may include email addresses, funding acknowledgments, or footnote markers that are NOT part of any institution's name.

Raw affiliation text:
{raw_text}

Institutions already known in our database that MIGHT be what this text refers to (it could also be none of these):
{candidates}

Return ONLY a compact JSON object, no other text:
{{"institutions": [{{"name": "<name>", "matched_existing": true}}, ...]}}

Rules:
- If the text names an institution that IS one of the ones listed above (even under a different name, abbreviation, or acronym -- e.g. "KTH" is the same institution as "KTH Royal Institute of Technology"), return that EXACT existing name with "matched_existing": true.
- If the text names a real institution that is NOT in the list above, propose a clean canonical name for it (just the institution's own name -- no department, city, country, or footnote text) with "matched_existing": false.
- If the text names more than one real institution (e.g. a joint appointment), include one object per institution.
- If the text contains no real institution name at all (just a person's name, an email, a footnote, an address fragment, funding text, etc.), return {{"institutions": []}}.
"""


def call_ollama(model, prompt, timeout=120):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json"}).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["response"]


def parse_extraction_response(raw_response):
    """Returns a list of {"name": str, "matched_existing": bool} dicts, or
    None if the response couldn't be parsed at all -- callers must treat
    that as a failure (retry later), not as "this text has no institution",
    which is instead an explicit empty list from a successfully parsed
    response."""
    try:
        obj = json.loads(raw_response)
        institutions = obj.get("institutions")
        if not isinstance(institutions, list):
            return None
        out = []
        for item in institutions:
            if not isinstance(item, dict):
                continue
            name = (item.get("name") or "").strip()
            if name:
                out.append({"name": name, "matched_existing": bool(item.get("matched_existing"))})
        return out
    except Exception:
        return None


def extract_institutions(raw_text, registry, model=DEFAULT_MODEL):
    """Extracts institution(s) from raw_text, anchored against `registry`
    (a list of already-known canonical names). Returns a list of
    {"name": ..., "matched_existing": ...} dicts (empty list if the text
    names no real institution). Raises on any failure (network down,
    unparseable response) -- callers decide how to handle that, but should
    never fall back to guessing (e.g. the old comma-split) on failure, only
    to retrying later, same as every other network call in this pipeline."""
    candidates = build_candidate_shortlist(raw_text, registry)
    prompt = PROMPT_TEMPLATE.format(
        raw_text=raw_text,
        candidates="\n".join(f"- {c}" for c in candidates) if candidates else "(none found)",
    )
    raw_response = call_ollama(model, prompt)
    parsed = parse_extraction_response(raw_response)
    if parsed is None:
        raise ValueError(f"could not parse LLM extraction response: {raw_response[:200]!r}")
    return parsed


def resolve_and_register(extracted, registry_set):
    """Takes extract_institutions()'s parsed result and the registry (a
    set, MUTATED IN PLACE with any genuinely new name) and returns the final
    list of institution name strings to actually use for this author.

    Guards against the model claiming matched_existing=True for a name
    that isn't actually present in the registry (a hallucinated "exact"
    match, or a name it subtly reworded while still marking it matched) --
    that case is silently treated as a new entry rather than trusted, since
    trusting an unverified match risks two different real institutions
    silently colliding under one name change. Either way the name gets
    added to the registry so later calls in the same run can match against
    it too, keeping duplicates from compounding within one pipeline run."""
    resolved = []
    for item in extracted:
        name = item["name"]
        registry_set.add(name)
        resolved.append(name)
    return resolved


def load_registry():
    if REGISTRY_FILE.exists():
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))["institutions"]
    return []


def save_registry(institutions):
    REGISTRY_FILE.write_text(
        json.dumps({"institutions": sorted(set(institutions))}, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n")


def load_cache():
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache):
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
