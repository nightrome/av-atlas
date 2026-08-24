#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Same Wikipedia pageimages lookup as fetch_institution_logos.py, for the
~20 venues this corpus covers. Venue acronyms (CVPR, RA-L, ...) don't match
Wikipedia article titles directly, so this maps each to its real article
title by hand rather than guessing -- a wrong guess here would either 404
(safe) or, worse, silently match an unrelated article of the same acronym.

Writes av-atlas/data/venue_logos.json: {venue: {"logo_url": ...,
"wiki_title": ...}}.

Usage: python fetch_venue_logos.py
"""
import json
import time
import urllib.parse

from fetch_common import BASE, fetch

OUT_FILE = BASE / "data" / "venue_logos.json"
WIKI_API = "https://en.wikipedia.org/w/api.php"

# Venue -> its real Wikipedia article title (hand-verified, not guessed).
VENUE_WIKI_TITLES = {
    "CVPR": "Conference on Computer Vision and Pattern Recognition",
    "ICCV": "International Conference on Computer Vision",
    "WACV": "Winter Conference on Applications of Computer Vision",
    "ECCV": "European Conference on Computer Vision",
    "NeurIPS": "Conference on Neural Information Processing Systems",
    "CoRL": "Conference on Robot Learning",
    "ICRA": "International Conference on Robotics and Automation",
    "IROS": "International Conference on Intelligent Robots and Systems",
    "RSS": "Robotics: Science and Systems",
    "ICLR": "International Conference on Learning Representations",
    "AAAI": "AAAI Conference on Artificial Intelligence",
    "IV": "IEEE Intelligent Vehicles Symposium",
    "ITSC": "IEEE Intelligent Transportation Systems Conference",
    "GCPR": "German Conference on Pattern Recognition",
    "IJCV": "International Journal of Computer Vision",
    "RA-L": "IEEE Robotics and Automation Letters",
    "T-RO": "IEEE Transactions on Robotics",
    "TPAMI": "IEEE Transactions on Pattern Analysis and Machine Intelligence",
    "IJRR": "International Journal of Robotics Research",
    "T-ITS": "IEEE Transactions on Intelligent Transportation Systems",
}


def wiki_lookup(title):
    params = urllib.parse.urlencode({
        "action": "query", "titles": title, "prop": "pageimages",
        "format": "json", "pithumbsize": "300", "redirects": "1",
    })
    data = json.loads(fetch(f"{WIKI_API}?{params}", timeout=15))
    pages = (data.get("query") or {}).get("pages") or {}
    for page in pages.values():
        if page.get("pageid") is None:
            continue
        thumb = page.get("thumbnail")
        if thumb and thumb.get("source"):
            return {"logo_url": thumb["source"], "wiki_title": page.get("title")}
    return None


def main():
    result = {}
    for i, (venue, title) in enumerate(VENUE_WIKI_TITLES.items(), 1):
        try:
            found = wiki_lookup(title)
        except Exception as e:
            print(f"  [{i}/{len(VENUE_WIKI_TITLES)}] FAILED {venue}: {e}")
            continue
        if found:
            result[venue] = found
            print(f"  [{i}/{len(VENUE_WIKI_TITLES)}] found: {venue} -> {found['wiki_title']}")
        else:
            print(f"  [{i}/{len(VENUE_WIKI_TITLES)}] no image: {venue} ({title})")
        time.sleep(0.3)

    OUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"\nWrote {OUT_FILE}: {len(result)}/{len(VENUE_WIKI_TITLES)} venues have a logo")


if __name__ == "__main__":
    main()
