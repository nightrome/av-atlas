#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Builds an in-corpus citation graph -- "who cites whom within this corpus",
a signal OpenAlex/Semantic Scholar can't give us -- from two raw-reference
sources:

  1. This script fetches PDFs for every paper hosted on CVF (CVPR/ICCV/WACV
     -- predictable URL, no rate limit; not just av_relevance=="AV" papers,
     see main()'s own comment), archives the raw PDF to data/pdfs_cvf/ (so
     it doesn't have to be re-downloaded to be reprocessed for something
     else later -- gitignored, this is a local cache, not a repo asset),
     extracts just the references section (PyMuPDF, stopping once past it,
     never touching figures), and splits it into raw entries.
  2. fetch_affiliations_arxiv.py separately fetches ar5iv's full-text HTML
     for author affiliations -- since that page is already downloaded, it
     extracts ar5iv's cleanly-structured bibliography (one <li class=
     "ltx_bibitem"> per reference, no PDF-layout noise) at zero extra
     network cost, and saves it to its own file.

Fetching (network-bound, slow) and matching (local string comparison,
fast) are deliberately two separate phases, not one pass that fetches-and-
matches-then-forgets:

  - Phase 1 (fetch): download each AV CVF paper's PDF once, extract and
    save its RAW reference entries to data/reference_lists_cvf.json --
    every entry, whether or not it currently matches anything in the
    corpus. Skips papers already fetched (successfully or not -- failures
    retry automatically, see fetch_with_retries below).
  - Phase 2 (match): runs on every invocation of this script, even if phase
    1 fetched nothing new. Reads BOTH data/reference_lists_cvf.json and
    data/reference_lists_arxiv.json (read-only -- that file belongs to
    fetch_affiliations_arxiv.py) and rematches every saved reference list
    against the CURRENT corpus, overwriting data/citation_graph.json fresh.
    It also adds the Semantic Scholar edges from fetch_s2_references.py
    (data/reference_lists_s2.json + data/s2_paper_ids.json, both read-only
    here). Those need no text matching: a reference is an S2 CorpusId, and
    it's an in-corpus citation when s2_paper_ids.json maps that id to a
    corpus paper. The two kinds of edges are merged per citing paper and
    deduplicated.

This split is the direct answer to "papers not yet indexed might be indexed
later": a reference to a paper that isn't in the corpus yet just doesn't
match today. Nothing is thrown away -- next time the corpus grows (a new
venue pulled, a gap filled) and this script reruns, phase 2 alone picks up
the newly-matchable edges, for every paper ever scanned, with no new network
requests at all.

Matching: a corpus title only counts as cited when it appears in the
reference text as a whole title, not as part of a longer one. Both sides are
compared with everything but letters and digits removed (PDF text often
glues words together, "nuScenes: A Multi-modalDatasetforAutonomousDriving"),
but the match has to start and end where the raw text has reference
structure around it -- the start or end of the entry, a period, comma,
quote, bracket, a year, a following "In", or (before it) a line break from
the other column of a two-column PDF. So "Objects as Points" no
longer picks up every citation of "Tracking Objects as Points", and
"Deep Reinforcement Learning for Autonomous Driving" no longer picks up
"...: A Survey". See TitleIndex/match_references below for the details
(very short titles, nested matches, the year guard).

The saved CVF lists are mostly one entry each: split_reference_entries()
only splits on "12." / "12)" markers and PDF text usually uses "[12]", so
the whole reference section stays one blob. That is fine for this matcher,
which works on positions inside the text rather than on whole entries.

data/reference_lists_cvf.json is this script's own file, in the same
succeeded/failed/side-file pattern as fetch_citations_openalex.py /
fetch_affiliations_arxiv.py -- never written by anything else, so it's safe
to run this alongside those. data/citation_graph.json is likewise only ever
written by this script.

Usage: python build_citation_graph.py               # fetch new PDFs, then match
   or: python build_citation_graph.py --match-only  # match phase only, no network

