#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Merges the venue-listing pulls into one corpus:
  - av-atlas/data/venues/*.json (CVPR/ICCV/WACV/ECCV/NeurIPS/CoRL/ICRA/IROS/
    RSS/ICLR/AAAI, complete populations, no keyword filtering) plus
    arxiv*.json (arxiv_s2_citing.json from fetch_semanticscholar_citing.py,
    and arxiv_monthly_<yyyy>-<mm>.json from fetch_arxiv_monthly.py -- NOT
    complete populations; see those scripts' docstrings).
    arXiv entries are only ever used to fill a title the other venues don't
    already have -- see the venue_files/arxiv_files split below.

An earlier citation-crawl pilot (seeded from nuScenes/KITTI/Waymo via Google
Scholar's "Cited by" lists, data/seeds.json -> data/raw/*.json ->
enrich.py -> data/enriched.json) was deliberately never folded in here and
has since been removed entirely. It used a different, non-uniform sampling
method -- a paper's presence depended on whether it happened to cite one of
~100 seed dataset papers, not on having been published at one of the venues
everything else is drawn from -- which would have made "how was this paper
found" an invisible, unstated variable across the whole corpus.

Dedupes by normalized title within the venue pulls, then folds an arXiv-file
record into the venue record that carries the same arXiv id (a preprint that
was renamed for its camera-ready version).

Rewrites author strings stored as "Last, First, Last, First" (NeurIPS) or
BibTeX "Last, First and Last, First" (ECCV 2018) into the plain
"First Last, First Last" form every other source uses.

Classifies every paper (category + av_relevance) via classify.py's
keyword-taxonomy approach, applied uniformly to the whole corpus -- this
labels papers, it does not decide which papers are included.

Writes av-atlas/data/papers_full.json.

Fails (non-zero exit, previous papers_full.json left alone) rather than
writing a quietly smaller corpus when: the previous papers_full.json exists
but can't be parsed (its carried-over enrichment would be lost), a venue file
can't be parsed (its papers would be lost), or the previous corpus had
papers from data/venues/arxiv_s2_citing.json and this merge has none (the
gitignored S2 file is missing or empty -- restore it with restore_corpus.py).

Usage: python merge_corpus.py [--allow-s2-loss]

--allow-s2-loss writes the merge anyway when the S2-discovered papers are
gone, for a deliberate rebuild without that file.
"""
import html
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from atomic_write import write_json_atomic
import classify as cl

BASE = Path(__file__).resolve().parent.parent
VENUES_DIR = BASE / "data" / "venues"
OUT_FILE = BASE / "data" / "papers_full.json"
CATEGORIES_FILE = BASE / "data" / "categories.json"

# Every entry in a per-venue-year file (e.g. cvpr2024.json) repeated the same
# "conference"/"year" values 265k times over -- 11.1MB of pure redundancy
# across data/venues/*.json, already fully determined by the filename
# (repo-size cleanup, user-requested; see DECISIONS.md). scripts/strip_
# redundant_venue_fields.py removes them from the tracked files; this dict
# (derived from the real, once-verified 1:1 filename-prefix -> conference
# mapping, not guessed) is how they're reconstructed on read.
VENUE_PREFIX_TO_CONFERENCE = {
    "aaai": "AAAI", "accv": "ACCV", "bmvc": "BMVC", "corl": "CoRL", "cvpr": "CVPR",
    "eccv": "ECCV", "gcpr": "GCPR", "iccv": "ICCV", "iclr": "ICLR", "icml": "ICML",
    "icra": "ICRA", "ijcv": "IJCV", "ijrr": "IJRR", "iros": "IROS", "itsc": "ITSC",
    "iv": "IV", "neurips": "NeurIPS", "ral": "RA-L", "rss": "RSS", "tits": "T-ITS",
    "tpami": "TPAMI", "tro": "T-RO", "wacv": "WACV",
}


def conference_and_year_for_file(filename):
    """(conference, year) implied by a venue filename, or (None, None) for a
    prefix this table doesn't recognize (e.g. an arxiv*.json file, which
    keeps its own real per-entry conference/year and is never looked up
    here -- see the venue_files/arxiv_files split below). Only a fallback:
    callers still prefer a per-entry "conference"/"year" field when the
    source (currently only arxiv_s2_citing.json, and any not-yet-migrated
    venue file) actually carries one -- see the "or" in the record-
    construction loop below. year is None for a "_all" journal file (e.g.
    ijcv_all.json -- continuous publication, not one proceedings per file,
    so the real year still has to come from each entry, not the filename)."""
    stem = filename[:-5] if filename.endswith(".json") else filename
    m = re.match(r"^([a-z]+)", stem)
    conference = VENUE_PREFIX_TO_CONFERENCE.get(m.group(1)) if m else None
    year_match = re.search(r"(\d{4})", stem)
    year = int(year_match.group(1)) if year_match else None
    return conference, year


# A community paper-list sometimes stores a title as a raw markdown link,
# "[Real Title](https://arxiv.org/abs/....)" (confirmed: ~600 IROS 2024
# entries via fetch_github_paper_lists.py) -- or wraps the whole title in
# literal quotes (several ECCV listings). Either leaves the paper un-deduped
# against its clean copy from another source, and shows the raw markup on
# the site. clean_title() strips both; it feeds the STORED display title as
# well as normalize_title() below.
_MARKDOWN_LINK_TITLE_RE = re.compile(r"^\s*\[([^\]]+)\]\((?:https?|ftp)://[^)]*\)\s*$")
_SUPERSCRIPT_DIGITS = str.maketrans(
    "¹²³⁴⁵⁶⁷⁸⁹⁰"
    "₀₁₂₃₄₅₆₇₈₉",
    "12345678900123456789")
# A decorative emoji dropped into a title ("🏘️ ProcTHOR: ...", "PooDLe🐩:",
# "⚡FLARES⚡: ...") -- always author whimsy, never part of the name, and it
# left the paper un-deduped against its plain copy.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌀-⏿️]")
# LaTeX math glue plus any whitespace it sat next to, so "R $^2$ Former" ->
# "R2Former" and "VIENA ^2 :" -> "VIENA2:".
_TITLE_MATH_RE = re.compile(r"\s*[\^\$\{\}\\]+\s*")
# A trailing plural "s" on the flattened key -- the citing-paper metadata
# routinely drops or adds it on the last word ("... Multi-View Image[s]",
# "... Spiking Neural Network[s]"). Folded so singular/plural of one paper
# collapse; "...ss" (address, progress) is left alone.
_TRAILING_PLURAL_S_RE = re.compile(r"(?<=[a-z]{4})s$")


def clean_title(t):
    # Some listings (DBLP, Semantic Scholar) hand over titles with HTML
    # entities still in them ("Detection &amp; Recognition", "B&#233;zier").
    # The site renders titles as plain text, so they showed up literally.
    t = html.unescape(t or "").strip()
    m = _MARKDOWN_LINK_TITLE_RE.match(t)
    if m:
        t = m.group(1).strip()
    for q in ('"', "'", "“", "”"):
        t = t.strip(q).strip()
    return re.sub(r"\s{2,}", " ", t).strip()


# A leading "ACRONYM: " (a single, space-free token, then a colon or -- as a
# GitHub-list rendering artifact -- a spaced dash) is usually a paper's own
# coined short-name for itself, not part of what makes the paper distinct
# from its other listings: the same paper is routinely titled with the
# prefix by one source and without it by another (confirmed on real data:
# ECCV's own listing of "Generative End-to-End Autonomous Driving" carries
# no "GenAD:", a citation-graph-discovered copy does). Stripped only when
# the prefix is a single word, so a descriptive lead-in ("Learning to
# Drive: A Survey") is untouched -- a multi-word phrase is far likelier to
# coincidentally share a generic remainder with an unrelated paper than a
# coined name is, and two DIFFERENT papers reusing the same acronym stay
# distinct here regardless (their remainders still differ). The dash form
# ("MVX-Net - Multimodal VoxelNet ...", ICRA's GitHub lists, vs everyone
# else's "MVX-Net: ...") is only accepted for a clearly coined leading
# token (internal capital / digit / separator, not "2D"/"3D") AND a
# substantial remainder, so "2D - X" / "3D - X" can't collapse together.
_ACRONYM_PREFIX_RE = re.compile(
    r"^\s*(?P<w>[A-Za-z0-9][A-Za-z0-9+._-]{1,19})\s*"
    r"(?P<sep>:|(?<=\s)[\-–—])\s*(?=\S)")


def _acronym_prefix_ok(w, sep, rest):
    if sep == ":":
        return True
    coined = not re.fullmatch(r"\d+[Dd]", w) and any(
        c.isupper() or c.isdigit() or c in "-+_" for c in w[1:])
    return coined and len(re.sub(r"[^a-z0-9]", "", rest.lower())) >= 25



# Same paper, retitled between its arXiv preprint and its camera-ready
# conference version, in a shape none of the heuristics above catch: a
# swapped LEADING QUALIFIER ahead of an otherwise identical remainder
# ("Deep Visual Odometry with Events and Frames" vs "End-to-end Learned
# Visual Odometry with Events and Frames"), not an acronym prefix or a
# trailing plural. Confirmed the same paper via an identical 7-author
# byline (Pellerito, Cannici, Gehrig, Belhadj, Dubois-Matra, Casasco,
# Scaramuzza) on both, found while manually verifying an arXiv-link
# candidate (2309.09947, "RAMP-VO") against this corpus -- without this,
# the IROS 2023 arXiv-discovered copy and the IROS 2024 venue-listing copy
# double-count one real publication. A hand-verified pair, not a general
# rule: a broader "strip any leading adjective" heuristic would risk
# merging genuinely different papers that happen to share a generic
# remainder, same reasoning as GLUED_INSTITUTION_SPLITS/INSTITUTION_ALIASES
# preferring an exact verified pair over a broader pattern (aggregate.py).
KNOWN_DUPLICATE_TITLES = {
    "End-to-end Learned Visual Odometry with Events and Frames": "Deep Visual Odometry with Events and Frames",
}


def normalize_title(t):
    t = clean_title(t)
    t = KNOWN_DUPLICATE_TITLES.get(t, t)
    t = _EMOJI_RE.sub("", t.translate(_SUPERSCRIPT_DIGITS))
    t = _TITLE_MATH_RE.sub("", t)
    t = re.sub(r"\s+:", ":", t)  # "FG2 :" -> "FG2:"
    t = re.sub(r"^[^0-9A-Za-z(\"']+", "", t).strip()  # leading symbol/marker junk
    m = _ACRONYM_PREFIX_RE.match(t)
    if m and _acronym_prefix_ok(m.group("w"), m.group("sep"), t[m.end():]):
        t = t[m.end():]
    key = re.sub(r"[^a-z0-9]", "", t.lower())
    return key if key.endswith("ss") else _TRAILING_PLURAL_S_RE.sub("", key)


# first_seen: the date (UTC, YYYY-MM-DD) a paper first showed up in
# papers_full.json. Nothing reads it now (the "New papers" page and Atom
# feed that used it were removed), but it's cheap to keep. Nothing in the
# venue files records it, so it lives only in
# papers_full.json and is carried from one run to the next like the
# enrichment fields below: matched by normalized title, then by arXiv ID so
# a preprint whose title changed between versions isn't counted as new again.
#
# Tracking started in September 2026. Every paper already in the corpus then
# got FIRST_SEEN_BASELINE instead of a real date -- it means "on or before
# this date", and was never shown as new. The same
# baseline is used when there's no previous papers_full.json to carry dates
# from (a fresh clone, or a lost file): stamping 250k papers with today's date
# would announce the whole corpus as new.
FIRST_SEEN_BASELINE = "2026-09-01"
# See assign_first_seen().
MAX_NEW_PER_RUN = 20000

def today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def first_seen_index(prior_papers):
    """{"titles": {key: first_seen or None}, "arxiv": {id: first_seen}} from
    the previous papers_full.json. A title with None was in the corpus but
    predates tracking."""
    titles, arxiv = {}, {}
    for p in prior_papers:
        key = normalize_title(p.get("title"))
        if not key:
            continue
        seen = p.get("first_seen")
        # Two old records folding into one key keep the earlier date.
        old = titles.get(key)
        titles[key] = min(old, seen) if old and seen else (old or seen)
        aid = arxiv_id(p.get("arxiv_url"))
        if aid and seen and (aid not in arxiv or seen < arxiv[aid]):
            arxiv[aid] = seen
    return {"titles": titles, "arxiv": arxiv}


def assign_first_seen(merged, index, today):
    """Sets first_seen on every merged record. index is first_seen_index()'s
    result, or None when there was no previous papers_full.json. Returns how
    many papers got today's date."""
    new_records = []
    for key, p in merged.items():
        if index is None:
            p["first_seen"] = FIRST_SEEN_BASELINE
        elif key in index["titles"]:
            p["first_seen"] = index["titles"][key] or FIRST_SEEN_BASELINE
        elif arxiv_id(p.get("arxiv_url")) in index["arxiv"]:
            p["first_seen"] = index["arxiv"][arxiv_id(p.get("arxiv_url"))]
        else:
            new_records.append(p)
    # A month of new editions plus arXiv intake is a few thousand papers.
    # Far more than that means a venue file came back after a build that
    # ran without it (arxiv_s2_citing.json alone is ~49k papers), or a whole
    # venue history was backfilled at once. Neither is news, so those get the
    # baseline rather than flooding the New papers page and the feed.
    if len(new_records) > MAX_NEW_PER_RUN:
        print(f"  WARNING: {len(new_records)} papers not in the previous {OUT_FILE.name} -- more than "
              f"{MAX_NEW_PER_RUN}, so treated as a backfill: first_seen={FIRST_SEEN_BASELINE}, not today")
        today = FIRST_SEEN_BASELINE
    for p in new_records:
        p["first_seen"] = today
    return len(new_records) if today != FIRST_SEEN_BASELINE else 0


def discovery_source(filename):
    # A paper's "source" field records HOW it entered the corpus, not just
    # that it did -- lets a reader tell a venue's own official proceedings
    # listing apart from a discovery path with different reliability.
    if filename.startswith("arxiv_s2_citing"):
        return "arxiv_s2_citing_discovery"  # verified citation edge (Semantic Scholar)
    if filename.startswith("arxiv_monthly"):
        return "arxiv_monthly_intake"  # fetch_arxiv_monthly.py, new preprints from arXiv's own listings
    if filename.startswith("arxiv"):
        return "arxiv_author_pull"
    return "venue_listing"


# Two sources don't store authors as "First Last, First Last":
#  - NeurIPS (proceedings.neurips.cc) joins its citation_author meta tags,
#    each "Last, First", with ", " -- "Fan, Lue, Wang, Feng, Wang, Naiyan".
#    Split on commas that reads as six one-word people, and a two-word
#    given name ("Lee, Gim Hee") turned up on the site as a person called
#    "Gim Hee".
#  - ECCV 2018 carries BibTeX: "Tsoli, Aggeliki and Argyros, Antonis A.".
# Nothing in a single "Surname, Given" string says which form it is ("Aakash,
# Indranil Saha" in AAAI 2024 is two people, one with a single name), so the
# pair form is decided per file: a file where most multi-name strings have an
# even count and at least one one-word part is in that form. That covers
# every NeurIPS year and ECCV 2018, and no other venue file comes close
# (under 1% of any other file has that shape).
_AUTHOR_AND_RE = re.compile(r"\s*,?\s+and\s+")


def _author_parts(s):
    return [t.strip() for t in s.split(",") if t.strip()]


def _is_bibtex_author_string(s):
    parts = _AUTHOR_AND_RE.split(s.strip())
    return len(parts) > 1 and all(p.count(",") == 1 for p in parts)


def _looks_last_first(s):
    if _is_bibtex_author_string(s):
        return True
    parts = _author_parts(s)
    return len(parts) % 2 == 0 and any(len(p.split()) == 1 for p in parts)


def uses_last_first_authors(papers):
    """Whether a venue file stores its author strings as "Last, First" pairs
    (see the comment above). Strings with a single part don't count either
    way."""
    strings = [p.get("authors") for p in papers
               if isinstance(p.get("authors"), str) and len(_author_parts(p["authors"])) > 1]
    shaped = sum(1 for s in strings if _looks_last_first(s))
    return bool(strings) and shaped >= 0.5 * len(strings)


def normalize_author_string(s, last_first=False):
    """"First Last, First Last" for any of the author-string forms above.
    last_first says the file uses "Last, First" pairs; an odd number of
    parts there can't be paired safely ("Choo, XianJun, Davin, ..." has a
    two-part given name), so such a string is left as it is. A plain list
    that ends in "and" ("A B, C D and E F") just loses the "and"."""
    if not isinstance(s, str) or not s.strip():
        return s
    parts = _AUTHOR_AND_RE.split(s.strip())
    if len(parts) > 1:
        if all(p.count(",") == 1 for p in parts):
            return ", ".join(" ".join(reversed([x.strip() for x in p.split(",")])).strip() for p in parts)
        return ", ".join(p.strip().strip(",").strip() for p in parts if p.strip())
    if last_first:
        names = _author_parts(s)
        if names and len(names) % 2 == 0:
            return ", ".join(f"{names[i + 1]} {names[i]}" for i in range(0, len(names), 2))
    return s


# An arXiv id in any of the URL forms the corpus stores ("https://arxiv.org/
# abs/2003.08799", "http://arxiv.org/abs/2003.08799v2"); the version suffix
# is dropped so v1 and v2 of one preprint match.
_ARXIV_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?/?$", re.I)


def arxiv_id(url):
    m = _ARXIV_ID_RE.search((url or "").strip())
    return m.group(1).lower() if m else None


# Filled on the venue record from the arXiv copy folded into it, when the
# venue record has nothing of its own. The venue listing wins everything it
# does have (title, venue, year, authors).
ARXIV_MERGE_FILL_FIELDS = ("authors", "abstract")


def merge_by_arxiv_id(merged, carry_over_fields=()):
    """Folds each arXiv-file record into a venue-listing record carrying the
    same arXiv id, so a preprint that was renamed for the camera-ready
    version ("Pedestrian Detection: The Elephant In The Room" vs CVPR 2021's
    "Generalizable Pedestrian Detection: ...") is one paper, not two. The
    venue record keeps its own fields and only takes what it lacks from the
    arXiv copy (an ICRA/IROS GitHub-list record has no authors, for one).

    Two venue records sharing an id are left alone: that's a conference
    paper and its journal version (CVPR 2020 and IJCV 2021, IROS and RA-L),
    two listings the site counts separately. Two arXiv-file records sharing
    an id are left alone too: Semantic Scholar sometimes files a different
    paper by the same authors under one arXiv id.

    Mutates `merged` (normalized title -> record) and returns the number of
    records folded away."""
    venue_by_id = {}
    for key, p in merged.items():
        if p.get("source") == "venue_listing":
            aid = arxiv_id(p.get("arxiv_url"))
            if aid:
                venue_by_id.setdefault(aid, key)
    folded = []
    for key, p in merged.items():
        if p.get("source") == "venue_listing":
            continue
        target_key = venue_by_id.get(arxiv_id(p.get("arxiv_url")))
        if not target_key:
            continue
        target = merged[target_key]
        for field in ARXIV_MERGE_FILL_FIELDS + tuple(carry_over_fields):
            if target.get(field) in (None, "", [], {}) and p.get(field) not in (None, "", [], {}):
                target[field] = p[field]
        if target.get("citations") is None and p.get("citations") is not None:
            target["citations"] = p["citations"]
            target["citations_updated"] = p.get("citations_updated")
        # The earlier date wins, so a camera-ready venue record that only
        # just turned up doesn't list its months-old preprint as new.
        if p.get("first_seen") and (not target.get("first_seen") or p["first_seen"] < target["first_seen"]):
            target["first_seen"] = p["first_seen"]
        folded.append(key)
    for key in folded:
        del merged[key]
    return len(folded)


S2_DISCOVERY_SOURCE = "arxiv_s2_citing_discovery"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild data/papers_full.json from data/venues/*.json.")
    parser.add_argument("--allow-s2-loss", action="store_true",
                        help="write the merge even if every S2-discovered paper is gone")
    args = parser.parse_args(argv if argv is not None else [])

    taxonomy_full = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
    taxonomy = taxonomy_full["categories"]
    # Lowercased once here rather than inside classify_paper's per-category
    # rank() -- that used to rebuild this same list from scratch for every
    # paper (rank() runs once per category per paper), ~466 keywords x
    # 235k+ papers of pure repeated .lower() work for a value that never
    # changes across the run. Confirmed via profiling as a meaningful slice
    # of merge_corpus.py's ~6-minute runtime.
    for cat in taxonomy:
        cat["keywords"] = [k.lower() for k in cat["keywords"]]
    known_dataset_titles = frozenset(cl.normalize_title(t) for t in taxonomy_full.get("known_dataset_papers", []))
    llm_av_titles = cl.load_llm_av_titles()
    valid_category_ids = frozenset(cat["id"] for cat in taxonomy)
    llm_category_labels = cl.load_llm_category_labels(valid_category_ids)

    # This script used to be safe to re-run at any time, but several other
    # scripts (enrich_av_authors.py, fetch_citations_openalex.py, the
    # in-corpus citation-graph builder) patch enrichment directly onto the
    # *output* of this script (papers_full.json) rather than onto
    # venues/*.json -- so a naive rebuild-from-sources silently discards that
    # enrichment (this actually happened once, for authors_detail). Fix:
    # carry forward any of these fields found on the previous papers_full.json,
    # keyed by normalized title, to matching records that don't already have
    # them from this rebuild. Self-healing reruns instead of a trap.
    # authors_detail_source travels with authors_detail (it records which of
    # the three enrichment scripts -- openalex/arxiv/cvf-pdf -- produced it)
    # but was missing from this list, so it got silently dropped on every
    # rebuild even though authors_detail itself survived: confirmed on real
    # data, every paper with authors_detail currently shows
    # authors_detail_source=None. Once dropped there's no way to reconstruct
    # which source a given paper's data came from after the fact -- fixing
    # the carry-over only stops new data from losing it going forward.
    CARRY_OVER_FIELDS = ("authors_detail", "authors_detail_source", "citations_by_source",
                         "arxiv_url", "abstract", "abstract_search_exhausted", "has_code_link")
    prior_by_field = {field: {} for field in CARRY_OVER_FIELDS}
    n_prior_s2 = 0
    prior_first_seen = None
    if OUT_FILE.exists():
        # A hard failure, not a printed note: this used to carry on and
        # write a corpus with no carried-over authors, abstracts or
        # citations at all, which then got published and backed up over
        # the last good copy. A truncated file here usually means a crawler
        # was killed mid-save; restore_corpus.py gets the last backup back.
        try:
            prior_papers = json.loads(OUT_FILE.read_text(encoding="utf-8"))
            prior_first_seen = first_seen_index(prior_papers)
        except Exception as e:
            raise SystemExit(f"Could not read the previous {OUT_FILE} ({e}). Not rebuilding over it: "
                             "restore it with scripts/restore_corpus.py, or delete it to rebuild "
                             "without any carried-over enrichment.")
        for p in prior_papers:
            if p.get("source") == S2_DISCOVERY_SOURCE:
                n_prior_s2 += 1
            key = normalize_title(p.get("title"))
            if not key:
                continue
            for field in CARRY_OVER_FIELDS:
                # "field in p and not None", not the truthy check this
                # used to be -- has_code_link is a real 3-state field
                # (True/False/never-checked), and a bare truthy check
                # would silently drop every confirmed-False value (a
                # paper actually checked and found to have no code
                # link), making it indistinguishable from "never
                # checked" on the very next rebuild.
                if field in p and p[field] is not None:
                    prior_by_field[field][key] = p[field]

    merged = {}  # normalized title -> record

    # arxiv*.json files are processed LAST, and only ever fill a gap, never
    # win a collision -- a paper that's both an arXiv preprint and published
    # at a real venue (the common case: most conference papers have an
    # arXiv version too) must keep the real venue name, not get relabeled
    # "arXiv preprint" just because that file happened to sort first
    # alphabetically. Without this, "arxiv2023.json" (a<c) would process
    # before "cvpr2023.json" and silently steal every dual-listed paper's
    # venue attribution under naive first-wins dedup.
    venue_files = sorted(f for f in VENUES_DIR.glob("*.json") if not f.name.startswith("arxiv"))
    arxiv_files = sorted(VENUES_DIR.glob("arxiv*.json"))

    for f in venue_files + arxiv_files:
        try:
            papers = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            # Skipping it would publish a corpus missing that whole venue.
            raise SystemExit(f"Could not parse {f} ({e}). Fix or re-fetch it before merging.")
        source = discovery_source(f.name)
        is_arxiv_file = f.name.startswith("arxiv")
        file_conference, file_year = conference_and_year_for_file(f.name)
        last_first = uses_last_first_authors(papers)
        for p in papers:
            key = normalize_title(p.get("title"))
            if not key:
                continue
            # arxiv*.json's own schema (fetch_arxiv.py, fetch_semanticscholar_
            # citing.py) predates arxiv_url existing as its own field and
            # puts the abstract URL in "doi" instead -- lift it into
            # arxiv_url here so every paper's arXiv link lives in one
            # consistent field regardless of which file it came from.
            # fetch_arxiv_monthly.py's files already have a real arxiv_url,
            # and their "doi" is a real DOI (or empty), so for those the
            # field is taken as is.
            arxiv_url = None
            if is_arxiv_file:
                arxiv_url = p["arxiv_url"] if "arxiv_url" in p else p.get("doi")
            if key not in merged:
                merged[key] = {
                    "title": clean_title(p.get("title")),
                    "authors": normalize_author_string(p.get("authors"), last_first),
                    "abstract": p.get("abstract"),
                    "venue": html.unescape(p.get("conference") or file_conference or "") or None,
                    "year": p.get("year") or file_year,
                    "citations": p.get("citations"),
                    "citations_updated": p.get("citations_updated"),
                    "doi": p.get("doi"),
                    "source": source,
                    "arxiv_url": arxiv_url,
                    # Exactly which page this paper's data was pulled from,
                    # when the fetcher recorded one (user-requested) -- most
                    # fetchers pull from one predictable per-venue URL
                    # pattern already documented in PIPELINE.md, but a
                    # community-maintained GitHub list (fetch_github_paper_
                    # lists.py) is a different repo per year/venue, so the
                    # exact source needs recording per paper, not just once
                    # in a docstring.
                    "source_url": p.get("source_url"),
                    # "missing" = looked up, no usable venue exists; absent on an
                    # arXiv record = venue not looked up yet (see fix_suspect_venues.py).
                    "venue_status": p.get("venue_status"),
                }
            elif arxiv_url and not merged[key].get("arxiv_url"):
                # A dual-listed paper: already merged from a real venue's
                # own listing (which wins for title/venue/etc, see the
                # ordering note above), but this arXiv-sourced record still
                # has a real arXiv link worth keeping, not discarding.
                merged[key]["arxiv_url"] = arxiv_url

    n_carried_over = 0
    for key, p in merged.items():
        for field in CARRY_OVER_FIELDS:
            if not p.get(field) and key in prior_by_field[field]:
                p[field] = prior_by_field[field][key]
                n_carried_over += 1
    if prior_first_seen is None:
        print(f"  no prior {OUT_FILE.name} to carry first_seen from -- every paper gets the "
              f"baseline date {FIRST_SEEN_BASELINE}")
    n_first_seen_today = assign_first_seen(merged, prior_first_seen, today_utc())

    # After the carry-over, because that is where a venue record gets the
    # arxiv_url apply_arxiv_links.py found for it.
    n_arxiv_folded = merge_by_arxiv_id(merged, CARRY_OVER_FIELDS)

    papers = list(merged.values())
    n_s2 = sum(1 for p in papers if p["source"] == S2_DISCOVERY_SOURCE)
    if n_prior_s2 and not n_s2 and not args.allow_s2_loss:
        raise SystemExit(
            f"The previous {OUT_FILE.name} has {n_prior_s2} papers found through Semantic Scholar "
            f"citations, but this merge has none: {VENUES_DIR / 'arxiv_s2_citing.json'} is missing "
            "or empty. Restore it with scripts/restore_corpus.py, or pass --allow-s2-loss to "
            "rebuild without them on purpose.")
    for p in papers:
        category, relevance = cl.classify_paper(
            p.get("title", ""), p.get("abstract"), taxonomy, llm_av_titles, known_dataset_titles,
            llm_category_labels)
        p["category"] = category
        p["av_relevance"] = relevance

    write_json_atomic(OUT_FILE, papers, indent=2)
    print(f"Wrote {len(papers)} unique papers to {OUT_FILE}")
    n_av = sum(1 for p in papers if p["av_relevance"] == "AV")
    print(f"  AV={n_av} non-AV={len(papers) - n_av}")
    if n_carried_over:
        print(f"  carried over {n_carried_over} enrichment fields from the previous run")
    if n_arxiv_folded:
        print(f"  folded {n_arxiv_folded} arXiv records into the venue record with the same arXiv id")
    print(f"  {n_first_seen_today} papers are new since the previous run (first_seen today)")


if __name__ == "__main__":
    main(sys.argv[1:])
