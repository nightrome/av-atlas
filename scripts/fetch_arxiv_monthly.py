#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monthly arXiv intake: new AV preprints since the last run, written as a
venue-style file that merge_corpus.py reads like any other.

Source: arXiv's OAI-PMH interface (https://oaipmh.arxiv.org/oai), not the
search API at export.arxiv.org/api/query. In September 2026 the search API
answered Python's urllib with HTTP 406 on every uncached query, while curl
sending the same URL and the same headers (checked side by side against a
local listener, then against arXiv) got 200. The refusal follows the client
library, not anything in the request we control, so we don't try to get
around it. OAI-PMH is arXiv's documented bulk-harvesting interface, answers
urllib normally, and is the better fit for "everything since last month"
anyway. See PIPELINE.md's "Monthly arXiv intake" section.

What it does, per run:
  1. Harvests ListRecords (metadataPrefix=arXiv) for the "cs" and "eess"
     sets from the last run's window end minus OVERLAP_DAYS (first run:
     INTAKE_START). No "until": an OAI datestamp is the record's last change,
     so a paper announced in the window and revised a day later would drop
     out of a closed window.
  2. Keeps papers first submitted on or after INTAKE_START that aren't in
     the ledger yet (see is_new_submission for why the month comes from the
     arXiv ID). Replacements of papers we already took are skipped by the
     ledger; old papers that were only revised are skipped by the date.
  3. Relevance: cs.CV and cs.RO papers go through classify_relevance as is.
     Everything else in cs/eess (cs.LG, cs.AI, eess.SY, ...) must also carry
     an explicit AV phrase in the title or abstract -- this is what the
     "AV-phrase query outside cs.CV/cs.RO" would have returned, and it keeps
     the title driving-word rule from pulling in "device driver" papers.
     There is no review tier: whatever passes goes straight in (maintainer
     decision, see DECISIONS.md).
  4. Drops anything the corpus already has, by arXiv ID first and then by
     merge_corpus.normalize_title().
  5. Appends the rest to data/venues/arxiv_monthly_<yyyy>-<mm>.json (the run
     month) and records their IDs plus the new window end in
     data/arxiv_monthly_state.json.

Both output files are tracked in git: arXiv's metadata, abstracts included,
is CC0, the files are small (tens of papers a month), and a job that runs
unattended must be able to carry its ledger from one run to the next
without a local-only backup. Both are written only after the whole harvest
succeeded, each via a temp file and os.replace(), venue file first -- so a
crash leaves either nothing or papers whose IDs the next run finds in the
corpus anyway.

Network etiquette: one request at a time, REQUEST_DELAY seconds apart, and
the OAI flow control is honoured (503 with Retry-After). A 403 or 429 stops
the run with nothing written.

Usage:
  python scripts/fetch_arxiv_monthly.py                 # window since last run
  python scripts/fetch_arxiv_monthly.py --dry-run       # harvest and report, write nothing
  python scripts/fetch_arxiv_monthly.py --from 2026-09-01   # override the window start
  python scripts/fetch_arxiv_monthly.py --summary-json out.json   # also write a run summary
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import classify as cl
import merge_corpus as mc
from fetch_common import ARXIV_OAI_URL, BASE, OUT_DIR, fetch

STATE_FILE = BASE / "data" / "arxiv_monthly_state.json"
PAPERS_FILE = BASE / "data" / "papers_full.json"
ARXIV_IDS_FILE = BASE / "data" / "arxiv_ids.json"
OUT_PREFIX = "arxiv_monthly_"

SETS = ("cs", "eess")
FOCUS_CATEGORIES = frozenset({"cs.CV", "cs.RO"})
# Nothing submitted before this goes through the monthly intake. Older
# preprints came in through fetch_semanticscholar_citing.py, whose newest
# entries are from mid-August 2026.
INTAKE_START = "2026-08-15"
OVERLAP_DAYS = 3
REQUEST_DELAY = 5.0
MAX_FLOW_CONTROL_WAITS = 5
MAX_RETRY_AFTER = 600
RUNS_KEPT = 24

