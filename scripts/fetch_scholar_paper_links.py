#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Finds each AV paper's own Google Scholar URL and writes the confirmed
ones to data/scholar_paper_links.json ({"links": {normalized title: url}}).

Two ways, chosen with --mode:

search (default): searches Scholar for the quoted title and accepts a result
only when its title is exactly the paper's (aggregate.normalize_title) and its
year, when Scholar shows one, is within two years of the paper's. The link is
the result's Scholar cluster (https://scholar.google.com/scholar?cluster=...),
which is Scholar's own page for that paper's versions. Papers already searched
without a match are remembered ("searched" in the output file) and not asked
again. Most-cited papers first. With --wait-minutes the run waits out a block
and carries on, so it can sit in the background for days.

profiles: the older route. It reads the Scholar profile pages already in
data/scholar_profiles.json, which needs no search but only reaches papers
whose author has a known profile. A paper listed
on an author's profile has a stable citation URL:
  https://scholar.google.com/citations?view_op=view_citation&hl=en
      &citation_for_view=<user>:<id>

A profile row only counts as the paper when ALL of these hold (see DECISIONS.md,
"Scholar profiles and photos: confirm or skip" -- a wrong link is worse than none):
  - the normalized title is identical (aggregate.normalize_title),
  - at least one author agrees (same surname and first initial),
  - the years do not contradict (within one year, since preprint and venue
    years differ),
  - the title is unique among the target papers and unique on that profile.
Anything else stays without a link.

In profiles mode only the --top most-cited AV papers (default 1000) are looked
at, and only the profiles of their authors are fetched, most-cited paper first.
One request every few seconds; the run stops at the first block (HTTP 429/403,
a CAPTCHA or an unexpected page) and can be rerun later, since finished
profiles are recorded in the output file.

Usage: python scripts/fetch_scholar_paper_links.py [--mode search|profiles]
           [--top N] [--max-searches N] [--wait-minutes 45] [--max-profiles N]
