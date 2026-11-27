#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Checks the affiliations read from arXiv HTML pages against those pages again.

Some papers got the wrong affiliation on some authors: one author carried
another author's institution, or an affiliation note was a co-author's name or
the rest of an address. This reads each page's author blocks once more (no
LLM) and compares them with what papers_full.json holds.

  --run      fetch the pages (about one per second, resumable) into
             data/affiliations_recheck.json; a page is never fetched twice
  --apply    correct papers_full.json from that file:
               * an entry that is a co-author's name, an e-mail address or a
                 corresponding-author note is dropped
               * an author with no affiliation gets an institution already
                 stored for a co-author when the author's own block names it
  --report   count what --apply would change

Usage: python recheck_affiliations_arxiv.py --run [--max-hours 3]
"""
import argparse
import json
import re
import sys
import time
import urllib.request

from atomic_write import write_json_atomic
from fetch_abstracts_web import DATA_DIR, PAPERS_FILE

OUT = DATA_DIR / "affiliations_recheck.json"
COUNTRY_NAMES = {norm_c for norm_c in (
    "".join(ch for ch in c.lower() if ch.isalnum()) for c in (
        "USA", "U.S.A.", "United States", "UK", "United Kingdom", "Germany", "China", "Canada", "France", "Italy",
        "Spain", "Japan", "Korea", "South Korea", "India", "Australia", "Switzerland", "Netherlands", "The Netherlands",
        "Sweden", "Norway", "Denmark", "Finland", "Austria", "Belgium", "Israel", "Singapore", "Taiwan", "Hong Kong",
        "Brazil", "Mexico", "Russia", "Turkey", "Greece", "Portugal", "Poland", "Czech Republic", "Ireland", "Iran",
        "Saudi Arabia", "UAE", "Egypt", "Pakistan", "Bangladesh", "Vietnam", "Thailand", "Malaysia", "Indonesia",
        "New Zealand", "South Africa", "Hungary", "Romania", "Ukraine", "Chile", "Argentina", "Colombia"))}
SPACING = 1.0


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def words(s):
    return [w for w in re.findall(r"[a-z]{3,}", (s or "").lower())]


def mentions(block_text, aff):
    """Does the author's own note on the page mention this affiliation?"""
    text = norm(block_text)
    if not text:
        return False
    if norm(aff) and norm(aff) in text:
        return True
    ws = [w for w in words(aff) if w not in {"the", "and", "for", "university", "institute", "department"}]
    ws = ws or words(aff)
    return bool(ws) and sum(1 for w in ws if w in text) >= max(1, (len(ws) + 1) // 2)


_KEY_VALUE = re.compile(r"^\s*(organization|city|country|addressline|postcode|state|department|institution)\s*=", re.I)


_INSTITUTION = re.compile(r"universit|college|polytechn|institute of|ecole|école|hochschule|universid|università|"
                          r"universität|ETH|EPFL|MIT", re.I)
_UNIT = re.compile(r"^\s*(the\s+)?(department|dept|school|faculty|division|laboratory|lab|center|centre|group|chair|"
                   r"key lab|state key|college of|institute for|institute of automation)", re.I)


def is_institution(x, known=None):
    """A whole institution, not a department, a city or a country fragment."""
    if _KEY_VALUE.match(x) or _UNIT.match(x) or len(x) < 4:
        return False
    return bool(_INSTITUTION.search(x)) or (known is not None and norm(x) in known)


def _junk(x, names):
    """Not an affiliation at all: a co-author's name, an e-mail address or a corresponding-author note."""
    return norm(x) in names or "@" in x or "corresponding author" in x.lower()


def reconcile(stored, page, known=None, countries=None):
    """stored: [{"name", "affiliations"}], page: {name: raw note text}; known: institution names the corpus
    already has; countries: normalized country names (never added as an affiliation).
    Returns (affiliations by author name, removals, additions).

    Only two kinds of change are made, because anything fuzzier added wrong institutions in a sample check:
      * an entry that is a co-author's name, an e-mail address or a corresponding-author note is dropped;
      * an author with no affiliation at all gets the institutions already stored for a co-author that this
        author's own block on the page names in full."""
    names = {norm(a["name"]) for a in stored}
    countries = countries or set()
    pool = []
    for a in stored:
        for x in a.get("affiliations") or []:
            if (not _junk(x, names) and x not in pool and norm(x) not in countries
                    and is_institution(x, known)):
                pool.append(x)
    out, removed, added = {}, 0, 0
    for a in stored:
        n, old = a["name"], list(a.get("affiliations") or [])
        new = [x for x in old if not _junk(x, names)]
        if not new and n in page and norm(page[n]):
            new = [x for x in pool if norm(x) in norm(page[n])]
        removed += len(old) - len([x for x in old if x in new])
        added += len([x for x in new if x not in old])
        out[n] = new
    return out, removed, added


def page_blocks(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    page = {}
    for blk in soup.select("span.ltx_creator.ltx_role_author"):
        n = blk.select_one(".ltx_personname")
        if not n:
            continue
        name = re.sub(r"\s+", " ", n.get_text()).strip()
        if not name or len(name.split()) > 4:
            continue
        page[name] = " ".join(re.sub(r"\s+", " ", a.get_text()) for a in blk.select(".ltx_role_affiliation"))
    return page


def fetch(arxiv_id):
    req = urllib.request.Request(f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}",
                                 headers={"User-Agent": "av-atlas-affiliation-check"})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")


def targets(papers):
    for p in papers:
        if p.get("authors_detail_source") == "arxiv-html" and p.get("arxiv_url"):
            yield p["arxiv_url"].rstrip("/").rsplit("/", 1)[-1], p


def run(max_hours, fetch_page=fetch, sleep=time.sleep, out=OUT, papers=None):
    papers = papers if papers is not None else json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    state = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    deadline = time.time() + max_hours * 3600
    n = 0
    for aid, p in targets(papers):
        if aid in state:
            continue          # checked before: never fetched twice
        if time.time() > deadline:
            break
        try:
            state[aid] = {"authors": page_blocks(fetch_page(aid))}
        except Exception as e:  # noqa: BLE001 -- nothing recorded, tried again next run
            msg = str(e)
            if "404" in msg:
                state[aid] = {"authors": {}, "error": "404"}
            else:
                print(f"  {aid}: {msg[:80]}", flush=True)
        n += 1
        if n % 50 == 0:
            write_json_atomic(out, state)
            print(f"{n} fetched, {len(state)} in total", flush=True)
        sleep(SPACING)
    write_json_atomic(out, state)
    print(f"done: {len(state)} pages checked")


def apply(report_only=False, out=OUT):
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    state = json.loads(out.read_text(encoding="utf-8"))
    cm = json.loads((DATA_DIR / "institution_countries.json").read_text(encoding="utf-8"))
    cm = {k: v for k, v in cm.items() if not k.startswith("_")}
    known = {norm(x) for x in cm}
    country_names = {norm(c) for c in cm.values()} | COUNTRY_NAMES
    papers_changed = removed = added = 0
    for aid, p in targets(papers):
        page = (state.get(aid) or {}).get("authors")
        if not page:
            continue
        new, r, a = reconcile(p["authors_detail"], page, known, country_names)
        if not (r or a):
            continue
        papers_changed += 1
        removed += r
        added += a
        if report_only:
            continue
        for d in p["authors_detail"]:
            d["affiliations"] = new[d["name"]]
            d["countries"] = sorted({cm[x] for x in d["affiliations"] if x in cm})
    print(f"{papers_changed} papers changed: {removed} affiliations removed, {added} added"
          + (" (report only)" if report_only else ""))
    if not report_only:
        write_json_atomic(PAPERS_FILE, papers, indent=2)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--max-hours", type=float, default=3.5)
    args = ap.parse_args(argv)
    if args.run:
        run(args.max_hours)
    elif args.apply or args.report:
        apply(report_only=args.report)
    else:
        ap.error("one of --run, --apply, --report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
