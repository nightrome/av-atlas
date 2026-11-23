#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Long-running, resumable backfill of missing abstracts, one title at a time,
from arXiv and Semantic Scholar (OpenAlex is bulk-harvested by
fetch_abstracts_openalex.py instead: its API charges per search, so a lookup
per title is not affordable). Built to run unattended for days; run one
process per source so that each keeps to its own rate limit:

  python fetch_abstracts_web.py --sources arxiv
  python fetch_abstracts_web.py --sources s2

For every paper in papers_full.json that has no abstract, the source is asked
for the paper's exact title. AV papers come first, then the rest, newest year
first. Papers another process or script already found are skipped. A title only counts when it
matches the paper's title after lower-casing and dropping everything but
letters and digits (the same rule as the other title-resolution steps).

Rate limits, which have hurt before, are handled per source:
  * a minimum gap between requests (OpenAlex 0.25 s, arXiv 3.5 s, Semantic
    Scholar 1.1 s);
  * a 429 or 5xx waits for Retry-After, else 30 s doubling up to 30 min;
  * after 6 failures in a row the source cools down for 10 min, doubling up to
    2 h, and the paper goes on to the next source meanwhile, to be retried on a
    later pass;
  * when every remaining paper is waiting on a cooling source, the script
    sleeps until the first one is ready instead of spinning.
A failure never marks a paper as tried; only a real answer does.

Writes only its own side file, data/abstracts_web_<sources>.json:
  {"abstracts": {normalizedTitle: {"text": ..., "source": ...}},
   "tried":     {normalizedTitle: "oas"}}      # letters of the sources that answered
and never touches papers_full.json; `--apply` folds it in (only into papers
that still have no abstract). Safe to stop and restart at any time.

Usage:
  python fetch_abstracts_web.py --sources arxiv    # run until done
  python fetch_abstracts_web.py --sources arxiv --batch 8   # several titles per request, much faster
  python fetch_abstracts_web.py --sources s2 --max-hours 8
  python fetch_abstracts_web.py --apply            # fold every abstracts_web_*.json in
  python fetch_abstracts_web.py --sources arxiv --limit 50   # try it out
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from atomic_write import write_json_atomic
from fetch_common import BASE, read_s2_key

PAPERS_FILE = BASE / "data" / "papers_full.json"
DATA_DIR = BASE / "data"
SIDE_GLOB = "abstracts_web_*.json"
S2_FILE = BASE / "data" / "abstracts_semanticscholar.json"
ARXIV_FILE = BASE / "data" / "abstracts_arxiv.json"

CONTACT = "holger@it-caesar.com"
USER_AGENT = f"av-atlas (mailto:{CONTACT})"
MIN_ABSTRACT_CHARS = 80
SAVE_EVERY = 100
FAILS_BEFORE_COOLDOWN = 6
BACKOFF_START = 30
BACKOFF_CAP = 1800
COOLDOWN_START = 600
COOLDOWN_CAP = 7200
TIMEOUT = 60

CODES = {"openalex": "o", "arxiv": "a", "s2": "s"}
GAPS = {"openalex": 0.25, "arxiv": 3.5, "s2": 1.1}
ORDER = ["openalex", "arxiv", "s2"]


def norm(title):
    return re.sub(r"[^a-z0-9]", "", (title or "").lower())


def words(title):
    return re.sub(r"[^\w]+", " ", title or "").strip()[:200]


