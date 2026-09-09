#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Real reverse-citation discovery, using Semantic Scholar's actual citation
graph -- given a seed paper, "/paper/{id}/citations" returns every paper
that genuinely cites it, verified edges, not text matches.

Requires SEMANTIC_SCHOLAR_API_KEY in av-atlas/.env (gitignored, never
commit it) -- Semantic Scholar's anonymous tier 429'd on every attempt when
this pipeline was first built; a free API key (requested via
semanticscholar.org/product/api, a human sign-up step) gives a documented
1 request/second, cumulative across every endpoint. Every single call in
this script -- resolving a seed's paper ID AND every page of its
citations -- shares that one global 1 req/sec budget, so pacing is a flat
sleep between every request, not per-endpoint.

Of everything that cites a seed paper, only citers with an arXiv ID (in
Semantic Scholar's own externalIds -- most CS/ML papers have one) are kept:
this is an arXiv-discovery script, a citer that's only in a paywalled venue
with no preprint can't be pulled in for free.

A seed paper's citation count can run into the thousands (nuScenes: 8761,
confirmed live) and most citers won't be AV-relevant at all -- classify.py's
usual relevance check runs on every one during merge_corpus.py regardless,
but there's no point spending rate-limit budget paginating through
thousands of certainly-irrelevant results for one seed. Capped at
MAX_PAGES_PER_SEED pages (1000 citations each).

Writes av-atlas/data/venues/arxiv_s2_citing.json, same arxiv*.json
schema fetch_arxiv.py uses -- merge_corpus.py already treats any
arxiv*.json as lowest-priority fill, and tags this file's papers with the
"arxiv_s2_citing_discovery" source (see discovery_source() there).

Usage: python fetch_semanticscholar_citing.py [N|all]   # top N most-cited
                                                           # core papers as
                                                           # seeds (default
                                                           # 150), or "all"
                                                           # for every core
                                                           # paper
