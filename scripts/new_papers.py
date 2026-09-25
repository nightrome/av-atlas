#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
The "New papers" list and its Atom feed.

merge_corpus.py stamps every paper with first_seen, the date it first showed
up in the corpus (see FIRST_SEEN_BASELINE there). This module turns that into
two small published files:

  - new_papers.json: AV papers first seen in the last WINDOW_DAYS days, with
    venue, year, authors and arXiv link. Written by aggregate.py next to
    stats.json, read by site/new.html.
  - feed.xml: an Atom feed of the newest FEED_LIMIT of those, written by
    build_public_site.py from new_papers.json.

Papers that carry the baseline date were already in the corpus before
first_seen tracking started, so they are never listed as new.

Titles, venues and author names only -- no abstracts. Most abstracts in the
corpus come from sources whose licence doesn't cover republishing them in a
feed.

Not run on its own; aggregate.py and build_public_site.py import it.
"""
import re
import urllib.parse
from datetime import date, timedelta
from xml.sax.saxutils import escape

from merge_corpus import FIRST_SEEN_BASELINE

# "The last three months", counted back from the build date.
WINDOW_DAYS = 92
# Enough for a large new edition (CVPR brings a few hundred AV papers) without
# letting a runaway first_seen bug ship a huge file.
MAX_PAPERS = 2000
FEED_LIMIT = 100
# Author names shown per entry; the paper page has the full list.
MAX_AUTHORS = 10

SITE_URL = "https://nightrome.github.io/av-atlas"

_HTTP_URL_RE = re.compile(r"^https?://", re.I)


def is_new(first_seen, since):
    """True when first_seen is a real date inside the window (not the
    baseline stamp, not missing)."""
    return bool(first_seen) and first_seen > FIRST_SEEN_BASELINE and first_seen >= since


def recent_papers(pairs, today, window_days=WINDOW_DAYS):
    """pairs: (papers_full entry, stats.json paper) for every AV paper that
    made it into stats.json. The entry supplies first_seen, the stats paper
    the cleaned-up fields the site already shows everywhere else.

    Returns the payload written to new_papers.json."""
    since = (date.fromisoformat(today) - timedelta(days=window_days)).isoformat()
    out = []
    for entry, paper in pairs:
        first_seen = entry.get("first_seen")
        if not is_new(first_seen, since) or not paper.get("title"):
            continue
        arxiv_url = paper.get("arxiv_url") or ""
        out.append({
            "title": paper["title"],
            "first_seen": first_seen,
            "venue": paper.get("venue"),
            "year": paper.get("year"),
            "category": paper.get("category"),
            "authors": (paper.get("authors") or [])[:MAX_AUTHORS],
            "n_authors": len(paper.get("authors") or []),
            # Only a real web link -- this ends up as an href on the page.
            "arxiv_url": arxiv_url if _HTTP_URL_RE.match(arxiv_url) else None,
        })
    # Newest first; within one day, by venue and then title.
    out.sort(key=lambda p: ((p["venue"] or "").lower(), p["title"].lower()))
    out.sort(key=lambda p: p["first_seen"], reverse=True)
    return {
        "generated_at": today,
        "since": since,
        "window_days": window_days,
        "papers": out[:MAX_PAPERS],
    }


def paper_url(title):
    return f"{SITE_URL}/paper.html?title={urllib.parse.quote(title, safe='')}"


def _entry_summary(p):
    parts = [" ".join(str(x) for x in (p.get("venue"), p.get("year")) if x)]
    authors = p.get("authors") or []
    if authors:
        names = ", ".join(authors[:3])
        if (p.get("n_authors") or len(authors)) > 3:
            names += " et al."
        parts.append(names)
    return ". ".join(x for x in parts if x) + ("." if any(parts) else "")


# Characters XML 1.0 doesn't allow at all, even escaped. A stray one in a
# scraped title would otherwise make the whole feed unparseable.
_XML_INVALID_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _text(value):
    return escape(_XML_INVALID_RE.sub("", str(value)))


def _attr(value):
    return escape(_XML_INVALID_RE.sub("", str(value)), {'"': "&quot;"})


def render_atom(payload, limit=FEED_LIMIT):
    """An Atom 1.0 document for the newest `limit` papers in a
    new_papers.json payload.

    Every date in it comes from the data (first_seen, or the payload's own
    generated_at when nothing is new), so rebuilding without new papers
    produces the same feed and readers don't see phantom updates."""
    papers = (payload.get("papers") or [])[:limit]
    updated = max((p["first_seen"] for p in papers), default=None) or payload.get("generated_at")
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<feed xmlns="http://www.w3.org/2005/Atom">',
        "  <title>AV Atlas: new papers</title>",
        "  <subtitle>Autonomous vehicle papers recently added to AV Atlas.</subtitle>",
        f'  <link rel="self" type="application/atom+xml" href="{SITE_URL}/feed.xml"/>',
        f'  <link rel="alternate" type="text/html" href="{SITE_URL}/new.html"/>',
        f"  <id>{SITE_URL}/feed.xml</id>",
        f"  <updated>{updated}T00:00:00Z</updated>",
        "  <author><name>AV Atlas</name></author>",
    ]
    for p in papers:
        url = _attr(paper_url(p["title"]))
        lines += [
            "  <entry>",
            f"    <title>{_text(p['title'])}</title>",
            f'    <link rel="alternate" type="text/html" href="{url}"/>',
        ]
        if p.get("arxiv_url"):
            lines.append(f'    <link rel="related" href="{_attr(p["arxiv_url"])}"/>')
        lines += [
            f"    <id>{url}</id>",
            f"    <updated>{p['first_seen']}T00:00:00Z</updated>",
        ]
        for name in (p.get("authors") or [])[:MAX_AUTHORS]:
            lines.append(f"    <author><name>{_text(name)}</name></author>")
        summary = _entry_summary(p)
        if summary:
            lines.append(f"    <summary>{_text(summary)}</summary>")
        lines.append("  </entry>")
    lines.append("</feed>")
    return "\n".join(lines) + "\n"