def clean_abstract(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < MIN_ABSTRACT_CHARS or text.lower().startswith(("no abstract", "abstract not")):
        return None
    return text


class Unavailable(Exception):
    """Rate limited or server trouble: try again later, say nothing about the paper."""

    def __init__(self, msg, retry_after=0):
        super().__init__(msg)
        self.retry_after = retry_after


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (429, 500, 502, 503, 504):
            try:
                ra = int(e.headers.get("Retry-After") or 0)
            except (TypeError, ValueError):
                ra = 0
            raise Unavailable(f"HTTP {e.code}", ra) from e
        if e.code in (403,):
            raise Unavailable(f"HTTP {e.code}", 0) from e
        if e.code == 404:
            return None  # an honest "no such thing"
        raise Unavailable(f"HTTP {e.code}", 0) from e
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise Unavailable(type(e).__name__, 0) from e


# --------------------------------------------------------------- the sources

def lookup_openalex(title):
    q = urllib.parse.quote(words(title))
    url = (f"https://api.openalex.org/works?filter=title.search:{q}&per-page=5"
           f"&select=title,abstract_inverted_index&mailto={CONTACT}")
    key = os.environ.get("OPENALEX_API_KEY")
    if key:
        url += f"&api_key={key}"
    raw = http_get(url)
    if raw is None:
        return None
    for work in json.loads(raw).get("results", []):
        if norm(work.get("title")) != norm(title):
            continue
        inv = work.get("abstract_inverted_index")
        if not inv:
            continue
        slots = {}
        for word, positions in inv.items():
            for pos in positions:
                slots[pos] = word
        return clean_abstract(" ".join(slots[i] for i in sorted(slots)))
    return None


def lookup_arxiv(title):
    q = urllib.parse.quote('ti:"' + words(title) + '"')
    raw = http_get(f"https://export.arxiv.org/api/query?search_query={q}&max_results=3")
    if raw is None:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in ET.fromstring(raw).findall("a:entry", ns):
        t = entry.findtext("a:title", default="", namespaces=ns)
        if norm(t) == norm(title):
            return clean_abstract(entry.findtext("a:summary", default="", namespaces=ns))
    return None


ARXIV_BATCH_MAX_QUERY = 1800  # characters of query text; arXiv rejects very long URLs


def lookup_arxiv_many(titles):
    """One arXiv request for several titles: {title: abstract or None}.

    The titles are OR-ed as exact-phrase searches. If the answer is as long as
    we allowed (so some matches may have been cut off), titles without a match
    raise Unavailable instead of counting as misses, and get asked again alone.
    """
    q = " OR ".join('ti:"' + words(t) + '"' for t in titles)
    limit = min(len(titles) * 12, 200)
    raw = http_get("https://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q)
                   + f"&max_results={limit}")
    if raw is None:
        raise Unavailable("no answer")
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = ET.fromstring(raw).findall("a:entry", ns)
    by_title = {}
    for entry in entries:
        by_title.setdefault(norm(entry.findtext("a:title", default="", namespaces=ns)),
                            clean_abstract(entry.findtext("a:summary", default="", namespaces=ns)))
    out = {t: by_title.get(norm(t)) for t in titles}
    if len(entries) >= limit:
        # Some matches may have been cut off: a title without a match is not a miss yet.
        out = {t: (v or False) for t, v in out.items()}
    return out


def make_lookup_s2():
    key = read_s2_key(BASE / ".env")

    def lookup(title):
        q = urllib.parse.quote(title[:300])
        raw = http_get(f"https://api.semanticscholar.org/graph/v1/paper/search/match?query={q}"
                       "&fields=title,abstract", {"x-api-key": key} if key else None)
        if raw is None:
            return None
        for item in json.loads(raw).get("data", []):
            if norm(item.get("title")) == norm(title):
                return clean_abstract(item.get("abstract"))
        return None
    return lookup


# ------------------------------------------------------------ rate limiting

class Source:
    def __init__(self, name, lookup, clock=time.monotonic, sleep=time.sleep):
        self.name, self.lookup, self.clock, self.sleep = name, lookup, clock, sleep
        self.gap = GAPS[name]
        self.next_request = 0.0
        self.fails = 0
        self.backoff = BACKOFF_START
        self.cooldown = COOLDOWN_START
        self.blocked_until = 0.0

    def ready(self):
        return self.clock() >= self.blocked_until

    def _guarded(self, call):
        """('ok', value) | ('miss', None) | ('later', None), with the waiting and
        cooling-down rules above applied to the request `call` makes."""
        wait = self.next_request - self.clock()
        if wait > 0:
            self.sleep(wait)
        try:
            value = call()
        except Unavailable as e:
            self.next_request = self.clock() + self.gap
            self.fails += 1
            if self.fails >= FAILS_BEFORE_COOLDOWN:
                self.blocked_until = self.clock() + self.cooldown
                print(f"  [{self.name}] {e}; {self.fails} failures in a row, cooling down "
                      f"{self.cooldown // 60} min", flush=True)
                self.cooldown = min(self.cooldown * 2, COOLDOWN_CAP)
                self.fails = 0
                self.backoff = BACKOFF_START
                return "later", None
            wait = min(max(e.retry_after, self.backoff), BACKOFF_CAP)
            print(f"  [{self.name}] {e}; waiting {wait:.0f}s", flush=True)
            self.sleep(wait)
            self.backoff = min(self.backoff * 2, BACKOFF_CAP)
            return "later", None
        except (ValueError, ET.ParseError):
            self.next_request = self.clock() + self.gap
            return "miss", None  # a body we cannot read says nothing about the paper
        self.fails = 0
        self.backoff = BACKOFF_START
        self.cooldown = COOLDOWN_START
        self.next_request = self.clock() + self.gap
        return "ok", value

    def ask(self, title):
        """('hit', text) | ('miss', None) | ('later', None)."""
        outcome, text = self._guarded(lambda: self.lookup(title))
        if outcome == "ok":
            return ("hit", text) if text else ("miss", None)
        return outcome, None

    def ask_many(self, titles):
        """('ok', {title: text or None}) | ('miss', None) | ('later', None); one request."""
        return self._guarded(lambda: self.lookup_many(titles))


# -------------------------------------------------------------------- driver

def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def peer_abstracts(own):
    """Titles any other side file already has an abstract for."""
    found = set()
    for path in DATA_DIR.glob(SIDE_GLOB):
        if path != own:
            try:
                found.update(load_json(path, {}).get("abstracts", {}))
            except (ValueError, OSError):
                pass  # being written right now; next refresh will have it
    return found


def pending_papers(papers, state, skip=()):
    """Title -> original title, AV first, newest year first, for papers with no abstract."""
    rows = {}
    for p in papers:
        if (p.get("abstract") or "").strip():
            continue
        t = p.get("title") or ""
        nt = norm(t)
        if nt and nt not in state["abstracts"] and nt not in skip and nt not in rows:
            rows[nt] = (0 if p.get("av_relevance") == "AV" else 1, -(p.get("year") or 0), t)
    ordered = sorted(rows.items(), key=lambda kv: kv[1][:2])
    return [(nt, v[2]) for nt, v in ordered]


def run(papers, state, sources, max_seconds=None, limit=None, save=None, clock=time.monotonic, sleep=time.sleep,
        preknown=None, peers=None):
    """Work through the pending papers until each source has answered for all of
    them, the budget is used up or the limit is reached. Returns counts."""
    preknown = preknown or {}
    started = clock()
    stats = {"hit": 0, "asked": 0}
    peers = peers or (lambda: set())
    skip = peers()
    todo = pending_papers(papers, state, skip)
    if limit:
        todo = todo[:limit]
    by_name = {s.name: s for s in sources}
    codes = {n: CODES[n] for n in by_name}
    since_save = 0
    while todo:
        still = []
        for nt, title in todo:
            if max_seconds and clock() - started > max_seconds:
                return stats
            done = set(state["tried"].get(nt, "")) | set(preknown.get(nt, ""))
            found = False
            blocked = False
            for name in ORDER:
                src = by_name.get(name)
                if src is None or codes[name] in done:
                    continue
                if not src.ready():
                    blocked = True
                    continue
                outcome, text = src.ask(title)
                stats["asked"] += 1
                if outcome == "later":
                    blocked = True
                    continue
                done.add(codes[name])
                if outcome == "hit":
                    state["abstracts"][nt] = {"text": text, "source": name}
                    stats["hit"] += 1
                    found = True
                    break
            state["tried"][nt] = "".join(sorted(done))
            if found:
                state["tried"].pop(nt, None)
            elif blocked:
                still.append((nt, title))
            since_save += 1
            if save and since_save >= SAVE_EVERY:
                save()
                since_save = 0
                print(f"  {stats['hit']} abstracts found, {len(still)} waiting on a cooling source, "
                      f"{len(todo)} left in this pass", flush=True)
        if save:
            save()
            since_save = 0
        skip = peers()
        todo = [(nt, t) for nt, t in still if nt not in skip]
        if todo:
            nxt = min((s.blocked_until for s in sources if not s.ready()), default=None)
            if nxt is not None:
                nap = max(5, nxt - clock())
                print(f"  every source is cooling down; sleeping {nap / 60:.0f} min "
                      f"({len(todo)} papers waiting)", flush=True)
                if max_seconds and clock() - started + nap > max_seconds:
                    return stats
                sleep(nap)
    return stats


def run_batched(papers, state, src, batch, max_seconds=None, limit=None, save=None, clock=time.monotonic,
                sleep=time.sleep, preknown=None, peers=None):
    """Like run() for a single source that can answer several titles per request
    (src.lookup_many). A title it has not been asked yet goes into the next batch;
    a failed request leaves its titles unmarked, so they come around again."""
    preknown = preknown or {}
    peers = peers or (lambda: set())
    code = CODES[src.name]
    started = clock()
    stats = {"hit": 0, "asked": 0}
    todo = [(nt, t) for nt, t in pending_papers(papers, state, peers())
            if code not in set(state["tried"].get(nt, "")) | set(preknown.get(nt, ""))]
    if limit:
        todo = todo[:limit]
    since_save = 0
    alone = set()  # titles a batch left undecided: asked one by one
    while todo:
        chunk, size = [], 0
        for nt, title in todo:
            if nt in alone:
                if not chunk:
                    chunk.append((nt, title))
                    alone.discard(nt)
                    break
                continue
            if chunk and (len(chunk) >= batch or size + len(title) > ARXIV_BATCH_MAX_QUERY):
                break
            chunk.append((nt, title))
            size += len(title) + 10
        if max_seconds and clock() - started > max_seconds:
            return stats
        if not src.ready():
            nap = max(5, src.blocked_until - clock())
            print(f"  {src.name} is cooling down; sleeping {nap / 60:.0f} min ({len(todo)} papers waiting)", flush=True)
            if max_seconds and clock() - started + nap > max_seconds:
                return stats
            sleep(nap)
            continue
        outcome, answers = src.ask_many([t for _, t in chunk])
        stats["asked"] += 1
        if outcome == "later" or (outcome == "miss" and len(chunk) > 1):
            if outcome == "miss":
                batch = max(1, len(chunk) // 2)  # an unreadable answer: try smaller batches
            continue
        undecided = set()
        for nt, title in chunk:
            text = (answers or {}).get(title)
            if text is False and len(chunk) > 1:
                undecided.add(nt)
                continue
            if text:
                state["abstracts"][nt] = {"text": text, "source": src.name}
                state["tried"].pop(nt, None)
                stats["hit"] += 1
            else:
                state["tried"][nt] = "".join(sorted(set(state["tried"].get(nt, "")) | {code}))
        done = {nt for nt, _ in chunk} - undecided
        alone |= undecided
        todo = [(nt, t) for nt, t in todo if nt not in done]
        since_save += len(chunk)
        if save and since_save >= SAVE_EVERY:
            save()
            since_save = 0
            known = peers()
            todo = [(nt, t) for nt, t in todo if nt not in known]
            print(f"  {stats['hit']} abstracts found, {len(todo)} left in this pass", flush=True)
    if save:
        save()
    return stats


def apply(papers_path=PAPERS_FILE, data_dir=DATA_DIR):
    from apply_abstracts_semanticscholar import fill_missing_abstract
    found = {}
    for path in sorted(data_dir.glob(SIDE_GLOB)):
        for nt, got in load_json(path, {}).get("abstracts", {}).items():
            found.setdefault(nt, got)
    papers = json.loads(papers_path.read_text(encoding="utf-8"))
    n = 0
    for p in papers:
        got = found.get(norm(p.get("title")))
        if got and fill_missing_abstract(p, got["text"], source=got["source"]):
            n += 1
    if n:
        write_json_atomic(papers_path, papers)
    print(f"Applied {n} abstracts to {papers_path.name}.")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="fold the side file into papers_full.json and exit")
    ap.add_argument("--max-hours", type=float, default=None)
    ap.add_argument("--limit", type=int, default=None, help="only the first N pending papers")
    ap.add_argument("--sources", default="arxiv", help="one of: " + ",".join(ORDER))
    ap.add_argument("--batch", type=int, default=1,
                    help="titles per request (arxiv only; 8 is a good value; default 1)")
    args = ap.parse_args(argv)
    if args.apply:
        apply()
        return 0
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    names = [n for n in args.sources.split(",") if n in ("arxiv", "s2", "openalex")]
    out_file = DATA_DIR / ("abstracts_web_" + "_".join(names) + ".json")
    state = load_json(out_file, {"abstracts": {}, "tried": {}})
    state.setdefault("abstracts", {})
    state.setdefault("tried", {})
    # What the older steps already asked: no need to ask again.
    preknown = {}
    for nt in load_json(S2_FILE, {}).get("succeeded", []):
        preknown[nt] = preknown.get(nt, "") + "s"
    for nt in load_json(ARXIV_FILE, {}):
        preknown[nt] = preknown.get(nt, "") + "a"
    lookups = {"openalex": lookup_openalex, "arxiv": lookup_arxiv, "s2": make_lookup_s2()}
    sources = [Source(n, lookups[n]) for n in names]

    def save():
        write_json_atomic(out_file, state)

    pend = len(pending_papers(papers, state, peer_abstracts(out_file)))
    print(f"{pend} papers without an abstract; sources: {', '.join(names)}", flush=True)
    budget = args.max_hours * 3600 if args.max_hours else None
    if args.batch > 1 and names == ["arxiv"]:
        sources[0].lookup_many = lookup_arxiv_many
        stats = run_batched(papers, state, sources[0], args.batch, max_seconds=budget, limit=args.limit,
                            save=save, preknown=preknown, peers=lambda: peer_abstracts(out_file))
    else:
        stats = run(papers, state, sources, max_seconds=budget, limit=args.limit, save=save,
                    preknown=preknown, peers=lambda: peer_abstracts(out_file))
    save()
    print(f"Done for now: {stats['hit']} abstracts found in {stats['asked']} requests.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
