#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bulk backfill of missing ICML abstracts from PMLR (proceedings.mlr.press).

Every PMLR volume carries one file with the whole proceedings, abstracts
included: https://proceedings.mlr.press/vNNN/assets/bib/bibliography.bib (a
few MB). That is one request per ICML year instead of one per paper. The
volumes that are the main ICML proceedings are found on the PMLR index page
(titles like "Proceedings of ICML 2023" or "ICML 2016 Proceedings"; workshop
volumes are skipped). ICML before 2013 is not on PMLR.

Only abstracts of papers we are missing, matched on exact normalized title
within the same year, are kept. Resumable: data/abstracts_web_pmlr.json
(written here, applied by fetch_abstracts_web.py --apply) lists the volumes
that are finished. A failed download never marks a volume done; rate limits
and server errors are waited out (Retry-After, else exponential backoff).

Usage:
  python fetch_abstracts_pmlr.py
  python fetch_abstracts_pmlr.py --max-hours 1
  python fetch_abstracts_pmlr.py --year 2023
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict

from atomic_write import write_json_atomic
from fetch_abstracts_web import (BACKOFF_CAP, BACKOFF_START, DATA_DIR, PAPERS_FILE, Unavailable,
                                 clean_abstract, http_get, load_json, norm)

OUT_FILE = DATA_DIR / "abstracts_web_pmlr.json"
INDEX_URL = "https://proceedings.mlr.press/"
BIB_URL = "https://proceedings.mlr.press/v{volume}/assets/bib/bibliography.bib"
VENUE = "ICML"
GAP = 1.0
MAX_WAIT = 2 * 3600

_VOLUME_RE = re.compile(
    r'<li><a href="v(\d+)"><b>Volume \d+</b></a>\s*'
    r'(?:Proceedings of ICML (\d{4})|ICML (\d{4}) Proceedings)\s*</li>')
_ACCENTS = {"'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302", "~": "\u0303",
            "=": "\u0304", ".": "\u0307", "v": "\u030c", "u": "\u0306", "H": "\u030b",
            "c": "\u0327", "k": "\u0328", "r": "\u030a"}
_SPECIAL = {"ss": "\u00df", "o": "\u00f8", "O": "\u00d8", "aa": "\u00e5", "AA": "\u00c5", "ae": "\u00e6",
            "AE": "\u00c6", "l": "\u0142", "L": "\u0141", "i": "i", "j": "j"}
_last = [0.0]


def parse_index(page):
    """{year: volume} for the main ICML proceedings volumes on the PMLR index page."""
    out = {}
    for m in _VOLUME_RE.finditer(page):
        out[int(m.group(2) or m.group(3))] = int(m.group(1))
    return out


def _accent(m):
    mark, letter = m.group(1), m.group(2)
    if mark in "'`\"^~=." or mark in "vuHckr":
        return unicodedata.normalize("NFC", letter + _ACCENTS[mark])
    return letter


def unlatex(text):
    """Turn the LaTeX in a bib field into plain text (math between $ is kept as written)."""
    parts = text.split("$")
    for i in range(0, len(parts), 2):  # even parts are outside math
        s = parts[i]
        s = re.sub(r"\\([\'`\"^~=.])\s*\{?\\?([a-zA-Z])\}?", _accent, s)
        s = re.sub(r"\\([vuHckr])\s*\{([a-zA-Z])\}", _accent, s)
        s = re.sub(r"\\([vuHckr]) ([a-zA-Z])", _accent, s)
        s = re.sub(r"\\(ss|aa|AA|ae|AE|o|O|l|L)(?![a-zA-Z])\s?", lambda m: _SPECIAL[m.group(1)], s)
        s = re.sub(r"\\([%&#_$])", r"\1", s)
        s = s.replace("{", "").replace("}", "").replace("~", " ")
        parts[i] = s
    return "$".join(parts)


def _braced(text, start):
    """The text inside the {...} that opens at text[start], honouring nesting and \\{ escapes."""
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return None


