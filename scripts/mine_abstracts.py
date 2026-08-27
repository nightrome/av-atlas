#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mines missing abstracts from arXiv for core papers that don't have one --
mostly venue-listing sources (ICRA, IROS, RSS, ICLR, AAAI, IV, ITSC, GCPR,
ICML, BMVC, IJCV, RA-L, T-RO, TPAMI, IJRR, T-ITS) that only ever carried
title+authors, no abstract, so classification for those has always run on
title text alone -- a known, documented limitation (see about.html).
Most AV/ML papers at these venues have an arXiv preprint even when the venue
itself is print-only, so this is a real, addressable gap, not a dead end.

Two passes:
  1. Papers that already have an arxiv_url (resolved by a previous run of
     fetch_arxiv_links.py) -- direct ID lookup, no title search needed, fast.
  2. Papers with no arxiv_url yet -- title search via arXiv's API (same
     exact-normalized-title-match standard as fetch_affiliations_arxiv.py's
     find_arxiv_id, to avoid a same-topic-different-paper false match),
     3s/request per arXiv's own etiquette guidance. This is the slow pass.
     A clean search that finds no match is recorded as "exhausted" --
     distinguishes "we looked, there's genuinely nothing on arXiv" from
     "haven't searched yet" (aggregate.py's coverage stat counts both a
     found abstract and a confirmed miss as done), and means a later run
     skips straight past it instead of re-searching the same paper forever.
     A network/API exception does NOT record it, so a transient failure
     still gets retried next run.

Writes ONLY to its own side file, data/abstracts_arxiv.json -- same
fetch/apply-with-side-file shape as fetch_affiliations_arxiv.py/
apply_affiliations_arxiv.py, not the "single writer straight into
papers_full.json" shape this script used before. That earlier shape meant
every abstract ever mined -- and every "confirmed no arXiv match" marker,
which is what prevents re-searching a dead end forever -- lived ONLY inside
the ~280MB gitignored papers_full.json, with no independent record: if that
file were ever lost, recovering it meant re-running this whole rate-limited
crawl from zero (see DECISIONS.md). The side file is still gitignored (large,
derived, same reasoning as affiliations_arxiv.json) but is now independently
regenerable-by-replay: run this, then apply_abstracts_arxiv.py, and
papers_full.json's abstract data is back without touching arXiv again.

Never mutates papers_full.json -- only reads it, to compute the mining
pool and to check what's already known (papers_full.json's own fields are
still consulted alongside the cache, since older abstracts predate this
side-file split and only ever landed there). Safe to run anytime, including
alongside another script that IS writing papers_full.json, unlike before.

Usage: python mine_abstracts.py
Then: python apply_abstracts_arxiv.py
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
ABSTRACTS_CACHE_FILE = BASE / "data" / "abstracts_arxiv.json"
ARXIV_API = "http://export.arxiv.org/api/query"
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
HEADERS = {"User-Agent": "av-atlas-corpus-builder (contact: h.caesar@tudelft.nl)"}
REQUEST_DELAY = 3.0
ID_BATCH_SIZE = 40  # arXiv's id_list accepts many at once, well under any practical limit


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def fetch_with_retries(url, max_retries=4):
    delay = 5.0
    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read()
        except Exception as e:
            last_error = e
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
    raise last_error


def clean_abstract(text):
    return re.sub(r"\s+", " ", (text or "")).strip() or None


def arxiv_id_from_url(url):
    m = re.search(r"arxiv\.org/abs/([\w.\-]+?)(v\d+)?/?$", url or "")
    return m.group(1) if m else None


def fetch_by_ids(ids):
    """Returns {arxiv_id: abstract} for every id arXiv actually has a record for."""
    query = urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    data = fetch_with_retries(f"{ARXIV_API}?{query}")
    root = ET.fromstring(data)
    out = {}
    for entry in root.findall("atom:entry", ATOM_NS):
        id_url = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
        aid = arxiv_id_from_url(id_url)
        summary = clean_abstract(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
        if aid and summary:
            out[aid] = summary
    return out


def fetch_by_title(title):
    """Title search, exact-normalized-title-match only. Returns abstract or None."""
    query = urllib.parse.urlencode({"search_query": f'ti:"{title}"', "max_results": 1})
    data = fetch_with_retries(f"{ARXIV_API}?{query}")
    root = ET.fromstring(data)
    entry = root.find("atom:entry", ATOM_NS)
    if entry is None:
        return None
    found_title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
    if normalize_title(found_title) != normalize_title(title):
        return None
    return clean_abstract(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))


