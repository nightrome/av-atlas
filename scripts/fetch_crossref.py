#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Crossref fetcher for the venues DBLP used to supply: the six journals
(T-ITS, RA-L, T-RO, TPAMI, IJCV, IJRR), the IEEE conferences IV and ITSC,
and GCPR's Springer proceedings. DBLP has served a bot challenge to every
scripted request since 2026-09-13 (see PIPELINE.md), so this replaces
fetch_dblp_listing.py for these venues.

Three ways of finding a venue's papers on Crossref:

- Journals by ISSN (all of a journal's print and online ISSNs), filtered by
  a date range so the monthly run only pulls what changed. The default
  filter is the metadata update date, not the creation date: IEEE redeposits
  an early-access article when it's assigned to an issue, and that's when
  its year can change (see "year" below). New papers are merged by
  normalized title into the existing data/venues/<journal>_all.json.
- IEEE conferences by exact container title ("2026 IEEE Intelligent Vehicles
  Symposium (IV)"). Crossref's container-title filter is asked for that one
  title, and every returned record is checked against it again here, so the
  IV Workshops volume (a separate container) never slips in.
- Springer proceedings (GCPR) by the book's ISBN, one chapter per paper.

Records use the same schema as every other venue file: title, authors (a
comma-separated "Given Family" string), abstract, doi, plus year for the
_all journal files (per-edition files get their year from the filename).
Titles and abstracts have their markup stripped (JATS in Springer/SAGE
abstracts, <inline-formula> and friends in IEEE titles) and HTML entities
unescaped.

Year of a journal paper: the print/issue year when Crossref has one, else
the online year. DBLP only listed a journal article once it was in an
issue, and gave it the issue's year, so this keeps the existing records'
years consistent. An early-access article (online, no issue yet) gets its
online year for now, and is corrected the next time it's seen with an issue.

Abstracts: IEEE deposits none with Crossref, so any record that has a DOI
but no abstract is looked up on Semantic Scholar's /paper/batch endpoint by
DOI, 500 per call. A Crossref abstract (IJRR, some IJCV) is never replaced;
same never-overwrite rule as apply_abstracts_semanticscholar.py.

Merging into an existing file never drops a record and never overwrites a
title, author list or abstract that's already there. It adds a doi where one
is missing and corrects a journal paper's year when Crossref has its issue
year. Rerunning a conference year that already has a DBLP file therefore
backfills DOIs and abstracts for it.

Crossref's polite pool: the User-Agent from fetch_common.py carries a mailto
address, and requests are spaced by REQUEST_DELAY.

Usage: python fetch_crossref.py T-ITS                       # updated in the last 40 days
       python fetch_crossref.py T-ITS --since 2026-08-01    # updated since a date
       python fetch_crossref.py T-ITS --published-since 2025-06-01
       python fetch_crossref.py journals --since 2026-08-01 # all six journals
       python fetch_crossref.py IV 2026
       python fetch_crossref.py ITSC 2012 2025              # a range of editions
       python fetch_crossref.py GCPR 2023
       add --no-abstracts to skip the Semantic Scholar step
       add --dry-run to print counts without writing anything
"""
import argparse
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache

from fetch_common import BASE, HEADERS, OUT_DIR, fetch as _fetch
from apply_abstracts_semanticscholar import fill_missing_abstract
from merge_corpus import normalize_title

CROSSREF_API = "https://api.crossref.org/works"
S2_BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
ENV_FILE = BASE / ".env"
REQUEST_DELAY = 1.0
S2_REQUEST_DELAY = 1.1  # documented 1 req/sec with a key, same as the other S2 scripts
S2_BATCH_SIZE = 500     # the endpoint's documented maximum
ROWS = 1000             # Crossref's maximum page size
DEFAULT_LOOKBACK_DAYS = 40  # a monthly run with some overlap; merging is idempotent
MIN_YEAR = 2012         # the corpus window; older records in an update delta are skipped

JOURNALS = {
    "T-ITS": ["1524-9050", "1558-0016"],
    "RA-L": ["2377-3766"],
    "T-RO": ["1552-3098", "1941-0468"],
    "TPAMI": ["0162-8828", "1939-3539", "2160-9292"],
    "IJCV": ["0920-5691", "1573-1405"],
    "IJRR": ["0278-3649", "1741-3176"],
}


def _ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# The exact container title IEEE uses for each edition. ITSC carries its
# edition number ("2025 IEEE 28th International ..."), counted from 1998.
# ICRA and IROS are here for when Crossref has 2026; they aren't run yet.
CONFERENCES = {
    "IV": lambda y: f"{y} IEEE Intelligent Vehicles Symposium (IV)",
    "ITSC": lambda y: f"{y} IEEE {_ordinal(y - 1997)} International Conference on Intelligent Transportation Systems (ITSC)",
    "ICRA": lambda y: f"{y} IEEE International Conference on Robotics and Automation (ICRA)",
    "IROS": lambda y: f"{y} IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)",
}

# Springer proceedings, by the ISBNs of the book(s) for each edition. Look a
# new year up on Crossref (type:book, subtitle "DAGM GCPR <year>") and add it.
PROCEEDINGS_ISBNS = {
    "GCPR": {2023: ["9783031546051", "9783031546044"]},
}

SELECT = "DOI,title,subtitle,author,abstract,published-print,published-online,issued,container-title,type"


def crossref_get(params):
    url = f"{CROSSREF_API}?{urllib.parse.urlencode(params)}"
    body = _fetch(url, timeout=60, max_retries=4, retry_status=(429, 500, 502, 503, 504), backoff=20)
    time.sleep(REQUEST_DELAY)
    return json.loads(body)["message"]


def crossref_items(filters):
    """Every record matching the filter, following Crossref's deep-paging
    cursor. filters is a list of "name:value" strings."""
    items = []
    cursor = "*"
    while True:
        params = {"filter": ",".join(filters), "rows": ROWS, "cursor": cursor, "select": SELECT}
        msg = crossref_get(params)
        page = msg.get("items") or []
        items.extend(page)
        print(f"  {len(items)} of {msg.get('total-results')} records", flush=True)
        cursor = msg.get("next-cursor")
        if not page or not cursor or len(page) < ROWS:
            return items


# Paragraph-level tags become a space so sentences don't run together;
# inline ones (<i>, <sub>, <mml:mi>) vanish so "H<sub>2</sub>O" stays "H2O".
_BLOCK_TAG_RE = re.compile(r"</?(?:jats:)?(?:p|sec|title|list|list-item|break|br|disp-formula)\b[^>]*>")
_TAG_RE = re.compile(r"<[^>]+>")
_JATS_TITLE_RE = re.compile(r"<jats:title>.*?</jats:title>", re.S)


def clean_text(s):
    """Strips markup (JATS, MathML, <i>...) and unescapes entities. Unescaping
    runs after the tag strip, so an escaped "&lt;" in the text stays text."""
    if not s:
        return ""
    s = _TAG_RE.sub("", _BLOCK_TAG_RE.sub(" ", s))
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def clean_abstract(s):
    # A leading <jats:title>Abstract</jats:title> is a heading, not text.
    return clean_text(_JATS_TITLE_RE.sub(" ", s or "")) or None


# IEEE pretty-prints title markup with every inline tag on its own line:
# "Scaling\n    <u>R</u>\n    ecovery", "VP\n    <sup>2</sup>\n    Net".
# Whether a real space sat at each line break can't be read off the data,
# so these rules glue the common cases and leave a space otherwise:
# nothing inside a tag, nothing before a sub/superscript or after a hyphen,
# nothing after a closing tag that's followed by punctuation, and nothing
# between a sub/superscript or a short <u>/<b> acronym letter and the rest
# of its word.
_NL = r"[ \t]*\n\s*"
_IEEE_MARKUP_RULES = [
    (re.compile(rf"(<[a-z]+>){_NL}"), r"\1"),
    (re.compile(rf"{_NL}(</[a-z]+>)"), r"\1"),
    (re.compile(rf"(?<=[-(/]){_NL}(?=<)"), ""),
    (re.compile(rf"{_NL}(?=<su[bp]>)"), ""),
    (re.compile(rf"(</[a-z]+>){_NL}(?=[:,.;)\-*])"), r"\1"),
    (re.compile(rf"(</su[bp]>){_NL}(?=[a-z0-9])"), r"\1"),
    (re.compile(rf"(<([ub])>[^<]{{1,3}}</\2>){_NL}(?=[a-z])"), r"\1"),
    (re.compile(rf"(<([ub])>[A-Z][^<]{{0,2}}</\2>){_NL}(?=\S)"), r"\1"),
    # A lowercase letter picked out mid-word: "Gr<b>a</b>dient", "Efficienc<u>y</u>".
    (re.compile(rf"(?<=[a-z]){_NL}(?=<([ub])>[a-z]{{1,3}}</\1>)"), ""),
]


def tidy_ieee_markup(s):
    for pattern, repl in _IEEE_MARKUP_RULES:
        s = pattern.sub(repl, s)
    return s


def item_title(item):
    title = clean_text(tidy_ieee_markup(" ".join(item.get("title") or [])))
    subtitle = clean_text(" ".join(item.get("subtitle") or []))
    if subtitle and normalize_title(subtitle) not in normalize_title(title):
        title = f"{title}: {subtitle}"
    return title.rstrip(".")


def item_authors(item):
    names = []
    for a in item.get("author") or []:
        name = " ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name") or ""
        name = clean_text(name)
        if name:
            names.append(name)
    return ", ".join(names)


def _date_year(item, field):
    parts = ((item.get(field) or {}).get("date-parts") or [[None]])[0]
    return parts[0] if parts and parts[0] else None


def item_year(item):
    """Print/issue year when there is one, else the online year (see the
    module docstring). Returns (year, is_print_year)."""
    print_year = _date_year(item, "published-print")
    if print_year:
        return print_year, True
    return _date_year(item, "published-online") or _date_year(item, "issued"), False


def to_record(item, with_year):
    rec = {"title": item_title(item), "authors": item_authors(item),
           "abstract": clean_abstract(item.get("abstract")), "doi": (item.get("DOI") or "").lower() or None}
    if with_year:
        rec = {"year": item_year(item)[0], **rec}
    return rec


def usable(item):
    # A record with no authors is a table of contents, a cover or an
    # "Information for authors" page. DBLP skipped those too.
    return bool(item_title(item)) and bool(item.get("author"))


def merge_records(existing, items, with_year):
    """Merges Crossref items into a venue file's records, matched by
    normalized title (the same key merge_corpus.py dedupes on). Returns
    (records, stats). Pure: never touches the network or disk."""
    records = [dict(p) for p in existing]
    by_key = {}
    by_doi = {}
    # A title shared by several records ("Scanning the Issue", "Editorial")
    # can't say which one a Crossref item is, so those are left alone unless
    # the DOI matches.
    ambiguous = set()
    for p in records:
        key = normalize_title(html.unescape(p.get("title") or ""))
        if key in by_key:
            ambiguous.add(key)
        by_key.setdefault(key, p)
        if p.get("doi"):
            by_doi.setdefault(p["doi"].lower(), p)
    stats = {"added": 0, "doi_added": 0, "year_fixed": 0, "abstract_added": 0, "skipped": 0}
    for item in items:
        if not usable(item):
            stats["skipped"] += 1
            continue
        rec = to_record(item, with_year)
        if with_year and (rec["year"] or 0) < MIN_YEAR:
            stats["skipped"] += 1
            continue
        key = normalize_title(rec["title"])
        if not key:
            stats["skipped"] += 1
            continue
        match = by_doi.get(rec["doi"]) if rec["doi"] else None
        match = match or by_key.get(key)
        if match is None:
            records.append(rec)
            by_key[key] = rec
            if rec["doi"]:
                by_doi[rec["doi"]] = rec
            stats["added"] += 1
            continue
        if key in ambiguous and match is not by_doi.get(rec["doi"]):
            continue
        if rec["doi"] and not match.get("doi"):
            match["doi"] = rec["doi"]
            by_doi[rec["doi"]] = match
            stats["doi_added"] += 1
        if rec["abstract"] and fill_missing_abstract(match, rec["abstract"]):
            stats["abstract_added"] += 1
        if with_year:
            year, is_print = item_year(item)
            if is_print and year and match.get("year") != year:
                match["year"] = year
                stats["year_fixed"] += 1
    return records, stats


@lru_cache(maxsize=None)
def load_s2_key():
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        return key.strip()
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None  # the batch endpoint also works without a key, just more slowly


def s2_batch(dois):
    """POSTs one /paper/batch request; returns a list aligned with dois
    (None where S2 has no such paper)."""
    body = json.dumps({"ids": [f"DOI:{d}" for d in dois]}).encode("utf-8")
    headers = {**HEADERS, "Content-Type": "application/json"}
    if load_s2_key():
        headers["x-api-key"] = load_s2_key()
    url = f"{S2_BATCH_URL}?fields=abstract"
    delay = 5
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        finally:
            time.sleep(S2_REQUEST_DELAY)


def fill_abstracts_by_doi(records, batch=None):
    """Fills missing abstracts on records that have a DOI. Returns the
    number filled."""
    batch = batch or s2_batch
    pending = [p for p in records if p.get("doi") and not (p.get("abstract") or "").strip()]
    filled = 0
    for i in range(0, len(pending), S2_BATCH_SIZE):
        chunk = pending[i:i + S2_BATCH_SIZE]
        results = batch([p["doi"] for p in chunk])
        for p, hit in zip(chunk, results):
            abstract = clean_text((hit or {}).get("abstract"))
            if abstract and fill_missing_abstract(p, abstract):
                filled += 1
        print(f"  S2: {min(i + S2_BATCH_SIZE, len(pending))} of {len(pending)} looked up, {filled} abstracts", flush=True)
    return filled


def read_venue_file(path):
    if not path.exists():
        return [], "\r\n" if os.linesep == "\r\n" else "\n"
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), "\r\n" if b"\r\n" in raw[:200] else "\n"


def write_venue_file(path, records, newline):
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8", newline=newline)


def journal_filters(venue, since, published_since, until):
    filters = [f"issn:{i}" for i in JOURNALS[venue]] + ["type:journal-article"]
    if published_since:
        filters.append(f"from-pub-date:{published_since}")
    else:
        filters.append(f"from-update-date:{since}")
    if until:
        filters.append(f"until-{'pub' if published_since else 'update'}-date:{until}")
    return filters


def conference_items(venue, year):
    container = CONFERENCES[venue](year)
    items = crossref_items([f"container-title:{container}", "type:proceedings-article"])
    return [it for it in items if container in (it.get("container-title") or [])], container


def proceedings_items(venue, year):
    isbns = PROCEEDINGS_ISBNS[venue].get(year)
    if not isbns:
        raise SystemExit(f"No ISBN on file for {venue} {year}; add it to PROCEEDINGS_ISBNS")
    return crossref_items([f"isbn:{i}" for i in isbns] + ["type:book-chapter"])


def run(venue, year, args):
    if venue in JOURNALS:
        out_file = OUT_DIR / f"{venue.lower().replace('-', '')}_all.json"
        filters = journal_filters(venue, args.since, args.published_since, args.until)
        print(f"{venue}: Crossref {', '.join(filters[len(JOURNALS[venue]):])}")
        items = crossref_items(filters)
        with_year = True
    else:
        out_file = OUT_DIR / f"{venue.lower()}{year}.json"
        if venue in CONFERENCES:
            items, container = conference_items(venue, year)
            print(f"{venue} {year}: {len(items)} records in '{container}'")
        else:
            items = proceedings_items(venue, year)
            print(f"{venue} {year}: {len(items)} chapters")
        if not items:
            print(f"{venue} {year}: nothing on Crossref yet, no file written")
            return
        with_year = False

    existing, newline = read_venue_file(out_file)
    records, stats = merge_records(existing, items, with_year)
    print(f"  {len(existing)} existing, {stats['added']} added, {stats['doi_added']} DOIs added, "
          f"{stats['year_fixed']} years corrected, {stats['abstract_added']} Crossref abstracts, "
          f"{stats['skipped']} skipped")
    if not args.no_abstracts:
        n = fill_abstracts_by_doi(records)
        print(f"  {n} abstracts from Semantic Scholar")
    if args.dry_run:
        print(f"  dry run, {out_file.name} not written")
        return
    write_venue_file(out_file, records, newline)
    print(f"  wrote {len(records)} records to {out_file}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("venue", help="T-ITS, RA-L, T-RO, TPAMI, IJCV, IJRR, 'journals' (all six), IV, ITSC, GCPR")
    ap.add_argument("years", nargs="*", type=int, help="edition year, or first and last year (conferences only)")
    default_since = (datetime.date.today() - datetime.timedelta(days=DEFAULT_LOOKBACK_DAYS)).isoformat()
    ap.add_argument("--since", default=default_since, help="journals: metadata updated on or after this date")
    ap.add_argument("--published-since", help="journals: published on or after this date (for a backlog run)")
    ap.add_argument("--until", help="journals: end of the date range")
    ap.add_argument("--no-abstracts", action="store_true", help="skip the Semantic Scholar abstract lookup")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.venue == "journals":
        for venue in JOURNALS:
            run(venue, None, args)
        return
    if args.venue in JOURNALS:
        run(args.venue, None, args)
        return
    if args.venue not in CONFERENCES and args.venue not in PROCEEDINGS_ISBNS:
        raise SystemExit(f"Unknown venue {args.venue}")
    if not args.years:
        raise SystemExit(f"{args.venue} needs a year")
    first, last = args.years[0], args.years[-1]
    for year in range(first, last + 1):
        run(args.venue, year, args)


if __name__ == "__main__":
    sys.exit(main())
