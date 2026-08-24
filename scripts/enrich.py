#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Enriches raw Scholar-extracted citing papers (av-atlas/data/raw/<seed_id>.json)
with author affiliations/countries from the free OpenAlex API (no signup, no
auth, generous rate limit), matched by paper title. Writes
av-atlas/data/enriched.json.

OpenAlex's author-institution linking is occasionally wrong (mis-disambiguated
affiliations on multi-author papers). To catch that, each paper's OpenAlex
author list is cross-checked against an independent source's author list:
arXiv first (most AV/ML papers have a preprint there), falling back to
Crossref (which indexes IEEE/ACM/Springer/Elsevier DOI metadata) for papers
with no arXiv record. Institution/country credit is only assigned when a
check passes or neither source has a record to check against; papers with a
confirmed author-list mismatch are kept (for the paper leaderboard) but
excluded from institution/country aggregation.

Only the Scholar-scraped citation count (citations_scholar) is kept -- an
OpenAlex citation count used to be captured here too, but the site never
ranks or displays anything but its own in-corpus citation graph (see
citation_count() in aggregate.py), so an external count has no use once
recorded and was dropped at the source.

Abstracts are captured for later text-based analysis (classification, etc.):
preferentially arXiv's full abstract, then Crossref's (when present -- many
publishers don't supply one), then Scholar's on-page snippet as a last
resort (raw entries may carry a "snippet" field for this fallback).

Raw file format (one per seed, produced by manually/agent-driven Google
Scholar browsing -- see av-atlas/data/seeds.json for the seed list):
[
  {"title": "...", "authors": "A. One, B. Two", "year": 2021, "venue": "CVPR",
   "citations": 123, "snippet": "Robust detection and tracking of..."},
  ...
]
"snippet" is optional -- omit it if not captured during extraction.

Usage: python enrich.py
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
OUT_FILE = BASE / "data" / "enriched.json"

OPENALEX_URL = "https://api.openalex.org/works"
ARXIV_URL = "https://export.arxiv.org/api/query"
CROSSREF_URL = "https://api.crossref.org/works"
# mailto param puts us in OpenAlex's/Crossref's "polite pool" (faster, more reliable) -- no signup needed.
CONTACT_EMAIL = "holger@it-caesar.com"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}

# Overlap ratio (by last name) between OpenAlex's and arXiv's author list
# below which we don't trust OpenAlex's affiliation data for this paper.
AUTHOR_MATCH_THRESHOLD = 0.6


def openalex_lookup(title, max_retries=4):
    params = urllib.parse.urlencode({"search": title, "per-page": 1, "mailto": CONTACT_EMAIL})
    req = urllib.request.Request(f"{OPENALEX_URL}?{params}", headers={"User-Agent": f"av-atlas (mailto:{CONTACT_EMAIL})"})
    delay = 3.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results") or []
            return results[0] if results else None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            print(f"  OpenAlex lookup failed for {title!r}: {e}")
            return None
        except Exception as e:
            print(f"  OpenAlex lookup failed for {title!r}: {e}")
            return None


def arxiv_lookup(title, max_retries=2):
    query = f'ti:"{title}"'
    params = urllib.parse.urlencode({"search_query": query, "start": 0, "max_results": 1})
    req = urllib.request.Request(f"{ARXIV_URL}?{params}", headers={"User-Agent": f"av-atlas (mailto:{CONTACT_EMAIL})"})
    delay = 3.0
    for attempt in range(max_retries):
        try:
            # arXiv's own search backend occasionally hangs on certain
            # quoted-phrase queries (reproduced with curl, not a urllib bug) --
            # use a short timeout and don't burn time retrying a slow query,
            # only an explicit rate-limit signal (429) is worth backing off for.
            with urllib.request.urlopen(req, timeout=8) as resp:
                xml_bytes = resp.read()
            root = ET.fromstring(xml_bytes)
            entry = root.find("atom:entry", ARXIV_NS)
            if entry is None:
                return None
            entry_title = (entry.findtext("atom:title", default="", namespaces=ARXIV_NS) or "").strip()
            # arXiv's search is fuzzy; require the returned title to actually match.
            if normalize_title(entry_title) != normalize_title(title):
                return None
            authors = [a.findtext("atom:name", default="", namespaces=ARXIV_NS).strip()
                       for a in entry.findall("atom:author", ARXIV_NS)]
            summary = (entry.findtext("atom:summary", default="", namespaces=ARXIV_NS) or "").strip()
            summary = re.sub(r"\s+", " ", summary)
            return {"authors": [a for a in authors if a], "abstract": summary or None}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        except (TimeoutError, urllib.error.URLError):
            return None  # likely a slow/hung query on arXiv's end -- fail fast, don't retry
        except Exception:
            return None


