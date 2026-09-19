#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Looks up an actual logo image (not a building/office photo) for the top
institutions (by paper count).

Primary source: Wikidata's "logo image" property (P154). Unlike Wikipedia's
pageimages API (which just returns whatever lead image an article happens to
have -- often a campus or headquarters photo for companies), P154 is
specifically curated to be the organization's logo/seal, so it reliably
avoids the "Google -> photo of the Googleplex" problem.

Fallbacks, in order: Wikipedia's pageimages API, then the Wikipedia REST
summary endpoint (whose lead image is usually the infobox crest/logo even
when pageimages returns nothing). Organizations with no Wikipedia article at
all (some corporate and national labs) are left without a logo.

Institutions are organizations, not private individuals, so there's no
namesake-collision privacy concern the way there was for author photos (see
DECISIONS.md's "Scholar profile/photo lookups" note).

Writes av-atlas/data/institution_logos.json: {name: {"logo_url": ...,
"source": "wikidata"|"clearbit"}}, only for institutions where a real image
was found. Skips institutions already in this file with a non-null logo_url
(rerun-safe, incremental) -- pass --refresh to re-check everything, which is
useful after improving the lookup logic.

Usage: python fetch_institution_logos.py [N] [--refresh]
"""
import json
import sys
import time
import urllib.parse

from fetch_common import BASE, fetch

STATS_FILE = BASE / "data" / "stats.json"
OUT_FILE = BASE / "data" / "institution_logos.json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_FILEPATH = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=300"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"

# A sub-lab or division's own name often doesn't match its parent
# organization's Wikidata item well (or the parent doesn't have a separate
# item at all) -- search for the parent instead, whose logo is also correct
# for the lab. Also covers a couple of corpus names Wikidata just searches
# poorly on.
NAME_ALIASES = {
    "Uber ATG": "Uber",
    "SenseTime Research": "SenseTime",
    "Huawei Noah’s Ark Lab": "Huawei",
    "Huawei Noah's Ark Lab": "Huawei",
    "NVIDIA Research": "NVIDIA",
    "Bosch": "Robert Bosch GmbH",
    "Bosch Research": "Robert Bosch GmbH",
    "Bosch Mobility Solutions": "Robert Bosch GmbH",
    "Google DeepMind": "DeepMind",
    "Microsoft Research": "Microsoft",
    "Meta": "Meta Platforms, Inc.",
    "HKUST (GZ)": "Hong Kong University of Science and Technology",
    "HKUST(GZ)": "Hong Kong University of Science and Technology",
    "HKUST": "Hong Kong University of Science and Technology",
    "The Hong Kong University of Science and Technology (Guangzhou)": "Hong Kong University of Science and Technology",
    "University of Hong Kong": "The University of Hong Kong",
    "Xi'an Jiaotong University": "Xi'an Jiao Tong University",
    "Department of Aeronautical & Aviation Engineering The Hong Kong Polytechnic University Kowloon": "Hong Kong Polytechnic University",
}


def top_institutions(n):
    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    counts = {}
    for p in stats.get("all_papers", []):
        for inst in (p.get("institutions") or []):
            counts[inst] = counts.get(inst, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:n]
    return [name for name, _ in ranked]


def wikidata_search(name):
    params = urllib.parse.urlencode({
        "action": "wbsearchentities", "search": name, "language": "en",
        "format": "json", "limit": "1", "type": "item",
    })
    data = json.loads(fetch(f"{WIKIDATA_API}?{params}", timeout=15))
    results = data.get("search") or []
    return results[0]["id"] if results else None


def wikidata_logo(qid):
    params = urllib.parse.urlencode({
        "action": "wbgetclaims", "entity": qid, "property": "P154", "format": "json",
    })
    data = json.loads(fetch(f"{WIKIDATA_API}?{params}", timeout=15))
    claims = (data.get("claims") or {}).get("P154") or []
    for c in claims:
        filename = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
        if filename:
            return COMMONS_FILEPATH.format(urllib.parse.quote(filename.replace(" ", "_")))
    return None


def wiki_pageimage(title):
    """Fallback: Wikipedia's lead image for the article. Only used when
    Wikidata has no curated P154 logo -- may be a building/office photo
    instead of a logo, but it's better than nothing for institutions where
    no cleaner source exists."""
    params = urllib.parse.urlencode({
        "action": "query", "titles": title, "prop": "pageimages",
        "format": "json", "pithumbsize": "300", "redirects": "1",
    })
    data = json.loads(fetch(f"{WIKI_API}?{params}", timeout=15))
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        thumb = page.get("thumbnail")
        if thumb and thumb.get("source"):
            return thumb["source"]
    return None


def wiki_rest_summary_image(title):
    """Fallback: the lead image the Wikipedia REST summary endpoint reports.
    For universities this is almost always the crest/seal/logo from the
    infobox (the plain pageimages API often returns nothing for the same
    article). May still be a building photo for a company, so it ranks
    below Wikidata's curated P154 but above giving up."""
    data = json.loads(fetch(WIKI_SUMMARY.format(urllib.parse.quote(title.replace(" ", "_"))), timeout=15))
    src = (data.get("originalimage") or data.get("thumbnail") or {}).get("source")
    if not src:
        return None
    # Strip the API's analytics query string so the stored URL is the bare
    # upload.wikimedia.org file.
    return src.split("?")[0]


def lookup(name):
    search_term = NAME_ALIASES.get(name, name)
    try:
        qid = wikidata_search(search_term)
        if qid:
            logo = wikidata_logo(qid)
            if logo:
                return {"logo_url": logo, "source": "wikidata"}
    except Exception as e:
        print(f"    wikidata lookup failed for {name}: {e}")

    try:
        img = wiki_pageimage(search_term)
        if img:
            return {"logo_url": img, "source": "wikipedia-pageimage"}
    except Exception as e:
        print(f"    wikipedia pageimage lookup failed for {name}: {e}")

    try:
        img = wiki_rest_summary_image(search_term)
        if img:
            return {"logo_url": img, "source": "wikipedia-summary"}
    except Exception as e:
        print(f"    wikipedia summary lookup failed for {name}: {e}")

    return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    refresh = "--refresh" in sys.argv
    n = int(args[0]) if args else 100
    names = top_institutions(n)

    existing = {}
    if OUT_FILE.exists():
        existing = json.loads(OUT_FILE.read_text(encoding="utf-8"))

    if refresh:
        pending = names
    else:
        pending = [name for name in names if not (existing.get(name) or {}).get("logo_url")]
    print(f"{len(pending)} institutions to look up (of {len(names)} requested)")

    for i, name in enumerate(pending, 1):
        result = lookup(name)
        if result:
            existing[name] = result
            print(f"  [{i}/{len(pending)}] found ({result['source']}): {name}")
        else:
            existing[name] = {"logo_url": None}
            print(f"  [{i}/{len(pending)}] no logo: {name}")
        time.sleep(0.2)
        if i % 20 == 0:
            OUT_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")

    OUT_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    found = sum(1 for v in existing.values() if v.get("logo_url"))
    print(f"\nWrote {OUT_FILE}: {found}/{len(existing)} institutions have a logo")


if __name__ == "__main__":
    main()