OAI_NS = "{http://www.openarchives.org/OAI/2.0/}"
ARXIV_NS = "{http://arxiv.org/OAI/arXiv/}"
ARXIV_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I)
BARE_ID_RE = re.compile(r"^(?:[0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})$", re.I)


class HarvestError(RuntimeError):
    pass


def _text(el, tag):
    s = el.findtext(tag)
    return " ".join(s.split()) if s else None


def parse_list_records(xml_text):
    """One ListRecords page -> (records, resumption_token or None).

    OAI reports "nothing changed in that window" as a 200 with
    <error code="noRecordsMatch">, which is an empty result, not a failure.
    Any other OAI error is raised.
    """
    root = ET.fromstring(xml_text)
    err = root.find(f"{OAI_NS}error")
    if err is not None:
        if err.get("code") == "noRecordsMatch":
            return [], None
        raise HarvestError(f"OAI error {err.get('code')}: {(err.text or '').strip()}")
    list_el = root.find(f"{OAI_NS}ListRecords")
    if list_el is None:
        raise HarvestError("response has neither ListRecords nor error")
    records = []
    for rec in list_el.findall(f"{OAI_NS}record"):
        header = rec.find(f"{OAI_NS}header")
        if header is not None and header.get("status") == "deleted":
            continue
        meta = rec.find(f"{OAI_NS}metadata/{ARXIV_NS}arXiv")
        if meta is None:
            continue
        authors = []
        for a in meta.findall(f"{ARXIV_NS}authors/{ARXIV_NS}author"):
            parts = [_text(a, f"{ARXIV_NS}forenames"), _text(a, f"{ARXIV_NS}keyname"),
                     _text(a, f"{ARXIV_NS}suffix")]
            name = " ".join(p for p in parts if p)
            if name:
                authors.append(name)
        records.append({
            "id": _text(meta, f"{ARXIV_NS}id"),
            "title": _text(meta, f"{ARXIV_NS}title"),
            "abstract": _text(meta, f"{ARXIV_NS}abstract"),
            "authors": authors,
            "created": _text(meta, f"{ARXIV_NS}created"),
            "updated": _text(meta, f"{ARXIV_NS}updated"),
            "categories": (_text(meta, f"{ARXIV_NS}categories") or "").split(),
            "comment": _text(meta, f"{ARXIV_NS}comments"),
            "journal_ref": _text(meta, f"{ARXIV_NS}journal-ref"),
            "doi": _text(meta, f"{ARXIV_NS}doi"),
        })
    token_el = list_el.find(f"{OAI_NS}resumptionToken")
    token = (token_el.text or "").strip() if token_el is not None else ""
    return records, token or None


def _get(url, fetch_fn, sleep_fn):
    """One polite GET. Waits out OAI flow control (503 + Retry-After) a few
    times; anything else, 403 and 429 included, ends the run."""
    for _ in range(MAX_FLOW_CONTROL_WAITS + 1):
        try:
            return fetch_fn(url)
        except urllib.error.HTTPError as e:
            if e.code != 503:
                raise HarvestError(f"HTTP {e.code} from {url}") from e
            try:
                wait = int(e.headers.get("Retry-After") or 30)
            except (TypeError, ValueError):
                wait = 30
            print(f"  OAI flow control: waiting {wait}s", flush=True)
            sleep_fn(min(max(wait, REQUEST_DELAY), MAX_RETRY_AFTER))
    raise HarvestError(f"still 503 after {MAX_FLOW_CONTROL_WAITS} waits: {url}")


def harvest(set_spec, from_date, fetch_fn=None, sleep_fn=time.sleep):
    """Every record in one OAI set with a datestamp on or after from_date."""
    fetch_fn = fetch_fn or (lambda url: fetch(url, timeout=300))
    params = {"verb": "ListRecords", "metadataPrefix": "arXiv", "set": set_spec, "from": from_date}
    out = []
    page = 0
    while True:
        if page:
            sleep_fn(REQUEST_DELAY)
        records, token = parse_list_records(_get(f"{ARXIV_OAI_URL}?{urllib.parse.urlencode(params)}",
                                                 fetch_fn, sleep_fn))
        out.extend(records)
        page += 1
        print(f"  set {set_spec}: page {page}, {len(out)} records so far", flush=True)
        if not token:
            return out
        # After the first page OAI takes the token and nothing else.
        params = {"verb": "ListRecords", "resumptionToken": token}


