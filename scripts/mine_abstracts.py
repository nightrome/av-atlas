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
     A clean search that finds no match sets abstract_search_exhausted on
     the paper -- distinguishes "we looked, there's genuinely nothing on
     arXiv" from "haven't searched yet" (aggregate.py's coverage stat counts
     both a found abstract and a confirmed miss as done), and means a later
     run skips straight past it instead of re-searching the same paper
     forever. A network/API exception does NOT set it, so a transient
     failure still gets retried next run.

Writes directly to papers_full.json (single writer for this field, no
separate side file -- unlike affiliations/institutions there's no
disambiguation step downstream that needs the raw fetch preserved). Saves
after every batch, so a killed/restarted run only redoes the current batch,
never loses prior progress.

Usage: python mine_abstracts.py
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


def save(papers):
    PAPERS_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    core = [e for e in papers if e.get("av_relevance") == "core"]
    missing = [e for e in core if not e.get("abstract")]
    print(f"{len(core)} core papers, {len(missing)} missing an abstract")

    with_url = [e for e in missing if e.get("arxiv_url")]
    # Skip papers a previous run already searched and confirmed have no
    # arXiv match -- re-searching them every run is pure wasted work (the
    # answer can't change unless the paper somehow gains an arxiv_url later,
    # which is handled by the with_url branch above, not this one).
    without_url = [e for e in missing if not e.get("arxiv_url") and not e.get("abstract_search_exhausted")]
    already_exhausted = sum(1 for e in missing if not e.get("arxiv_url") and e.get("abstract_search_exhausted"))
    print(f"  {len(with_url)} already have an arxiv_url (fast pass), "
          f"{len(without_url)} need a title search (slow pass), "
          f"{already_exhausted} previously confirmed no arXiv match (skipped)")

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
            id_to_entry[aid]["abstract"] = abstract
            filled += 1
        save(papers)
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
        if abstract:
            e["abstract"] = abstract
            filled += 1
        else:
            e["abstract_search_exhausted"] = True
            exhausted += 1
        if (j + 1) % 25 == 0:
            save(papers)
            print(f"  pass 2: {j + 1}/{len(without_url)} processed, {filled} filled, "
                  f"{exhausted} confirmed no match so far (total)", flush=True)
        time.sleep(REQUEST_DELAY)

    save(papers)
    print(f"Done. {filled} abstracts filled, {exhausted} confirmed no arXiv match, in total.")


if __name__ == "__main__":
    main()