def parse_bib(text):
    """[(title, abstract)] for every @InProceedings entry; entries without both are skipped."""
    out = []
    for chunk in re.split(r"^@", text, flags=re.M)[1:]:
        if not chunk.lower().startswith("inproceedings"):
            continue
        fields = {}
        for name in ("title", "abstract"):
            m = re.search(r"^\s*" + name + r"\s*=\s*\{", chunk, re.M)
            if m:
                fields[name] = _braced(chunk, m.end() - 1)
        if fields.get("title") and fields.get("abstract"):
            out.append((unlatex(fields["title"]), unlatex(fields["abstract"])))
    return out


def get(url, sleep=time.sleep, clock=time.monotonic, fetch=http_get):
    """One download with the waiting rules above. Returns the body, or None for a 404."""
    backoff = BACKOFF_START
    while True:
        gap = _last[0] + GAP - clock()
        if gap > 0:
            sleep(gap)
        _last[0] = clock()
        try:
            return fetch(url)
        except Unavailable as e:
            wait = min(max(e.retry_after, backoff), MAX_WAIT)
            print(f"  PMLR {e}; waiting {wait / 60:.1f} min", flush=True)
            sleep(wait)
            backoff = min(backoff * 2, BACKOFF_CAP)


def harvest_volume(entries, wanted, found):
    """Keep abstracts of wanted titles (normalized) from parsed entries; returns how many were added."""
    added = 0
    for title, abstract in entries:
        nt = norm(title)
        if nt in wanted and nt not in found:
            text = clean_abstract(abstract)
            if text:
                found[nt] = {"text": text, "source": "pmlr"}
                added += 1
    return added


def missing_by_year(papers, venue=VENUE):
    out = defaultdict(set)
    for p in papers:
        if p.get("venue") == venue and p.get("year") and not (p.get("abstract") or "").strip():
            nt = norm(p.get("title"))
            if nt:
                out[p["year"]].add(nt)
    return out


def run(papers, state, volumes, max_seconds=None, only_year=None, save=None, clock=time.monotonic, fetch_bib=None,
        log=print):
    """Process each year's volume that still has missing papers and is not done. Returns True if all finished."""
    started = clock()
    missing = missing_by_year(papers)
    fetch_bib = fetch_bib or (lambda v: get(BIB_URL.format(volume=v)))
    for year in sorted(missing, reverse=True):
        if only_year and year != only_year:
            continue
        volume = volumes.get(year)
        if volume is None:
            log(f"{VENUE} {year}: not on PMLR, {len(missing[year])} papers still missing")
            continue
        if volume in state["done"]:
            continue
        if max_seconds is not None and clock() - started > max_seconds:
            log("Time budget used up; stopping.")
            return False
        raw = fetch_bib(volume)
        if raw is None:
            log(f"{VENUE} {year}: volume {volume} has no bibliography.bib")
            state["done"].append(volume)  # an honest 404, nothing to retry
        else:
            before = len(state["abstracts"])
            entries = parse_bib(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw)
            harvest_volume(entries, missing[year], state["abstracts"])
            state["done"].append(volume)
            log(f"{VENUE} {year} (v{volume}): {len(entries)} papers with abstracts, "
                f"{len(state['abstracts']) - before} of {len(missing[year])} missing abstracts found")
        if save:
            save()
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--year", type=int, default=None, help="only this ICML year")
    args = ap.parse_args(argv)
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    state = load_json(OUT_FILE, {"abstracts": {}, "done": []})
    state.setdefault("abstracts", {})
    state.setdefault("done", [])
    index = get(INDEX_URL)
    volumes = parse_index(index.decode("utf-8", "replace"))
    if not volumes:
        print("Could not find any ICML volumes on the PMLR index page; layout changed?", file=sys.stderr)
        return 1
    print(f"PMLR index: ICML volumes {dict(sorted(volumes.items()))}", flush=True)
    run(papers, state, volumes, max_seconds=args.max_hours * 3600 if args.max_hours else None,
        only_year=args.year, save=lambda: write_json_atomic(OUT_FILE, state))
    write_json_atomic(OUT_FILE, state)
    print(f"Finished: {len(state['abstracts'])} abstracts in total.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