def arxiv_id_from(value):
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    m = ARXIV_ID_RE.search(value)
    if m:
        return m.group(1).lower()
    value = re.sub(r"v[0-9]+$", "", value)
    return value.lower() if BARE_ID_RE.match(value) else None


def load_corpus_keys(venues_dir=OUT_DIR, papers_file=PAPERS_FILE, arxiv_ids_file=ARXIV_IDS_FILE):
    """arXiv IDs and normalised titles the corpus already has.

    Reads every venues/*.json (tracked, so always there, and it includes
    the earlier monthly files) plus papers_full.json and arxiv_ids.json when
    they exist -- those two are gitignored, and only add IDs that a later
    enrichment step attached to a venue paper.
    """
    ids, titles = set(), set()

    def add(p):
        t = mc.normalize_title(p.get("title"))
        if t:
            titles.add(t)
        for field in ("arxiv_id", "arxiv_url", "doi"):
            aid = arxiv_id_from(p.get(field))
            if aid:
                ids.add(aid)

    for f in sorted(Path(venues_dir).glob("*.json")):
        try:
            for p in json.loads(f.read_text(encoding="utf-8")):
                add(p)
        except (OSError, ValueError) as e:
            print(f"  could not read {f.name} for dedupe: {e}")
    if Path(papers_file).exists():
        for p in json.loads(Path(papers_file).read_text(encoding="utf-8")):
            add(p)
    else:
        print(f"  note: {Path(papers_file).name} not found, deduping against venue files only")
    if Path(arxiv_ids_file).exists():
        for v in json.loads(Path(arxiv_ids_file).read_text(encoding="utf-8")).values():
            aid = arxiv_id_from(v)
            if aid:
                ids.add(aid)
    return ids, titles


def id_month(aid):
    """"2609.12371" -> "2026-09". None for old-style IDs (cs/0112017), which
    are all from before 2007 and so never new."""
    m = re.match(r"^([0-9]{2})([0-9]{2})\.", aid or "")
    return f"20{m.group(1)}-{m.group(2)}" if m else None


def is_new_submission(record, aid, floor):
    """Was this paper first submitted on or after floor?

    The ID's YYMM is the month arXiv assigned it, which is reliable. The
    "created" field alone is not: in the 2026-09-14 harvest, 2204.07865 (an
    April 2022 paper) came back with created=2026-09-11. So the month comes
    from the ID, and "created" only decides within the floor's own month.
    """
    month = id_month(aid)
    if not month:
        return False
    return month > floor[:7] or (month == floor[:7] and (record.get("created") or "") >= floor)


def is_av(record):
    title, abstract = record.get("title") or "", record.get("abstract") or ""
    if cl.classify_relevance(title, abstract) != "AV":
        return False
    if FOCUS_CATEGORIES.intersection(record.get("categories") or ()):
        return True
    return bool(cl.AV_RELEVANCE_COMBINED.search(title.lower())
                or cl.AV_RELEVANCE_COMBINED.search(abstract.lower()))


def select(records, corpus_ids, corpus_titles, seen, floor=INTAKE_START):
    """Pure filter over harvested records -> (accepted records, counts)."""
    counts = {"harvested": 0, "new_in_window": 0, "withdrawn": 0, "not_av": 0,
              "already_in_corpus": 0, "accepted": 0}
    accepted, taken_ids, taken_titles = [], set(), set()
    for r in records:
        counts["harvested"] += 1
        aid = arxiv_id_from(r.get("id"))
        if not aid or not r.get("title"):
            continue
        if not is_new_submission(r, aid, floor) or aid in seen or aid in taken_ids:
            continue
        counts["new_in_window"] += 1
        if "withdrawn" in (r.get("comment") or "").lower():
            counts["withdrawn"] += 1
            continue
        if not is_av(r):
            counts["not_av"] += 1
            continue
        key = mc.normalize_title(r["title"])
        if aid in corpus_ids or key in corpus_titles or key in taken_titles:
            counts["already_in_corpus"] += 1
            continue
        taken_ids.add(aid)
        taken_titles.add(key)
        accepted.append(r)
    counts["accepted"] = len(accepted)
    return accepted, counts


