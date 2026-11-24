#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bulk backfill of author affiliations for AV papers from OpenAlex.

Most AV papers have no institution because the venue listings only carried a
plain author-name string. The per-paper OpenAlex lookup (enrich_av_authors.py)
is slow and runs into the rate limit, so this works in bulk like
fetch_abstracts_openalex.py, in two passes:

  venues  List all works of a venue's OpenAlex source year by year (200 per
          request, 1 credit each) and keep the authorships of papers we have
          no affiliation for, matched on exact normalized title. This covers
          ITSC, T-ITS, IV, ICRA, IROS, RA-L and the other venues whose
          proceedings OpenAlex knows by source. The sources found by the
          abstract backfill are reused.
  dois    Look up the remaining papers that have a DOI or an arXiv ID, 50 per
          request with filter=doi:a|b|c (arXiv papers use their DataCite DOI,
          10.48550/arXiv.<id>).

Results go to data/affiliations_openalex.json, so a crash or a rate limit loses
nothing, and the script resumes where it stopped. Rate limits are waited out
with the same Retry-After and backoff rules as the abstract backfill. --apply
writes them into papers_full.json (authors_detail): papers with no author
details get OpenAlex's list, papers that already have author names keep them
and only get affiliations and country codes filled in by matching names.

Usage:
  python fetch_affiliations_openalex.py                # both passes
  python fetch_affiliations_openalex.py --only dois
  python fetch_affiliations_openalex.py --apply        # fold the side file in
