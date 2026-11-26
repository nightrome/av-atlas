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
               * an affiliation on an author whose own block on the page does
                 not mention it is dropped (also when it is another author's
                 name, or the page shows no note for that author)
               * an author left without one gets the affiliation of a
                 co-author whose stored value matches this author's block
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


def reconcile(stored, page, known=None):
    """stored: [{"name", "affiliations"}], page: {name: raw note text}; known: institution names the corpus
    already has (only those may be added). Returns (affiliations by author name, removals, additions).

    Changes are made only where they are certain: an affiliation that is a co-author's name goes; one the
    author's own block does not mention goes, but only if the author keeps at least one; an author without
    any gets the institutions of co-authors that their own block mentions."""
    names = {norm(a["name"]) for a in stored}
    pool = []
    for a in stored:
        for x in a.get("affiliations") or []:
            if norm(x) not in names and x not in pool and is_institution(x, known):
                pool.append(x)
    out, removed, added = {}, 0, 0
    for a in stored:
        n, old = a["name"], list(a.get("affiliations") or [])
        new = old
        if n in page:
            clean = [x for x in old if norm(x) not in names]
            keep = [x for x in clean if mentions(page[n], x)] if norm(page[n]) else clean
            if norm(page[n]):
                for x in pool:
                    if x not in keep and mentions(page[n], x) and not any(norm(x) == norm(y) for y in keep):
                        keep.append(x)
            new = keep if keep else clean
        if new != old:
            removed += len([x for x in old if x not in new])
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
    from aggregate import normalize_institution
    import collections
    counts = collections.Counter(normalize_institution(x) for p in papers for d in p.get("authors_detail") or []
                                 for x in d.get("affiliations") or [])
    known = {norm(x) for x, k in counts.items() if k >= 3}
    known = {norm(x) for x in cm}
    papers_changed = removed = added = 0
    for aid, p in targets(papers):
        page = (state.get(aid) or {}).get("authors")
        if not page:
            continue
        new, r, a = reconcile(p["authors_detail"], page, known)
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