def crossref_lookup(title, max_retries=3):
    # Crossref's top relevance hit is sometimes a supplementary-material entry
    # rather than the paper itself, so scan a few candidates for a real title match.
    params = urllib.parse.urlencode({
        "query.bibliographic": title, "rows": 5, "mailto": CONTACT_EMAIL,
    })
    req = urllib.request.Request(f"{CROSSREF_URL}?{params}", headers={"User-Agent": f"av-atlas (mailto:{CONTACT_EMAIL})"})
    delay = 3.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = (data.get("message") or {}).get("items") or []
            for item in items:
                item_titles = item.get("title") or []
                if item_titles and normalize_title(item_titles[0]) == normalize_title(title):
                    authors = []
                    for a in (item.get("author") or []):
                        name = " ".join(p for p in [a.get("given"), a.get("family")] if p)
                        if name:
                            authors.append(name)
                    abstract = item.get("abstract")
                    if abstract:
                        abstract = re.sub(r"<[^>]+>", "", abstract)  # Crossref abstracts are JATS-XML-tagged
                        abstract = re.sub(r"\s+", " ", abstract).strip()
                    return {"authors": authors, "publisher": item.get("publisher"), "abstract": abstract or None}
            return None
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            return None
        except (TimeoutError, urllib.error.URLError):
            return None  # fail fast rather than retry a possibly-hung query
        except Exception:
            return None


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def last_name(full_name):
    parts = full_name.strip().split()
    return parts[-1].lower() if parts else ""


def author_overlap_ratio(names_a, names_b):
    if not names_a or not names_b:
        return None
    set_a = {last_name(n) for n in names_a if last_name(n)}
    set_b = {last_name(n) for n in names_b if last_name(n)}
    if not set_a or not set_b:
        return None
    overlap = len(set_a & set_b)
    return overlap / max(len(set_a), len(set_b))


def main():
    seed_files = sorted(RAW_DIR.glob("*.json"))
    if not seed_files:
        raise SystemExit(f"No raw seed files found in {RAW_DIR} -- run the Scholar extraction first")

    enriched = []
    seen_titles = set()
    for seed_file in seed_files:
        seed_id = seed_file.stem
        entries = json.loads(seed_file.read_text(encoding="utf-8"))
        for entry in entries:
            title = entry.get("title", "").strip()
            if not title or title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())

            work = openalex_lookup(title)
            time.sleep(0.15)

            record = {
                "title": title,
                "authors": entry.get("authors"),
                "year": entry.get("year"),
                "venue": entry.get("venue"),
                "seed": seed_id,
                "citations_scholar": entry.get("citations"),
                # Deliberately NOT capturing work["cited_by_count"] -- the
                # site only ever ranks/displays the in-corpus citation graph
                # (see citation_count() in aggregate.py), never OpenAlex or
                # any other external provider's count. `work` is still
                # looked up above for its author/institution data below.
                # Citation counts are a point-in-time snapshot from whichever
                # source reported them, not a live figure -- stamped so a
                # future re-run can tell which papers are due for a refresh.
                "citations_updated": TODAY,
            }

            authors_detail = []
            if work:
                for a in (work.get("authorships") or []):
                    name = (a.get("author") or {}).get("display_name")
                    if not name:
                        continue
                    insts = a.get("institutions") or []
                    authors_detail.append({
                        "name": name,
                        "affiliations": [i.get("display_name") for i in insts if i.get("display_name")],
                        "countries": [i.get("country_code") for i in insts if i.get("country_code")],
                    })
            record["authors_detail"] = authors_detail

            arxiv = arxiv_lookup(title)
            time.sleep(3.1)  # arXiv API usage policy: max ~1 request per 3 seconds
            abstract = None
            abstract_source = None
            if arxiv is not None:
                ratio = author_overlap_ratio([d["name"] for d in authors_detail], arxiv["authors"])
                verified = ratio is not None and ratio >= AUTHOR_MATCH_THRESHOLD
                record["author_verification"] = {
                    "status": "verified_arxiv" if verified else "mismatch",
                    "source": "arxiv",
                    "overlap_ratio": ratio,
                    "reference_authors": arxiv["authors"],
                }
                if arxiv.get("abstract"):
                    abstract, abstract_source = arxiv["abstract"], "arxiv"
            else:
                crossref = crossref_lookup(title)
                time.sleep(0.5)  # Crossref polite pool: generous, light delay is enough
                if crossref is None:
                    record["author_verification"] = {"status": "no_reference_record"}
                else:
                    if crossref.get("abstract"):
                        abstract, abstract_source = crossref["abstract"], "crossref"
                    ratio = author_overlap_ratio([d["name"] for d in authors_detail], crossref["authors"])
                    verified = ratio is not None and ratio >= AUTHOR_MATCH_THRESHOLD
                    record["author_verification"] = {
                        "status": "verified_crossref" if verified else "mismatch",
                        "source": "crossref",
                        "publisher": crossref.get("publisher"),
                        "overlap_ratio": ratio,
                        "reference_authors": crossref["authors"],
                    }

            if not abstract and entry.get("snippet"):
                abstract, abstract_source = entry["snippet"], "scholar_snippet"
            record["abstract"] = abstract
            record["abstract_source"] = abstract_source

            enriched.append(record)
            status = record["author_verification"]["status"]
            print(f"  [{seed_id}] ({status}) {title[:60]}")

    OUT_FILE.write_text(json.dumps(enriched, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    statuses = [e["author_verification"]["status"] for e in enriched]
    counts = {s: statuses.count(s) for s in set(statuses)}
    print(f"\nWrote {len(enriched)} enriched entries to {OUT_FILE}")
    print(f"  {counts}")


if __name__ == "__main__":
    main()