Do not run --apply while another script is writing papers_full.json.
"""
import argparse
import json
import re
import sys
import time
import urllib.parse
from collections import Counter, defaultdict

from atomic_write import write_json_atomic
from checked_titles import left, mark
from fetch_abstracts_openalex import OUT_FILE as ABSTRACT_STATE_FILE
from fetch_abstracts_openalex import get_json
from fetch_abstracts_web import DATA_DIR, PAPERS_FILE, load_json, norm

OUT_FILE = DATA_DIR / "affiliations_openalex.json"
MIN_MISSING = 20
BATCH = 50
SELECT = "title,doi,authorships"


def has_affiliation(p):
    return any(a.get("affiliations") for a in (p.get("authors_detail") or []))


def norm_doi(raw):
    m = re.search(r"10\.\d{4,9}/[^\s\"'<>]+", (raw or "").strip().lower())
    return m.group(0).rstrip(".") if m else None


def paper_doi(p):
    d = norm_doi(p.get("doi"))
    if d:
        return d
    m = re.search(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?$", p.get("arxiv_url") or "", re.I)
    return f"10.48550/arxiv.{m.group(1).lower()}" if m else None


def short_id(url):
    return url.rsplit("/", 1)[-1] if url else None


def details_from_work(work):
    out = []
    for a in work.get("authorships") or []:
        au = a.get("author") or {}
        if not au.get("display_name"):
            continue
        insts = a.get("institutions") or []
        out.append({
            "name": au["display_name"],
            "affiliations": [i["display_name"] for i in insts if i.get("display_name")],
            "countries": [i["country_code"] for i in insts if i.get("country_code")],
            "openalex_id": short_id(au.get("id")),
            "orcid": short_id(au.get("orcid")),
        })
    return out if any(d["affiliations"] for d in out) else []


def targets(papers):
    return [p for p in papers if p.get("av_relevance") == "AV" and not has_affiliation(p)]


def pass_venues(papers, state, max_deadline):
    missing = defaultdict(lambda: defaultdict(set))
    for p in targets(papers):
        nt = norm(p.get("title"))
        if nt and p.get("venue") and p.get("year"):
            missing[p["venue"]][p["year"]].add(nt)
    sources = load_json(ABSTRACT_STATE_FILE, {}).get("sources", {})
    totals = Counter({v: sum(len(t) for t in ys.values()) for v, ys in missing.items()})
    for venue, n in totals.most_common():
        if n < MIN_MISSING or not sources.get(venue):
            continue
        for src in sources[venue]:
            for year in sorted(missing[venue], reverse=True):
                key = f"{src['id']}:{year}"
                wanted = left(state, key, missing[venue][year], state["done_venue_years"])
                if not wanted:
                    continue
                if time.monotonic() > max_deadline:
                    return False
                cursor, seen, got = "*", 0, 0
                while cursor:
                    data = get_json(f"/works?filter=primary_location.source.id:{src['id']},publication_year:{year}"
                                    f"&per-page=200&select={SELECT}&cursor={urllib.parse.quote(cursor)}")
                    for work in data.get("results", []):
                        seen += 1
                        nt = norm(work.get("title"))
                        if nt in wanted and "t:" + nt not in state["details"]:
                            d = details_from_work(work)
                            if d:
                                state["details"]["t:" + nt] = d
                                got += 1
                    cursor = (data.get("meta") or {}).get("next_cursor") if data.get("results") else None
                mark(state, key, wanted)
                write_json_atomic(OUT_FILE, state)
                print(f"{venue} {year}: {seen} works, affiliations for {got} of {len(wanted)}", flush=True)
    return True


def pass_dois(papers, state, max_deadline):
    todo = []
    for p in targets(papers):
        d = paper_doi(p)
        nt = norm(p.get("title"))
        if d and "t:" + nt not in state["details"] and "d:" + d not in state["details"] \
                and d not in state["done_dois"]:
            todo.append(d)
    todo = list(dict.fromkeys(todo))
    print(f"{len(todo)} papers with a DOI or arXiv ID still without affiliations", flush=True)
    for i in range(0, len(todo), BATCH):
        if time.monotonic() > max_deadline:
            return False
        chunk = todo[i:i + BATCH]
        data = get_json("/works?filter=doi:" + "|".join(urllib.parse.quote(d, safe="/") for d in chunk)
                        + f"&per-page={BATCH}&select={SELECT}")
        for work in data.get("results", []):
            d = norm_doi(work.get("doi"))
            details = details_from_work(work)
            if d and details:
                state["details"]["d:" + d] = details
        state["done_dois"].extend(chunk)
        if (i // BATCH) % 10 == 9 or i + BATCH >= len(todo):
            write_json_atomic(OUT_FILE, state)
            print(f"  {min(i + BATCH, len(todo))}/{len(todo)} looked up, "
                  f"{sum(k.startswith('d:') for k in state['details'])} with affiliations", flush=True)
    return True


def merge_details(existing, found):
    """Fill affiliations into existing author rows by name; or take the new list."""
    if not existing:
        return found, True
    by_name = {re.sub(r"\W+", "", a["name"].lower()): a for a in found}
    changed = False
    for a in existing:
        if a.get("affiliations"):
            continue
        hit = by_name.get(re.sub(r"\W+", "", (a.get("name") or "").lower()))
        if hit and hit["affiliations"]:
            a["affiliations"], a["countries"] = hit["affiliations"], hit["countries"]
            a.setdefault("openalex_id", hit.get("openalex_id"))
            changed = True
    return existing, changed


def apply(state):
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    n = 0
    for p in papers:
        if p.get("av_relevance") != "AV" or has_affiliation(p):
            continue
        found = state["details"].get("t:" + norm(p.get("title")))
        if not found:
            d = paper_doi(p)
            found = state["details"].get("d:" + d) if d else None
        if not found:
            continue
        detail, changed = merge_details(p.get("authors_detail"), found)
        if changed:
            p["authors_detail"] = detail
            p["authors_detail_source"] = p.get("authors_detail_source") or "openalex"
            n += 1
    write_json_atomic(PAPERS_FILE, papers)
    print(f"Added affiliations to {n} papers in papers_full.json.")
    left = sum(1 for p in papers if p.get("av_relevance") == "AV" and not has_affiliation(p))
    print(f"AV papers still without an affiliation: {left}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=("venues", "dois"))
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    state = load_json(OUT_FILE, {})
    state.setdefault("details", {})
    state.setdefault("done_venue_years", [])
    state.setdefault("done_dois", [])
    if args.apply:
        apply(state)
        return 0
    deadline = time.monotonic() + (args.max_hours or 1e6) * 3600
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    print(f"{len(targets(papers))} AV papers without an affiliation", flush=True)
    done = True
    if args.only in (None, "venues"):
        done = pass_venues(papers, state, deadline)
    if done and args.only in (None, "dois"):
        done = pass_dois(papers, state, deadline)
    write_json_atomic(OUT_FILE, state)
    print(f"{'Finished' if done else 'Stopped on the time budget'}: {len(state['details'])} papers with affiliations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