def load_cache():
    if ABSTRACTS_CACHE_FILE.exists():
        return json.loads(ABSTRACTS_CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def save_cache(cache):
    ABSTRACTS_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def already_known(e, cache):
    """True if an abstract is already known OR this paper's arXiv search is
    already confirmed exhausted -- checks papers_full.json's own fields
    (older data, from before the side-file split) as well as the cache
    (this script's own, possibly not yet folded back in by
    apply_abstracts_arxiv.py) so neither source of truth causes a re-fetch
    the other one already answered."""
    if e.get("abstract") or e.get("abstract_search_exhausted"):
        return True
    cached = cache.get(normalize_title(e.get("title")))
    return bool(cached and (cached.get("abstract") or cached.get("exhausted")))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    cache = load_cache()
    core = [e for e in papers if e.get("av_relevance") == "core"]

    # Also target a narrow, well-justified slice of "adjacent" papers: those
    # from a venue that is EXCLUSIVELY about intelligent vehicles/transportation
    # (IV, ITSC, T-ITS -- unlike CVPR/ICRA/etc., these venues carry no non-AV
    # content at all in the first place) AND whose category score already
    # landed on a specific AV-perception/planning category rather than
    # "uncategorized". User-flagged real examples (Qingwen Zhang's own
    # papers -- "DoGFlow: Self-Supervised LiDAR Scene Flow...", "DUFOMap:
    # Efficient Dynamic Awareness Mapping", several motion-prediction and
    # mapping-localization papers) all had no abstract at all, so
    # classify_relevance() had only a short, coined title to go on and none
    # of AV_RELEVANCE_TERMS' specific phrases happened to appear in it --
    # even though the SAME title, run through the richer category-keyword
    # scorer, already matched a real AV-specific category (confirming the
    # signal was there, just not in the narrower relevance term list).
    # Deliberately NOT every "adjacent, no abstract" paper (~110k of them,
    # genuinely off-topic across the full unfiltered venue proceedings this
    # corpus is built from) -- this venue+category combination is a real,
    # bounded slice, not a blanket re-check. Worst case for a false-positive
    # candidate here is a wasted API call: fetching its real abstract only
    # changes its classification if the abstract text itself now matches,
    # nothing here forces a relabel.
    AV_ONLY_VENUES = {"IV", "ITSC", "T-ITS"}
    adjacent_recheck = [
        e for e in papers
        if e.get("av_relevance") == "adjacent"
        and e.get("venue") in AV_ONLY_VENUES
        and e.get("category") not in (None, "uncategorized")
    ]
    print(f"{len(adjacent_recheck)} adjacent papers from AV-only venues with a real category match "
          f"(likely miscategorized for lack of an abstract) added to the mining pool")
    core = core + adjacent_recheck

    missing = [e for e in core if not already_known(e, cache)]
    print(f"{len(core)} papers in the mining pool (core + the adjacent-recheck slice above), "
          f"{len(missing)} missing an abstract")

    with_url = [e for e in missing if e.get("arxiv_url")]
    without_url = [e for e in missing if not e.get("arxiv_url")]
    print(f"  {len(with_url)} already have an arxiv_url (fast pass), "
          f"{len(without_url)} need a title search (slow pass)")

    # -- Pass 1: batch ID lookup for papers with a known arxiv_url --
    filled = 0
    for i in range(0, len(with_url), ID_BATCH_SIZE):
        batch = with_url[i:i + ID_BATCH_SIZE]
        id_to_entry = {}
        for e in batch:
            aid = arxiv_id_from_url(e["arxiv_url"])
            if aid:
                id_to_entry[aid] = e
        if not id_to_entry:
            continue
        try:
            found = fetch_by_ids(list(id_to_entry.keys()))
        except Exception as ex:
            print(f"  batch {i // ID_BATCH_SIZE} failed: {ex}", flush=True)
            time.sleep(REQUEST_DELAY)
            continue
        for aid, abstract in found.items():
            cache[normalize_title(id_to_entry[aid]["title"])] = {"abstract": abstract}
            filled += 1
        save_cache(cache)
        print(f"  pass 1: {i + len(batch)}/{len(with_url)} processed, {filled} filled so far", flush=True)
        time.sleep(REQUEST_DELAY)

    # -- Pass 2: title search for papers with no arxiv_url at all --
    consecutive_failures = 0
    exhausted = 0
    for j, e in enumerate(without_url):
        try:
            abstract = fetch_by_title(e["title"])
        except Exception as ex:
            print(f"  title search failed for {e['title'][:60]!r}: {ex}", flush=True)
            consecutive_failures += 1
            if consecutive_failures >= 20:
                print("  20 consecutive failures, stopping pass 2 early (network/rate-limit issue, not 'no papers left')", flush=True)
                break
            time.sleep(REQUEST_DELAY)
            continue
        consecutive_failures = 0
        key = normalize_title(e["title"])
        if abstract:
            cache[key] = {"abstract": abstract}
            filled += 1
        else:
            cache[key] = {"exhausted": True}
            exhausted += 1
        if (j + 1) % 25 == 0:
            save_cache(cache)
            print(f"  pass 2: {j + 1}/{len(without_url)} processed, {filled} filled, "
                  f"{exhausted} confirmed no match so far (total)", flush=True)
        time.sleep(REQUEST_DELAY)

    save_cache(cache)
    print(f"Done. {filled} abstracts filled, {exhausted} confirmed no arXiv match, in total.")
    print("Run apply_abstracts_arxiv.py to fold this into papers_full.json.")


if __name__ == "__main__":
    main()