def to_venue_record(r, run_date):
    aid = arxiv_id_from(r["id"])
    return {
        "title": r["title"],
        "authors": ", ".join(r.get("authors") or []),
        "abstract": r.get("abstract"),
        "conference": "arXiv preprint",
        # The ID's year, as arXiv citations use it ("arXiv:2609.12371, 2026").
        "year": int(id_month(aid)[:4]),
        "arxiv_id": aid,
        "arxiv_url": f"https://arxiv.org/abs/{aid}",
        # A real DOI when the authors gave one (usually the published
        # version), never the arXiv URL -- that lives in arxiv_url.
        "doi": r.get("doi"),
        "journal_ref": r.get("journal_ref"),
        "comment": r.get("comment"),
        "primary_category": (r.get("categories") or [None])[0],
        "categories": r.get("categories") or [],
        "submitted": r.get("created"),
        "first_seen": run_date,
    }


def load_state(path=STATE_FILE):
    if Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return {"window_end": None, "seen": {}, "runs": []}


def window_start(state, override=None):
    if override:
        return override
    if not state.get("window_end"):
        return INTAKE_START
    start = dt.date.fromisoformat(state["window_end"]) - dt.timedelta(days=OVERLAP_DAYS)
    return max(start.isoformat(), INTAKE_START)


def write_json_atomic(path, data):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run(fetch_fn=None, sleep_fn=time.sleep, today=None, from_override=None, dry_run=False,
        venues_dir=OUT_DIR, state_file=STATE_FILE, papers_file=PAPERS_FILE, arxiv_ids_file=ARXIV_IDS_FILE):
    today = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    state = load_state(state_file)
    start = window_start(state, from_override)
    print(f"arXiv monthly intake: sets {', '.join(SETS)}, datestamps from {start}", flush=True)

    records = []
    for i, set_spec in enumerate(SETS):
        if i:
            sleep_fn(REQUEST_DELAY)
        records.extend(harvest(set_spec, start, fetch_fn, sleep_fn))

    corpus_ids, corpus_titles = load_corpus_keys(venues_dir, papers_file, arxiv_ids_file)
    accepted, counts = select(records, corpus_ids, corpus_titles, set(state.get("seen") or {}))
    new = [to_venue_record(r, today) for r in accepted]
    month = today[:7]
    out_file = Path(venues_dir) / f"{OUT_PREFIX}{month}.json"
    summary = {"run_date": today, "window_start": start, "output": out_file.name, **counts,
               "titles": [p["title"] for p in new]}
    for k, v in counts.items():
        print(f"  {k}: {v}")
    if dry_run:
        print("Dry run: nothing written.")
        return summary

    # A month with nothing new gets no file at all rather than an empty one.
    if new:
        existing = json.loads(out_file.read_text(encoding="utf-8")) if out_file.exists() else []
        merged = existing + new
        merged.sort(key=lambda p: p["arxiv_id"])
        write_json_atomic(out_file, merged)

    seen = dict(state.get("seen") or {})
    for p in new:
        seen[p["arxiv_id"]] = month
    runs = (state.get("runs") or []) + [{k: summary[k] for k in summary if k != "titles"}]
    write_json_atomic(state_file, {"window_end": today, "seen": dict(sorted(seen.items())),
                                   "runs": runs[-RUNS_KEPT:]})
    print(f"Added {len(new)} papers to {out_file.name}.")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="from_date", help="window start (YYYY-MM-DD), overrides the ledger")
    ap.add_argument("--dry-run", action="store_true", help="harvest and report, write nothing")
    ap.add_argument("--summary-json", help="also write the run summary to this path")
    args = ap.parse_args(argv)
    if args.from_date:
        dt.date.fromisoformat(args.from_date)
    try:
        summary = run(from_override=args.from_date, dry_run=args.dry_run)
    except HarvestError as e:
        print(f"Stopped, nothing written: {e}", file=sys.stderr)
        return 1
    if args.summary_json:
        Path(args.summary_json).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
                                           encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
