#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fills in abstracts for an ICRA/IROS edition from the conference's official
PaperCept program pages, which list every paper with its title, authors,
affiliations and abstract:

  https://ras.papercept.net/conferences/conferences/<CODE>/program/<CODE>_ContentListWeb_<N>.html

The community GitHub lists we take the paper list from (fetch_github_paper_lists.py)
have titles and authors only. This matches each program entry to a paper in
data/venues/<venue><year>_github.json by normalized title and adds the abstract
where the paper has none. Nothing else in the file changes, and papers with no
program match are left as they are.

The program pages are fetched once each (a handful of requests) and cached in
data/papercept_<code>/ (gitignored), so a rerun doesn't refetch them. Program
pages disappear some months after a conference (ICRA24's already return 403),
which is why it's worth running as soon as an edition's program is up.

Usage: python fetch_papercept_abstracts.py ICRA 2026
"""
import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from fetch_common import HEADERS, OUT_DIR

sys.path.insert(0, str(Path(__file__).parent))
from aggregate import normalize_title  # noqa: E402

BASE = "https://ras.papercept.net/conferences/conferences/{code}/program/{code}_ContentListWeb_{n}.html"
ENTRY_RE = re.compile(
    r"viewAbstract\('(?P<id>\d+)'\)[^>]*>(?P<title>.*?)</a>.*?"
    r'<div id="Ab(?P=id)"[^>]*>(?P<body>.*?)</div>',
    re.S,
)
ABSTRACT_RE = re.compile(r"<strong>Abstract:</strong>(.*)", re.S)
TAG_RE = re.compile(r"<[^>]+>")


def clean(text):
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", text))).strip()


def parse_program(page):
    """Yields (title, abstract) for every paper on one program page."""
    for m in ENTRY_RE.finditer(page):
        ab = ABSTRACT_RE.search(m.group("body"))
        if not ab:
            continue
        title, abstract = clean(m.group("title")), clean(ab.group(1))
        if title and abstract:
            yield title, abstract


def program_pages(code, cache_dir, max_pages=12):
    cache_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, max_pages + 1):
        cached = cache_dir / f"{n}.html"
        if cached.exists():
            yield cached.read_text(encoding="utf-8")
            continue
        # The pages are Windows-1252, which fetch_common.fetch() would decode as
        # UTF-8 and lose every curly quote, so read the raw bytes here.
        req = urllib.request.Request(BASE.format(code=code, n=n), headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("cp1252", errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return
            raise
        cached.write_text(text, encoding="utf-8")
        yield text
        time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("venue", choices=["ICRA", "IROS"])
    ap.add_argument("year", type=int)
    args = ap.parse_args()

    code = f"{args.venue}{args.year % 100:02d}"
    out = OUT_DIR / f"{args.venue.lower()}{args.year}_github.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    papers = data["papers"] if isinstance(data, dict) else data

    abstracts = {}
    for page in program_pages(code, OUT_DIR.parent / f"papercept_{code.lower()}"):
        for title, abstract in parse_program(page):
            abstracts.setdefault(normalize_title(title), abstract)
    print(f"{code}: {len(abstracts)} abstracts in the program")

    filled = 0
    for p in papers:
        if p.get("abstract"):
            continue
        ab = abstracts.get(normalize_title(p.get("title", "")))
        if ab:
            p["abstract"] = ab
            filled += 1
    print(f"filled {filled} of {len(papers)} papers in {out.name}")
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline=chr(10))
    tmp.replace(out)


if __name__ == "__main__":
    main()
