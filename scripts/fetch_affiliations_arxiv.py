#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Alternative to enrich_core_authors.py's OpenAlex per-paper lookup, for
av_relevance=="core" papers that still lack authors_detail: resolves each
paper's arXiv ID via arXiv's own search API (no rate limit issue like
OpenAlex -- arXiv just asks for a few seconds between requests), then fetches
ar5iv.labs.arxiv.org's full-text HTML rendering of that paper (LaTeX -> HTML,
~490KB vs a 2.9MB PDF, no figures ever touched) and parses the
"ltx_role_affiliation" spans next to each author's name.

The actual institution name(s) inside that raw affiliation text are
extracted by a local LLM (institution_extraction_llm.py), not by splitting
the text on commas -- a real affiliation note is prose ("Authors are with
the Division of Robotics, Perception, and Learning (RPL), KTH Royal
Institute of Technology, Stockholm, Sweden"), not a flat list of
independent fields, and a blind split produces unrecoverable garbage
fragments (confirmed real cost, see aggregate.py's INVALID_INSTITUTIONS
comment on "Perception"). The extraction is anchored against a growing
registry of already-confirmed institution names (data/institution_registry.json)
so the same real institution isn't re-invented under a slightly different
name every time a paper phrases it differently -- see
institution_extraction_llm.py's own docstring for the full design.

Coverage tradeoff vs OpenAlex: only works for papers with an arXiv preprint
that has affiliations in its LaTeX source (common but not universal -- some
papers use anonymous/no-affiliation templates, some never get an arXiv
version). Where it works, it's much faster and has zero rate-limit budget to
run out of, so it's meant to run alongside enrich_core_authors.py, not
replace it.

Important limitation: ar5iv's affiliation text is the institution NAME, not
an ISO country code the way OpenAlex's authors_detail is -- there is no
country data in what this script extracts on its own. See
apply_affiliations_arxiv.py for how institution names get resolved to
countries (a small curated map, built by hand via web lookups, not an
automated per-institution API call).

Also extracts this paper's OWN reference list while the ar5iv page is
already in hand -- ar5iv's bibliography is cleanly structured (one
<li class="ltx_bibitem"> per entry, no PDF-layout noise the way
build_citation_graph.py's CVF-PDF extraction has to deal with), so this is
essentially free: no extra network request, just parsing more of a page
already downloaded. Saved to its own file, av-atlas/data/
reference_lists_arxiv.json, for build_citation_graph.py's match phase to
read (read-only from here -- see that script for why raw references and
corpus-matching are kept as two separate steps).

Same free-byproduct reasoning for has_code_link (see detect_code_link): a
link to a github.com/gitlab.com/bitbucket.org repo found anywhere on the
page outside the bibliography (a cited work's own repo link doesn't count
as THIS paper's code release). Feeds Insights' open-source-adoption panel.

Writes to its own side files, never touching papers_full.json or anything
build_citation_graph.py owns directly -- same reasoning as
fetch_citations_openalex.py (avoids a concurrent-writer race if run
alongside those):
  av-atlas/data/affiliations_arxiv.json:
    {normalizedTitle: {"authors": [{"name": "...", "affiliations": ["..."]}],
                        "arxiv_id": "..." or null,
                        "has_code_link": true/false/null}}
  av-atlas/data/reference_lists_arxiv.json:
    {normalizedTitle: ["<raw reference text>", ...]}

Small-batch, re-read-before-each-batch, resumable, retries transient
failures, exits early on sustained failure (see build_citation_graph.py for
the same pattern and why).

Usage: python fetch_affiliations_arxiv.py
"""
import copy
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from bs4 import BeautifulSoup

import institution_extraction_llm as iel

BASE = Path(__file__).resolve().parent.parent
PAPERS_FILE = BASE / "data" / "papers_full.json"
AFFS_FILE = BASE / "data" / "affiliations_arxiv.json"
REFS_FILE = BASE / "data" / "reference_lists_arxiv.json"
HEADERS = {"User-Agent": "av-atlas (mailto:holger@it-caesar.com)"}
ARXIV_API = "http://export.arxiv.org/api/query"
BATCH_SIZE = 15
MAX_CONSECUTIVE_FAILURES = 20
ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())


def fetch_url(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_with_retries(url, timeout=20, max_retries=4):
    delay = 5.0
    last_error = None
    for attempt in range(max_retries):
        try:
            return fetch_url(url, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last_error = e
        except Exception as e:
            last_error = e
        if attempt < max_retries - 1:
            time.sleep(delay)
            delay *= 2
    raise last_error


def find_arxiv_id(title):
    # A failed request (429 rate-limited, timeout, ...) MUST NOT be treated
    # the same as "arXiv genuinely has no record of this paper" -- confirmed
    # in practice: arXiv's API started 429-ing under sustained use (this
    # script isn't the only thing that calls it -- fetch_semanticscholar_
    # citing.py's own arxiv.org discovery hits it too), fetch_with_retries'
    # 3 attempts (~9s of backoff) weren't enough to recover, and this
    # function used to swallow that into a bare `return None` -- main()
    # then wrote {"authors": [], "arxiv_id": None} to the checkpoint file as
    # if the paper had been genuinely checked and confirmed absent. 240
    # mostly-CVPR/ECCV papers (venues where an arXiv preprint is the norm,
    # not the exception) got permanently marked "no arXiv match" this way
    # in a single run before the pattern was caught and purged. Letting the
    # exception propagate here means main()'s own try/except (which does
    # NOT write to the checkpoint on failure) is what actually handles it.
    query = urllib.parse.urlencode({"search_query": f'ti:"{title}"', "max_results": 1})
    data = fetch_with_retries(f"{ARXIV_API}?{query}")
    root = ET.fromstring(data)
    entry = root.find("atom:entry", ATOM_NS)
    if entry is None:
        return None
    found_title = (entry.findtext("atom:title", default="", namespaces=ATOM_NS) or "").strip()
    # arXiv's search is fuzzy, not exact -- only accept a normalized-title
    # match, same standard used everywhere else in this pipeline, so a
    # same-topic-different-paper false match never gets treated as a hit.
    if normalize_title(found_title) != normalize_title(title):
        return None
    id_url = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
    m = re.search(r"abs/([\w.\-]+?)(v\d+)?$", id_url)
    return m.group(1) if m else None


def parse_ar5iv_affiliations(soup, registry, cache):
    # ar5iv marks each author's name in an "ltx_personname" span and any
    # affiliation note in a following "ltx_role_affiliation" span, both
    # nested inside the same "ltx_creator ltx_role_author" block -- confirmed
    # against a real paper (UniAD, arXiv 2212.10156) before building this
    # parser, not a guess.
    #
    # Institution extraction itself is institution_extraction_llm's job, not
    # this function's -- a real affiliation note is prose ("Authors are with
    # the Division of Robotics, Perception, and Learning (RPL), KTH Royal
    # Institute of Technology, Stockholm, Sweden"), not a flat comma-
    # separated field list, and a naive split (this used to call a
    # clean_affiliations() helper that did exactly that) produces
    # unrecoverable garbage fragments no amount of downstream regex cleanup
    # can fully undo (user-flagged, confirmed real cost: "Perception"
    # surviving as its own fake institution -- see aggregate.py's
    # INVALID_INSTITUTIONS comment). `registry` (a set) and `cache` (a dict,
    # raw text -> extraction result) are threaded through from main() and
    # mutated in place, so a genuinely new institution or an already-seen
    # raw text is shared across every author and paper in the run instead of
    # being re-extracted or re-invented each time.
    authors = []
    for block in soup.select("span.ltx_creator.ltx_role_author"):
        name_el = block.select_one(".ltx_personname")
        if not name_el:
            continue
        name = re.sub(r"\s+", " ", name_el.get_text()).strip()
        # Some LaTeX templates (IEEE-style \IEEEauthorblockN with several
        # authors sharing one block) render as a single ltx_personname
        # containing multiple people run together, e.g. "Julian Wiederer Arij
        # Bouazizi Marco Troina" -- a real person's name is essentially never
        # more than 4 words, so treat that as a template ar5iv can't parse
        # cleanly and skip it rather than record a garbled non-person.
        if not name or len(name.split()) > 4:
            continue
        affs = []
        for aff_el in block.select(".ltx_role_affiliation"):
            text = re.sub(r"^\s*Affiliation:\s*", "", aff_el.get_text())
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                continue
            if text in cache:
                extracted = cache[text]
            else:
                # Not caught here -- an Ollama failure (server down, bad
                # response) propagates up to the caller, which treats it
                # exactly like any other failed fetch (retry later, never
                # fall back to guessing at a split).
                extracted = iel.extract_institutions(text, list(registry))
                cache[text] = extracted
            affs.extend(iel.resolve_and_register(extracted, registry))
        authors.append({"name": name, "affiliations": affs})
    return authors


def parse_ar5iv_references(soup):
    # Unlike CVF's PDF-extracted references (one text blob per page, split
    # heuristically on numbering), ar5iv's bibliography is real structured
    # HTML -- one <li class="ltx_bibitem"> per reference, already separated
    # by LaTeX's own \bibitem markup. Just read the text out, no splitting
    # logic needed.
    entries = []
    for item in soup.select("li.ltx_bibitem"):
        text = re.sub(r"\s+", " ", item.get_text()).strip()
        if len(text) > 15:
            entries.append(text)
    return entries


# Not just github/gitlab/bitbucket (user-flagged: "code cannot just be at
# github/gitlab") -- huggingface.co/paperswithcode.com/codeberg.org/gitee.com
# are all real code/model-hosting destinations that show up in this corpus
# too (a HuggingFace Space or model repo, a self-hosted Gitee mirror common
# for Chinese institutions, ...).
CODE_HOST_RE = re.compile(
    r"(github\.com|gitlab\.com|bitbucket\.org|huggingface\.co|paperswithcode\.com|"
    r"codeberg\.org|gitee\.com|sourceforge\.net)/", re.I)

# A sentence-level statement that code is (or will be) released, independent
# of whether a recognized code-host URL appears anywhere near it -- catches
# a project page on the authors' own domain, a link the ar5iv HTML strips
# during conversion, or a plain textual promise with no link at all yet.
# Deliberately anchored on an explicit availability/release verb next to
# "code"/"implementation", not the bare word "code" alone (which would
# false-positive on ordinary methods text like "we implement our approach in
# code using PyTorch"). Known limitation, same as any keyword heuristic: a
# negated claim ("code is not yet available") reads as a positive match --
# accepted as a rare case, since authors who have no code to offer almost
# always just omit the sentence entirely rather than write a negation.
CODE_AVAILABILITY_TEXT_RE = re.compile(
    r"\b(?:code|codes|implementation|source\s*code)\b(?:[^.\n]{0,60})?\b"
    r"(?:is|are|will\s+be|has\s+been)\s+(?:publicly\s+|freely\s+)?"
    r"(?:available|released|open-?sourced?)\b"
    r"|\bwe\s+(?:release|open-?source|publicly\s+release|will\s+release)\s+"
    r"(?:the|our)?\s*(?:code|source\s*code|implementation)\b",
    re.I)


def detect_code_link(soup):
    # A link to the paper's OWN code release, not a citation that happens to
    # reference one -- links inside the bibliography are excluded, since a
    # bibitem's own rendered metadata sometimes carries a github.com URL for
    # the CITED work, which isn't a signal about THIS paper. Same soup ar5iv
    # page already fetched for affiliations/references, so this costs no
    # extra network request.
    #
    # ar5iv itself also injects a github.com link into every single page it
    # renders -- a "Report an issue" footer button pointing at
    # github.com/dginev/ar5iv/issues/new -- which is site chrome, not paper
    # content, and isn't inside a bibitem so the exclusion above doesn't
    # catch it. Left unfixed this made detect_code_link() return True for
    # nearly every paper regardless of whether it actually links code
    # (caught when the resulting ~90% "has code" rate in Insights turned out
    # to be this footer link, not real signal). Excluded the same way: any
    # link inside the `.ar5iv-footer` chrome, or carrying an `ar5iv-*` class
    # itself (the site's own nav/toggle buttons), doesn't count.
    bibitem_links = {a["href"] for item in soup.select("li.ltx_bibitem") for a in item.select("a[href]")}
    chrome_links = {a["href"] for a in soup.select(".ar5iv-footer a[href]")}
    chrome_links |= {a["href"] for a in soup.select('a[class*="ar5iv"][href]')}
    for a in soup.select("a[href]"):
        href = a.get("href") or ""
        if href in bibitem_links or href in chrome_links:
            continue
        if CODE_HOST_RE.search(href):
            return True
    # Text-based fallback: not every code release is a recognized-host link
    # (a project page, a link ar5iv's conversion dropped, ...) -- same
    # bibliography/chrome exclusion as the link scan above, applied by
    # removing those elements from a COPY of the soup before reading text
    # (never mutate the caller's soup, which parse_ar5iv_references/
    # parse_ar5iv_affiliations still need afterward).
    text_soup = copy.deepcopy(soup)
    for el in text_soup.select("li.ltx_bibitem, .ar5iv-footer"):
        el.decompose()
    if CODE_AVAILABILITY_TEXT_RE.search(text_soup.get_text(" ")):
        return True
    return False


def fetch_ar5iv_page(arxiv_id):
    html = fetch_with_retries(f"https://ar5iv.labs.arxiv.org/html/{arxiv_id}", timeout=30).decode("utf-8", errors="replace")
    return BeautifulSoup(html, "html.parser")


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")


def load_pending(already_done):
    papers = json.loads(PAPERS_FILE.read_text(encoding="utf-8"))
    core = [p for p in papers if p.get("av_relevance") == "core" and not p.get("authors_detail")]
    pending = [p["title"] for p in core if normalize_title(p.get("title")) not in already_done]
    # Shuffled, not left in papers_full.json's own order -- that order
    # clusters by venue (confirmed: the first ~250 pending titles in a real
    # run were almost entirely CVPR/ECCV), so an interrupted run only ever
    # gives even partial coverage of whichever venues happen to sort first,
    # not a representative slice of the corpus (user-requested: "make sure
    # all crawlers proceed in a random order, so that the page content
    # already feels meaningful/representative").
    random.shuffle(pending)
    return pending, len(core)


def main():
    affs = load_json(AFFS_FILE, {})
    refs = load_json(REFS_FILE, {})
    # registry: canonical institution names, shared across this whole run
    # (and future runs, via save_registry below) so the LLM extraction in
    # parse_ar5iv_affiliations reuses an existing name instead of inventing
    # a slightly different one for the same real institution every time.
    # cache: raw affiliation text -> already-extracted result, so identical
    # boilerplate seen on an earlier paper (or an earlier run) never costs a
    # second Ollama call.
    registry = set(iel.load_registry())
    cache = iel.load_cache()
    pending, core_total = load_pending(affs)
    print(f"{len(pending)} core papers without authors_detail to try via arXiv (of {core_total} missing total)", flush=True)

    done_with_affs = 0
    done_no_match = 0
    total_refs_found = 0
    with_code_link = 0
    processed = 0
    consecutive_failures = 0
    # A clean result (even an empty one -- no arxiv_id, no affiliations)
    # already marks affs[key], so the batch filter below skips it on its
    # own. Only an exception (an ar5iv 404, a timeout, ...) leaves nothing
    # recorded -- without this, that paper gets re-selected into every
    # subsequent batch for the rest of THIS run (same bug as
    # build_citation_graph.py's identical loop shape; see that file).
    attempted_this_run = set()

    while pending:
        affs = load_json(AFFS_FILE, {})
        batch = [t for t in pending if normalize_title(t) not in affs and t not in attempted_this_run][:BATCH_SIZE]
        if not batch:
            break

        for title in batch:
            attempted_this_run.add(title)
            key = normalize_title(title)
            try:
                arxiv_id = find_arxiv_id(title)
                if arxiv_id:
                    soup = fetch_ar5iv_page(arxiv_id)
                    authors = parse_ar5iv_affiliations(soup, registry, cache)
                    references = parse_ar5iv_references(soup)
                    has_code_link = detect_code_link(soup)
                else:
                    authors, references, has_code_link = [], [], False
                # arxiv_id was previously resolved here and then discarded --
                # now kept alongside the affiliations so fetch_arxiv_links.py
                # doesn't have to re-resolve (a fresh arXiv search) for every
                # paper this script has already checked. has_code_link is a
                # free byproduct of the same already-fetched ar5iv page (see
                # detect_code_link) -- only meaningful when arxiv_id resolved
                # at all, since with none there was no page to check.
                affs[key] = {"authors": authors, "arxiv_id": arxiv_id, "has_code_link": has_code_link if arxiv_id else None}
                if references:
                    refs[key] = references
                    total_refs_found += len(references)
                if any(a["affiliations"] for a in authors):
                    done_with_affs += 1
                else:
                    done_no_match += 1
                if has_code_link:
                    with_code_link += 1
                consecutive_failures = 0
            except Exception as e:
                print(f"  failed on {title[:60]!r}: {e}", flush=True)
                consecutive_failures += 1
            processed += 1
            # 3s, not 1s -- the comment already said "a few seconds" but the
            # code didn't match it, and this under-pacing is the likely
            # cause of the 429 storm that corrupted 240 checkpoint entries
            # (see find_arxiv_id's comment) in the first place.
            time.sleep(3.0)

        save_json(AFFS_FILE, affs)
        save_json(REFS_FILE, refs)
        iel.save_registry(registry)
        iel.save_cache(cache)
        print(f"  [{processed}/{len(pending)}] progress saved "
              f"({done_with_affs} got affiliations, {done_no_match} no arXiv/no affiliation data, "
              f"{total_refs_found} references collected across {len(refs)} papers, "
              f"{with_code_link} with a detected code link)", flush=True)

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            print(f"Stopping early: {consecutive_failures} consecutive failures. Resume later by rerunning.", flush=True)
            return

    print(f"Done: {done_with_affs} papers got affiliations, {done_no_match} had no arXiv match/affiliation data, "
          f"{total_refs_found} references collected.", flush=True)


if __name__ == "__main__":
    main()
