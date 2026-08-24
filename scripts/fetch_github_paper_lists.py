#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ICRA/IROS have no bulk-scrapable official source for most years (IEEE Xplore
blocks direct access outright, OpenAlex only ever catalogued ICRA2022/
IROS2021-2022 and has since moved to a paid API -- see PIPELINE.md/
DECISIONS.md). This fetches from community-maintained GitHub repos that hand-
list each year's accepted papers instead -- found via hrjp/ICRA-IROS-
PaperList, itself just an index of these (https://github.com/hrjp/ICRA-IROS-
PaperList), not fetched directly since it only links out, it doesn't carry
paper data itself.

Three genuinely different formats across repo families, confirmed by reading
each family's raw README:
  - PaoPaoRobot (ICRA/IROS 2019-2020) and dectrfov (2021): "## Category"
    headings, "- Title" bullets. Title only, no authors at all.
  - ryanbgriffiths (2023-2024): a markdown table with a "Authors" column,
    comma-separated full names ("First Last, First Last").
  - DoongLi (2025): a markdown table with an "Authors" column,
    semicolon-separated "Last, First" pairs -- reformatted to "First Last"
    here so author names read the same way regardless of source, matching
    the convention every other venue in this corpus already uses.

User-requested: save a reference to exactly which page each paper came from,
not just that it came from "a venue listing" -- every entry here carries
source_url (the exact GitHub repo it was pulled from), which merge_corpus.py
threads through onto papers_full.json.