"""
import argparse
import html
import json
import random
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aggregate import clean_author_name, normalize_title
from fetch_common import BASE, by_citations

PROFILES_FILE = BASE / "data" / "scholar_profiles.json"
# Profile URLs people list on their ORCID record (fetch_orcid_scholar_profiles.py).
CANDIDATES_FILE = BASE / "data" / "scholar_profile_candidates.json"
LINKS_FILE = BASE / "data" / "scholar_paper_links.json"
STATS_FILE = BASE / "data" / "stats.json"

PAGE_SIZE = 100
MAX_PAGES_PER_PROFILE = 5      # 500 most-cited works; enough for the target set
DELAY_SECONDS = (4.0, 7.0)
BROWSER_UA = "Mozilla/5.0 (compatible; av-atlas link check; mailto:holger@it-caesar.com)"

ROW_RE = re.compile(r'<tr class="gsc_a_tr">(.*?)</tr>', re.S)
TITLE_RE = re.compile(r'<a href="([^"]*citation_for_view=([^"&]+))"[^>]*class="gsc_a_at">(.*?)</a>', re.S)
GRAY_RE = re.compile(r'<div class="gs_gray">(.*?)</div>', re.S)
YEAR_RE = re.compile(r'<td class="gsc_a_y">.*?>(\d{4})<', re.S)
TAG_RE = re.compile(r"<[^>]+>")


class Blocked(Exception):
    pass


def citation_url(citation_for_view):
    return ("https://scholar.google.com/citations?view_op=view_citation&hl=en"
            "&citation_for_view=" + citation_for_view)


def parse_profile_rows(page_html):
    """Every work listed on one page of a Scholar profile."""
    rows = []
    for body in ROW_RE.findall(page_html):
        m = TITLE_RE.search(body)
        if not m:
            continue
        grays = [html.unescape(TAG_RE.sub("", g)).strip() for g in GRAY_RE.findall(body)]
        ym = YEAR_RE.search(body)
        rows.append({
            "title": html.unescape(TAG_RE.sub("", m.group(3))).strip(),
            "citation_for_view": html.unescape(m.group(2)),
            "authors": grays[0] if grays else "",
            "venue": grays[1] if len(grays) > 1 else "",
            "year": int(ym.group(1)) if ym else None,
        })
    return rows


def _fold(s):
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def _surname_initial(full_name):
    parts = clean_author_name(full_name).replace("-", " ").split()
    if len(parts) < 2:
        return None
    return _fold(parts[-1]), _fold(parts[0])[:1]


def _row_author_keys(authors_line):
    """{(surname, initials)}. Scholar abbreviates ("JM Zöllner", "H Caesar");
    a trailing "..." means the list was cut off, which only ever hides
    authors, never invents them."""
    keys = set()
    for a in authors_line.split(","):
        a = a.strip()
        if not a or a == "...":
            continue
        parts = a.replace("-", " ").split()
        if len(parts) < 2:
            continue
        keys.add((_fold(parts[-1]), _fold(parts[0])))
    return keys


def row_matches_paper(row, paper):
    """The confirmation rule described in the module docstring (title
    uniqueness is checked by the caller, which sees the whole target set)."""
    if normalize_title(row["title"]) != normalize_title(paper.get("title")):
        return False
    paper_keys = {k for k in map(_surname_initial, paper.get("authors") or []) if k}
    row_keys = _row_author_keys(row["authors"])
    # "JM Zöllner" is listed for Marius Zöllner: the corpus initial may be any
    # of the row's initials.
    if not any(rs == ps and pi in ri for ps, pi in paper_keys for rs, ri in row_keys):
        return False
    try:
        py = int(paper.get("year"))
    except (TypeError, ValueError):
        py = None
    if row["year"] and py and abs(row["year"] - py) > 1:
        return False
    return True


def find_links(rows, papers_by_title, user):
    """{normalized title: url} for the rows of one profile that confirm a paper."""
    by_norm = {}
    for r in rows:
        by_norm.setdefault(normalize_title(r["title"]), []).append(r)
    out = {}
    for norm, paper in papers_by_title.items():
        if paper is None:               # ambiguous among the target papers
            continue
        hits = by_norm.get(norm, [])
        if len(hits) != 1:              # absent, or listed twice on this profile
            continue
        if row_matches_paper(hits[0], paper):
            out[norm] = citation_url(hits[0]["citation_for_view"])
    return out


def target_papers(all_papers, top):
    av = [p for p in all_papers if p.get("av_relevance") == "AV"]
    return by_citations(av)[:top]


def index_by_title(papers):
    """normalized title -> paper, or None where two target papers share it."""
    idx = {}
    for p in papers:
        n = normalize_title(p.get("title"))
        idx[n] = None if n in idx else p
    return idx


def profile_users_for(papers, profiles):
    """[(user id, [target papers by that person])] ordered by the person's
    most-cited target paper, so a cut-off run has covered the best first."""
    by_name = {clean_author_name(n): prof for n, prof in profiles.items()}
    users = {}
    order = []
    for p in papers:
        for a in p.get("authors") or []:
            prof = by_name.get(clean_author_name(a if isinstance(a, str) else a.get("name")))
            if not prof:
                continue
            m = re.search(r"user=([^&]+)", prof.get("scholar_url", ""))
            if not m:
                continue
            u = m.group(1)
            if u not in users:
                users[u] = []
                order.append(u)
            users[u].append(p)
    return [(u, users[u]) for u in order]


def fetch_page(user, cstart):
    url = (f"https://scholar.google.com/citations?user={user}&hl=en"
           f"&pagesize={PAGE_SIZE}&cstart={cstart}")
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise Blocked(f"HTTP {e.code} for {user}")
    if "gsc_a_tr" not in body and "gsc_a_e" not in body:
        raise Blocked(f"unexpected page (CAPTCHA or block?) for {user}")
    return body


# ---- search mode -------------------------------------------------------------------

SEARCH_DELAY_SECONDS = (12.0, 25.0)
SEARCH_YEAR_TOLERANCE = 2
CITATION_TAIL_CHARS = 40
RESULT_TAG_RE = re.compile(r'<div[^>]*class="gs_r [^"]*"[^>]*>')
RESULT_TITLE_RE = re.compile(r'<h3 class="gs_rt"[^>]*>(.*?)</h3>', re.S)
RESULT_META_RE = re.compile(r'<div class="gs_a"[^>]*>(.*?)</div>', re.S)
LEADING_TYPE_RE = re.compile(r"^(?:\[[A-Za-z]+\]\s*)+")
YEAR_IN_TEXT_RE = re.compile(r"\b((?:19|20)\d{2})\b")


def cluster_url(cid):
    return f"https://scholar.google.com/scholar?cluster={cid}&hl=en"


def parse_search_results(page_html):
    """Every result on a Scholar search page: title, cluster id, year (or None)."""
    tags = list(RESULT_TAG_RE.finditer(page_html))
    results = []
    for i, tag in enumerate(tags):
        cid = re.search(r'data-cid="([^"]+)"', tag.group(0))
        end = tags[i + 1].start() if i + 1 < len(tags) else len(page_html)
        block = page_html[tag.end():end]
        t = RESULT_TITLE_RE.search(block)
        if not cid or not t:
            continue
        title = html.unescape(TAG_RE.sub("", t.group(1))).strip()
        citation_only = "[CITATION]" in title
        title = LEADING_TYPE_RE.sub("", title).strip()
        meta = RESULT_META_RE.search(block)
        years = YEAR_IN_TEXT_RE.findall(html.unescape(TAG_RE.sub("", meta.group(1)))) if meta else []
        results.append({"title": title, "cid": html.unescape(cid.group(1)),
                        "year": int(years[-1]) if years else None, "citation_only": citation_only})
    return results


def match_search_result(results, paper):
    """The Scholar URL of the first result whose title is exactly this paper's,
    or None. A year that is far off rules a result out (the same title is
    sometimes used by an unrelated paper); a missing year does not.

    One loosening: a "[CITATION]" record (a reference Scholar has no page for)
    often carries the citation text after the title, e.g. "..., CoRR
    abs/1604.01685". It counts when it starts with the exact title and adds
    at most CITATION_TAIL_CHARS more characters."""
    want = normalize_title(paper.get("title"))
    try:
        py = int(paper.get("year"))
    except (TypeError, ValueError):
        py = None
    for r in results:
        got = normalize_title(r["title"])
        if got != want and not (r.get("citation_only") and got.startswith(want)
                                and len(got) - len(want) <= CITATION_TAIL_CHARS):
            continue
        if r["year"] and py and abs(r["year"] - py) > SEARCH_YEAR_TOLERANCE:
            continue
        return cluster_url(r["cid"])
    return None


def fetch_search(title):
    url = ("https://scholar.google.com/scholar?hl=en&as_sdt=0%2C5&q="
           + urllib.parse.quote('"' + title + '"'))
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise Blocked(f"HTTP {e.code}")
    if "gs_r" not in body and "did not match any articles" not in body:
        raise Blocked("unexpected page (CAPTCHA or block?)")
    return body


def search_todo(papers, idx, state):
    """Target papers still to search, most-cited first: not linked, not already
    searched, and whose title no other target paper shares."""
    out = []
    for p in papers:
        n = normalize_title(p.get("title"))
        if idx.get(n) is None or n in state["links"] or n in state["searched"]:
            continue
        out.append(p)
    return out


def run_search(papers, idx, state, max_searches=0, wait_minutes=0, sleep=time.sleep, fetch=fetch_search):
    todo = search_todo(papers, idx, state)
    print(f"{len(todo)} papers to search, {len(state['links'])} links so far")
    done = blocks = 0
    i = 0
    while i < len(todo):
        if max_searches and done >= max_searches:
            break
        p = todo[i]
        sleep(random.uniform(*SEARCH_DELAY_SECONDS))
        try:
            results = parse_search_results(fetch(p["title"]))
        except Blocked as e:
            save_state(state)
            blocks += 1
            if not wait_minutes or blocks > 20:
                print("stopping:", e)
                break
            print(f"blocked ({e}); waiting {wait_minutes} min (block {blocks})", flush=True)
            sleep(wait_minutes * 60)
            continue
        blocks = 0
        n = normalize_title(p["title"])
        url = match_search_result(results, p)
        if url:
            state["links"][n] = url
        else:
            state["searched"][n] = date.today().isoformat()
            # What Scholar did return, so a low hit rate can be explained from the log.
            seen = [(r["title"][:70], r["year"]) for r in results[:3]]
            print(f"miss: {p['title'][:70]!r} ({p.get('year')}) -> {len(results)} results {seen}", flush=True)
        i += 1
        done += 1
        if done % 5 == 0:
            save_state(state)
        if done % 25 == 0:
            print(f"{done} searched, {len(state['links'])} links", flush=True)
    save_state(state)
    print(f"done: {done} searched this run, {len(state['links'])} links in total")


def load_profiles():
    """scholar_profiles.json plus the ORCID-listed candidates. The confirmed
    profiles win on a name clash. A candidate only says where to look; every
    paper is still confirmed row by row (find_links)."""
    profiles = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    if CANDIDATES_FILE.exists():
        candidates = json.loads(CANDIDATES_FILE.read_text(encoding="utf-8")).get("profiles", {})
        profiles = {**candidates, **profiles}
    return profiles


def load_state():
    if LINKS_FILE.exists():
        s = json.loads(LINKS_FILE.read_text(encoding="utf-8"))
    else:
        s = {}
    s.setdefault("links", {})
    s.setdefault("profiles_done", {})
    s.setdefault("searched", {})
    return s


def save_state(state):
    LINKS_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                          encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["search", "profiles"], default="search")
    ap.add_argument("--top", type=int, default=None,
                    help="most-cited AV papers to look at (search: all, profiles: 1000)")
    ap.add_argument("--max-profiles", type=int, default=0, help="profiles mode; 0 = no limit")
    ap.add_argument("--max-searches", type=int, default=0, help="search mode; 0 = no limit")
    ap.add_argument("--wait-minutes", type=int, default=0,
                    help="search mode: wait this long after a block and carry on (0 = stop)")
    ap.add_argument("--delay", type=float, nargs=2, metavar=("MIN", "MAX"), default=None,
                    help="search mode: seconds between searches (default 12 25)")
    args = ap.parse_args()
    if args.delay:
        global SEARCH_DELAY_SECONDS
        SEARCH_DELAY_SECONDS = tuple(args.delay)

    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    if args.mode == "search":
        papers = target_papers(stats["all_papers"], args.top or 10**9)
        run_search(papers, index_by_title(papers), load_state(), args.max_searches, args.wait_minutes)
        return
    papers = target_papers(stats["all_papers"], args.top or 1000)
    idx = index_by_title(papers)
    profiles = load_profiles()
    todo = profile_users_for(papers, profiles)
    state = load_state()
    print(f"{len(papers)} target papers, {len(todo)} profiles, "
          f"{len(state['profiles_done'])} already done, {len(state['links'])} links so far")

    fetched = 0
    for user, mine in todo:
        if user in state["profiles_done"]:
            continue
        if args.max_profiles and fetched >= args.max_profiles:
            break
        wanted = {normalize_title(p["title"]) for p in mine}
        rows = []
        try:
            for page in range(MAX_PAGES_PER_PROFILE):
                time.sleep(random.uniform(*DELAY_SECONDS))
                batch = parse_profile_rows(fetch_page(user, page * PAGE_SIZE))
                rows += batch
                if len(batch) < PAGE_SIZE or wanted <= {normalize_title(r["title"]) for r in rows}:
                    break
        except Blocked as e:
            print("stopping:", e)
            break
        found = find_links(rows, {n: idx[n] for n in wanted if n in idx}, user)
        for n, url in found.items():
            state["links"].setdefault(n, url)
        state["profiles_done"][user] = date.today().isoformat()
        save_state(state)
        fetched += 1
        print(f"{user}: {len(found)}/{len(wanted)} confirmed ({len(state['links'])} total)")


if __name__ == "__main__":
    main()