build_public_site.py runs the --match-only form on every build, so the graph
always reflects every reference list saved so far. Before that, the match
phase only ran at the end of a full fetch run, and a crawl that was stopped
part way (as the long whole-corpus crawl was) left citation_graph.json built
from the 2,136 CVF lists that existed at the last completed run, while
19,481 were on disk.
"""
import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from fetch_common import by_citations

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
VENUES_DIR = BASE / "data" / "venues"
REFS_CVF_FILE = BASE / "data" / "reference_lists_cvf.json"
REFS_ARXIV_FILE = BASE / "data" / "reference_lists_arxiv.json"
REFS_S2_FILE = BASE / "data" / "reference_lists_s2.json"
S2_IDS_FILE = BASE / "data" / "s2_paper_ids.json"
GRAPH_FILE = BASE / "data" / "citation_graph.json"
PDF_DIR = BASE / "data" / "pdfs_cvf"
CVF_BASE = "https://openaccess.thecvf.com"
CVF_VENUES = {"CVPR", "ICCV", "WACV"}
HEADERS = {"User-Agent": "av-atlas (mailto:holger@it-caesar.com)"}
BATCH_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 15
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

# A citing paper can be dated before the paper it cites (a 2021 journal
# version of a 2018 preprint citing something from 2019), but not by much.
# An edge whose citer is more than this many years older than the cited
# paper is dropped as a false match.
YEAR_GUARD_SLACK = 2

# A title shorter than this (in words or in characters) is too generic to
# match on boundaries alone ("Welcome", "Editorial", "Objects"). See
# TitleIndex.is_short.
SHORT_TITLE_WORDS = 3
SHORT_TITLE_CHARS = 15

# Reference-structure characters that can sit between the text before a
# title and the title itself, and between the title and what follows. A
# colon and a hyphen are deliberately missing: "TrafficSim: Learning to
# Simulate ..." and "Planning and Decision-Making for ..." must not match
# "Learning To Simulate" or "Decision Making for Autonomous Vehicles".
# U+FFFD is in there because many saved lists have their curly quotes
# replaced by it ("\ufffdThe trimmed iterative closest point algorithm,\ufffd in").
# A line break counts on the left only: two-column PDF text interleaves the
# columns line by line, so a title often starts right after a line from the
# other column ("... Proceed-\nDeep residual learning for image recognition.").
LEFT_BOUNDARY_CHARS = set('.,;"\u201c\u201d\u2018\ufffd?!()[]\n')
RIGHT_BOUNDARY_CHARS = set('.,;"\u201c\u201d\u2019\ufffd?!()[]')
# The stricter set a short title needs on both sides (a whole sentence-like
# segment of the reference, not a stretch between two commas).
STRONG_BOUNDARY_CHARS = set('."\u201c\u201d\u2018\u2019\ufffd?!')
YEAR_RE = re.compile(r"(?:19|20)\d\d[a-z]?")
RUN_RE = re.compile(r"[A-Za-z0-9]+")
# How far from a short title's match to look for its publication year.
SHORT_TITLE_YEAR_WINDOW = 250
# Matching looks keys up by their first PREFIX_LEN characters (shorter keys
# by their whole text), so each position in a reference costs a few dict
# lookups instead of a scan over every corpus title.
PREFIX_LEN = 16


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def pdf_url_from_path(path):
    # CVF's convention: the listing links to .../html/<slug>.html; the PDF is
    # at the same slug under .../papers/<slug>.pdf. Confirmed against a live
    # paper before building this script (200 OK, not a guess). Older years
    # (pre ~2019) use relative paths with no leading slash, e.g.
    # "content_cvpr_2015/html/..." instead of "/content/CVPR2015/html/..."
    # (same quirk fetch_cvf.py works around) -- always join with exactly one
    # slash rather than relying on the path already having one.
    pdf_path = path.replace("/html/", "/papers/").replace(".html", ".pdf")
    # ICCV 2017 specifically directories its PDFs under "content_ICCV_2017"
    # even though its own html/ listing pages live under lowercase
    # "content_iccv_2017" -- every other year keeps html/ and papers/ under
    # the same-cased directory. Confirmed live: the stored "content_iccv_2017/
    # html/<slug>.html" path 200s, but naively lowercasing papers/ 404s,
    # while the html page's own outgoing PDF link is "../../content_ICCV_2017/
    # papers/<slug>.pdf" -- caught because every one of that year's fetches
    # in fetch_cvf_affiliations.py/build_citation_graph.py was 404ing.
    pdf_path = pdf_path.replace("content_iccv_2017/papers/", "content_ICCV_2017/papers/")
    return f"{CVF_BASE}/{pdf_path.lstrip('/')}"


def build_cvf_pdf_url_index():
    """normalized title -> pdf_url, for every CVPR/ICCV/WACV paper we have a listing for."""
    index = {}
    for f in sorted(VENUES_DIR.glob("*.json")):
        name = f.stem
        venue = re.match(r"[a-z]+", name).group(0).upper()
        if venue not in CVF_VENUES:
            continue
        try:
            papers = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for p in papers:
            key = normalize_title(p.get("title"))
            path = p.get("path")
            if key and path:
                index[key] = pdf_url_from_path(path)
    return index


class TitleIndex:
    """Every corpus title key, set up for match_references(): looked up by
    prefix, with each key's year range and whether it counts as short.

    A key shared by several papers (the same title in two venues, or a
    preprint and its conference version) keeps the earliest and latest year
    across them, so the year checks give it the benefit of the doubt."""

    def __init__(self, papers):
        self.min_year = {}
        self.max_year = {}
        self.short = set()
        self.by_prefix = defaultdict(list)
        self.exact = set()
        seen = self.keys = set()
        for p in papers:
            key = normalize_title(p.get("title"))
            if not key:
                continue
            year = p.get("year")
            if isinstance(year, int):
                self.min_year[key] = min(year, self.min_year.get(key, year))
                self.max_year[key] = max(year, self.max_year.get(key, year))
            if key in seen:
                continue
            seen.add(key)
            if self.is_short(p.get("title")):
                self.short.add(key)
            if len(key) < PREFIX_LEN:
                self.exact.add(key)
            else:
                self.by_prefix[key[:PREFIX_LEN]].append(key)
        self.exact_lengths = sorted({len(k) for k in self.exact})

    @staticmethod
    def is_short(title):
        return (len(RUN_RE.findall(title or "")) < SHORT_TITLE_WORDS
                or len(normalize_title(title)) < SHORT_TITLE_CHARS)

    def keys_at(self, norm, pos):
        """Every key that occurs in `norm` starting exactly at `pos`."""
        found = []
        for n in self.exact_lengths:
            if pos + n > len(norm):
                break
            if norm[pos:pos + n] in self.exact:
                found.append(norm[pos:pos + n])
        for key in self.by_prefix.get(norm[pos:pos + PREFIX_LEN], ()):
            if norm.startswith(key, pos):
                found.append(key)
        return found


def fetch_with_retries(url, max_retries=3):
    # Most failures seen in practice are transient local-network blips (DNS
    # resolution failing, "network unreachable" -- classic symptoms of Wi-Fi
    # dropping for a moment during a long-running process), not CVF blocking
    # us or anything wrong with that specific paper. A short retry with
    # backoff recovers the large majority of these without waiting for the
    # next full script run.
    delay = 3.0
    last_error = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise  # a real 404 won't fix itself on retry -- fail immediately
            last_error = e
        except Exception as e:
            last_error = e
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
    raise last_error


def fetch_pdf_text(url, key):
    raw = fetch_with_retries(url)
    # Archived before parsing, so even a PDF that fails to parse (corrupt,
    # scanned image, whatever) still leaves the raw file on disk for later
    # reprocessing -- user-requested, this is meant as a general local PDF
    # cache for this corpus, not just a reference-extraction input.
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    (PDF_DIR / f"{key}.pdf").write_bytes(raw)
    # PyMuPDF instead of pdfplumber: pdfplumber's extract_text() does its own
    # from-scratch character-layout reconstruction and was measured at
    # ~23 sec/paper on this corpus's PDFs -- almost entirely spent on pages
    # we then throw away (this function's own early-stop check ran
    # extract_text() on every page up to and including the references
    # section, not just up to it, since the "stop" was never actually early).
    # PyMuPDF's get_text() is backed by MuPDF's C parser and is the standard
    # go-to for bulk text extraction where layout fidelity doesn't matter --
    # confirmed already installed locally (1.28.2) before switching.
    # Imported here rather than at the top so the match phase (and its tests)
    # run without pymupdf installed.
    import pymupdf
    with pymupdf.open(stream=raw, filetype="pdf") as pdf:
        pages_text = []
        started = False
        for page in pdf:
            text = page.get_text() or ""
            if not started and re.search(r"\bReferences\b", text):
                started = True
            if started:
                pages_text.append(text)
        if not pages_text and pdf.page_count:
            # No explicit "References" heading found (rare, some templates use
            # "Bibliography" or a different heading) -- fall back to the last
            # two pages, where the reference list almost always lives.
            pages_text = [pdf[i].get_text() or "" for i in range(max(0, pdf.page_count - 2), pdf.page_count)]
        return "\n".join(pages_text)


def split_reference_entries(text):
    # Reference lists are numbered, either "[12] ..." or "12. ..." at the
    # start of an entry -- split on that marker rather than on newlines,
    # since a single reference often wraps across several PDF text lines.
    parts = re.split(r"(?:^|\n)\s*\[?\d{1,3}\]?[.\)]\s+", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def _gap_has(gap, chars):
    return any(c in chars for c in gap)


def _starts_venue(run):
    # "In" opening the venue part ("... Title In CVPR" or, with the PDF's
    # spaces lost, "...Title InIEEE/CVF"), or a page range or arXiv id
    # following the title with no punctuation in between.
    return (run in ("In", "pp", "arXiv")
            or (run.startswith("In") and len(run) > 2 and run[2].isupper()))


def _has_year_near(raw, start, end, years):
    window = raw[max(0, start - SHORT_TITLE_YEAR_WINDOW):end + SHORT_TITLE_YEAR_WINDOW]
    return any(int(y[:4]) in years for y in re.findall(r"(?<!\d)(?:19|20)\d\d(?!\d)", window))


def match_entry(raw, index):
    """The corpus keys a single raw reference entry cites, as a set.

    The entry is reduced to its runs of letters and digits, joined into one
    lowercase string, which is what the keys are compared against. A key
    counts only when its match
      - starts at the start of a run, with the entry start, a boundary
        character (LEFT_BOUNDARY_CHARS) or a year right before it,
      - ends at the end of a run, with the entry end, a boundary character
        (RIGHT_BOUNDARY_CHARS), a year or "In" right after it,
      - for a short title (TitleIndex.is_short), has a period, quote or
        year on both sides (or "In" after it) and the paper's year (+-1)
        somewhere nearby, since a
        one-word title like "Editorial" can't be told apart from other text
        by its boundaries alone,
      - and isn't inside a longer key that also matched there (when both
        "Objects as Points" and a longer title containing it are corpus
        papers, a citation of the longer one only counts for it).
    """
    runs = [(m.start(), m.end()) for m in RUN_RE.finditer(raw)]
    if not runs:
        return set()
    texts = [raw[s:e] for s, e in runs]
    offsets = []
    total = 0
    for t in texts:
        offsets.append(total)
        total += len(t)
    norm = "".join(t.lower() for t in texts)
    run_ending_at = {offsets[i] + len(texts[i]): i for i in range(len(texts))}
    last = len(runs) - 1

    def gap(i):  # raw text between run i and run i + 1
        return raw[runs[i][1]:runs[i + 1][0]]

    def after_author(i):
        # ar5iv's bibliographies put the title straight after the last
        # author with only a space ("... and O. Beijbom Nuscenes: a
        # multimodal dataset ..."): an initial, a period, a surname, a space.
        # The surname can be several runs ("Moreno-Noguer", or a name with
        # an accented letter, which RUN_RE splits).
        if not gap(i - 1).isspace():
            return False
        j = i - 1
        while j >= 1 and (gap(j - 1) == "-" or gap(j - 1) == "\ufffd" or gap(j - 1).isalpha()):
            j -= 1
        return (j >= 1 and len(texts[j - 1]) == 1 and texts[j - 1].isupper()
                and "." in gap(j - 1) and texts[j][:1].isupper())

    found = []
    for i in range(len(runs)):
        if i > 0:
            g = gap(i - 1)
            # A year right before a short title ("(2023) Segment anything.
            # In ...") pins it down as well as a period does.
            left_strong = _gap_has(g, STRONG_BOUNDARY_CHARS) or bool(YEAR_RE.fullmatch(texts[i - 1]))
            # A bare number before the title is a page back-reference
            # ("..., 2020. 1, 2, 6\nnuScenes: ...") or a year. A hyphen then a
            # space is the other column's line ending mid-word ("Predicting
            # fu- Self-supervised monocular depth hints. In ICCV").
            if not (left_strong or _gap_has(g, LEFT_BOUNDARY_CHARS) or g.startswith("- ")
                    or YEAR_RE.fullmatch(texts[i - 1]) or texts[i - 1].isdigit() or after_author(i)):
                continue
        else:
            left_strong = True
        pos = offsets[i]
        for key in index.keys_at(norm, pos):
            j = run_ending_at.get(pos + len(key))
            if j is None:
                continue
            if j < last:
                g = gap(j)
                # Same after it: "Segment anything, 2023." or "... anything. In".
                right_strong = (_gap_has(g, STRONG_BOUNDARY_CHARS) or bool(YEAR_RE.fullmatch(texts[j + 1]))
                                or _starts_venue(texts[j + 1]))
                if not (right_strong or _gap_has(g, RIGHT_BOUNDARY_CHARS)):
                    continue
            else:
                right_strong = True
            if key in index.short:
                if not (left_strong and right_strong):
                    continue
                lo, hi = index.min_year.get(key), index.max_year.get(key)
                if lo is None or not _has_year_near(raw, runs[i][0], runs[j][1], range(lo - 1, hi + 2)):
                    continue
            found.append((pos, pos + len(key), key))

    # Longest first; a match lying inside an already-kept one is dropped.
    kept = []
    for start, end, key in sorted(found, key=lambda m: m[0] - m[1]):
        if not any(s <= start and end <= e for s, e, _ in kept):
            kept.append((start, end, key))
    return {key for _, _, key in kept}


def match_references(entries, index):
    matched = set()
    for raw_entry in entries:
        matched |= match_entry(raw_entry, index)
    return matched


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def load_refs_cvf():
    refs = load_json(REFS_CVF_FILE, {"succeeded": [], "failed": {}, "references": {}})
    refs.setdefault("succeeded", [])
    refs.setdefault("failed", {})
    refs.setdefault("references", {})
    return refs


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def fetch_phase(cvf_titles, pdf_url_index):
    refs = load_refs_cvf()
    succeeded = set(refs["succeeded"])
    # Most-cited-first, not corpus order (which clusters by venue/year) --
    # an interrupted or session-limited run should have already extracted
    # reference lists for the papers readers actually visit (feeds each
    # paper's own self-citation/CD-index numbers and its detail page),
    # not whichever ones happen to sort first (user-requested, applied to
    # every incremental crawler in this pipeline, not just this one; see
    # fetch_common.by_citations). cvf_titles already carries this order
    # from main() below -- filtered here, not re-sorted, since it's a list
    # of title keys with no citation count left to sort by.
    pending = [k for k in cvf_titles if k in pdf_url_index and k not in succeeded]
    n_retrying = sum(1 for k in pending if k in refs["failed"])
    print(f"{len(pending)} CVF papers left to fetch (of {len(cvf_titles)} CVF total, "
          f"AV and non-AV), including {n_retrying} retrying a previous failure", flush=True)

    processed = 0
    consecutive_failures = 0
    # A key only ever leaves `pending` (the static list built above) by
    # entering `succeeded` -- a failure, permanent or transient, never did,
    # so without this a paper that fails once got re-selected into every
    # subsequent batch for the rest of THIS run, forever, until fetch_with_
    # retries' 404 (which never changes) or a real outage inflated
    # consecutive_failures past the threshold. Confirmed in practice: a
    # cluster of permanently-404 ICCV 2017 papers got retried every single
    # batch, tripping the "likely blocked or network down" early-stop after
    # burning through several batches on a handful of already-known-dead
    # URLs instead of ever reaching the rest of the queue. Cross-run retry
    # (next invocation) is unaffected -- `pending` is rebuilt from `failed`
    # again at the top of the next run.
    attempted_this_run = set()

    while pending:
        refs = load_refs_cvf()
        succeeded = set(refs["succeeded"])
        batch = [k for k in pending if k not in succeeded and k not in attempted_this_run][:BATCH_SIZE]
        if not batch:
            break

        for key in batch:
            attempted_this_run.add(key)
            url = pdf_url_index[key]
            try:
                text = fetch_pdf_text(url, key)
                refs["references"][key] = split_reference_entries(text)
                refs["succeeded"].append(key)
                refs["failed"].pop(key, None)
                consecutive_failures = 0
            except Exception as e:
                print(f"  failed on {url}: {e}", flush=True)
                # A 404 is permanent (fetch_with_retries never retries one) --
                # the paper simply isn't there, not "not yet fetched". Tagged
                # explicitly so match_phase/aggregate.py can count it as done
                # (nothing more this script could ever do) rather than pending.
                # Doesn't count toward consecutive_failures either -- a 404 is
                # a conclusive, expected outcome, not evidence the connection
                # or this script is broken (which is what that counter exists
                # to detect).
                permanent = isinstance(e, urllib.error.HTTPError) and e.code == 404
                refs["failed"][key] = {"error": str(e), "last_attempt": TODAY, "permanent": permanent}
                consecutive_failures = 0 if permanent else consecutive_failures + 1
            processed += 1
            time.sleep(0.3)

        save_json(REFS_CVF_FILE, refs)
        print(f"  [{processed}/{len(pending)}] fetched ({len(refs['failed'])} papers currently failing)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping fetch early: {consecutive_failures} consecutive PDF fetch/parse failures "
                  f"(likely blocked or network down). Failed papers retry automatically next run.", flush=True)
            return False

    return True


def reference_list_edges(ref_lists, index):
    """{citing key: set of cited keys} from saved raw reference lists
    ({citing key: [raw entry, ...]}), leaving out self-matches."""
    edges = {}
    for source, entries in ref_lists.items():
        matches = match_references(entries, index) - {source}
        if matches:
            edges[source] = matches
    return edges


def merge_edge_sources(edge_maps):
    """Unions several {citing key: set of cited keys} maps, one per source
    of citation data (CVF PDFs, ar5iv, and any later one), into one."""
    merged = defaultdict(set)
    for edges in edge_maps:
        for citer, cited in edges.items():
            merged[citer] |= set(cited)
    return dict(merged)


def apply_year_guard(edges, index, slack=YEAR_GUARD_SLACK):
    """Drops edges whose citing paper is dated more than `slack` years
    before the paper it cites, which a real citation can't be. Uses the
    latest year for the citer and the earliest for the cited key, so a
    preprint/conference pair sharing one title isn't penalised. Returns
    (kept edges, number dropped)."""
    kept = {}
    dropped = 0
    for citer, cited in edges.items():
        citer_year = index.max_year.get(citer)
        ok = set()
        for key in cited:
            cited_year = index.min_year.get(key)
            if citer_year is not None and cited_year is not None and citer_year < cited_year - slack:
                dropped += 1
            else:
                ok.add(key)
        if ok:
            kept[citer] = ok
    return kept, dropped


def s2_edges(refs_s2, s2_ids, corpus_keys=None):
    """citing key -> set of cited keys, from Semantic Scholar reference
    lists. Exact by construction: a reference counts only when its CorpusId
    is the one s2_paper_ids.json holds for a corpus paper. corpus_keys, when
    given, drops keys that are no longer in the corpus (the id map is only
    ever added to, papers can leave)."""
    key_by_id = {}
    for key, cid in (s2_ids.get("ids") or {}).items():
        if corpus_keys is None or key in corpus_keys:
            key_by_id[cid] = key
    edges = {}
    for source, cids in (refs_s2.get("references") or {}).items():
        if corpus_keys is not None and source not in corpus_keys:
            continue
        cited = {key_by_id[c] for c in cids if c in key_by_id} - {source}
        if cited:
            edges[source] = cited
    return edges


def match_phase(index):
    # Always runs, fetch or no fetch -- pure local computation over whatever
    # raw reference text has been saved so far by either source, against
    # whatever the corpus looks like right now. This is what lets a later
    # corpus expansion surface new edges for a paper scanned long ago,
    # without re-fetching anything.
    cvf_all_refs = load_refs_cvf()
    cvf_refs = cvf_all_refs["references"]
    arxiv_refs = load_json(REFS_ARXIV_FILE, {})
    # Confirmed-404 papers can never be fetched no matter how many times this
    # reruns -- counted as "done" (nothing left to do), not "still pending",
    # by aggregate.py's coverage stat.
    cvf_permanent_failures = sum(1 for v in cvf_all_refs["failed"].values() if v.get("permanent"))

    refs_s2 = load_json(REFS_S2_FILE, {})

    # One edge map per source of citation data, merged below. A new source
    # (another kind of saved reference list, or edges that already come
    # resolved) only needs an entry here and in sources_scanned. Semantic
    # Scholar's lists come already resolved to CorpusIds, so they need no
    # title matching; keys that have left the corpus are dropped.
    edge_maps = {
        "cvf": reference_list_edges(cvf_refs, index),
        "arxiv": reference_list_edges(arxiv_refs, index),
        "s2": s2_edges(refs_s2, load_json(S2_IDS_FILE, {}), index.keys),
    }
    text_edges = merge_edge_sources([edge_maps["cvf"], edge_maps["arxiv"]])
    s2_only_edges = sum(len(cited - text_edges.get(citer, set()))
                        for citer, cited in edge_maps["s2"].items())
    edges, n_year_dropped = apply_year_guard(merge_edge_sources(edge_maps.values()), index)
    total_edges = sum(len(v) for v in edges.values())

    graph = {
        "generated_at": TODAY,
        "sources_scanned": {"cvf": len(cvf_refs), "arxiv": len(arxiv_refs),
                            "s2": len(refs_s2.get("references") or {})},
        "cvf_permanent_failures": cvf_permanent_failures,
        "edges_by_source": {name: sum(len(v) for v in m.values()) for name, m in edge_maps.items()},
        "year_guard_dropped": n_year_dropped,
        "edges": {k: sorted(v) for k, v in sorted(edges.items())},
    }
    save_json(GRAPH_FILE, graph)
    print(f"Match phase: {len(cvf_refs)} CVF + {len(arxiv_refs)} arXiv + "
          f"{len(refs_s2.get('references') or {})} Semantic Scholar reference lists rematched "
          f"against the current corpus -- {total_edges} in-corpus citation edges found "
          f"across {len(edges)} papers ({s2_only_edges} edges came only from Semantic Scholar, "
          f"{n_year_dropped} dropped by the year check).", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Fetch CVF reference lists and rebuild the in-corpus citation graph.")
    parser.add_argument("--match-only", action="store_true",
                        help="Skip fetching; only rematch the saved reference lists against the current "
                             "corpus and rewrite data/citation_graph.json. No network, no PDFs.")
    args = parser.parse_args()

    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    index = TitleIndex(papers)
    if args.match_only:
        # The corpus backup (backup_corpus.py) carries citation_graph.json but
        # not the reference lists, so a restored checkout has a graph and
        # nothing to rebuild it from. Rematching there would replace it with
        # a near-empty one.
        missing = [f.name for f in (REFS_CVF_FILE, REFS_ARXIV_FILE) if not f.exists()]
        if missing and GRAPH_FILE.exists():
            print(f"Keeping the existing {GRAPH_FILE.name}: {', '.join(missing)} not on disk.", flush=True)
            return
        match_phase(index)
        return

    # Whole corpus, not just av_relevance=="AV" -- user-requested: a paper's
    # citation count should reflect who cites it anywhere in this corpus,
    # not just its AV-relevant slice (see aggregate.py's citations_by_
    # source.in_corpus_all and DECISIONS.md's "Whole-corpus citations" entry
    # for the two-metric design this feeds). Most-cited-first still applies
    # (see fetch_common.by_citations) -- for a non-AV paper this naturally
    # prioritizes ones already known to be cited by AV papers (foundational
    # CV/robotics work our own corpus already points at) over an arbitrary
    # non-AV paper nothing here has any reason to care about yet.
    cvf_titles = [
        normalize_title(p["title"]) for p in by_citations(
            p for p in papers if (p.get("venue") or "") in CVF_VENUES
        )
    ]
    pdf_url_index = build_cvf_pdf_url_index()

    fetch_phase(cvf_titles, pdf_url_index)
    match_phase(index)


if __name__ == "__main__":
    main()
