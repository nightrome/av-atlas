#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finds Google Scholar profile URLs without asking Google: a person can list
their own Scholar profile among the "websites & social links" on their ORCID
record, and ORCID's public API returns those.

Reads the ORCIDs OpenAlex gave the authors of AV papers (authors_detail[].orcid
in data/papers_full.json), asks ORCID for each one's researcher URLs, and keeps
any that are a Scholar profile. Writes data/scholar_profile_candidates.json:

  {"profiles": {author name: {"scholar_url": ..., "orcid": ...}},
   "checked":  {orcid: date}}

These are candidates, not confirmed profiles, and are kept apart from
data/scholar_profiles.json (which also drives the photos shown on the site).
fetch_scholar_paper_links.py uses them only to find rows to read, and still
accepts a paper only when the title, an author and the year agree, so a profile
that belongs to someone else can never produce a link on its own.

Resumable: an ORCID is recorded as checked once ORCID has answered for it.
Stops at the first HTTP 429.

Usage: python scripts/fetch_orcid_scholar_profiles.py [--max N]
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate import clean_author_name
from fetch_common import BASE

PAPERS_FILE = BASE / "data" / "papers_full.json"
CANDIDATES_FILE = BASE / "data" / "scholar_profile_candidates.json"
ORCID_URL = "https://pub.orcid.org/v3.0/{}/researcher-urls"
SCHOLAR_USER_RE = re.compile(r"scholar\.google\.[a-z.]+/citations\?(?:[^\"'#\s]*?&)?user=([\w-]{12})", re.I)
DELAY_SECONDS = 0.3
CONTACT = "holger@it-caesar.com"


class Blocked(Exception):
    pass


def scholar_profile_from_urls(researcher_urls):
    """The canonical Scholar profile URL among an ORCID record's URLs, or None."""
    for item in researcher_urls or []:
        value = ((item or {}).get("url") or {}).get("value") or ""
        m = SCHOLAR_USER_RE.search(value)
        if m:
            return f"https://scholar.google.com/citations?user={m.group(1)}"
    return None


def orcids_from_papers(papers):
    """{orcid: author name} for the authors of AV papers."""
    out = {}
    for p in papers:
        if p.get("av_relevance") != "AV":
            continue
        for a in p.get("authors_detail") or []:
            if a.get("orcid") and a.get("name"):
                out.setdefault(a["orcid"], clean_author_name(a["name"]))
    return out


def fetch_researcher_urls(orcid):
    req = urllib.request.Request(ORCID_URL.format(orcid), headers={
        "Accept": "application/json", "User-Agent": f"av-atlas (mailto:{CONTACT})"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8")).get("researcher-url") or []
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise Blocked("HTTP 429")
        if e.code in (404, 409):      # no such record / locked or deprecated
            return []
        raise


def load_state():
    if CANDIDATES_FILE.exists():
        s = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8"))
    else:
        s = {}
    s.setdefault("profiles", {})
    s.setdefault("checked", {})
    return s


def save_state(state):
    CANDIDATES_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                               encoding="utf-8")


def run(orcids, state, max_checks=0, fetch=fetch_researcher_urls, sleep=time.sleep):
    todo = [o for o in orcids if o not in state["checked"]]
    print(f"{len(orcids)} ORCIDs, {len(todo)} still to check, {len(state['profiles'])} profiles so far")
    done = 0
    for orcid in todo:
        if max_checks and done >= max_checks:
            break
        sleep(DELAY_SECONDS)
        try:
            url = scholar_profile_from_urls(fetch(orcid))
        except Blocked as e:
            print("stopping:", e)
            break
        except Exception as e:                       # one bad record must not end the run
            print(f"skipping {orcid}: {e}")
            continue
        if url:
            state["profiles"][orcids[orcid]] = {"scholar_url": url, "orcid": orcid}
        state["checked"][orcid] = date.today().isoformat()
        done += 1
        if done % 200 == 0:
            save_state(state)
            print(f"{done} checked, {len(state['profiles'])} Scholar profiles found", flush=True)
    save_state(state)
    print(f"done: {done} checked this run, {len(state['profiles'])} Scholar profiles in total")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="0 = no limit")
    args = ap.parse_args()
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    run(orcids_from_papers(papers), load_state(), args.max)


if __name__ == "__main__":
    main()