Usage: python fetch_github_paper_lists.py
"""
import json
import re
import time
import urllib.error

from fetch_common import BASE, OUT_DIR, HEADERS, fetch as _fetch

# (year, venue, "owner/repo", branch, format) -- format is "bullet" (title
# only) or "table" with an author-cell convention tag.
REPOS = [
    # ICRA2019/IROS2019/IROS2020 are |index|title| or |title|index| tables
    # (no "author" column at all -- confirmed by reading the raw file), NOT
    # bullet lists like their sibling years from the same org; parse_table
    # is header-aware so "table_comma" here just means "no authors column
    # will be found, title only" -- same as bullet's actual output.
    (2019, "ICRA", "PaoPaoRobot/ICRA2019-paper-list", "master", "table_comma"),
    (2020, "ICRA", "PaoPaoRobot/ICRA2020-paper-list", "master", "bullet"),
    (2021, "ICRA", "dectrfov/ICRA2021PaperList", "main", "bullet"),
    (2023, "ICRA", "ryanbgriffiths/ICRA2023PaperList", "main", "table_comma"),
    (2024, "ICRA", "ryanbgriffiths/ICRA2024PaperList", "main", "table_comma"),
    (2025, "ICRA", "DoongLi/ICRA2025-Paper-List", "main", "table_semicolon"),
    (2019, "IROS", "PaoPaoRobot/IROS2019-paper-list", "master", "table_comma"),
    (2020, "IROS", "PaoPaoRobot/IROS2020-paper-list", "master", "table_comma"),
    (2021, "IROS", "dectrfov/IROS2021PaperList", "main", "bullet"),
    (2022, "IROS", "PaoPaoRobot/IROS2022-paper-list", "main", "bullet"),
    # Confirmed by reading each raw file directly (not assumed from the
    # sibling ICRA years, which turned out to use a different convention
    # than at least one of these): IROS2023 is "<br>"-joined "Last, First,
    # Institution..." per author, IROS2024 is ";"-joined "Last, First".
    (2023, "IROS", "ryanbgriffiths/IROS2023PaperList", "main", "table_br_lastfirst"),
    (2024, "IROS", "ryanbgriffiths/IROS2024PaperList", "main", "table_semicolon"),
    (2025, "IROS", "DoongLi/IROS2025-Paper-List", "main", "table_semicolon"),
]

RAW_BASE = "https://raw.githubusercontent.com"
BRANCH_FALLBACKS = ("main", "master")


def fetch_readme(repo, branch):
    branches = (branch,) + tuple(b for b in BRANCH_FALLBACKS if b != branch)
    last_error = None
    for b in branches:
        for filename in ("README.md", "readme.md"):
            url = f"{RAW_BASE}/{repo}/{b}/{filename}"
            try:
                return _fetch(url, timeout=20, max_retries=3, retry_status=(429, 503), backoff=5)
            except urllib.error.HTTPError as e:
                last_error = e
                continue
    raise last_error


def parse_bullets(text):
    # "- Title" lines under a category heading are real papers -- but every
    # repo in this family also opens with a "# Topics"/"# Categories"
    # table of contents that lists every category name as its OWN
    # "- Category Name" bullet, syntactically identical to a real paper
    # bullet. Confirmed on real data: naively collecting every bullet
    # captured "Active Perception in Robotics" and "Awards I: Service
    # Robots; Medical Robotics" (TOC entries) as if they were paper titles.
    #
    # Gating on heading LEVEL (e.g. "only after the first ## heading") only
    # works for repos that use h1 for the TOC and h2 for real sections --
    # confirmed a different repo in this same family (PaoPaoRobot/IROS2022)
    # uses h1 for its real category headings too, with no separate TOC
    # section at all, so a level-based gate would wrongly discard every
    # real paper there. What's actually consistent across every repo seen
    # is the TOC heading's own TEXT ("Topics" or "Categories") -- skip
    # bullets only while directly under a heading matching that, at
    # whatever level, and otherwise collect bullets under any heading.
    titles = []
    heading_count = 0
    in_toc_section = False
    for line in text.splitlines():
        heading_m = re.match(r"^#+\s+(.*\S)\s*$", line)
        if heading_m:
            heading_count += 1
            in_toc_section = bool(re.fullmatch(r"(topics|categories)", heading_m.group(1).strip(), re.I))
            continue
        # Bullets directly under the doc's very first heading (its title)
        # are front matter, not a category section -- confirmed on real
        # data: one repo puts its TOC bullet list straight after the title
        # with no dedicated "Topics"/"Categories" sub-heading in between at
        # all, so name-matching a TOC heading alone isn't enough; a bullet
        # only counts once we're past heading #1 too.
        if heading_count < 2 or in_toc_section:
            continue
        m = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if m:
            title = m.group(1).strip()
            if title and not title.startswith("["):
                titles.append(title)
    return [{"title": t, "authors": ""} for t in titles]


def parse_table(text, author_format):
    # Header-aware, not position-based: confirmed different repos in this
    # "table" family use different column layouts for the same idea (some
    # are |title|authors|org|session|, some are |index|title| or
    # |title|index| with no authors at all) -- fix the title/authors
    # columns by matching the actual header row's cell text rather than
    # assuming column 0/1, so a repo with "index" in the second column
    # doesn't get that number parsed as an "author".
    papers = []
    title_col, authors_col = None, None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|") or not line.endswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue  # the "|---|---|" separator row
        lower = [c.lower() for c in cells]
        # Substring match, not exact -- confirmed a real repo's header is
        # "paper title", not the bare "title" an exact-equality check
        # against the cell list would require.
        title_candidates = [i for i, h in enumerate(lower) if "title" in h]
        if title_col is None and title_candidates:
            title_col = title_candidates[0]
            for i, h in enumerate(lower):
                if "author" in h:
                    authors_col = i
            continue  # this row IS the header, not a paper
        if title_col is None or title_col >= len(cells):
            continue  # haven't seen a header yet, or a malformed row
        title = cells[title_col]
        if not title:
            continue
        authors_raw = cells[authors_col] if authors_col is not None and authors_col < len(cells) else ""
        if author_format == "table_comma":
            authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
        elif author_format == "table_semicolon":
            # Each segment is "Last, First".
            authors = []
            for seg in authors_raw.split(";"):
                seg = seg.strip().strip(",").strip()
                if not seg:
                    continue
                if "," in seg:
                    last, first = (p.strip() for p in seg.split(",", 1))
                    authors.append(f"{first} {last}".strip())
                else:
                    authors.append(seg)
        else:  # table_br_lastfirst: "<br>"-joined "Last, First, Institution,
            # Institution2..." per author -- confirmed on real data (a
            # different repo than table_semicolon's, same conference,
            # different year: the institution count per author varies, so
            # only the first two comma-separated pieces of each "<br>"
            # segment are trustworthy as the name; anything after that is
            # institution text, discarded here (author-affiliation data
            # comes from the OpenAlex/arXiv enrichment stage elsewhere in
            # this pipeline, not from this venue-listing fetch).
            authors = []
            for seg in authors_raw.split("<br>"):
                parts = [p.strip() for p in seg.split(",")]
                parts = [p for p in parts if p]
                if len(parts) >= 2:
                    authors.append(f"{parts[1]} {parts[0]}".strip())
                elif parts:
                    authors.append(parts[0])
        papers.append({"title": title, "authors": ", ".join(authors)})
    return papers


def main():
    for year, venue, repo, branch, fmt in REPOS:
        out_file = OUT_DIR / f"{venue.lower()}{year}_github.json"
        if out_file.exists():
            print(f"{venue}{year} ({repo}): already fetched, skipping (delete {out_file.name} to refetch)")
            continue
        try:
            text = fetch_readme(repo, branch)
        except Exception as e:
            print(f"{venue}{year} ({repo}): failed to fetch README: {e}")
            continue
        if fmt == "bullet":
            raw_papers = parse_bullets(text)
        else:
            raw_papers = parse_table(text, fmt)
        # A parse that finds implausibly few papers for a major conference
        # (each of these years accepted 1000+ papers) means the format
        # assumption was wrong for this repo, not that the conference
        # really had 10 papers -- skip writing garbage rather than silently
        # shipping a near-empty year.
        if len(raw_papers) < 100:
            print(f"{venue}{year} ({repo}): only parsed {len(raw_papers)} papers, "
                  f"format assumption ({fmt}) likely wrong for this repo -- skipped, not written")
            continue
        source_url = f"https://github.com/{repo}"
        papers = [{
            "conference": venue, "year": year, "title": p["title"], "authors": p["authors"],
            "abstract": None, "source_url": source_url,
        } for p in raw_papers]
        out_file.write_text(json.dumps(papers, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
        print(f"{venue}{year} ({repo}): wrote {len(papers)} papers to {out_file.name}")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
