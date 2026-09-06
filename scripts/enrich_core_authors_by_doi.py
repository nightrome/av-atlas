#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The small, precise counterpart to enrich_core_authors.py's title-search
lookup: for the av_relevance=="core" papers that still have no
authors_detail but DO carry a DOI, resolve them against OpenAlex in
batches of 50 via `filter=doi:a|b|c|...` -- one request per 50 papers
instead of one per paper. ~900 such papers today => ~19 requests total, so
this finishes in well under a minute and doesn't come anywhere near
OpenAlex's rate ceiling (which the per-paper title search keeps hitting;
see that script).

Writes authors_detail (name + affiliations + country codes + OpenAlex
author id + ORCID) straight onto papers_full.json, stamped
authors_detail_source="openalex", exactly like enrich_core_authors.py --
only touches papers with no authors_detail at all, never overwrites a
richer existing source. Re-reads the file immediately before writing so a
concurrent merge_corpus.py rerun isn't clobbered.

Do NOT run this at the same time as enrich_core_authors.py or
apply_affiliations_arxiv.py -- all three write papers_full.json directly.
(fetch_affiliations_arxiv.py only writes its own side files, so THAT is
fine to have running alongside this.)

Usage: python enrich_core_authors_by_doi.py
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
IN_FILE = BASE / "data" / "papers_full.json"
CONTACT_EMAIL = "holger@it-caesar.com"
BATCH = 50


def norm_doi(raw):
    """Bare lowercase DOI ('10.1109/icra.2022.123'), or None. Accepts a full
    https://doi.org/ URL or a bare DOI, with or without a trailing junk."""
    if not raw or not isinstance(raw, str):
        return None
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", raw.strip().lower())
    return m.group(0).rstrip(".") if m else None


def _short_id(url):
    return url.rsplit("/", 1)[-1] if url else None


def fetch_json(url, max_retries=4):
    req = urllib.request.Request(url, headers={"User-Agent": f"av-atlas (mailto:{CONTACT_EMAIL})"})
    delay = 5.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        except Exception:
            return None


def authors_detail_from_work(work):
    out = []
    for a in (work.get("authorships") or []):
        au = a.get("author") or {}
        name = au.get("display_name")
        if not name:
            continue
        insts = a.get("institutions") or []
        out.append({
            "name": name,
            "affiliations": [i.get("display_name") for i in insts if i.get("display_name")],
            "countries": [i.get("country_code") for i in insts if i.get("country_code")],
            "openalex_id": _short_id(au.get("id")),
            "orcid": _short_id(au.get("orcid")),
        })
    return out


def main():
    papers = json.loads(IN_FILE.read_text(encoding="utf-8"))
    targets = {}  # bare doi -> normalized title (first paper wins per doi)
    for p in papers:
        if p.get("av_relevance") != "core" or p.get("authors_detail"):
            continue
        d = norm_doi(p.get("doi"))
        if d and d not in targets:
            targets[d] = p["title"]
    dois = list(targets)
    print(f"{len(dois)} core papers with a DOI and no authors_detail", flush=True)
    if not dois:
        return

    found = {}  # bare doi -> authors_detail
    for i in range(0, len(dois), BATCH):
        chunk = dois[i:i + BATCH]
        filt = "doi:" + "|".join(chunk)
        params = urllib.parse.urlencode({"filter": filt, "per-page": BATCH, "mailto": CONTACT_EMAIL})
        data = fetch_json(f"https://api.openalex.org/works?{params}")
        time.sleep(0.3)
        for work in (data or {}).get("results", []) or []:
            wd = norm_doi(work.get("doi"))
            if not wd or wd not in targets:
                continue
            ad = authors_detail_from_work(work)
            if ad:
                found[wd] = ad
        print(f"  [{min(i + BATCH, len(dois))}/{len(dois)}] matched {len(found)} so far", flush=True)

    if not found:
        print("Nothing matched -- OpenAlex has none of these DOIs, or is rate-limiting.")
        return

    # Re-read fresh right before writing (concurrent-merge safety).
    papers = json.loads(IN_FILE.read_text(encoding="utf-8"))
    n = 0
    for p in papers:
        if p.get("av_relevance") != "core" or p.get("authors_detail"):
            continue
        ad = found.get(norm_doi(p.get("doi")))
        if ad:
            p["authors_detail"] = ad
            p["authors_detail_source"] = "openalex"
            n += 1
    IN_FILE.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote authors_detail onto {n} papers.", flush=True)


if __name__ == "__main__":
    main()
