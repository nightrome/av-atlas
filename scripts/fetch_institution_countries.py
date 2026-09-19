#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backfills av-atlas/data/institution_countries.json -- historically a
small hand-curated {name: "US"} map (see apply_affiliations_arxiv.py's own
docstring: built via real web lookups, one entry at a time, as arXiv-sourced
affiliations without a country turned up). This script extends the SAME
file with the same flat schema, automated, so aggregate.py's own fallback
(see paper_countries_institutions) and apply_affiliations_arxiv.py both
benefit without either needing to change. Never touches an existing entry
(including the "_readme" key) -- only adds names that aren't in the file
yet, so it can't clobber a verified hand-curated value with an automated
guess.

Why this exists at all: per-author country codes (from OpenAlex's own
authors_detail enrichment) are frequently missing even when the institution
NAME resolved fine -- confirmed on real data: "Computer Vision Center" and
"CSIC-UPC" both show up correctly as institution names on several papers,
with zero resolved country on any of them, because OpenAlex's author-
affiliation record had the name but no country_code attached.

Source: Wikidata's "country" property (P17), via the same wbsearchentities/
wbgetclaims lookup fetch_institution_logos.py already does for P154 (logo)
-- reuses that fast, free, no-auth path instead of a slower per-institution
OpenAlex institution search (tried first; Wikidata resolves faster and this
pipeline gets nothing else from OpenAlex's institution index that Wikidata
doesn't also have).

P17 resolves to a QID for the country itself (e.g. Q29 for Spain), not an
ISO code directly -- resolved to a 2-letter code via that QID's own P297
claim, cached across institutions (most of the corpus clusters into a few
dozen distinct countries even across 500+ institutions, so this second
lookup only happens once per NEW country encountered, not once per
institution).

Institutions with no resolved country are NOT written as a null/miss marker
here (unlike fetch_institution_logos.py's pattern) -- this file's existing
convention is "absent = unknown", and a flat map has no natural place to
record "checked, found nothing" without risking it being misread as a real
value. Re-running this script will re-attempt any institution still absent;
pass nothing to skip ones already present (default), there is no --refresh.

Usage: python fetch_institution_countries.py [N]   # N = min papers, default 1
"""
import json
import sys
import time
import urllib.parse

from fetch_common import BASE, fetch

STATS_FILE = BASE / "data" / "stats.json"
OUT_FILE = BASE / "data" / "institution_countries.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Same aliasing idea as fetch_institution_logos.py's own table (kept
# separate since a country lookup and a logo lookup don't always want the
# same "closest good enough" substitute) -- a sub-lab or consortium name
# often doesn't resolve well on its own; its parent/umbrella org's country
# is also correct for it.
NAME_ALIASES = {
    "CSIC-UPC": "Consejo Superior de Investigaciones Científicas",
    "Computer Vision Center": "Universitat Autònoma de Barcelona",
    "Institut de Robòtica i Informàtica Industrial": "Consejo Superior de Investigaciones Científicas",
    "Uber ATG": "Uber",
    "SenseTime Research": "SenseTime",
    "Huawei Noah's Ark Lab": "Huawei",
    "Huawei Noah’s Ark Lab": "Huawei",
    "NVIDIA Research": "NVIDIA",
    "Google DeepMind": "DeepMind",
    "Microsoft Research": "Microsoft",
}

_country_qid_cache = {}  # QID -> ISO2 code, shared across every institution this run


def wikidata_search(name):
    params = urllib.parse.urlencode({
        "action": "wbsearchentities", "search": name, "language": "en",
        "format": "json", "limit": "1", "type": "item",
    })
    data = json.loads(fetch(f"{WIKIDATA_API}?{params}", timeout=15))
    results = data.get("search") or []
    return results[0]["id"] if results else None


def wikidata_claim_value(qid, prop):
    params = urllib.parse.urlencode({
        "action": "wbgetclaims", "entity": qid, "property": prop, "format": "json",
    })
    data = json.loads(fetch(f"{WIKIDATA_API}?{params}", timeout=15))
    claims = (data.get("claims") or {}).get(prop) or []
    for c in claims:
        value = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if value:
            return value
    return None


def country_qid_to_iso2(qid):
    if qid in _country_qid_cache:
        return _country_qid_cache[qid]
    iso2 = None
    try:
        value = wikidata_claim_value(qid, "P297")  # ISO 3166-1 alpha-2 code
        if isinstance(value, str):
            iso2 = value.upper()
    except Exception as e:  # noqa: BLE001
        print(f"    P297 lookup failed for {qid}: {e}")
    _country_qid_cache[qid] = iso2
    return iso2


def lookup(name):
    search_term = NAME_ALIASES.get(name, name)
    try:
        qid = wikidata_search(search_term)
        if not qid:
            return None
        country_claim = wikidata_claim_value(qid, "P17")  # country
        country_qid = (country_claim or {}).get("id") if isinstance(country_claim, dict) else None
        if not country_qid:
            return None
        return country_qid_to_iso2(country_qid)
    except Exception as e:  # noqa: BLE001
        print(f"    wikidata lookup failed for {name}: {e}")
        return None


def target_institutions(min_papers):
    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    counts = {}
    for p in stats.get("all_papers", []):
        for inst in (p.get("institutions") or []):
            counts[inst] = counts.get(inst, 0) + 1
    return sorted([name for name, n in counts.items() if n > min_papers])


def main():
    min_papers = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    existing = {}
    if OUT_FILE.exists():
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8"))

    names = target_institutions(min_papers)
    todo = [n for n in names if n not in existing]
    print(f"{len(todo)} institutions to look up (of {len(names)} with >{min_papers} paper(s)); "
          f"{len(existing)} already in the file (never overwritten)")

    found = 0
    for i, name in enumerate(todo, 1):
        code = lookup(name)
        if code:
            existing[name] = code
            found += 1
            print(f"  [{i}/{len(todo)}] {name}: {code}")
        else:
            print(f"  [{i}/{len(todo)}] {name}: no match")
        if i % 20 == 0:
            OUT_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        time.sleep(0.1)

    OUT_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {OUT_FILE}: {found} new institutions resolved, {len(existing)} total")


if __name__ == "__main__":
    main()