"""
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from fetch_common import BASE, HEADERS

STATS_FILE = BASE / "data" / "stats.json"
SEEDS_FILE = BASE / "data" / "s2_citing_seeds.json"
OUT_FILE = BASE / "data" / "venues" / "arxiv_s2_citing.json"
ENV_FILE = BASE / ".env"
API_BASE = "https://api.semanticscholar.org/graph/v1"
REQUEST_DELAY = 1.1  # documented 1 req/sec, cumulative across every endpoint -- small buffer over the raw limit
MAX_PAGES_PER_SEED = 3  # 1000 citations/page -- bounds a mega-cited seed (nuScenes: 8761) from eating the whole budget
PAGE_SIZE = 1000


def load_api_key():
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} -- add a line SEMANTIC_SCHOLAR_API_KEY=... (never commit this file)")
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("SEMANTIC_SCHOLAR_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"SEMANTIC_SCHOLAR_API_KEY not found in {ENV_FILE}")


API_KEY = load_api_key()


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def top_seed_titles(n):
    # n=None means every core paper, not just ones with an in-corpus
    # citation already -- a paper with zero IN-corpus citations can easily
    # still have real citations Semantic Scholar knows about that we don't,
    # which is the whole point of seeding from it.
    #
    # Used to sort by in-corpus citations descending, on the theory that an
    # interrupted/resumed run should work through "the papers most likely to
    # matter first" -- but that's exactly self-defeating for any paper (or
    # whole venue) that has zero in-corpus citations *because* it's never
    # been queried yet: it always sorts last, so a long-tail venue never
    # gets its turn no matter how many runs complete. User-reported: "IV and
    # arXiv still have no citations" after a run that got well over a third
    # of the way through the corpus. Shuffled instead -- every paper gets an
    # equal shot at being queried soon, not just the ones already well-cited
    # (n, when given, still bounds the run to a random sample rather than a
    # fixed top-N).
    stats = json.loads(STATS_FILE.read_text(encoding="utf-8"))
    papers = [p for p in stats.get("all_papers", []) if p.get("title")]
    titles = [p["title"] for p in papers]
    random.shuffle(titles)
    return titles if n is None else titles[:n]


def s2_get(path, params):
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={**HEADERS, "x-api-key": API_KEY})
    delay = REQUEST_DELAY
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise
        finally:
            time.sleep(REQUEST_DELAY)


def resolve_paper_id(title):
    data = s2_get("/paper/search/match", {"query": title, "fields": "title"})
    results = data.get("data") or []
    if not results:
        return None
    # search/match already returns its single best match, but exact-title
    # verification stays the standard everywhere else in this pipeline --
    # a fuzzy same-topic match must never be treated as a real hit.
    if normalize_title(results[0].get("title")) != normalize_title(title):
        return None
    return results[0]["paperId"]


def fetch_citing_arxiv_papers(paper_id):
    entries = []
    offset = 0
    for _page in range(MAX_PAGES_PER_SEED):
        data = s2_get(f"/paper/{paper_id}/citations",
                       {"fields": "title,year,authors,abstract,externalIds,venue", "limit": PAGE_SIZE, "offset": offset})
        rows = data.get("data") or []
        if not rows:
            break
        for row in rows:
            cp = row.get("citingPaper") or {}
            arxiv_id = (cp.get("externalIds") or {}).get("ArXiv")
            if not arxiv_id or not cp.get("title") or not cp.get("year"):
                continue
            authors = ", ".join(a.get("name") for a in (cp.get("authors") or []) if a.get("name"))
            # A paper discovered via this arXiv-citation crawl was always
            # labeled "arXiv preprint" regardless of whether it was actually
            # published somewhere real -- confirmed on real data: "Argoverse
            # 2" is a NeurIPS 2021 Datasets & Benchmarks paper, shown on the
            # site as arXiv-only. Semantic Scholar's own `venue` field (not
            # previously requested) has the real venue when S2 knows one;
            # only fall back to "arXiv preprint" when it's blank, which is
            # still the right label for a genuine not-yet-published preprint.
            entries.append({
                "conference": cp.get("venue") or "arXiv preprint",
                "year": cp["year"],
                "title": cp["title"],
                "authors": authors,
                "abstract": cp.get("abstract"),
                "doi": f"https://arxiv.org/abs/{arxiv_id}",
            })
        if "next" not in data:
            break
        offset = data["next"]
    return entries


def load_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def main():
    # A paper title containing a character outside the Windows console's
    # default cp1252 encoding (e.g. a Greek letter) crashes a plain print()
    # with UnicodeEncodeError -- confirmed in practice: a run died partway
    # through a 12,369-seed "all" pass on exactly this, losing the rest of
    # that run's progress (already-checked seeds are still checkpointed via
    # SEEDS_FILE, so a rerun resumes rather than restarts, but it still
    # needs to be manually restarted and can hit the same crash again on
    # the next non-cp1252 title). errors='replace' keeps the run going with
    # an unreadable character or two in the log instead of dying outright.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    arg = sys.argv[1] if len(sys.argv) > 1 else "150"
    n = None if arg == "all" else int(arg)
    seed_titles = top_seed_titles(n)

    seeds = load_json(SEEDS_FILE, {})
    pending = [t for t in seed_titles if normalize_title(t) not in seeds]
    print(f"{len(pending)} seed papers left to check (of {len(seed_titles)} requested, "
          f"{len(seeds)} already checked in a previous run)", flush=True)

    all_papers = {}
    for f in load_json(OUT_FILE, []):
        all_papers[normalize_title(f["title"])] = f

    for i, title in enumerate(pending, 1):
        key = normalize_title(title)
        try:
            paper_id = resolve_paper_id(title)
            found = fetch_citing_arxiv_papers(paper_id) if paper_id else []
        except Exception as e:
            print(f"  [{i}/{len(pending)}] {title[:60]}: failed ({e})", flush=True)
            continue

        new_count = 0
        for entry in found:
            fkey = normalize_title(entry["title"])
            if fkey == key or fkey in all_papers:
                continue
            all_papers[fkey] = entry
            new_count += 1

        seeds[key] = {"resolved": bool(paper_id), "found": len(found), "new": new_count}
        print(f"  [{i}/{len(pending)}] \"{title[:50]}\": {len(found)} citing arXiv papers, {new_count} new",
              flush=True)

        if i % 10 == 0 or i == len(pending):
            save_json(SEEDS_FILE, seeds)
            save_json(OUT_FILE, list(all_papers.values()))

    save_json(SEEDS_FILE, seeds)
    save_json(OUT_FILE, list(all_papers.values()))
    print(f"\nWrote {OUT_FILE}: {len(all_papers)} papers found via verified Semantic Scholar citations", flush=True)


if __name__ == "__main__":
    main()
