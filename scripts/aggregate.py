#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Reads av-atlas/data/papers_full.json (the full merged corpus: venue
proceedings 2013-2026 for CVPR/ICCV/WACV/ECCV/NeurIPS/CoRL/ICRA/IROS,
produced by merge_corpus.py) and computes leaderboards. Writes
av-atlas/data/stats.json, consumed by index.html and authors.html.

The main leaderboards (top papers/authors/institutions/countries,
category breakdown, best-by-year/venue) are computed over av_relevance ==
"core" only -- the corpus is mostly general CV/ML/robotics proceedings, not
AV research, so ranking the unfiltered corpus would just surface generic
vision papers again (this was confirmed during merge testing: DINOv2 etc.
topped the raw list). "core" was assigned by classify.py's explicit
AV-specific phrase matching, not generic category keywords, precisely so
this filter is defensible rather than a content-based inclusion filter --
every paper in papers_full.json is present regardless of relevance, only
the *label* is used to choose what these leaderboards rank.

Author/institution/country data is only available for av_relevance=="core"
papers that have been through affiliation enrichment (enrich_core_authors.py
via OpenAlex, or the CVF/arXiv alternatives for papers OpenAlex misses --
fetch_cvf_affiliations.py, fetch_affiliations_arxiv.py). The venue-listing
pulls themselves only capture plain author-name strings, not affiliations.
This means institution/country leaderboards reflect a subset of the corpus,
not its full breadth; that's a known, disclosed limitation, not a bug.

Usage: python aggregate.py
"""
import html
import json
import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
IN_FILE = BASE / "data" / "papers_full.json"
OUT_FILE = BASE / "data" / "stats.json"
# Separate from stats.json (not a field inside it) because of scale: ~212k
# adjacent (not core-AV-relevant) papers at even a slimmed-down ~160
# bytes/record is ~34MB, versus stats.json's own size for the ~17k core
# papers everything else on the site is built from. Bundling that into the
# page every reader loads by default would make every page slower for a
# feature only some readers want (user-requested "list the non-AV
# papers"). Fetched lazily by index.html only when its AV-relevance filter
# is switched away from the "AV relevant" default.
ADJACENT_OUT_FILE = BASE / "data" / "stats_adjacent.json"
# Abstracts are ~20MB of the ~70MB stats.json (raw), but only paper.html
# ever reads one, one paper at a time -- every other page pays that weight
# on every load for a field it never touches. Sharded into ABSTRACT_SHARD_COUNT
# small files instead of one shared blob so paper.html fetches roughly
# 1/64th of the abstract text, not all of it, to show a single paper's
# abstract. paper.html picks the shard with the same djb2-hash-mod-N used
# here (see shard_index below); the two must stay in sync by construction,
# not by convention -- change one, change both.
ABSTRACTS_DIR = BASE / "data" / "abstracts"
ABSTRACT_SHARD_COUNT = 64
SCHOLAR_PROFILES_FILE = BASE / "data" / "scholar_profiles.json"
ORCIDS_FILE = BASE / "data" / "orcids.json"
INSTITUTION_LOGOS_FILE = BASE / "data" / "institution_logos.json"
INSTITUTION_COUNTRIES_FILE = BASE / "data" / "institution_countries.json"
# Institution names an LLM review pass (Claude, reading the full institution
# list one-by-one) flagged as not being real institution names -- a
# genuinely scalable follow-on to the regex-pattern approach above (user-
# flagged: "I believe you are extracting specific words from the papers and
# then filtering them with LLMs... a more scalable solution to fixing
# entries"). Regex only ever catches the SHAPES it was specifically written
# for; this catches whatever those missed by using actual judgment on each
# name. {name: reason}, loaded once and checked as a flat exclusion set in
# is_valid_institution() below -- same mechanism as INVALID_INSTITUTIONS,
# just sourced from a review pass instead of hand-typed.
INSTITUTION_FLAGS_LLM_FILE = BASE / "data" / "institution_flags_llm.json"
# Same idea, but a redirect instead of a rejection: {variant name: canonical
# name} for institutions that are real and valid but get spelled/branded
# differently across papers (diacritic variants, abbreviation vs. spelled-out
# form, a department/school prefix in front of an identifiable parent
# university, a corporate lab name that's really just its parent company).
# The hand-typed INSTITUTION_ALIASES dict below covers the common cases found
# by eye; this is the same idea at registry scale -- an LLM review pass over
# every currently-valid institution name, see that file's own generating
# comment and DECISIONS.md's "Institution extraction switched..." entry.
INSTITUTION_ALIASES_LLM_FILE = BASE / "data" / "institution_aliases_llm.json"
VENUE_LOGOS_FILE = BASE / "data" / "venue_logos.json"
CITATION_GRAPH_FILE = BASE / "data" / "citation_graph.json"
CODE_LINKS_LLM_FILE = BASE / "data" / "code_links_llm.json"
# Must match build_citation_graph.py's CVF_VENUES -- the set of venues its
# PDF-download path can reach at all (CVF-hosted, predictable URL).
CVF_CITATION_GRAPH_VENUES = {"CVPR", "ICCV", "WACV"}


def normalize_title(t):
    return re.sub(r"[^a-z0-9]", "", (t or "").lower())

COUNTRY_NAMES = {
    "US": "United States", "CN": "China", "DE": "Germany", "GB": "United Kingdom",
    "CA": "Canada", "KR": "South Korea", "JP": "Japan", "NL": "Netherlands",
    "SG": "Singapore", "FR": "France", "CH": "Switzerland", "SE": "Sweden",
    "AU": "Australia", "IT": "Italy", "IL": "Israel", "IN": "India", "ES": "Spain",
    "AT": "Austria", "BE": "Belgium", "DK": "Denmark", "FI": "Finland", "NO": "Norway",
    "RU": "Russia", "BR": "Brazil",
    # User-requested: Hong Kong and Macao merge into China rather than
    # showing as their own countries -- both are Chinese special
    # administrative regions, not separate countries.
    "HK": "China", "MO": "China",
    "TW": "Taiwan", "IE": "Ireland",
    "PL": "Poland", "PT": "Portugal", "NZ": "New Zealand", "ZA": "South Africa",
    "EG": "Egypt", "BD": "Bangladesh", "MX": "Mexico", "TR": "Turkey",
    "TH": "Thailand", "MY": "Malaysia", "ID": "Indonesia", "VN": "Vietnam",
    "SA": "Saudi Arabia", "AE": "United Arab Emirates", "GR": "Greece", "CZ": "Czechia",
    "HU": "Hungary", "RO": "Romania", "UA": "Ukraine", "CL": "Chile", "AR": "Argentina",
    "PK": "Pakistan", "IR": "Iran", "QA": "Qatar", "LU": "Luxembourg", "IS": "Iceland",
    "IQ": "Iraq", "LB": "Lebanon", "KE": "Kenya", "NG": "Nigeria", "CO": "Colombia",
    "PE": "Peru", "EC": "Ecuador", "UY": "Uruguay", "HR": "Croatia", "SI": "Slovenia",
    "SK": "Slovakia", "BG": "Bulgaria", "RS": "Serbia", "CY": "Cyprus", "MT": "Malta",
    "EE": "Estonia", "LT": "Lithuania", "LV": "Latvia", "DO": "Dominican Republic",
    "PR": "Puerto Rico",
}


# Preferred order for the server-computed default (before the client's own
# citation-source toggle, see filters.js, overrides it). Internal (in-corpus)
# is preferred: it's fully computed in-house and redistributable, unlike
# OpenAlex -- see Methodology. Only these two sources are offered as a user
def citation_count(entry):
    # Only our own in-corpus citation graph counts -- how many other papers
    # already in this corpus cite this one, computed entirely in-house by
    # build_citation_graph.py. OpenAlex/Semantic Scholar/Scholar counts are
    # never used here, for ranking or for display: those are external,
    # differently-scoped counts that aren't comparable to a number computed
    # over this specific corpus, and blending them in previously let a paper
    # rank highly (server-side sort key) while displaying 0 citations
    # (client always showed in-corpus only) -- a real user-reported mismatch.
    #
    # A paper the in-corpus citation graph has no incoming edge for gets 0,
    # not None: "how many papers in this corpus reference it" is a question
    # the corpus can always answer, and the answer is just often zero. A
    # partial reference-list crawl is a coverage caveat surfaced on the
    # About page ("What's not covered yet"), not a reason to blank the
    # number. (User-decided; the old None sentinel left ~half the corpus
    # reading as "unknown" on every page.)
    return ((entry.get("citations_by_source") or {}).get("in_corpus") or {}).get("count") or 0


def citations_by_source_for_client(entry):
    # Only our own in-corpus count is ever shipped to the client -- see
    # citation_count() above for why external sources are excluded entirely,
    # not just deprioritized.
    in_corpus = (entry.get("citations_by_source") or {}).get("in_corpus")
    return {"in_corpus": in_corpus} if in_corpus and in_corpus.get("count") is not None else None


# Some sources report a venue's full formal name instead of the acronym used
# everywhere else (e.g. OpenAlex/Crossref-sourced entries say "Advances in
# Neural Information Processing Systems" while the main CVF/PMLR/etc. pulls
# say "NeurIPS") -- without normalizing, the same venue silently splits into
# two separate rows on every venue-grouped view.
VENUE_ALIASES = {
    "Advances in Neural Information Processing Systems": "NeurIPS",
    "IEEE Transactions on Pattern Analysis and Machine Intelligence": "TPAMI",
    "European Conference on Computer Vision": "ECCV",
    # User-requested: shorten "arXiv preprint" to "arXiv" everywhere the
    # venue name is displayed -- every other venue on the site is already
    # shown as its short form (CVPR, not "Conference on Computer Vision and
    # Pattern Recognition"), so the fuller "arXiv preprint" stood out.
    "arXiv preprint": "arXiv",
    # Semantic Scholar's own venue strings for the Datasets & Benchmarks
    # track of a conference we already track under its short form --
    # confirmed on real data via backfill_citing_venues.py (e.g. "Argoverse
    # 2" is "NeurIPS Datasets and Benchmarks" per S2, not a separate venue
    # from NeurIPS itself). Not exhaustive -- S2's venue field is free text
    # and the backfill surfaces new variant spellings as it runs; add here
    # as they're found, same as VENUE_ALIASES' other entries.
    "NeurIPS Datasets and Benchmarks": "NeurIPS",
    "Neural Information Processing Systems": "NeurIPS",
    # User-requested acronym fixes, plus every other duplicate long-form
    # spelling found by reviewing the full post-backfill venue list (see
    # the "big_venues" comment below for why this list matters beyond just
    # tidiness -- an unmerged duplicate can knock an otherwise-common venue
    # below the display threshold).
    "arXiv.org": "arXiv",
    "IEEE Transactions on Intelligent Vehicles": "T-IV",
    "IEEE/RJS International Conference on Intelligent RObots and Systems": "IROS",
    "Computer Vision and Pattern Recognition": "CVPR",
    "International Conference on 3D Vision": "3DV",
    "ECCV Workshops": "ECCVW",
    "IEEE Transactions on Image Processing": "T-IP",
    "IEEE International Conference on Robotics and Automation": "ICRA",
    "IEEE transactions on circuits and systems for video technology (Print)": "T-CSVT",
    "American Control Conference": "ACC",
    "International Joint Conference on Artificial Intelligence": "IJCAI",
    "International Conference on Pattern Recognition": "ICPR",
    "IEEE Conference on Decision and Control": "CDC",
    "IEEE transactions on multimedia": "T-MM",
    "IEEE International Conference on Acoustics, Speech, and Signal Processing": "ICASSP",
    "IEEE International Conference on Systems, Man and Cybernetics": "SMC",
    "IEEE International Joint Conference on Neural Network": "IJCNN",
    "IEEE Transactions on Neural Networks and Learning Systems": "TNNLS",
    "IEEE Transactions on Control Systems Technology": "T-CST",
    "IEEE transactions on intelligent transportation systems (Print)": "T-ITS",
    "IEEE Robotics and Automation Letters": "RA-L",
    "International Conference on Intelligent Transportation Systems": "ITSC",
    "International Conference on Learning Representations": "ICLR",
    "Advances in Neural Information Processing Systems 38": "NeurIPS",
    "International Journal of Computer Vision": "IJCV",
    "Robotics: Science and Systems Conference": "RSS",
    "Conference on Robot Learning": "CoRL",
    "IEEE Workshop/Winter Conference on Applications of Computer Vision": "WACV",
    "International Conference on Machine Learning": "ICML",
    "IFAC-PapersOnLine": "IFAC",
    "ACM Multimedia": "ACM MM",
    # Not actually acronym-suffixed -- a publisher-disambiguation city/format
    # tag some journal sources append (e.g. OpenAlex), which would otherwise
    # get mistaken for an acronym by the generic pattern below ("Applied
    # intelligence (Boston)" is NOT abbreviated "Boston").
    "Applied intelligence (Boston)": "Applied Intelligence",
    "Computer graphics forum (Print)": "Computer Graphics Forum",
    "Neural computing & applications (Print)": "Neural Computing & Applications",
    "Displays (Guildford)": "Displays",
}

# A venue string sometimes carries its own year, either leading ("2023
# IEEE/CVF Conference on ... (CVPRW)") or trailing ("CVPR 2020") -- stripped
# generically (user-requested: "remove the year from the venue") rather than
# needing a per-year alias.
YEAR_TOKEN_RE = re.compile(r"\b(19[9]\d|20[0-4]\d)\b")


def strip_year(v):
    v = YEAR_TOKEN_RE.sub("", v)
    v = re.sub(r"\s{2,}", " ", v)
    return v.strip(" ,-–")


# Many IEEE/CVF-style venue strings spell out the full name and then repeat
# its acronym in a trailing "(XXXX)" -- e.g. "2023 IEEE/CVF Conference on
# Computer Vision and Pattern Recognition Workshops (CVPRW)". Once that's
# recognized, the acronym is the only part worth keeping (user-requested,
# generalized to any year/venue rather than one alias per year). Requires
# the whole parenthesized token to be upper-case letters/digits/hyphens --
# a real acronym (CVPRW, ICCVW, PLANS, DSN-W, ISC2) is always shouted in
# caps, while a few journal sources instead append a publisher city or
# format tag this way ("Applied intelligence (Boston)", "... (Print)") that
# is NOT an acronym; requiring all-caps rejects those safely (they just fall
# through unshortened) without needing to enumerate every one by hand.
TRAILING_ACRONYM_RE = re.compile(r"\(([A-Z][A-Z0-9\-]{1,9})\)\s*$")


def normalize_venue(v):
    if v in VENUE_ALIASES:
        return VENUE_ALIASES[v]
    stripped = strip_year(v)
    if stripped != v and stripped in VENUE_ALIASES:
        return VENUE_ALIASES[stripped]
    m = TRAILING_ACRONYM_RE.search(v)
    if m:
        return m.group(1)
    return stripped


# OpenAlex's own author->institution/country linking is occasionally wrong,
# independent of anything in this pipeline. Known case: "InternetLab" (the
# Shanghai AI Lab collaboration hub) gets tagged country BR because OpenAlex
# has conflated it with an unrelated Brazilian NGO of a similar name -- every
# author affiliated with InternetLab comes back with countries ["BR", "CN"],
# where only CN is real. Documented in Methodology; corrected here rather
# than left for readers to puzzle over ("why is Brazil #1?").
COUNTRY_MISLABELS = {
    ("InternetLab", "BR"),
    # Same OpenAlex mis-link as the Nutrasource->Motional institution alias
    # above -- the wrong "CA" (Canada) country code came along with the
    # wrong institution name, keyed on the raw pre-alias name since that's
    # what's actually stored in a.get("affiliations") at lookup time.
    ("Nutrasource", "CA"),
}


# OpenAlex occasionally returns a bare surname instead of a full name for an
# author (seen on real data: "Wang", "Li", "Zhang", ... each attached to 20-100+
# papers -- clearly a data issue on OpenAlex's side, not a one-off). A surname
# alone can't identify a person (there's no way to tell if every "Wang" credit
# is the same researcher), so these would otherwise show up as implausibly
# prolific "authors" on the Researchers leaderboard. Require at least two
# whitespace-separated name parts before an author is counted at all.
# DBLP disambiguates same-named authors with a trailing 4-digit number baked
# into the name string itself (e.g. "Cheng-Zhong Xu 0001") -- an artifact of
# DBLP's own ID scheme, not part of the person's actual name. Stripped before
# display/aggregation so the same person doesn't also show up correctly
# elsewhere as just "Cheng-Zhong Xu".
DBLP_DISAMBIG_SUFFIX_RE = re.compile(r"\s+\d{4}$")

# A LaTeX \footnotemark superscript-reference command, glued onto the name
# with no space (arXiv LaTeX-source author-block parsing artifact, e.g.
# "Zhan Qu11footnotemark: 1" -- the digits before "footnotemark" are the
# rendered footnote number, not part of the name). Also strips a bare
# trailing footnote-marker glyph left over from the same kind of parse.
FOOTNOTEMARK_SUFFIX_RE = re.compile(r"[�†‡*§¶✉]?\d*footnotemark:?\s*\d*\s*$", re.I)

# A trailing footnote/correspondence-marker symbol glued onto a name with no
# accompanying "footnotemark" text for FOOTNOTEMARK_SUFFIX_RE above to key
# off of -- e.g. "Jianbing Shen♠" and "Jianbing Shen🖂" (a spade suit symbol
# and an envelope emoji respectively, both real LaTeX correspondence-author
# markers), confirmed on real data as three separate leaderboard entries for
# the same person. Category-based (Unicode Symbol: "So"/"Sk"/"Sm") rather
# than a hardcoded list of specific glyphs, so a marker symbol not seen
# before -- a different envelope emoji, a different suit, a star operator --
# is still caught; a real person's name is never encoded as ending in a
# Symbol-category character. †‡*§¶ are added explicitly since they're
# Unicode Punctuation ("Po"), not Symbol, and academic footnote convention
# uses them the same way -- but Po is too broad a category to strip on its
# own (it also covers ordinary punctuation like periods and commas that
# legitimately end other kinds of strings, just never a person's own name
# by construction here).
TRAILING_NAME_MARKER_PUNCTUATION = set("†‡*§¶")


def strip_trailing_symbol_markers(name):
    while name and (name[-1] in TRAILING_NAME_MARKER_PUNCTUATION
                    or unicodedata.category(name[-1]) in ("So", "Sk", "Sm")):
        name = name[:-1].rstrip()
    return name


# Some sources (confirmed: DBLP-derived venue listings) leak raw numeric
# HTML entities into the author-name field instead of decoding them, e.g.
# "Marius Z&#246;llner" instead of "Marius Zöllner" -- html.unescape() below
# fixes those. A separate, unrecoverable case: one paper's author string has
# an actual U+FFFD replacement character where an umlaut should be
# ("J. Marius Z�llner") -- that's what an already-lossy decode upstream
# produces, no entity to unescape, so it's hand-corrected here since the
# correct spelling is known from this same person's other papers.
KNOWN_NAME_FIXES = {
    "J. Marius Z�llner": "Marius Zöllner",
    # User-requested: consolidate every spelling of this one person (FZI/KIT)
    # found across the corpus -- full name, abbreviated first name(s), and
    # the oe-for-ö transliteration some sources use -- into one canonical
    # name. Deliberately does NOT touch "R. Zöllner"/"Raoul Zöllner" (a
    # different, real person who happens to share the surname) -- excluded
    # by name, not by the accident of not matching one of these keys.
    "J. Marius Zöllner": "Marius Zöllner",
    # A triple-"n" typo (source: arXiv listing metadata for one paper) --
    # user-flagged as a duplicate author record split off the real "Johannes
    # Betz" (Technical University of Munich, autonomous racing research;
    # confirmed same person via the one paper carrying the typo -- RoboRacer
    # benchmark work, the same research area as the rest of his corpus).
    "Johannnes Betz": "Johannes Betz",
    "Johann Marius Zöllner": "Marius Zöllner",
    "J. M. Zöllner": "Marius Zöllner",
    "J. Zollner": "Marius Zöllner",
    "J. Zoellner": "Marius Zöllner",
    "J. Marius Zoellner": "Marius Zöllner",
    # Mojibake: a straight ASCII apostrophe standing in for í, same class of
    # already-lossy-upstream-decode issue as the Zöllner entry above (the
    # correctly-accented spelling is confirmed from this same person's other
    # papers' authors_detail).
    "Santiago Montiel-Mar'in": "Santiago Montiel-Marín",
    # User-requested: consolidate every spelling of this one person found
    # across the corpus into the fully-written-out form (per the site's
    # general preference for full names over abbreviations) -- the
    # abbreviated "Julian F. P. Kooij" is far more common in the raw data
    # (39 papers) than the fully spelled-out form (4 papers), but the full
    # form is what should be canonical, not the more common one.
    "Julian F. P. Kooij": "Julian Francisco Pieter Kooij",
    "Julian Kooij": "Julian Francisco Pieter Kooij",
    # User-requested: merge every Gavrila spelling into the canonical form
    # already used on the large majority of his papers. Deliberately keyed
    # on the exact abbreviated/bare forms only, not a surname-only match --
    # "Gavrila" alone is ambiguous key material that could collide with
    # future data, so only the specific confirmed variants are listed.
    "D. Gavrila": "Dariu M. Gavrila",
    "Dariu Gavrila": "Dariu M. Gavrila",
    # "Lastname, Firstname" raw format, broken by a naive comma-split
    # upstream into two separate name-list entries ("Gavrila" and "Dariu"/
    # "Dariu M.") -- caught here as a targeted fix for the two confirmed
    # papers with this exact formatting, not a general comma-order fix.
    "Gavrila": "Dariu M. Gavrila",
}

# PDF/font text extraction occasionally renders a hyphenated name's hyphen
# as a different Unicode dash glyph instead of plain ASCII "-" (confirmed on
# real data: "Yi‐Ting Chen" -- U+2010 HYPHEN -- extracted from a CVF PDF,
# splitting that author from the correctly-ASCII-hyphenated "Yi-Ting Chen"
# found on his other papers into two separate leaderboard entries for the
# same person). Normalized to ASCII hyphen so every source agrees on one
# spelling.
UNICODE_HYPHEN_RE = re.compile(r"[‐‑‒–−]")


def clean_author_name(name):
    name = (name or "").strip()
    name = html.unescape(name)
    name = UNICODE_HYPHEN_RE.sub("-", name)
    name = KNOWN_NAME_FIXES.get(name, name)
    name = DBLP_DISAMBIG_SUFFIX_RE.sub("", name).strip()
    name = FOOTNOTEMARK_SUFFIX_RE.sub("", name).strip()
    name = strip_trailing_symbol_markers(name).strip()
    return name


# An affiliation/LaTeX-formatting fragment that leaked into the author-name
# field instead of the affiliation field (seen on real data: "[2mm] UC
# Berkeley" -- a LaTeX vertical-spacing command glued onto an institution
# name, from an author-block line that got mis-split). Not a person's name,
# so it must never reach the Researchers/Authors leaderboard.
BAD_AUTHOR_NAME_RE = re.compile(
    r"^\[-?\d+(\.\d+)?(mm|cm|pt|ex|in|em)\]|university|institute|laboratory", re.I,
)


def is_full_name(name):
    name = name or ""
    if len(name.split()) < 2:
        return False
    return not BAD_AUTHOR_NAME_RE.search(name)


# A sentence-starting word means whatever comes before the separator is a
# clause, not a name (e.g. "A Survey of Motion Planning: Recent Advances"),
# even though it's short enough to otherwise pass the word/length check --
# checked against the FIRST WORD only, case-insensitively.
_SHORT_NAME_STOPWORDS = {
    "a", "an", "the", "on", "towards", "toward", "learning", "improving",
    "understanding", "exploring", "revisiting", "rethinking", "leveraging",
    "exploiting", "investigating", "analyzing", "is", "are", "does", "do",
    "what", "how", "why", "when", "can",
}


def _short_name_candidate(text, max_words):
    words = text.strip().split()
    if not words or len(words) > max_words or len(text.strip()) > 40:
        return None
    if words[0].lower() in _SHORT_NAME_STOPWORDS:
        return None
    return text.strip()



# Common technical acronyms/units that show up in an abstract's own
# parentheticals but aren't THIS paper's coined name -- excluded so
# "...bird's-eye-view (BEV) representation..." doesn't get read as the
# paper's short name the way "...Unified Autonomous Driving (UniAD)..."
# should. Not exhaustive by design -- see _abstract_short_name's CamelCase
# preference below, which is what actually carries most of the precision.
_GENERIC_ABSTRACT_ACRONYMS = {
    "cnn", "rnn", "gan", "lstm", "gru", "rgb", "rgbd", "sota", "iou", "map",
    "gpu", "cpu", "ai", "ml", "hd", "gps", "imu", "lidar", "api", "cv", "nlp",
    "2d", "3d", "bev", "fps", "eg", "ie", "etc", "vs", "ego", "av", "hmi",
    "llm", "vlm", "vqa", "dnn", "mlp", "nn", "svm", "knn", "pca", "fov",
    "ssim", "psnr", "fid", "auc", "roc", "mse", "mae", "sdk", "ar",
    "vr", "ui", "ux", "id", "url", "os", "ram", "cad", "ros", "ppo", "rl",
    # AV-domain terms an abstract routinely defines for the reader's
    # benefit before getting to its own actual contribution -- confirmed on
    # real data: "(AD)" for "autonomous driving" and "(V2I)" for "vehicle-
    # to-infrastructure" both got mined as if they were the paper's own
    # name, when the paper's real coined name (e.g. "TOCOM-V2I") appeared
    # elsewhere without parentheses at all (see the "propose/introduce X,"
    # pattern above, which is checked first specifically because of this).
    "ad", "adas", "cav", "cavs", "hdv", "hdvs", "v2v", "v2i", "v2x", "i2v",
    "its", "oem", "oems", "adv", "avs", "cf", "fv", "vla", "vlas",
}


def _abstract_short_name(abstract):
    """Papers routinely coin their own name mid-abstract -- two patterns,
    tried in this order because the first is the more reliable of the two:

    1. "We propose/introduce/present/call/name/term/dub TOCOM-V2I, a task-
       oriented..." -- a single distinctive token immediately after one of
       these verbs, itself immediately followed by a comma/period (so it
       doesn't greedily grab the first word of a multi-word ordinary
       phrase, e.g. "We introduce Unified Autonomous Driving" -- "Unified"
       isn't immediately followed by punctuation there, so this pattern
       correctly does NOT fire and pattern 2 below handles that case).
    2. "Full Expanded Name (ShortName)" -- e.g. UniAD's "We introduce
       Unified Autonomous Driving (UniAD)...". Prefers a CamelCase-looking
       token (mixed upper/lowercase, like "UniAD" or "BEVFormer") over a
       plain all-caps one, since a real coined name usually isn't styled
       like a generic acronym; an all-caps candidate is only accepted if
       it's reused later in the abstract (appears a 2nd time outside its
       own parenthetical) AND isn't a common AV-domain term the abstract is
       just defining for context (confirmed false positives on real data:
       "(AD)" for "autonomous driving", "(V2I)" for "vehicle-to-
       infrastructure" -- see _GENERIC_ABSTRACT_ACRONYMS).
    """
    if not abstract:
        return None
    verb_m = re.search(
        r"\b(?:propose|introduce|present|call|name|term|dub)\w*\s+(?:it\s+|them\s+|)(?:as\s+|)"
        r"([A-Z][A-Za-z0-9]*(?:[-+][A-Za-z0-9]+)*)\s*(?=[,.])",
        abstract,
    )
    if verb_m and len(verb_m.group(1)) >= 2 and verb_m.group(1).lower() not in _GENERIC_ABSTRACT_ACRONYMS:
        return verb_m.group(1)
    # "Our framework, CenterPoint, first detects..." -- same idea as the verb
    # pattern above (a coined name set off by commas, immediately after the
    # word that introduces it), just introduced by "Our X" instead of a verb
    # (confirmed on real data: CenterPoint's own abstract uses exactly this
    # phrasing and has no colon/dash in its title and no parenthetical
    # acronym in its abstract either, so neither this function's other
    # pattern nor paper_short_name's title-based checks caught it).
    our_m = re.search(
        r"\bOur\s+\w+,\s+([A-Z][A-Za-z0-9]*(?:[-+][A-Za-z0-9]+)*)\s*(?=[,.])",
        abstract,
    )
    if our_m and len(our_m.group(1)) >= 2 and our_m.group(1).lower() not in _GENERIC_ABSTRACT_ACRONYMS:
        return our_m.group(1)
    candidates = re.findall(r"\(([A-Za-z][A-Za-z0-9\-]{1,14})\)", abstract)
    camel = [c for c in candidates if re.search(r"[a-z]", c) and re.search(r"[A-Z]", c)]
    for c in camel:
        if c.lower() not in _GENERIC_ABSTRACT_ACRONYMS:
            return c
    for c in candidates:
        if c.lower() in _GENERIC_ABSTRACT_ACRONYMS or not c.isupper():
            continue
        if abstract.count(c) >= 2:
            return c
    return None


DISRUPTION_MIN_CITERS = 3
# How many years after publication counts as "early" for the early-citation-
# velocity signal on index.html -- 2 full calendar years (publication year
# + the next 2) is long enough for a paper to plausibly have been read and
# cited by other AV-corpus work, short enough that the window closes for
# most of the corpus rather than only its oldest papers.
EARLY_CITATION_WINDOW_YEARS = 2

# Abstract-based supplementary code-availability signal (user-flagged: "code
# cannot just be at github/gitlab. We should do a keyword search for things
# like 'code' in the abstract"). has_code_link's primary source
# (fetch_affiliations_arxiv.py's detect_code_link, see its own comment) only
# ever reaches papers with a resolved arXiv ID and a successful ar5iv fetch
# -- this runs over every core paper's ABSTRACT instead, which exists for
# almost the whole corpus regardless of arXiv status, so it can upgrade a
# paper straight from "never checked" to a confirmed release. Deliberately
# one-directional -- an abstract that DOESN'T mention code says nothing
# (most papers with real code releases don't say so in the abstract itself,
# they put it in the paper body or just a repo README), so this only ever
# sets has_code_link True, never False; a real False still only ever comes
# from the ar5iv full-text check actually looking and not finding anything.
# Same phrasing this project already uses in fetch_affiliations_arxiv.py's
# CODE_AVAILABILITY_TEXT_RE, kept here as its own copy since the two run in
# different scripts against different text (full paper vs. abstract only).
ABSTRACT_CODE_AVAILABILITY_RE = re.compile(
    r"\b(?:code|codes|implementation|source\s*code)\b(?:[^.\n]{0,60})?\b"
    r"(?:is|are|will\s+be|has\s+been)\s+(?:publicly\s+|freely\s+)?"
    r"(?:available|released|open-?sourced?)\b"
    r"|\bwe\s+(?:release|open-?source|publicly\s+release|will\s+release)\s+"
    r"(?:the|our)?\s*(?:code|source\s*code|implementation)\b",
    re.I)


def compute_disruption_index(edges):
    """CD index (Funk & Owen-Smith 2017, "A Dynamic Network Measure of
    Technological Change", Management Science) -- a citation-graph measure
    of whether later work treats a paper as having SUPERSEDED its own
    references (disruptive, citers engage with the paper but not its
    intellectual predecessors) or as EXTENDING them (consolidating, citers
    cite the paper alongside the same references it built on).

    For a focal paper P with citers C: for each citer c in C, c is a
    "disruptive vote" (+1) if c does NOT also cite any of P's own
    (in-corpus) references, or a "consolidating vote" (-1) if it does.
    CD(P) = mean of these votes, in [-1, 1].

    `edges` is citer_key -> [cited_key, ...] (a paper's own in-corpus
    reference list, exactly what build_citation_graph.py's match_phase
    produces). A citer only ever appears here BECAUSE its own reference
    list was scanned (that's the only way this pipeline discovers "X cites
    Y" at all) -- so unlike the general CD-index literature, there's no
    "citer with unknown outgoing citations" case to guard against here;
    every citer's own edges[citer_key] is already fully known. Returns
    {target_key: {"cd_index": float, "n_citers": int}}, only for papers
    with at least DISRUPTION_MIN_CITERS citers -- below that a +1/-1
    average is too coarse to mean anything (3 citers can only ever land on
    -1, -0.33, 0.33, or 1).
    """
    citers_of = defaultdict(list)
    for citer_key, cited_keys in edges.items():
        for cited_key in cited_keys:
            citers_of[cited_key].append(citer_key)

    result = {}
    for target_key, citer_keys in citers_of.items():
        if len(citer_keys) < DISRUPTION_MIN_CITERS:
            continue
        target_refs = set(edges.get(target_key, ()))
        votes = sum(-1 if (set(edges.get(c, ())) & target_refs) else 1 for c in citer_keys)
        result[target_key] = {"cd_index": round(votes / len(citer_keys), 3), "n_citers": len(citer_keys)}
    return result


def shard_index(title, num_shards=ABSTRACT_SHARD_COUNT):
    """djb2 string hash mod num_shards -- picks which abstracts/shard-NN.json
    a paper's abstract lives in. paper.html re-implements this exact
    algorithm in JS to compute the same shard client-side from a title
    alone, with no index file needed. Only stable for BMP characters
    (ord() vs JS's charCodeAt() diverge above U+FFFF), which every real
    paper title in this corpus is well within."""
    h = 5381
    for ch in title:
        h = ((h * 33) + ord(ch)) & 0xFFFFFFFF
    return h % num_shards


def paper_short_name(title, authors, year, abstract=None):
    """A short, human-recognizable label for a paper -- the well-known short
    name where the title's own structure gives one away (nuScenes, KITTI,
    PointPillars, ...), then the same signal from the abstract's own text
    when the title doesn't have one (user-requested: "Planning-Oriented
    Autonomous Driving" has no colon/dash, but its abstract introduces
    "Unified Autonomous Driving (UniAD)"), otherwise "{Surname}{YY}" from
    the first author and publication year. User-requested: the text before
    a colon is often this name and is used first; the text before a dash is
    the same signal but noisier (a dash shows up inside plenty of ordinary
    sentence titles too, not just before a short name), so it's only used
    when nothing else qualifies and held to a stricter word-count/stopword
    check. Collisions between different papers sharing a surname+year (or
    an abstract-derived name) are expected and accepted, same as the
    original single-purpose version of this logic that lived only in
    compute_insights()."""
    title = title or ""
    colon_idx = title.find(":")
    if colon_idx > 0:
        candidate = _short_name_candidate(title[:colon_idx], max_words=6)
        if candidate:
            return candidate
    # en dash, em dash, or a plain hyphen surrounded by spaces (a bare "-"
    # inside a compound word like "self-driving" must not be treated as a
    # title separator).
    dash_m = re.search(r"\s[-–—]\s", title)
    if dash_m:
        candidate = _short_name_candidate(title[:dash_m.start()], max_words=3)
        if candidate:
            return candidate
    abstract_name = _abstract_short_name(abstract)
    if abstract_name:
        return abstract_name
    authors = authors or []
    surname = authors[0].split()[-1] if authors and authors[0].split() else "Unknown"
    yy = str(year)[-2:].zfill(2) if year else ""
    return f"{surname}{yy}"


# Affiliation strings pulled from OpenAlex/arXiv/PDF-text parsing occasionally
# come back as something other than an institution: a bare country/city (the
# parser fell back to the *location* field instead of the institution name),
# a URL glued onto the real name by a text-extraction bug, a street address,
# a generic team/department label with no parent institution, or a person's
# name. These are all confirmed-bad exact strings seen on real data, not a
# guess -- filtered out here (client-facing display only; the underlying
# authors_detail data on disk is untouched, so a better fix later isn't
# blocked by this). A real fix needs LLM-based affiliation parsing to reduce
# every institution to its top-level entity (see DECISIONS.md); this is a
# stopgap for the worst, most obviously-wrong entries.
INVALID_INSTITUTIONS = {
    # Bare countries -- an affiliation field split on commas sometimes leaves
    # just the country as its own entry (e.g. "..., Beijing, China" splitting
    # into "Beijing" and "China" instead of one string).
    "China", "USA", "United States", "Germany", "France", "UK", "Japan", "Canada",
    "India", "Australia", "Sweden", "Spain", "Italy", "South Korea", "Korea",
    "Singapore", "Israel", "Switzerland", "Netherlands", "Austria", "Belgium",
    # Bare cities, same cause.
    "Beijing", "Los Angeles", "Shenzhen", "Xiamen", "Nanjing", "Zurich", "Paris",
    "London", "Guangzhou", "Chongqing", "Redmond", "Blacksburg", "Bangalore",
    "Tokyo", "Stockholm", "Barcelona", "Ann Arbor", "Santa Cruz", "Trento", "Graz",
    "Merced", "College Park", "Pittsburgh", "West Lafayette", "University Park",
    "Telangana", "Shanghai", "Berkeley", "Tubingen", "Tübingen", "Atsugi", "Fujian",
    "Department of Computer Science", "College of Computer Science and Electronic Engineering",
    "AI Group", "Ye Li", "Otto-Hahn-Str. 1", "Inc", "Ltd.",
    # A bare corporate suffix with nothing before it -- same comma-split
    # cause as the bare countries/cities above (e.g. "..., Some Co., Ltd"
    # splitting into a real name and a stray "Ltd" fragment). Note this is
    # the bare word only -- a real name that legitimately ends in "Ltd"
    # (e.g. "Shenzhen Forward Innovation Digital Technology Co. Ltd") is
    # untouched.
    "Ltd",
    # Second-pass finds: a truncated fragment (a comma-split "Chinese Academy
    # of Sciences" losing everything after "of"), a co-authorship marker
    # rather than an institution, and a LaTeX escape sequence leaking
    # through unescaped ("Computer Science &\& Engineering").
    "Chinese Academy of", "Equal Contribution", "Autonomous Driving",
    "Computer Science &\\& Engineering",
    # Third-pass finds: more bare Chinese provinces/cities, an
    # affiliation-block co-authorship marker with a stray symbol attached,
    # and a bare person's name (like "Ye Li" above -- caught when a
    # comma-split author list leaks one name into the affiliation field).
    "Zhejiang", "Zhengzhou", "Equal contribution✉", "Wei Zhan",
    # Fourth-pass finds (user-flagged: "provided by the Computer Vision
    # Foundation" is a PDF-hosting credit line, not an author affiliation --
    # auditing the full institution list surfaced these alongside it): more
    # credit/byline fragments, LaTeX artifacts, and bare initials/codes that
    # leaked into the affiliation field the same way the entries above did.
    "provided by the Computer Vision Foundation", "and", "respectively", "research",
    "Correspondence:", "Corresponding", "Project Leader Corresponding author",
    "as a strong baseline to facilitate further research", "in Prague", "ltd (d)",
    "liqy", "wangwen", "zhangli", "Sand 1", "T6G1H9", "ON N2L3G1", "MA", "PRC", "US",
    # Bare city names with no institution attached (same comma-split cause as
    # the bare-city block above) -- user-flagged (Eindhoven, Waterloo) plus
    # the rest of that same pattern found while auditing the full list.
    "Eindhoven", "Waterloo", "Munich", "Boston", "Seoul", "Toronto", "Vancouver",
    "Karlsruhe", "Ulm", "Leuven", "Milano", "Istanbul", "Hangzhou", "Suzhou",
    "Genoa", "Florence", "Turin", "Toulouse", "Essen", "Daejeon", "Tempe", "Orlando",
    "Cary", "Brookline", "Minneapolis", "Sunnyvale", "Mountain View", "Santa Clara",
    "San Antonio", "San Diego", "San Francisco", "El Paso", "Dearborn", "Tacoma",
    "Parkville", "Pasadena", "Charlottesville", "Wuhan", "Ningbo", "Hillsboro",
    "Kronach", "Renningen", "Sindelfingen", "Delft", "Oslo",
    # Bare country abbreviation, same comma-split cause as the bare full
    # country names above -- user-flagged.
    "UAE",
    # A generic lab/institute name with a qualifier BEFORE the generic noun
    # (e.g. "Robotics Institute", "Multimedia Laboratory") -- SUBUNIT_PREFIX_RE
    # above only catches the noun-first form ("Institute for...", "Laboratory
    # of..."); this word order needs its own exact-match entries since a
    # generic regex here would also reject real institution names that
    # legitimately end the same way (e.g. "Beijing Institute of Technology").
    # User-flagged.
    "Robotics Institute", "Multimedia Laboratory",
    # Fifth-pass finds (from a systematic audit of authors with implausibly
    # long institution lists, prompted by user-flagged real examples on
    # "Tsinghua University" and "Jun Zhu"): a bare Tokyo ward name, a bare
    # building name, a paper-title fragment, and a concatenated-institutions
    # string that neither DOUBLE_UNIVERSITY_RE nor any alias could safely
    # generalize (Peking University / MPI Tübingen / TTI Chicago are three
    # real, unrelated organizations glued together with no separator; there
    # is no single correct institution to alias this to, so it's dropped).
    "Bunkyo-ku", "Jacom Building",
    "Three Pillars improving Vision Foundation Model Distillation for Lidar",
    "Peking University MPI Tübingen TTI Chicago",
    # A bare fragment word left behind when "Division of Robotics,
    # Perception, and Learning (RPL)" -- KTH's real division name -- gets
    # comma-split into pieces (user-flagged, real example: Qingwen Zhang's
    # affiliations list). "and Learning (RPL)" is separately caught by
    # LEADING_AND_RE; "Perception" alone matches no other pattern, since
    # it's an ordinary English word with no institutional keyword to key a
    # general regex off of.
    "Perception",
}

# Loaded once at import time, same as the hand-typed INVALID_INSTITUTIONS
# set above conceptually -- just sourced from institution_flags_llm.json
# (an LLM review pass over the full institution list, see that file's own
# generating comment) instead of being hand-typed one at a time. Defensive
# against the file being absent (e.g. in a test environment that imports
# this module directly) the same way INSTITUTION_COUNTRIES_FILE is below.
INVALID_INSTITUTIONS_LLM = set(
    json.loads(INSTITUTION_FLAGS_LLM_FILE.read_text(encoding="utf-8")).keys()
    if INSTITUTION_FLAGS_LLM_FILE.exists() else ()
)

# Loaded once at import time, same pattern as INVALID_INSTITUTIONS_LLM above
# -- {variant: canonical}, see INSTITUTION_ALIASES_LLM_FILE's own comment.
INSTITUTION_ALIASES_LLM = (
    json.loads(INSTITUTION_ALIASES_LLM_FILE.read_text(encoding="utf-8"))
    if INSTITUTION_ALIASES_LLM_FILE.exists() else {}
)


# A department/school/lab/center name with no parent institution attached
# (e.g. "Department of Computer Science" with nothing after it -- contrast
# with "University of California, Berkeley", which legitimately starts
# similarly-generic but does carry a real top-level name). Confirmed on real
# data: none of these ever co-occur with "University"/"Institute of
# Technology" in the same string, so filtering the whole prefix class is
# safe rather than guessing which fragments might secretly be complete.
# This is the enforcement of "only show the highest-level entity" -- since
# there's no parent name to promote to, excluding entirely beats showing a
# fragment generic enough to belong to any university on earth.
SUBUNIT_PREFIX_RE = re.compile(
    r"^(College|School|Faculty|Department|Institute|Center|Centre|Lab|Laboratory|"
    r"Division|Graduate School|Chair|Group|Program)\b", re.I,
)


# Countries that show up as a trailing " (Country)" suffix on an
# institution string (an OpenAlex formatting convention, applied
# inconsistently -- most affiliations never carry it at all) -- e.g.
# "Meta (Israel)", "Google (United States)", "Baidu (China)". Stripped so
# the same real-world institution isn't fragmented into "Meta" and "Meta
# (Israel)" as two separate rows. Not every country ever seen in the corpus
# (see data/institution_countries.json for that), just the ones observed on
# this specific pattern.
TRAILING_COUNTRY_RE = re.compile(
    r"\s*\((United States|United Kingdom|China|France|Germany|Israel|Sweden|"
    r"South Korea|Switzerland|Canada|Japan|Netherlands|Australia|Singapore|"
    r"India|Italy|Spain|Belgium|Austria|Denmark|Norway|Finland|Ireland|"
    r"Poland|Brazil|Russia|Shenzhen)\)\s*$"
)

# Known variant spellings/brandings of the same real-world institution,
# normalized to one canonical name so paper/citation counts aren't split
# across near-duplicate rows. Found by inspecting the full affiliation
# frequency list (see DECISIONS.md) -- not exhaustive (hundreds of one-off
# author-supplied strings exist further down the tail), just the ones
# common enough to visibly fragment a leaderboard entry.
INSTITUTION_ALIASES = {
    # OpenAlex's own institution disambiguation mis-splits some authors'
    # "UC Berkeley"-shaped affiliation string into TWO institution records --
    # confirmed on real data: three separate papers each list both "Berkeley
    # College" (a small, unrelated New Jersey/New York career college) AND
    # "University of California, Berkeley" for the very same author, on
    # every paper. Not a namesake risk to alias away (the real record is
    # always present alongside it), just OpenAlex noise.
    "Berkeley College": "University of California, Berkeley",
    "PSI": "KU Leuven",  # PSI (Processing Speech & Images) is a KU Leuven research group, not a separate institution
    "Cooperative Medianet Innovation Center": "Shanghai Jiao Tong University",
    "Baidu Inc": "Baidu",  # trailing "." already stripped before this lookup runs
    "Baidu Research": "Baidu",
    "Waymo LLC": "Waymo",
    "Alibaba Inc": "Alibaba",
    "Alibaba Group": "Alibaba",
    "Alibaba Group Holding Limited": "Alibaba",
    "Google LLC": "Google",
    "Google Research": "Google",
    "Uber Advanced Technologies Group": "Uber ATG",
    "Advanced Technologies Group": "Uber ATG",  # bare form seen with the "Uber" prefix dropped (e.g. affiliation just says "Advanced Technologies Group (United States)")
    # nuTonomy was acquired by Aptiv in 2017; Aptiv's automated-driving unit
    # (which absorbed nuTonomy) was spun off and merged with Hyundai's ADAS
    # arm to form Motional in 2020 -- same corporate lineage, one canonical
    # name. User-requested: two different capitalizations of the nuTonomy
    # byline seen in the raw affiliation data, plus the bare "Aptiv" form.
    "nuTonomy: an APTIV company": "Motional",
    "nuTonomy: an Aptiv Company": "Motional",
    "Aptiv": "Motional",
    "Aptiv (Ireland)": "Motional",
    # User-flagged: OpenAlex mis-linked "nuTonomy" to an unrelated Canadian
    # nutraceutical-research company of a similar-sounding name on at least
    # the CoverNet paper -- same class of error as InternetLab/Brazil below
    # (see COUNTRY_MISLABELS), just on the institution-name side instead of
    # the country-code side.
    "Nutrasource": "Motional",
    # User-requested: every KIT spelling variant found in the raw data
    # (parenthetical abbreviation, hyphenated "KIT -", bare "KIT" prefix)
    # collapsed to one canonical name. Leaked-footnote-sentence variants
    # ("Authors are with the Karlsruhe Institute of Technology", "Eric Sax
    # is with...") are handled by the corresponding-author/footnote strip in
    # normalize_institution() instead, not listed here individually.
    "Karlsruhe Institute of Technology (KIT)": "Karlsruhe Institute of Technology",
    "KIT Karlsruhe Institute of Technology": "Karlsruhe Institute of Technology",
    "KIT - Karlsruhe Institute of Technology": "Karlsruhe Institute of Technology",
    "The Chinese University of Hong Kong (Shenzhen)": "The Chinese University of Hong Kong",
    "Chinese University of Hong Kong": "The Chinese University of Hong Kong",
    "Shanghai Jiaotong": "Shanghai Jiao Tong University",
    "SJTU": "Shanghai Jiao Tong University",
    "CMU": "Carnegie Mellon University",
    "UGE": "Univ. Gustave Eiffel",
    "Un. Sofia": "Sofia University",
    "Sofia University St. Kliment Ohridski": "Sofia University",
    "Nanyang Technological": "Nanyang Technological University",
    # Tsinghua's own sub-lab/department/center names, seen on their own with
    # no parent institution attached (user-flagged: these were fragmenting
    # e.g. "Jun Zhu"'s institution list into 8 separate-looking entries that
    # are really all just Tsinghua). Same pattern as the Bosch/Huawei/EPFL
    # entries above.
    "Dept. of Comp. Sci. and Tech": "Tsinghua University",
    "Tsinghua-Bosch Joint ML Center": "Tsinghua University",
    "THBI Lab": "Tsinghua University",
    "BNRist": "Tsinghua University",
    "BNRist Center": "Tsinghua University",
    # NOT aliased: "Chinese Institute for Brain Research (CIBR)" -- a
    # separately-funded Beijing research institute closely associated with
    # Tsinghua (among other universities), not confidently "the same
    # organization" the way BNRist/THBI Lab clearly are. Left as its own
    # institution rather than risk misattributing it.
    # User-requested groupings: every corporate-lab/division spelling of the
    # same company folded to one canonical name, same rationale as the
    # Baidu/Alibaba/Google entries above.
    "Huawei Technologies": "Huawei", "Huawei Technologies Ireland": "Huawei",
    "Huawei Cloud Computing Technologies Co": "Huawei",
    "Huawei Foundation Model Department": "Huawei",
    "Huawei Intelligent Automotive Solution BU": "Huawei",
    "Huawei International Pte Ltd": "Huawei", "Huawei Paris Research Center": "Huawei",
    "Huawei Noah's Ark": "Huawei Noah's Ark Lab", "Noah's Ark": "Huawei Noah's Ark Lab",
    "Noah's Ark Lab": "Huawei Noah's Ark Lab",
    "Robert Bosch": "Bosch", "Robert Bosch GmbH": "Bosch",
    # User-requested: fold the AI-center spelling into the same "Bosch" row
    # too, not just alias it to its own separate canonical name.
    "Bosch Center for AI": "Bosch", "Bosch Center for Artificial Intelligence": "Bosch",
    "Bosch Mobility Solutions": "Bosch", "Bosch Research": "Bosch",
    "Bosch Research North America": "Bosch",
    "Bosch Research North America & Bosch Center for Artificial Intelligence (BCAI)": "Bosch",
    # HKISI-CAS is written with an underscore in some author blocks
    # ("HKISI_CAS"), which normalize_institution already turns into a space
    # ("HKISI CAS") before this lookup runs -- spelled out in full here per
    # user request, same treatment as any other abbreviation-only entry.
    "HKISI CAS": "Hong Kong Institute of Science & Innovation, CAS",
    "HKISI": "Hong Kong Institute of Science & Innovation, CAS",
    "École Polytechnique Fédérale de Lausanne": "EPFL",
    "École Polytechnique Fédérale de Lausanne (EPFL)": "EPFL",
    "EPFL VITA lab": "EPFL", "EPFL VITA Lab": "EPFL",
    # User-requested: fold every spelling of a given Max Planck Institute
    # together. The Max Planck Society runs several genuinely distinct
    # institutes (different cities, different research focus) -- these stay
    # SEPARATE canonical names, only same-institute spelling/abbreviation
    # variants are merged, same principle as the Huawei/Bosch groups above.
    "MPI Informatics": "Max Planck Institute for Informatics",
    "MPI for Informatics": "Max Planck Institute for Informatics",
    "Max Planck Institute for Informatics Saarland Informatics Campus": "Max Planck Institute for Informatics",
    "MPI for Intelligent Systems Tübingen": "Max Planck Institute for Intelligent Systems",
    "MPI for Intelligent Systems T�bingen": "Max Planck Institute for Intelligent Systems",
    # A Max Planck/university joint PhD program, run out of and staffed by
    # the Institute itself -- treated as the same institute for a
    # paper-affiliation count rather than kept as its own separate row.
    "Max Planck Research School for Intelligent Systems": "Max Planck Institute for Intelligent Systems",
    "MPI-SWS": "Max Planck Institute for Software Systems",
    # A bare company domain leaking in as the affiliation string instead of
    # the company's actual name.
    "valeo.ai": "Valeo",
    "Mercedes-Benz Research & Development North America": "Mercedes-Benz",
    "Mercedes-Benz AG": "Mercedes-Benz", "Mercedes-Benz Group China Ltd": "Mercedes-Benz",
    "Mercedes-Benz AG R&D": "Mercedes-Benz", "Mercedes-Benz AG Ulm University": "Mercedes-Benz",
    # The mojibake fix above corrects "O¨" -> "Ö" but can't tell, in general,
    # whether a stray space right after a fixed letter was itself part of
    # the same extraction artifact (as here, "O¨ rebro" -> "Örebro") or a
    # real word boundary (e.g. "Koc¸ University" -- the space before
    # "University" must stay). One real instance observed, handled as a
    # literal post-fix alias rather than a riskier general space-eating rule.
    "Ö rebro University": "Örebro University",
}

# A LaTeX spacing command (e.g. "[2mm]", "[1ex]") leaking in front of an
# otherwise-real institution name -- author-block formatting artifact from
# the source PDF/LaTeX, not part of the name itself. Stripped rather than
# rejecting the whole entry, since what follows (e.g. "Applied Intuition")
# is a real institution.
LATEX_SPACING_PREFIX_RE = re.compile(r"^\[-?(\d+(\.\d+)?|\.\d+)(mm|cm|pt|ex|in|em)\]\s*", re.I)
# Same artifact, trailing instead of leading (e.g. "Munich Center for
# Machine Learning[-0.2em]"). The unit-length can be written without a
# leading zero ("[.2cm]", not just "[0.2cm]") -- confirmed on real data.
LATEX_SPACING_SUFFIX_RE = re.compile(r"\s*\[-?(\d+(\.\d+)?|\.\d+)(mm|cm|pt|ex|in|em)\]$", re.I)

# A leading footnote/affiliation marker (†, ‡, *, §, ¶, ✉, ⋆, ∗) glued onto
# the name with no space (e.g. "†Nokia Bell Labs", "⋆ CEA LIST Vision and
# Learning Lab") -- author-block formatting, not part of the institution's
# name. ⋆ (star operator) and ∗ (asterisk operator) are distinct Unicode
# codepoints from a plain "*" and show up from the same LaTeX footnote
# convention, just a different symbol/font choice.
FOOTNOTE_MARKER_RE = re.compile(r"^[†‡*§¶✉⋆∗]+\s*")
# The same marker glued onto the END instead, with nothing else following it
# ("Singapore †", "Pengcheng Laboratory †", "Yingcong Chen†") -- unlike
# TRAILING_FOOTNOTE_ARTIFACT_RE below, there's no accompanying footnote TEXT
# here to key off of, just a bare marker. Safe to strip unconditionally
# (no MIN_STRIPPED_INSTITUTION_LENGTH gate) since a real, standalone marker
# character is itself strong evidence of "name, then a footnote reference"
# -- unlike prose that merely happens to be short (see
# strip_trailing_footnote_junk's docstring for why that case DOES need the
# length gate). Confirmed on real data: "Singapore †" survived as its own
# fake institution because a bare country name is only 9 characters, under
# the length gate the sentence-level stripper uses.
TRAILING_FOOTNOTE_MARKER_RE = re.compile(r"\s*[†‡*§¶✉⋆∗]+$")
# A "Corresponding author(s)" footnote, or a raw LaTeX \footnotetext/\dagger
# command, or the U+FFFD replacement character an already-lossy upstream
# decode leaves behind -- glued onto the END of a real institution name with
# no separator ("Tsinghua University Corresponding author",
# "NVIDIA ResearchCorresponding authors:", "Nankai University. �\dagger:
# Corresponding authors: Diange Yang"). Confirmed on real data as a
# systemic pattern (225+ authors_detail entries), not a one-off. No leading
# \b -- the contamination is sometimes glued directly onto the institution
# name with no space ("ResearchCorresponding"), so a word-boundary
# requirement would miss exactly the cases that most need it.
TRAILING_FOOTNOTE_ARTIFACT_RE = re.compile(r"(?:correspond(?:ing|ence)|�|\\dagger\b|\d*footnotetext\b).*", re.I | re.S)
# Minimum length of what's left after stripping the artifact above for the
# strip to actually be applied -- see strip_trailing_footnote_junk().
MIN_STRIPPED_INSTITUTION_LENGTH = 10

# A LaTeX \NEXTAFF affiliation-macro name leaking through unrendered (e.g.
# "\NEXTAFFStanford University") -- the real institution name follows
# immediately with no space, so this is stripped rather than the whole entry
# rejected.
NEXTAFF_PREFIX_RE = re.compile(r"^\\?NEXTAFF", re.I)

# A base letter followed by a SPACING (non-combining) diacritic mark glued
# on immediately after it, instead of the proper single accented character
# -- a PDF-text-extraction artifact where the diacritic's glyph and the
# base letter's glyph get emitted as two separate characters in the wrong
# visual order (e.g. "Osnabru¨ck" instead of "Osnabrück", "Koc¸" instead of
# "Koç"). Confirmed on real data across several German/Scandinavian/Turkish
# institution names. Order matters: longest/most-specific replacements
# aren't needed here since each pair is only 2 characters, but this must run
# BEFORE any other normalization that might reflow whitespace around these.
MOJIBAKE_DIACRITIC_PAIRS = {
    "a¨": "ä", "A¨": "Ä", "o¨": "ö", "O¨": "Ö", "u¨": "ü", "U¨": "Ü",
    "a˚": "å", "A˚": "Å", "a˜": "ã", "A˜": "Ã", "o˜": "õ",
    "c¸": "ç", "C¸": "Ç", "n˜": "ñ", "N˜": "Ñ",
    "e´": "é", "E´": "É", "a´": "á", "A´": "Á", "i´": "í", "I´": "Í",
    "o´": "ó", "O´": "Ó", "u´": "ú", "U´": "Ú",
}


def fix_mojibake_diacritics(name):
    for broken, fixed in MOJIBAKE_DIACRITIC_PAIRS.items():
        name = name.replace(broken, fixed)
    return name


# A leading "the"/"The" article -- never part of a real institution's own
# name in the sense that matters here (even for the handful of institutions
# whose FORMAL name does start with "The", e.g. "The University of
# Edinburgh", dropping it just yields the more common short form, not a
# wrong one). Confirmed real bug: "the Netherlands"/"The Netherlands"
# (comma-split off a full address string) survived as its own fake
# institution because the bare-country-name check further down only ever
# matched the exact "Netherlands" from COUNTRY_NAMES, never the
# article-prefixed form.
LEADING_ARTICLE_RE = re.compile(r"^the\s+", re.I)

# A footnote/affiliation-marker NUMBER glued directly onto the front with no
# space ("3Intelligent Vehicles Lab", from a superscript footnote reference
# like "³Intelligent Vehicles Lab" that lost its superscript formatting) --
# same cause and shape as FOOTNOTE_MARKER_RE's symbol markers above, just a
# digit instead of a dagger/asterisk/etc. Requires the digit to be followed
# by a capital letter and then at least 2 lowercase letters -- i.e. the
# start of an ordinary word ("Intelligent...") -- not just any capital.
# Deliberately narrow: this corpus has real institution names/abbreviations
# that legitimately start with a digit ("3D Optical Metrology Unit", TU
# Delft's own "3mE" faculty short code, and real companies like "3M" or
# "4Paradigm" elsewhere) -- a plain "digit then capital letter" rule would
# have mangled "3D..." to "D..." and "3mE" to "mE...", and did mangle "3M"
# to "M" before this got tightened (caught in testing, not live).
LEADING_FOOTNOTE_NUMBER_RE = re.compile(r"^\d+(?=[A-Z][a-z]{2,})")

# An obfuscated email address ("l.ferranti at tudelft.nl" instead of
# "l.ferranti@tudelft.nl", a common anti-spam convention in PDF-extracted
# author blocks) -- EMAIL_LABEL_RE above only catches an explicit
# "email:"/"e-mail:" label, not this "X at Y.tld" form with no label at all.
OBFUSCATED_EMAIL_RE = re.compile(
    r"\b[\w.+-]+\s+at\s+[\w.-]+\.(nl|com|edu|org|net|de|uk|co\.uk|fr|se|dk|no|fi|ch|it|es|jp|cn|kr|ca|au|io)\b",
    re.I)

# A funding/acknowledgment credit line ("Her work is supported by the NWO
# VENI grant (n. 18165)"), the same class of footnote content as
# CREDIT_LINE_RE's "equal contributions"/"corresponding author" but for
# funding acknowledgments specifically -- confirmed real: glued onto the end
# of a genuine institution+email fragment with no separator that survived to
# be split off as (part of) its own "institution" entry.
FUNDING_CREDIT_RE = re.compile(r"\b(is |was )?supported by\b|\bfunded by\b|\bgrant\s*\(?\s*(no\.|n\.|#)", re.I)

# An abbreviated person name used as an "institution" ("E. Eaton", "L.
# Ferranti") -- confirmed real: a neighboring author's name-with-initial
# leaking into this author's affiliation field via a footnote-block parsing
# error. No real institution is named in this exact "single initial + period
# + one capitalized surname-like word" shape.
PERSON_INITIAL_NAME_RE = re.compile(r"^[A-Z]\.\s?[A-Z][a-z'\-]+$")


# A legal-entity suffix (LLC, Inc, GmbH) trailing an otherwise-real company
# name -- user-flagged, and the branding a company actually goes by never
# includes it (nobody writes "I work at Waymo LLC" in conversation), so it
# just fragments the same real institution into "Foo" and "Foo LLC" as two
# separate leaderboard rows. Deliberately narrow (just these three, not
# Ltd/Co/Corp/AG) -- "Ltd"/"Co." are left alone on purpose elsewhere (see
# the bare-"Ltd" comment above): a name legitimately ending in "Co. Ltd" is
# real Chinese-company branding, not a fragment to strip.
TRAILING_LEGAL_SUFFIX_RE = re.compile(r",?\s+(LLC|Inc\.?|GmbH)$", re.I)

# A trailing postal/zip code -- "Beijing 100084" (plain digits) or "Kanagawa
# 243-0198"/"Tokyo 169-8555" (Japan's NNN-NNNN postal format). "10129
# Torino" (leading digits) is already caught by the leading-postal-code
# check below.
TRAILING_POSTAL_CODE_RE = re.compile(r"\s+\d{2,6}(-\d{3,6})?$")

# A bare "STATE-ZIP"-style token with nothing else ("WA-98052") -- no city
# or institution name attached at all.
BARE_STATE_ZIP_RE = re.compile(r"^[A-Z]{2}-\d{4,6}$")
# A US state abbreviation + ZIP code (+ optional "USA") with nothing else
# attached ("PA 15213 USA", "PA 15213") -- same bare-address-fragment cause
# as BARE_STATE_ZIP_RE above, just space- instead of hyphen-separated.
BARE_STATE_ZIP_USA_RE = re.compile(r"^[A-Z]{2}\s+\d{5}(-\d{4})?(\s+USA)?$")

# A bare domain (no http(s):// scheme, so the existing URL check above
# misses it) glued onto the name, e.g. "SE3 Labs
# philippwulff.github.io/dream-to-recon" -- a project page URL leaking into
# the affiliation field, not part of the name.
BARE_DOMAIN_RE = re.compile(r"[a-zA-Z0-9-]+\.(github\.io|com|org|net|edu|io)\b")

# A field label leaking in from a mis-split "affiliation, email" author
# block ("USAEmail:", "Email: foo@bar.com", "e-mail:") -- the hyphenated
# spelling is common enough on its own to need matching too (user-flagged:
# "Canada. e-mail:" survived as an institution because the un-hyphenated
# pattern didn't match it).
EMAIL_LABEL_RE = re.compile(r"e-?mail\s*:", re.I)

# A street address, not an institution ("200 University Ave W", "333 Avenue
# Georges Clemenceau", "7-3-1 Hongo") -- a leading house number (plain or
# hyphenated block-lot form) followed by more text. Deliberately narrow
# (requires the string to START with a digit) so it can't reject a real
# institution name that happens to contain a number later on (e.g. "3M"
# or "Toyota Research Institute" pass through untouched).
STREET_ADDRESS_RE = re.compile(r"^\d+[\d\-]*\s+\S")

# A raw LaTeX command name leaking through unrendered (e.g. "Samsung R&D
# Institute China...00footnotetext: \dagger The first two authors
# contributed equally...") -- confirmed real: an entire footnote sentence
# glued onto an institution name via a \footnotetext command that never got
# stripped during PDF/LaTeX-source extraction.
FOOTNOTETEXT_RE = re.compile(r"footnotetext", re.I)

# A footnote sentence describing WHEN work was done, not an institution name
# (e.g. "Work done while at Uber Advanced Technologies Group", "Work done at
# Ulm University") -- an author-block footnote that got parsed into the
# affiliation field instead of staying attached to its footnote marker. The
# real institution it names is usually already credited separately via the
# author's actual affiliation entry, so this is dropped rather than
# stripped-and-kept: the sentence wrapping means normalize_institution can't
# safely extract just the institution name from arbitrary phrasing.
WORK_DONE_FOOTNOTE_RE = re.compile(r"^work (done|performed|conducted)\b", re.I)
# A "this work was done/performed/conducted..." aside can also appear
# mid-string, not just at the very start (e.g. glued onto a real
# institution name by a failed footnote split: "...Nanyang Technological
# University, Singapore. This work was done during Z. Huang's visit to the
# University of California, Berkeley." -- confirmed on real data), unlike
# WORK_DONE_FOOTNOTE_RE above which only catches it as the whole string.
WORK_DONE_ASIDE_RE = re.compile(r"\bthis work was (done|performed|conducted)\b", re.I)

# A full author-affiliation SENTENCE ("Z. Huang and C. Lv are with the
# School of Mechanical and Aerospace Engineering...") leaking into the
# affiliations list whole, instead of being split down to just the
# institution name -- confirmed on real data from a garbled arXiv-HTML
# footnote parse. "School of" (in INSTITUTION_HINTS-style matching
# upstream) is itself a real institution-name substring, so this can't be
# rejected on keyword content alone; the "X and Y are/is with the" phrasing
# is the actual tell that it's a sentence fragment, not a name.
# Up to a few interjected words between "are/is" and "with" ("are all with",
# "is currently with") -- confirmed on real data: "Yaru Niu and Ding Zhao are
# all with the Department of Mechanical Engineering" survived because the
# original exact-adjacency version only matched "are with", not "are all
# with".
AFFILIATION_SENTENCE_RE = re.compile(r"\b(are|is)\b(?:\s+\S+){0,3}\s+with\b", re.I)
# Likewise "Corresponding Author: <name>", "Equal contribution", "Indicates
# corresponding author", etc. are byline credits, not institutions -- unlike
# CORRESPONDING_AUTHOR_RE (kept for the plain "Corresponding Author: ..."
# case), this one searches anywhere in the string, not just an exact-anchor
# start, since these also show up wrapped in other footnote punctuation
# (confirmed on real data: "Equal contributions; Corresponding author",
# "\dagger indicates the corresponding author"). A bare postal/zip code with
# nothing else attached ("639798") is the kind of thing that should have
# stayed paired with the institution name it followed, not survived alone as
# its own entry.
CORRESPONDING_AUTHOR_RE = re.compile(r"^corresponding author\b", re.I)
# Broadened to any remaining "correspond(ing/ence)" mention at all, not just
# the "indicates ... correspond"/"correspondence:" phrasings -- by the time
# this runs (after normalize_institution's strip passes), a real institution
# name glued to corresponding-author text via a footnote MARKER has already
# been cleanly separated by strip_trailing_footnote_junk's marker-delimited
# case above ("Huawei † Corresponding author" -> "Huawei"). What's left
# still containing "correspond" at this point is prose with no clean
# boundary to split on -- confirmed on real data as pure junk with no
# institution content worth keeping ("USA; corresponding author email",
# "Correspondence", "the USA (corresponding author)", "Corresponding
# authors:", "MITCorrespondance:") -- so the whole string is rejected
# rather than guessing at which part might be a real name. No \b before
# "correspond" -- same reasoning as TRAILING_FOOTNOTE_ARTIFACT_RE above,
# the contamination is sometimes glued on with no space at all
# ("FranceCorresponding", "MITCorrespondance:"), which a word-boundary
# requirement would miss.
CREDIT_LINE_RE = re.compile(r"\bequal contributions?\b|correspond", re.I)
BARE_POSTAL_CODE_RE = re.compile(r"^\d{4,6}$")
# A bare street NUMBER, not a postal code -- comma-split addresses leave
# these behind the same way ("Korea Advanced Institute of Science and
# Technology, 193, Munji-ro, ..." split into a "193" fragment separate from
# "Munji-ro"). Narrower digit range than BARE_POSTAL_CODE_RE above (which
# only starts at 4 digits) since a real street number is usually 1-3 digits.
BARE_STREET_NUMBER_RE = re.compile(r"^\d{1,3}$")
# A sentence fragment continuing from a previous, separately-split footnote
# ("and Diange Yang are with State Key Laboratory...", "and also with the
# Department of..."), not a name in its own right -- English institution
# names never legitimately start with "and".
LEADING_AND_RE = re.compile(r"^and\b", re.I)
# A PDF/proceedings-hosting credit line ("Copyright ... provided by the
# Computer Vision Foundation"), not an author affiliation -- user-flagged.
PROVIDED_BY_RE = re.compile(r"\bprovided by\b", re.I)

# Multiple co-authors' institutions glued into one string with no separator
# preserved, e.g. "Cleveland State University Nanyang Technological
# University1" or "East China Normal University Nanyang Technological
# University" -- an author-block splitting bug (confirmed on real data: 22
# distinct instances) where several authors' one-line affiliations end up
# concatenated instead of split per-author. A single legitimate institution
# name essentially never contains "University" (or "Institute of
# Technology") twice, so 2+ occurrences is a reliable signal this is several
# names glued together, not one real institution -- except the one
# confirmed real exception below, whose OWN official name happens to.
# "Laboratory" added alongside University/Institute of Technology --
# confirmed on real data: "RealAI Peng Cheng Laboratory Pazhou Laboratory
# (Huangpu)" is three separate organizations (a company plus two unrelated
# labs) glued together with no separator, same failure mode as a
# multi-university string, just using a different repeated noun.
DOUBLE_UNIVERSITY_RE = re.compile(r"(University|Institute of Technology|Laboratory)", re.I)
MULTI_UNIVERSITY_ALLOWLIST = {"University at Buffalo, State University of New York"}


def is_concatenated_multi_institution(name):
    if name in MULTI_UNIVERSITY_ALLOWLIST:
        return False
    return len(DOUBLE_UNIVERSITY_RE.findall(name)) >= 2


def strip_trailing_footnote_junk(name):
    """Removes a "Corresponding author"/\\dagger/� footnote glued onto
    the end of a real institution name. Below MIN_STRIPPED_INSTITUTION_LENGTH,
    returns `name` UNCHANGED rather than the short leftover fragment --
    "Indicates corresponding author" must still reach is_valid_institution()
    with the word "correspond" intact so CREDIT_LINE_RE there can reject the
    whole thing, not arrive as a bare "Indicates" that no longer matches
    anything and would otherwise slip through as a fake institution.

    Exception: when a real footnote MARKER (†‡*§¶✉⋆∗) directly precedes the
    matched text ("Huawei † Corresponding author", "NVIDIA ✉ Correspondence
    Authors"), the length gate is skipped -- the marker itself is strong,
    unambiguous evidence of a "name, then footnote" boundary, unlike prose
    that merely happens to be short. Without this, "Huawei"/"NVIDIA" (6
    characters) never passed the gate and the whole junk string survived as
    its own fake institution (confirmed on real data)."""
    m = TRAILING_FOOTNOTE_ARTIFACT_RE.search(name)
    if not m:
        return name
    prefix = name[:m.start()].strip().rstrip(".,;:").strip()
    marker_delimited = bool(re.search(r"[†‡*§¶✉⋆∗]\s*$", name[:m.start()]))
    if marker_delimited:
        prefix = re.sub(r"[†‡*§¶✉⋆∗]\s*$", "", prefix).strip()
    return prefix if marker_delimited or len(prefix) >= MIN_STRIPPED_INSTITUTION_LENGTH else name


def normalize_institution(name):
    name = html.unescape((name or "").strip()).strip().rstrip(".").strip()
    name = fix_mojibake_diacritics(name)
    name = TRAILING_COUNTRY_RE.sub("", name).strip()
    name = FOOTNOTE_MARKER_RE.sub("", name).strip()
    name = strip_trailing_footnote_junk(name).strip()
    # Bare trailing marker with no accompanying footnote text ("Singapore
    # †", "Pengcheng Laboratory †") -- strip_trailing_footnote_junk above
    # only fires when a trigger word (correspond/dagger/footnotetext/�)
    # follows the marker; a marker with nothing after it needs this
    # separate pass. Re-strip a trailing period afterward ("Nankai
    # University. †" -> "Nankai University." -> "Nankai University").
    name = TRAILING_FOOTNOTE_MARKER_RE.sub("", name).strip().rstrip(".").strip()
    name = LEADING_ARTICLE_RE.sub("", name).strip()
    name = LEADING_FOOTNOTE_NUMBER_RE.sub("", name).strip()
    name = NEXTAFF_PREFIX_RE.sub("", name).strip()
    name = LATEX_SPACING_PREFIX_RE.sub("", name).strip()
    name = LATEX_SPACING_SUFFIX_RE.sub("", name).strip()
    name = TRAILING_LEGAL_SUFFIX_RE.sub("", name).strip()
    # A bare underscore used as a word-join in place of a space or hyphen
    # (e.g. "HKISI_CAS") -- not part of the institution's actual name.
    name = name.replace("_", " ")
    name = re.sub(r"\s+", " ", name).strip()
    name = INSTITUTION_ALIASES.get(name, name)
    return INSTITUTION_ALIASES_LLM.get(name, name)


# A keyword strongly indicating a degree-granting or public-research
# institution -- deliberately does NOT include the bare word "Institute" on
# its own, since plenty of corporate R&D arms use it too (Toyota Research
# Institute, Bosch Center for Artificial Intelligence's sibling labs, ...);
# those are handled by the explicit override lists below instead of a
# keyword that would misclassify them.
ACADEMIC_KEYWORD_RE = re.compile(
    r"\b(Universit|College|Institute of Technology|Technical University|"
    r"Ecole|École|Politecnico|Polytechnic|ETH |EPFL|CNRS|Academy of Sciences|"
    r"Hochschule|Universitat)\b", re.I,
)
# Named research institutes/labs that are academic or nonprofit despite not
# matching the keyword regex above (no "University" in the name, and some
# use "Institute" the way a company R&D arm would).
ACADEMIC_INSTITUTION_OVERRIDES = {
    "max planck institute", "max planck society", "allen institute for ai",
    "allen institute for artificial intelligence", "ai2", "vector institute",
    "mila", "mila - quebec ai institute", "idiap research institute", "idiap",
    "inria", "riken", "nict", "kist", "csiro", "nrc", "national research council",
    "dfki", "german research center for artificial intelligence",
    "toyota technological institute at chicago", "ttic",
    "weizmann institute of science", "italian institute of technology", "iit",
    "chinese academy of sciences", "korea advanced institute of science and technology",
    "kaist", "indian institute of technology", "indian institute of science", "iisc",
    "tata institute of fundamental research", "broad institute", "flatiron institute",
    "fraunhofer", "fraunhofer institute", "helmholtz association",
    "shanghai ai laboratory", "shanghai artificial intelligence laboratory",
    "korea institute of science and technology",
}
# Company/industry R&D names, including ones that would otherwise misread
# as academic under the keyword regex ("Toyota Research Institute") or that
# don't match it at all. Not exhaustive -- covers the AV/robotics/CV
# industry players that actually show up repeatedly in this corpus's own
# institution leaderboard; a real, checkable methodology limit, not hidden
# (see about.html). An institution matching neither this nor the academic
# side is left unclassified rather than guessed at.
INDUSTRY_INSTITUTION_OVERRIDES = {
    "waymo", "tesla", "nvidia", "nvidia research", "baidu", "huawei",
    "mercedes-benz", "mercedes-benz ag", "bmw", "bmw group", "motional",
    "nutonomy", "aptiv", "amazon", "google", "google research", "google deepmind",
    "deepmind", "microsoft", "microsoft research", "apple", "qualcomm", "uber",
    "uber atg", "uber advanced technologies group", "cruise", "cruise llc",
    "zoox", "nuro", "aurora", "aurora innovation", "argo ai", "valeo",
    "continental", "denso", "hyundai", "hyundai motor group", "samsung",
    "samsung research", "intel", "intel labs", "meta", "meta ai", "facebook",
    "facebook ai research", "fair", "sony", "sony ai", "honda",
    "honda research institute", "ford", "ford motor company", "general motors",
    "gm", "nio", "xpeng", "didi", "didi chuxing", "tusimple", "pony.ai",
    "wayve", "five ai", "horizon robotics", "sensetime", "megvii", "momenta",
    "plus.ai", "bosch", "bosch center for artificial intelligence", "toyota",
    "toyota research institute", "volkswagen", "audi", "porsche", "stellantis",
    "renault", "nissan", "jaguar land rover", "great wall motors", "byd",
    "geely", "faraday future", "rivian", "lucid motors", "ibm", "ibm research",
    "adobe", "adobe research", "alibaba", "tencent", "jd.com", "oppo",
    "xiaomi", "lyft", "here technologies", "mapbox", "luminar", "innoviz",
    "mobileye", "zf friedrichshafen", "magna international", "hitachi",
    "panasonic", "lg electronics", "lg ai research", "airbus", "boeing",
    "siemens", "abb", "kuka", "anthropic", "openai", "salesforce",
    "salesforce research", "criteo", "naver", "naver labs", "kakao", "line",
    "yandex", "preferred networks", "flir", "flir systems", "avl", "hexagon",
    "trimble", "cepton", "innovusion", "hesai", "quanergy", "velodyne",
    "ouster", "aeva", "aeye",
}


def classify_institution_sector(name):
    """"academic" / "industry" / None (unclassified). A heuristic, not a
    verified ground truth -- an explicit override list settles the cases the
    keyword regex would get wrong in either direction (a company R&D arm
    named "... Institute", a nonprofit research institute with no
    "University" in its name), but plenty of real institutions match
    neither and are deliberately left unclassified rather than guessed at.
    See categories.html's Industry column for where this is used."""
    key = (name or "").strip().lower()
    if key in INDUSTRY_INSTITUTION_OVERRIDES:
        return "industry"
    if key in ACADEMIC_INSTITUTION_OVERRIDES:
        return "academic"
    if ACADEMIC_KEYWORD_RE.search(name or ""):
        return "academic"
    return None


def is_valid_institution(name):
    if not name or name in INVALID_INSTITUTIONS or name in INVALID_INSTITUTIONS_LLM:
        return False
    if "http://" in name or "https://" in name:
        return False
    if BARE_DOMAIN_RE.search(name):
        return False
    if EMAIL_LABEL_RE.search(name):
        return False
    if OBFUSCATED_EMAIL_RE.search(name):
        return False
    if FUNDING_CREDIT_RE.search(name):
        return False
    if PERSON_INITIAL_NAME_RE.match(name):
        return False
    if WORK_DONE_FOOTNOTE_RE.match(name):
        return False
    if WORK_DONE_ASIDE_RE.search(name):
        return False
    if AFFILIATION_SENTENCE_RE.search(name):
        return False
    if CORRESPONDING_AUTHOR_RE.match(name):
        return False
    if CREDIT_LINE_RE.search(name):
        return False
    if LEADING_AND_RE.match(name):
        return False
    if PROVIDED_BY_RE.search(name):
        return False
    if BARE_STREET_NUMBER_RE.match(name):
        return False
    if BARE_POSTAL_CODE_RE.match(name):
        return False
    if is_concatenated_multi_institution(name):
        return False
    if FOOTNOTETEXT_RE.search(name):
        return False
    if BARE_STATE_ZIP_RE.match(name):
        return False
    if BARE_STATE_ZIP_USA_RE.match(name):
        return False
    # A leading house number/postal code ("44227 Dortmund", "200 University
    # Ave W", "333 Avenue Georges Clemenceau") or a street-address suffix
    # ("Str. 1", "Straße 12") -- an address is not an institution name.
    if STREET_ADDRESS_RE.match(name):
        return False
    if TRAILING_POSTAL_CODE_RE.search(name):
        return False
    if re.search(r"(Str\.|Stra(ss|ß)e)\s*\d", name):
        return False
    if SUBUNIT_PREFIX_RE.match(name) and "university" not in name.lower() \
            and "institute of technology" not in name.lower():
        return False
    # A bare country name with nothing else attached ("Bulgaria") -- same
    # comma-split cause as the bare-country/bare-city entries in
    # INVALID_INSTITUTIONS above, just checked against the full country list
    # (COUNTRY_NAMES) instead of manually duplicating it here.
    if name in COUNTRY_NAMES.values():
        return False
    # The Unicode replacement character -- unlike MOJIBAKE_DIACRITIC_PAIRS'
    # double-encoding artifacts (reversible, since the original bytes are
    # still there just wrongly interpreted), U+FFFD means the original byte
    # sequence was already discarded by an earlier lossy decode. There's no
    # way to recover which character it was, so a name that still contains
    # one after fix_mojibake_diacritics -- e.g. "Universit� de
    # Technologie de Compi�gne" (should be "Université ... Compiègne")
    # -- is rejected rather than shown with a visible mangled glyph in it.
    if "�" in name:
        return False
    # An unmatched parenthesis -- a real institution's own name always opens
    # and closes any parenthetical it uses ("Technology and Research
    # (A*STAR)", "University at Buffalo (SUNY)"). A lone "(" with nothing to
    # close it is what's left of a footnote/sentence fragment that got cut
    # off mid-string by an earlier split or strip (confirmed on real data:
    # "China. (Yu Pan* is the", "Cleveland State University (*" -- the
    # latter only surfaces this way after FOOTNOTE_MARKER_RE above strips
    # the trailing "*", exposing the dangling "(" it was attached to).
    if name.count("(") != name.count(")"):
        return False
    return True


def author_country_codes(a):
    affs = a.get("affiliations") or []
    bad = {code for inst, code in COUNTRY_MISLABELS if inst in affs}
    return [c for c in (a.get("countries") or []) if c not in bad]


def _looks_like_a_coauthor_with_garbage_suffix(candidate, co_author_names):
    # The co-author's name plus a short garbage suffix the affiliation-block
    # parser glued on ("Jyh-Jing Hwang22footnotemark: 2") -- an exact match
    # is handled separately (cheap set lookup); this is the more expensive
    # prefix scan, so it only ever runs over the handful of co-authors on
    # ONE paper, never the whole corpus's author list. Capped at 25 extra
    # characters so this can't accidentally reject a real institution that
    # merely starts with the same words as someone's name (no real
    # institution name is this long past a person's name and isn't itself
    # also a person-name match).
    return any(candidate.startswith(n) and len(candidate) - len(n) <= 25 for n in co_author_names)


def author_affiliations(a, all_author_names=None, co_author_names=None):
    normalized = (normalize_institution(aff) for aff in (a.get("affiliations") or []))
    candidates = (n for n in normalized if is_valid_institution(n))
    # A person's name leaking into an "institution" field -- confirmed on
    # real data as a systemic pattern, not a one-off: a garbled
    # affiliation-block parse regularly glues a name (a co-author's, or even
    # someone NOT on this specific paper -- e.g. a whole team's byline
    # attached to one author) onto the affiliation list instead of the
    # institution it belongs with ("Ben Sapp", "Christos Sakaridis", dozens
    # more). all_author_names is every real author name in the WHOLE
    # corpus (computed once, see the top of main()) -- an exact-match
    # reject, not a name-SHAPE heuristic ("two capitalized words, no
    # institutional keyword" would also reject real short institution names
    # like "ETH Zurich"/"Johns Hopkins"/"UC Berkeley"). co_author_names (just
    # this paper's own author list) additionally catches a co-author's name
    # with a garbled suffix still attached, which wouldn't exact-match.
    if all_author_names:
        candidates = (n for n in candidates if n not in all_author_names)
    if co_author_names:
        candidates = (n for n in candidates if not _looks_like_a_coauthor_with_garbage_suffix(n, co_author_names))
    # dict.fromkeys, not a set: a paper crediting the same normalized name
    # twice (e.g. "Baidu Inc." and "Baidu Research" on one author) should
    # only count once, but insertion order doesn't matter here since callers
    # only ever use this as a flat list to count occurrences over.
    return list(dict.fromkeys(candidates))


def is_fully_processed(e):
    """A paper must have a real title and a publication year before it's
    eligible to appear on the site at all. An abstract is NOT required --
    RSS/ICLR/AAAI (via fetch_dblp_listing.py) and DBLP-sourced CVF gap-fills
    are title+authors+year only, DBLP doesn't have abstracts at all, and
    classify.py already falls back to title-only keyword matching for these
    (see classify_relevance's abstract_l = abstract or ""). Requiring an
    abstract here would silently drop every paper from those sources instead
    of showing them with whatever signal is actually available -- excluding
    a paper because a source it came from doesn't have a field is a stronger
    claim than "classified on incomplete information."
    """
    return bool((e.get("title") or "").strip()) and e.get("year") is not None



def compute_insights(papers, all_entries, citation_graph, category_stats,
                      top_authors_avg, top_authors_total, top_institutions, datasets=None,
                      author_lifetimes=None):
    """Everything in here is a genuinely computed finding over the real
    corpus, not a canned template -- see insights.html for how each field
    renders. Kept as a standalone function (not inlined into main()) so it's
    unit-testable against small fixture data, same convention as every other
    pure computation in this file.

    The single most recent year is always a partial year (this script runs
    mid-year) -- excluded outright from every year-based figure here, not
    just flagged, since a partial year compared or plotted next to complete
    ones reads as a fake trend rather than "still being collected."
    """
    insights = {}

    years_present = sorted({p["year"] for p in papers if p.get("year")})
    latest_year = years_present[-1] if years_present else None
    complete_years = [y for y in years_present if y != latest_year]
    core_by_year = Counter(p["year"] for p in papers if p.get("year"))
    total_by_year = Counter(e["year"] for e in all_entries if e.get("year"))

    # -- Growth: average annual growth rate over the last several COMPLETE
    # years (a proper compound annual growth rate, not an arithmetic mean of
    # single-year percentages, which over- or under-weights whichever year
    # happened to be noisiest) -- one bad or one great year doesn't dominate
    # the headline number the way a single year-over-year comparison would. --
    GROWTH_WINDOW_YEARS = 5
    growth_years = complete_years[-GROWTH_WINDOW_YEARS:]
    insights["corpus_growth"] = {
        "years": growth_years,
        "core_papers": [core_by_year[y] for y in growth_years],
        "total_papers": [total_by_year.get(y, 0) for y in growth_years],
    }
    if len(growth_years) >= 2 and core_by_year[growth_years[0]]:
        n_intervals = len(growth_years) - 1
        cagr = (core_by_year[growth_years[-1]] / core_by_year[growth_years[0]]) ** (1 / n_intervals) - 1
        insights["avg_annual_growth"] = {
            "from_year": growth_years[0], "to_year": growth_years[-1],
            "pct": round(cagr * 100, 1),
        }

    # -- Self-citations: how much of the raw citation graph they actually
    # were, now that they're excluded from every count on the site -- a
    # concrete number for a decision that's otherwise just asserted. --
    by_norm_title = {normalize_title(p["title"]): p for p in papers}
    total_edges = 0
    self_edges = 0
    for citer_key, cited_keys in (citation_graph.get("edges") or {}).items():
        citer = by_norm_title.get(citer_key)
        if not citer:
            continue
        citer_authors = set(citer.get("authors") or [])
        for cited_key in cited_keys:
            target = by_norm_title.get(cited_key)
            if not target:
                continue
            total_edges += 1
            if citer_authors & set(target.get("authors") or []):
                self_edges += 1
    if total_edges:
        insights["self_citation_rate_pct"] = round(100 * self_edges / total_edges, 1)
        insights["self_citation_counts"] = {"total_edges": total_edges, "self_edges": self_edges}

    # -- Citation concentration: does impact follow a power law (a small
    # share of papers holding most of the citations) the way it does in
    # most citation networks, or is this corpus flatter than that. --
    cited = sorted((p["citations"] for p in papers if p.get("citations")), reverse=True)
    if cited:
        total_citations = sum(cited)
        top1pct_n = max(1, round(len(cited) * 0.01))
        top10pct_n = max(1, round(len(cited) * 0.10))
        insights["citation_concentration"] = {
            "cited_papers": len(cited),
            "top1pct_share_pct": round(100 * sum(cited[:top1pct_n]) / total_citations, 1),
            "top10pct_share_pct": round(100 * sum(cited[:top10pct_n]) / total_citations, 1),
        }

    # A short, human-recognizable name for a paper -- the curated dataset
    # name (nuScenes, KITTI, ...) takes priority over the general
    # title-structure-based paper_short_name() below, since it's a verified
    # real-world name rather than a guess from title punctuation. Computed
    # here too (not just reused from papers[i]["short_name"]) so the
    # Insights page never has to display a full paper title as the headline
    # of a "did you know" fact.
    dataset_name_by_title = {}
    for d in (datasets or []):
        dataset_name_by_title[d["paper_title"]] = d["name"]
        for extra in d.get("also_introduced_in") or []:
            dataset_name_by_title[extra["title"]] = d["name"]

    def short_paper_name(p):
        if p["title"] in dataset_name_by_title:
            return dataset_name_by_title[p["title"]]
        return p.get("short_name") or paper_short_name(p.get("title"), p.get("authors"), p.get("year"), p.get("abstract"))

    # -- Single standout data points -- the "did you know" facts. Each
    # carries the full title too (for the frontend's link href), but the
    # short_title is what's meant to actually be displayed as text. --
    cited_papers = [p for p in papers if p.get("citations")]
    if cited_papers:
        top_paper = max(cited_papers, key=lambda p: p["citations"])
        insights["most_cited_paper"] = {
            "title": top_paper["title"], "short_title": short_paper_name(top_paper),
            "citations": top_paper["citations"], "year": top_paper.get("year"),
        }
    collab_papers = [p for p in papers if p.get("institutions") and len(p["institutions"]) > 1]
    if collab_papers:
        p = max(collab_papers, key=lambda p: len(p["institutions"]))
        insights["most_collaborative_paper"] = {
            "title": p["title"], "short_title": short_paper_name(p),
            "institution_count": len(p["institutions"]), "year": p.get("year"),
        }
    intl_papers = [p for p in papers if p.get("countries") and len(p["countries"]) > 1]
    if intl_papers:
        p = max(intl_papers, key=lambda p: len(p["countries"]))
        insights["most_international_paper"] = {
            "title": p["title"], "short_title": short_paper_name(p),
            "country_count": len(p["countries"]), "countries": sorted(p["countries"]),
            "year": p.get("year"),
        }

    # -- Disruption/consolidation: does later work treat this paper as
    # superseding its own references (disruptive) or as extending them
    # alongside those references (consolidating)? See compute_disruption_
    # index()'s own docstring for the method (Funk & Owen-Smith 2017) and
    # the min-informative-citers guard -- p["cd_index"]/p["cd_n_citers"] are
    # already set on `papers` by main() before this function runs, only for
    # papers with enough scored citers to be meaningful.
    scored = [p for p in papers if p.get("cd_index") is not None]
    if scored:
        def disruption_entry(p):
            return {
                "title": p["title"], "short_title": short_paper_name(p),
                "year": p.get("year"), "venue": p.get("venue"), "cd_index": p["cd_index"],
                "n_citers": p.get("cd_n_citers"),
            }
        # cd_index is only ever one of a handful of exact fractions (n
        # citers -> n+1 possible scores -- 3 citers can only ever land on
        # -1, -0.33, 0.33, or 1), so a huge share of the corpus is tied at
        # the exact extremes: confirmed on real data, 2,197 papers tied at
        # exactly +1.0 and 1,669 at exactly -1.0 out of 5,249 scored papers
        # (user-flagged: "probably thousands of works are equally
        # irrelevant"). Sorting on cd_index alone left the tie order an
        # accident of corpus insertion order -- effectively a random pick
        # from a ~2,000-way tie, not a real "most disruptive" ranking.
        # n_citers as the tiebreak fixes this: among papers that land on the
        # same score, the one with MORE citers unanimously agreeing is
        # stronger evidence, not an arbitrary pick -- confirmed this surfaces
        # actual landmark papers (KITTI, CARLA, BEVFormer) for "most
        # disruptive" instead of obscure ones nobody's heard of.
        most_disruptive = sorted(scored, key=lambda p: (-p["cd_index"], -(p.get("cd_n_citers") or 0)))
        most_consolidating = sorted(scored, key=lambda p: (p["cd_index"], -(p.get("cd_n_citers") or 0)))
        # Sends enough rows for the page's "Show" dropdown to slice from
        # client-side (same pattern as top_authors/top_institutions) rather
        # than being hardcoded to exactly 5 with no way to see more.
        DISRUPTION_LIST_CAP = 50
        insights["disruption_index"] = {
            "scored_papers": len(scored),
            "mean_cd_index": round(sum(p["cd_index"] for p in scored) / len(scored), 3),
            "most_disruptive": [disruption_entry(p) for p in most_disruptive[:DISRUPTION_LIST_CAP]],
            "most_consolidating": [disruption_entry(p) for p in most_consolidating[:DISRUPTION_LIST_CAP]],
        }

    # -- Open-source signal: does releasing code correlate with citation
    # impact, and has doing so become more common over time? Restricted
    # throughout to CHECKED papers (has_code_link is not None) -- comparing
    # "has code" against "everyone else" would silently include papers this
    # site never even looked at, biasing the comparison in an unknowable
    # direction. See fetch_affiliations_arxiv.py's detect_code_link for how
    # this is found (a free byproduct of the same ar5iv page fetched for
    # affiliations/references, arXiv-sourced papers only).
    checked = [p for p in papers if p.get("has_code_link") is not None]
    OPEN_SOURCE_MIN_CHECKED = 20
    if len(checked) >= OPEN_SOURCE_MIN_CHECKED:
        with_code = [p for p in checked if p["has_code_link"]]

        def avg_citations(group):
            cited = [p["citations"] for p in group if p.get("citations") is not None]
            return round(sum(cited) / len(cited), 1) if cited else None

        open_source = {
            "checked_papers": len(checked),
            "with_code_pct": round(100 * len(with_code) / len(checked), 1),
            "avg_citations_with_code": avg_citations(with_code),
            "avg_citations_without_code": avg_citations([p for p in checked if not p["has_code_link"]]),
        }
        # By year -- only years with enough checked papers to say anything;
        # the latest (partial) year is excluded, same reasoning as
        # avg_team_size_by_year below.
        by_year = defaultdict(lambda: {"checked": 0, "with_code": 0})
        for p in checked:
            if p.get("year") and p["year"] != latest_year:
                by_year[p["year"]]["checked"] += 1
                if p["has_code_link"]:
                    by_year[p["year"]]["with_code"] += 1
        open_source["with_code_pct_by_year"] = [
            {"year": y, "pct": round(100 * v["with_code"] / v["checked"], 1)}
            for y, v in sorted(by_year.items()) if v["checked"] >= OPEN_SOURCE_MIN_CHECKED
        ]
        insights["open_source"] = open_source

    # -- People: reuse the same rankings already computed for the
    # leaderboards (top_authors_by_avg / top by total), just the first
    # couple entries -- "leading" means the same thing here as it does on
    # the Authors page, not a separately-invented definition. top_authors_avg
    # here is deliberately a STRICTER ranking than the Authors page's own
    # (min 10 papers, not min 1) -- an author with 2 papers and one lucky hit
    # topping "highest average impact" isn't a claim this page should make. --
    if top_authors_total:
        insights["most_prolific_author"] = top_authors_total[0]
    if top_authors_avg:
        insights["highest_impact_author"] = top_authors_avg[0]
    if top_institutions:
        insights["leading_institution"] = top_institutions[0]

    # Topics: rising vs declining used to be precomputed here (a fixed
    # multi-year window average); it's now computed client-side in
    # insights.html instead, from a single recent year against a single
    # prior year, N years back where N is a reader-adjustable dropdown --
    # nothing here can offer that without either baking in a fixed set of
    # windows or bloating stats.json with every possible N.

    # -- Team size over time: has AV research become more or less
    # collaborative, in terms of raw author-list length. Latest (partial)
    # year excluded -- same reasoning as corpus_growth above. --
    team_size_by_year = defaultdict(list)
    for p in papers:
        if p.get("year") and p.get("year") != latest_year and p.get("authors"):
            team_size_by_year[p["year"]].append(len(p["authors"]))
    insights["avg_team_size_by_year"] = [
        {"year": y, "avg_authors": round(sum(sizes) / len(sizes), 1)}
        for y, sizes in sorted(team_size_by_year.items()) if sizes
    ]

    # -- Venue AV-relevance ratio: what fraction of EACH venue's total
    # (unfiltered) output is actually AV-relevant -- extremes in both
    # directions are the interesting part (a venue that's almost entirely
    # AV vs. one where AV work is a rounding error). arXiv is excluded: its
    # "venue" population here isn't a real random sample of arXiv the way
    # the other venues are complete conference/journal proceedings -- it's
    # cherry-picked (an AV-author-focused pull plus reverse-citation
    # discovery, see the pipeline in About), so its AV-relevance ratio isn't
    # comparable to a venue whose full membership was actually collected. --
    venue_totals = Counter(e["venue"] for e in all_entries if e.get("venue") and e["venue"] != "arXiv")
    venue_core = Counter(e["venue"] for e in all_entries if e.get("venue") and e["venue"] != "arXiv"
                          and e.get("av_relevance") == "core")
    # Ships the full list (not just top/bottom 3) at a shippable floor of 10
    # total papers -- Insights' own "how many papers" threshold is an
    # adjustable dropdown (user-requested, default 80), not a fixed cutoff,
    # so the client needs every venue that could plausibly qualify at any of
    # the dropdown's choices, not just whatever was highest/lowest at one
    # fixed threshold.
    venue_relevance = [
        {"venue": v, "total_papers": n, "core_papers": venue_core.get(v, 0),
         "av_relevance_pct": round(100 * venue_core.get(v, 0) / n, 1)}
        for v, n in venue_totals.items() if n >= 10
    ]
    venue_relevance.sort(key=lambda v: v["av_relevance_pct"], reverse=True)
    insights["venue_relevance"] = venue_relevance

    # -- Researcher lifetime: the span between an author's first and last
    # AV-relevant publication in this corpus (0 for someone who's only
    # published in a single year so far) -- user-requested, as a proxy for
    # career stage within THIS corpus specifically (not a real age, and not
    # necessarily their whole career: someone active well before this
    # corpus's earliest covered year, or outside the covered venues, reads
    # as having a shorter lifetime here than they really do). --
    if author_lifetimes:
        MAX_LIFETIME_BUCKET = 14
        # -- Distribution: how many researchers fall at each lifetime,
        # capped at MAX_LIFETIME_BUCKET (lumping longer careers into one
        # "14+" bucket rather than a long tail of buckets with 1-2 people
        # each). --
        lifetime_counts = Counter(min(a["lifetime"], MAX_LIFETIME_BUCKET) for a in author_lifetimes.values())
        insights["researcher_lifetime_distribution"] = [
            {"lifetime": lt, "researchers": lifetime_counts.get(lt, 0)}
            for lt in range(MAX_LIFETIME_BUCKET + 1)
        ]

        # -- Citations by lifetime, as percentiles rather than mean+-stdev --
        # only over authors with at least one cited paper (an author with
        # zero known citation data isn't a real 0, see the None-vs-0
        # convention used everywhere else on this site), and only for
        # lifetime buckets with enough authors for the figure to mean
        # anything -- a bucket with 1-2 people isn't a distribution, so a
        # small-N figure is suppressed rather than shown misleadingly
        # precise.
        #
        # Mean+-1stdev used to be shown here, but citation counts are
        # heavily right-skewed (a handful of breakout papers inflate stdev
        # far past the mean for almost every bucket) -- mean-stdev routinely
        # went negative, got clamped to 0 for the chart's log scale, and the
        # lower band edge ended up pinned to the axis for nearly every point
        # (user-reported: "the lower end of the stdev is not visible").
        # Percentiles don't have that failure mode -- p25/p75 are always
        # real values actually present in the data, never a clamped
        # artifact -- and are the more honest summary of a skewed
        # distribution anyway (the median isn't dragged around by the same
        # handful of outliers the mean is).
        MIN_AUTHORS_FOR_LIFETIME_STATS = 5
        citations_by_lifetime = defaultdict(list)
        for a in author_lifetimes.values():
            if a["lifetime"] <= MAX_LIFETIME_BUCKET and a["cited_papers"] > 0:
                citations_by_lifetime[a["lifetime"]].append(a["citations"])

        def quartiles(vals):
            vals = sorted(vals)
            q1, median, q3 = statistics.quantiles(vals, n=4, method="inclusive")
            return round(q1, 1), round(median, 1), round(q3, 1)

        insights["citations_by_researcher_lifetime"] = [
            dict(zip(("lifetime", "researchers", "p25_citations", "median_citations", "p75_citations"),
                     (lt, len(vals)) + quartiles(vals)))
            for lt, vals in sorted(citations_by_lifetime.items())
            if len(vals) >= MIN_AUTHORS_FOR_LIFETIME_STATS
        ]

        # -- Most promising young researchers: short career span (early
        # career, within this corpus) but already publishing at a real clip,
        # and still active -- last publication in the last complete year,
        # not someone who published a burst of papers years ago and stopped.
        # Ranked by average citations per paper, same definition used
        # everywhere else on this site (highest_impact_author above), not
        # raw paper count. cited_papers > 0 is a technical floor, not a
        # business rule (avoids a division by zero below).
        YOUNG_MAX_LIFETIME = 3
        YOUNG_MIN_PAPERS = 5
        last_complete_year = complete_years[-1] if complete_years else None
        young = [
            {"name": name, "papers": a["papers"], "citations": a["citations"],
             "avg_citations": round(a["citations"] / a["cited_papers"]),
             "lifetime": a["lifetime"], "first_year": a["first_year"], "last_year": a["last_year"]}
            for name, a in author_lifetimes.items()
            if a["lifetime"] <= YOUNG_MAX_LIFETIME and a["papers"] >= YOUNG_MIN_PAPERS
            and a["cited_papers"] > 0
            and (last_complete_year is None or a["last_year"] == last_complete_year)
        ]
        young.sort(key=lambda a: a["avg_citations"], reverse=True)
        insights["most_promising_young_researchers"] = young[:5]
        insights["young_researchers_cutoff_year"] = last_complete_year

    return insights


def main():
    all_entries = json.loads(IN_FILE.read_text(encoding="utf-8"))
    # LLM code-link verdicts (scripts/classify_code_links_llm.py) -- the
    # source of truth for has_code_link wherever a "yes"/"no" verdict
    # exists, replacing the old regex detector (an audit put that at ~40%
    # false positives: HuggingFace doc links, third-party baseline repos,
    # and a "code ... available" text match that also fired on the negated
    # form). Keyed by normalized title. "unclear", or no verdict yet
    # (the classifier runs in slow periodic batches over ~10k papers),
    # falls back to the regex has_code_link exactly as before -- so the
    # Insights numbers shift toward the LLM's answer as coverage grows.
    code_links_llm = {}
    if CODE_LINKS_LLM_FILE.exists():
        code_links_llm = json.loads(CODE_LINKS_LLM_FILE.read_text(encoding="utf-8"))
    for e in all_entries:
        if e.get("venue"):
            e["venue"] = normalize_venue(e["venue"])
    entries = [e for e in all_entries if e.get("av_relevance") == "core"]
    n_before_completeness = len(entries)
    entries = [e for e in entries if is_fully_processed(e)]
    if len(entries) != n_before_completeness:
        print(f"  excluded {n_before_completeness - len(entries)} core papers as not fully processed "
              f"(missing title/abstract/year)")

    # Every real author name in the corpus, computed up front (before any
    # institution extraction) so author_affiliations() below can reject a
    # candidate "institution" that's actually a bare person's name -- a
    # systemic pattern, not a one-off: a garbled affiliation-block parse
    # regularly glues a co-author's (or even an unrelated team member's,
    # not necessarily anyone on THIS paper) name onto the affiliation list
    # instead of the institution it belongs with (confirmed on real data:
    # "Ben Sapp", "Christos Sakaridis", "Tim Brädermann" and dozens more
    # were showing up as "institutions" this way). Exact-match against real
    # corpus author names, not a name-SHAPE heuristic (e.g. "two capitalized
    # words, no institutional keyword") -- a shape-only rule would also
    # reject real short institution names that happen to look the same way
    # ("ETH Zurich", "Johns Hopkins", "UC Berkeley", "KU Leuven" are all
    # exactly two capitalized words with no institutional keyword). No
    # institution in this corpus happens to share its exact display name
    # with a real author, so this check is both safe and effective.
    all_author_names = set()
    for e in entries:
        for name in (e.get("authors") or "").split(","):
            name = clean_author_name(name)
            if name:
                all_author_names.add(name)

    # A wrong-paper enrichment match: authors_detail ends up holding some
    # OTHER paper's full author list, not this paper's -- confirmed on real
    # data, user-flagged twice now: a 7-author CoRL 2025 paper ("Leveraging
    # Correlation Across Test Platforms...") somehow carried 100
    # authors_detail entries, every one of them a SciPy contributor (Pauli
    # Virtanen, Ralf Gommers, ...); separately, a 6-Chinese-author AAAI 2026
    # paper ("Driving with Advice...") carried 8 authors_detail entries that
    # were the real author list of an unrelated ~2012 urban-mobility paper
    # (Michael Batty, Kay Axhausen, ...) -- driving a bogus "most
    # international paper" insight ("only from China" per the raw author
    # string, "9 countries" per the mismatched authors_detail).
    #
    # A pure COUNT check (authors_detail wildly longer than the real author
    # list) caught the first case but not the second -- 8 vs. 6 authors
    # isn't implausible on count alone, so that bug kept resurfacing paper
    # by paper as each individual mismatch got reported and fixed one at a
    # time. Checking whether the two lists are even about the SAME PEOPLE
    # (surname overlap against a threshold) catches both: a real enrichment has most
    # authors_detail names' surnames present in the raw string; a wrong-paper
    # match has none. No authors_detail_source or author_verification tag
    # exists on this legacy-enriched data to catch it directly, so this
    # stays a plausibility check -- dropped (not guessed-and-kept) so it
    # can't corrupt institution/country/co-author stats anywhere downstream.
    def raw_author_surnames(e):
        names = [a.strip() for a in (e.get("authors") or "").split(",") if a.strip()]
        return {n.split()[-1].lower() for n in names if n.split()}

    n_mismatched_authors_detail = 0
    for e in entries:
        detail = e.get("authors_detail")
        if not detail:
            continue
        raw_count = len([a for a in (e.get("authors") or "").split(",") if a.strip()])
        count_implausible = raw_count and len(detail) > max(raw_count * 2, raw_count + 5)
        raw_surnames = raw_author_surnames(e)
        detail_surnames = {(a.get("name") or "").split()[-1].lower()
                            for a in detail if (a.get("name") or "").split()}
        overlap = len(raw_surnames & detail_surnames) / len(detail_surnames) if detail_surnames else 1
        name_mismatch = raw_surnames and detail_surnames and overlap < 0.5
        if count_implausible or name_mismatch:
            e["authors_detail"] = None
            n_mismatched_authors_detail += 1
    if n_mismatched_authors_detail:
        print(f"  dropped implausible authors_detail (wrong-paper match) on {n_mismatched_authors_detail} papers")

    # A second, different failure mode from the same source (arXiv/ar5iv
    # affiliation parsing, see fetch_affiliations_arxiv.py): when a paper's
    # LaTeX doesn't mark which author belongs to which affiliation, every
    # listed author sometimes gets credited with the SAME full multi-
    # institution list instead of their own one -- confirmed on real data
    # (multiple user reports of an author's page showing a co-author's
    # institution, or an author's recent affiliations being wrong; one case
    # a 5-way glued string "UC Berkeley Stanford UCL Virginia Tech Nvidia" that
    # split cleanly into 5 real institutions once re-extracted, still
    # identically shared by 2 authors afterward). This can't be corrected
    # per-author without knowing which institution is whose (data the
    # source doesn't give), so no INDIVIDUAL author is credited with any of
    # it -- flagged here, checked in the author_institutions/author_countries
    # accumulation loop further down, not applied by mutating the raw
    # affiliations/countries fields the way an earlier version of this fix
    # did. That earlier version nulled the fields directly, which also
    # silently zeroed out paper_institutions/paper_countries below (and, by
    # extension, the Institutions/Countries LEADERBOARD pages, which read
    # from that same paper-level set) -- an unnecessary second loss, since
    # "this paper involved these institutions" stays true even when no
    # single author can be tied to any one of them. Only flags a paper when
    # 2+ authors share an identical 2+-institution list -- a single shared
    # institution (a paper genuinely written entirely from one lab) is real
    # signal, not this bug, and is left alone.
    blanket_shared_papers = set()
    n_shared_affiliation_block = 0
    for e in entries:
        detail = e.get("authors_detail")
        if not detail or len(detail) < 2:
            continue
        aff_lists = [tuple(sorted(a.get("affiliations") or [])) for a in detail]
        non_empty = [af for af in aff_lists if af]
        if len(non_empty) >= 2 and len(set(non_empty)) == 1 and len(non_empty[0]) >= 2:
            blanket_shared_papers.add(id(e))
            n_shared_affiliation_block += 1
    if n_shared_affiliation_block:
        print(f"  {n_shared_affiliation_block} papers have a shared-affiliation-block (every author credited "
              f"with the same multi-institution list, not verifiably any one person's) -- excluded from "
              f"individual author credit, kept for paper-level Institutions/Countries aggregation")

    # Fallback country-by-institution-name lookup for when the per-author
    # country code above is missing even though the institution name itself
    # resolved -- confirmed on real data: several Spanish institutions
    # (Computer Vision Center, CSIC-UPC, ...) show up correctly as
    # institution names on multiple papers with zero resolved country on any
    # of them, because OpenAlex's author-affiliation record had the name but
    # no country_code attached. Only fills in, never overrides -- countries
    # is a union set, so this can only add a real institution's real
    # country, never replace an already-correct one.
    #
    # Same file apply_affiliations_arxiv.py already reads (hand-curated
    # historically, now also backfilled by fetch_institution_countries.py --
    # see that script's docstring): {name: "US", ...} flat map, plus a
    # "_readme" key that isn't a real institution.
    institution_country_codes = {
        k: v
        for k, v in (
            json.loads(INSTITUTION_COUNTRIES_FILE.read_text(encoding="utf-8"))
            if INSTITUTION_COUNTRIES_FILE.exists() else {}
        ).items() if v and not k.startswith("_")
    }
    institution_countries = {k: COUNTRY_NAMES.get(v, v) for k, v in institution_country_codes.items()}

    def paper_countries_institutions(e):
        countries, institutions = set(), set()
        details = e.get("authors_detail") or []
        co_author_names = {clean_author_name(x.get("name")) for x in details if x.get("name")}
        for a in details:
            for code in author_country_codes(a):
                countries.add(COUNTRY_NAMES.get(code, code))
            for aff in author_affiliations(a, all_author_names, co_author_names):
                institutions.add(aff)
        for inst in institutions:
            if inst in institution_countries:
                countries.add(institution_countries[inst])
        return sorted(countries), sorted(institutions)

    def paper_authors(e):
        # Prefer full names from authors_detail (OpenAlex enrichment) over the
        # plain "authors" string, which for several venue-listing sources
        # (e.g. DBLP-sourced CVPR 2018-2020) is first-initial-abbreviated
        # ("C Sima" instead of "Chonghao Sima") -- using the abbreviated form
        # would both misrepresent the person and fail to match the full-name
        # keys used in author_institutions/author_countries/author_detail.
        details = e.get("authors_detail")
        if details:
            authors = [clean_author_name(a["name"]) for a in details if a.get("name")]
        else:
            authors = [clean_author_name(a) for a in (e.get("authors") or "").split(",") if a.strip()]
        # This "authors" list is what the Researchers page actually aggregates
        # from client-side (all_papers, see filters.js) -- a bare surname here
        # (OpenAlex's own occasional data issue, or a mis-split "authors"
        # string) is what was showing up as an implausibly prolific "Wang"/
        # "Li"/etc. "researcher". Same is_full_name() rule as the server-side
        # author_citations/author_detail computation above.
        return [a for a in authors if is_full_name(a)]

    papers = []
    for e in entries:
        countries, institutions = paper_countries_institutions(e)
        authors = paper_authors(e)
        citations = citation_count(e)
        papers.append({
            "title": e.get("title"), "year": e.get("year"), "venue": e.get("venue"),
            "short_name": paper_short_name(e.get("title"), authors, e.get("year"), e.get("abstract")),
            # Not "abstract" -- see ABSTRACTS_DIR above, sharded out to its
            # own files so every page but paper.html skips this weight.
            "citations_by_source": citations_by_source_for_client(e),
            "citations": citations,
            "citations_updated": e.get("citations_updated"),
            "category": e.get("category"),
            "av_relevance": e.get("av_relevance"),
            "doi": e.get("doi"),
            "countries": countries,
            "institutions": institutions,
            "authors": authors,
            "author_verification": (e.get("author_verification") or "no_reference_record"),
            "arxiv_url": e.get("arxiv_url"),
            # How this paper entered the corpus -- "venue_listing" (a real
            # conference/journal's own proceedings), "arxiv_author_pull"
            # (fetch_arxiv.py, biased toward authors already prominent
            # here), or "arxiv_s2_citing_discovery" (fetch_semanticscholar_
            # citing.py, a verified citation edge via Semantic Scholar).
            "source": e.get("source") or "venue_listing",
            # The exact page this paper's data was pulled from, when the
            # fetcher recorded one (currently only fetch_github_paper_
            # lists.py's community-maintained ICRA/IROS lists) -- surfaced
            # on paper.html so a reader can check the original listing.
            "source_url": e.get("source_url"),
        })
        # Only included when actually checked -- omitted, not null, for the
        # majority of papers not yet reached, same convention as
        # self_citations/cd_index below (a present-but-null field on every
        # one of ~20k papers would bloat stats.json for no reason -- see
        # DECISIONS.md's stats.json size history). LLM verdict wins; the
        # regex has_code_link (fetch_affiliations_arxiv.py) and the
        # abstract-text upgrade are fallbacks only until the classifier
        # reaches every paper.
        llm_cl = code_links_llm.get(normalize_title(e.get("title")))
        if llm_cl and llm_cl.get("verdict") in ("yes", "no"):
            papers[-1]["has_code_link"] = llm_cl["verdict"] == "yes"
        elif e.get("has_code_link") is not None:
            papers[-1]["has_code_link"] = e["has_code_link"]
        elif e.get("abstract") and ABSTRACT_CODE_AVAILABILITY_RE.search(e["abstract"]):
            # Only ever upgrades a never-checked paper to True -- see
            # ABSTRACT_CODE_AVAILABILITY_RE's comment for why this can't
            # also supply a False.
            papers[-1]["has_code_link"] = True
    # Unknown-citation papers sort after every known-citation paper, regardless
    # of magnitude -- "no data" must never look like "definitely fewer than 1".
    papers.sort(key=lambda p: (p["citations"] is not None, p["citations"] or 0), reverse=True)

    # Who cites whom, within the corpus -- the reverse of the counts folded
    # into citations_by_source.in_corpus by apply_citation_sources.py. That
    # script only needed a count; the paper detail page (paper.html) needs
    # the actual citing papers (title + year) to list them and draw a
    # citation timeline, so this is computed directly from the same graph
    # rather than trying to recover it from a count.
    citation_graph = {}
    if CITATION_GRAPH_FILE.exists():
        citation_graph = json.loads(CITATION_GRAPH_FILE.read_text(encoding="utf-8"))
    by_norm_title = {normalize_title(p["title"]): p for p in papers}
    citing_by_target = defaultdict(list)
    # A self-citation (the citing paper shares at least one author with the
    # cited paper) counts toward citing_papers/the citation total just like
    # any other edge now -- self-citations used to be excluded outright, but
    # with a separate per-author Self-citation % metric (the Authors table)
    # showing how much of an author's citations are self-citations, there's
    # no need to also suppress them from the total (user-requested). Still
    # tracked here as its own count so that % column has something to read.
    self_citing_count = defaultdict(int)
    for citer_key, cited_keys in (citation_graph.get("edges") or {}).items():
        citer = by_norm_title.get(citer_key)
        if not citer:
            continue
        citer_authors = set(citer.get("authors") or [])
        for cited_key in cited_keys:
            target = by_norm_title.get(cited_key)
            if target and citer_authors & set(target.get("authors") or []):
                self_citing_count[cited_key] += 1
            citing_by_target[cited_key].append({
                "title": citer["title"], "year": citer["year"],
                "venue": citer.get("venue"), "category": citer.get("category"),
                "authors": citer.get("authors") or [],
            })
    disruption_by_key = compute_disruption_index(citation_graph.get("edges") or {})
    current_year = datetime.now(timezone.utc).year
    for p in papers:
        key = normalize_title(p["title"])
        citers = citing_by_target.get(key)
        if citers:
            # Just the title, not the full {title, year, venue, category,
            # authors} object citing_by_target builds above -- every citer
            # is itself a top-level entry in `papers`/all_papers (that's
            # what by_norm_title.get(citer_key) above already required), so
            # embedding its venue/year/category/authors here duplicated data
            # already present elsewhere in the same stats.json payload. At
            # corpus scale this was ~29MB of stats.json's ~68MB (109,636
            # edges x a denormalized object each, `authors` alone dead
            # weight -- no page ever reads a citing entry's own author list)
            # and was most of what pushed the file over GitHub's 100MB push
            # limit. paper.html/insights.html now resolve each title against
            # their own already-fetched all_papers array instead.
            p["citing_papers"] = [c["title"] for c in sorted(citers, key=lambda c: c["year"] or 0)]
        if self_citing_count.get(key):
            p["self_citations"] = self_citing_count[key]
        d = disruption_by_key.get(key)
        if d:
            p["cd_index"] = d["cd_index"]
            # How many citers the score is averaged over -- needed to break
            # ties among papers that land on the exact same cd_index (see
            # compute_insights' disruption_index block for why that's the
            # common case, not an edge case).
            p["cd_n_citers"] = d["n_citers"]
        # Early-citation velocity: in-corpus citations received within
        # EARLY_CITATION_WINDOW_YEARS of publication -- a leading indicator,
        # tested against eventual standing rather than assumed. Only set
        # once the window has actually closed (current_year - p.year >=
        # window); a paper published last year hasn't had the chance to
        # accumulate its "early" citations yet, and 0 so far would read as
        # "never got any" rather than "too soon to tell".
        if p.get("year") and (current_year - p["year"]) >= EARLY_CITATION_WINDOW_YEARS:
            cutoff = p["year"] + EARLY_CITATION_WINDOW_YEARS
            p["early_citations"] = sum(
                1 for c in (citers or []) if c.get("year") is not None and c["year"] <= cutoff
            )

    # Datasets page: "does this paper use dataset X" is only checkable at all
    # for the papers whose reference list was actually scanned by
    # build_citation_graph.py (see citation_graph_coverage below) -- but for
    # that subset, citing_by_target above is already the real answer (a paper
    # only appears there because ITS OWN parsed reference list names the
    # dataset paper), not a keyword match against the dataset's name showing
    # up in an abstract. Reusing it here means "cites the dataset" on this
    # page and "citing papers" on paper.html are the exact same computation,
    # just entered from the dataset's paper instead of an arbitrary paper's.
    # Most datasets have one introducing paper, but some (KITTI) split their
    # citable contribution across two -- the original benchmark paper and a
    # later fuller writeup -- and a citing paper might reference either one.
    # A label maps to a LIST of titles so both get counted; single-paper
    # datasets just use a one-element list.
    DATASET_SEED_TITLES = [
        ("KITTI", [
            "Are we ready for autonomous driving? The KITTI vision benchmark suite",
            "Vision meets robotics: The KITTI dataset",
        ]),
        ("nuScenes", ["nuScenes: A Multimodal Dataset for Autonomous Driving"]),
        ("Waymo Open Dataset", ["Scalability in Perception for Autonomous Driving: Waymo Open Dataset"]),
        ("Argoverse", ["Argoverse: 3D Tracking and Forecasting With Rich Maps"]),
        ("Cityscapes", ["The Cityscapes Dataset for Semantic Urban Scene Understanding"]),
        ("BDD100K", ["BDD100K: A Diverse Driving Dataset for Heterogeneous Multitask Learning"]),
        ("CARLA", ["CARLA: An Open Urban Driving Simulator"]),
        ("ApolloScape", ["The ApolloScape Open Dataset for Autonomous Driving and Its Application"]),
        ("SemanticKITTI", ["SemanticKITTI: A Dataset for Semantic Scene Understanding of LiDAR Sequences"]),
        ("Lyft Level 5", ["One Thousand and One Hours: Self-driving Motion Prediction Dataset"]),
        ("Waymo Open Motion Dataset", ["Large Scale Interactive Motion Forecasting for Autonomous Driving: The Waymo Open Motion Dataset"]),
        ("PandaSet", ["PandaSet: Advanced Sensor Suite Dataset for Autonomous Driving"]),
        ("Zenseact Open Dataset (ZOD)", ["Zenseact Open Dataset: A Large-Scale and Diverse Multimodal Dataset for Autonomous Driving"]),
        ("KITTI-360", ["KITTI-360: A Novel Dataset and Benchmarks for Urban Scene Understanding in 2D and 3D"]),
        ("V2X-Sim", ["V2X-Sim: Multi-Agent Collaborative Perception Dataset and Benchmark for Autonomous Driving"]),
        ("V2X-Seq", ["V2X-Seq: A Large-Scale Sequential Dataset for Vehicle-Infrastructure Cooperative Perception and Forecasting"]),
        ("CODA", ["CODA: A Real-World Road Corner Case Dataset for Object Detection in Autonomous Driving"]),
        ("Boreas", ["Boreas: A multi-season autonomous driving dataset"]),
        ("TJ4DRadSet", ["TJ4DRadSet: A 4D Radar Dataset for Autonomous Driving"]),
        # Expanded from 19 (user-requested "up to 100") -- every title below
        # was found by searching papers_full.json for the dataset's own
        # name and copying its EXACT stored title, not guessed from memory,
        # since a near-miss title silently fails to match anything
        # (by_norm_title lookup, below) rather than erroring, and a wrong/
        # hallucinated title would never surface at all.
        #
        # Not every title added here actually shows up on the Datasets
        # page, though: `papers` here is core-AV-relevant papers ONLY (see
        # `by_norm_title` below), and several genuinely real, exact-title
        # matches -- Cityscapes, MulRan, the Oxford Radar RobotCar Dataset,
        # highD/inD/rounD/exiD, CommonRoad, D2-City, A9-Dataset, IPS300+,
        # WOMD-LiDAR, ParisLuco3D, STCrowd, DeepScenario, MAN TruckScenes,
        # DDAD, Ford Multi-AV Seasonal Dataset -- are in papers_full.json
        # but classified "adjacent", not "core", by classify.py's own
        # AV-relevance rules (confirmed by checking each one directly, not
        # assumed). That's a genuine scope boundary of what this site calls
        # AV-relevant, not a title-matching bug, so they're listed here
        # anyway (title matching costs nothing extra) in case a future
        # classify.py change reclassifies any of them as core. Real count
        # landed: 59 titles actually resolve to a row on Datasets today,
        # not 100 -- further growth needs either more verified titles or a
        # classify.py change, not a round number asserted without checking.
        ("SODA10M", ["SODA10M: A Large-Scale 2D Self/Semi-Supervised Object Detection Dataset for Autonomous Driving"]),
        ("DAIR-V2X", ["DAIR-V2X: A Large-Scale Dataset for Vehicle-Infrastructure Cooperative 3D Object Detection"]),
        ("V2X-Radar", ["V2X-Radar: A Multi-modal Dataset with 4D Radar for Cooperative Perception"]),
        ("Rank2Tell", ["Rank2Tell: A Multimodal Driving Dataset for Joint Importance Ranking and Reasoning"]),
        ("LingoQA", ["LingoQA: Video Question Answering for Autonomous Driving"]),
        ("RoScenes", ["RoScenes: A Large-scale Multi-view 3D Dataset for Roadside Perception"]),
        ("RCooper", ["RCooper: A Real-world Large-scale Dataset for Roadside Cooperative Perception"]),
        ("HoloVIC", ["HoloVIC: Large-scale Dataset and Benchmark for Multi-Sensor Holographic Intersection and Vehicle-Infrastructure Cooperative"]),
        ("V2X-Real", ["V2X-Real: a Largs-Scale Dataset for Vehicle-to-Everything Cooperative Perception"]),
        ("TUMTraf V2X", ["TUMTraf V2X Cooperative Perception Dataset"]),
        ("DeepScenario", ["Highly Accurate and Diverse Traffic Data: The DeepScenario Open 3D Dataset"]),
        ("ScenarioNet", ["ScenarioNet: Open-Source Platform for Large-Scale Traffic Scenario Simulation and Modeling"]),
        ("SafeShift", ["SafeShift: Safety-Informed Distribution Shifts for Robust Trajectory Prediction in Autonomous Driving"]),
        ("V2V4Real", ["V2V4Real: A Real-World Large-Scale Dataset for Vehicle-to-Vehicle Cooperative Perception"]),
        ("nuPlan", ["nuPlan: A closed-loop ML-based planning benchmark for autonomous vehicles"]),
        ("ONCE", ["One Million Scenes for Autonomous Driving: ONCE Dataset"]),
        ("DDAD", ["DDAD: Detachable Crowd Density Estimation Assisted Pedestrian Detection"]),
        ("A*3D", ["A*3D Dataset: Towards Autonomous Driving in Challenging Environments"]),
        ("A2D2", ["A2D2: Audi Autonomous Driving Dataset"]),
        ("Argoverse 2", ["Argoverse 2: Next Generation Datasets for Self-Driving Perception and Forecasting"]),
        ("Rope3D", ["Rope3D: The Roadside Perception Dataset for Autonomous Driving and Monocular 3D Object Detection Task"]),
        ("OPV2V", ["OPV2V: An Open Benchmark Dataset and Fusion Pipeline for Perception with Vehicle-to-Vehicle Communication"]),
        ("ONCE-3DLanes", ["ONCE-3DLanes: Building Monocular 3D Lane Detection"]),
        ("PIE", ["PIE: A Large-Scale Dataset and Models for Pedestrian Intention Estimation and Trajectory Prediction"]),
        ("BLVD", ["BLVD - Building a Large-Scale 5D Semantics Benchmark for Autonomous Driving"]),
        ("Waymo Open Sim Agents Challenge", ["The Waymo Open Sim Agents Challenge"]),
        ("DeepAccident", ["DeepAccident: A Motion and Accident Prediction Benchmark for V2X Autonomous Driving"]),
        ("Ford Multi-AV Seasonal Dataset", ["Ford Multi-AV Seasonal Dataset"]),
        ("MulRan", ["MulRan: Multimodal Range Dataset for Urban Place Recognition"]),
        ("Oxford Radar RobotCar", ["The Oxford Radar RobotCar Dataset: A Radar Extension to the Oxford RobotCar Dataset"]),
        ("Ithaca365", ["Ithaca365: Dataset and Driving Perception under Repeated and Challenging Weather Conditions"]),
        ("ParisLuco3D", ["ParisLuco3D: A High-Quality Target Dataset for Domain Generalization of LiDAR Perception"]),
        ("STCrowd", ["STCrowd: A Multimodal Dataset for Pedestrian Perception in Crowded Scenes"]),
        ("NuScenes-QA", ["NuScenes-QA: A Multi-Modal Visual Question Answering Benchmark for Autonomous Driving Scenario"]),
        ("Talk2BEV", ["Talk2BEV: Language-Enhanced Bird’s-Eye View Maps for Autonomous Driving"]),
        ("DriveLM", ["DriveLM: Driving with Graph Visual Question Answering"]),
        ("MAPLM", ["MAPLM: A Real-World Large-Scale Vision-Language Benchmark for Map and Traffic Scene Understanding"]),
        ("MetaDrive", ["MetaDrive: Composing Diverse Driving Scenarios for Generalizable Reinforcement Learning"]),
        ("InterSim", ["InterSim: Interactive Traffic Simulation via Explicit Relation Modeling"]),
        ("WOMD-LiDAR", ["WOMD-LiDAR: Raw Sensor Dataset Benchmark for Motion Forecasting"]),
        ("highD", ["The highD Dataset: A Drone Dataset of Naturalistic Vehicle Trajectories on German Highways for Validation of Highly Automated Driving Systems"]),
        ("inD", ["The inD Dataset: A Drone Dataset of Naturalistic Road User Trajectories at German Intersections"]),
        ("rounD", ["The rounD Dataset: A Drone Dataset of Road User Trajectories at Roundabouts in Germany"]),
        ("exiD", ["The exiD Dataset: A Real-World Trajectory Dataset of Highly Interactive Highway Scenarios in Germany"]),
        ("V2X-ViT", ["V2X-ViT: Vehicle-to-Everything Cooperative Perception with Vision Transformer"]),
        ("MAN TruckScenes", ["MAN TruckScenes: A multimodal dataset for autonomous trucking in diverse conditions"]),
        ("aiMotive Dataset", ["aiMotive Dataset: A Multimodal Dataset for Robust Autonomous Driving with Long-Range Perception"]),
        ("Panoptic nuScenes", ["Panoptic Nuscenes: A Large-Scale Benchmark for LiDAR Panoptic Segmentation and Tracking"]),
        ("CommonRoad", ["CommonRoad: Composable benchmarks for motion planning on roads"]),
        ("D2-City", ["D2-City: A Large-Scale Dashcam Video Dataset of Diverse Traffic Scenarios"]),
        ("DADA-2000", ["DADA-2000: Can Driving Accident be Predicted by Driver Attentionƒ Analyzed by A Benchmark"]),
        ("DR(eye)VE", ["Predicting the Driver's Focus of Attention: The DR(eye)VE Project"]),
        ("ApolloCar3D", ["ApolloCar3D: A Large 3D Car Instance Understanding Benchmark for Autonomous Driving"]),
        ("A9-Dataset", ["A9-Dataset: Multi-Sensor Infrastructure-Based Dataset for Mobility Research"]),
        ("IPS300+", ["IPS300+: a Challenging multi-modal data sets for Intersection Perception System"]),
        ("BAAI-VANJEE", ["BAAI-VANJEE Roadside Dataset: Towards the Connected Automated Vehicle Highway technologies in Challenging Environments of China"]),
        ("CitySim", ["CitySim: A Drone-Based Vehicle Trajectory Dataset for Safety-Oriented Research and Digital Twins"]),
    ]
    datasets = []
    for label, titles in DATASET_SEED_TITLES:
        seed_papers = []
        citing = {}  # normalized citer title -> citer dict, deduped across the label's seed papers
        for title in titles:
            key = normalize_title(title)
            dataset_paper = by_norm_title.get(key)
            if not dataset_paper:
                continue  # this particular seed title not found in this corpus -- skip just it
            seed_papers.append(dataset_paper)
            for c in citing_by_target.get(key, []):
                citing[normalize_title(c["title"])] = c
        if not seed_papers:
            continue  # none of this label's seed titles are in the corpus -- skip the row entirely
        citing_list = sorted(citing.values(), key=lambda c: c["year"] or 0)
        primary = seed_papers[0]
        datasets.append({
            "name": label,
            "paper_title": primary["title"],
            "year": primary["year"],
            "venue": primary.get("venue"),
            "also_introduced_in": [
                {"title": p["title"], "year": p["year"], "venue": p.get("venue")} for p in seed_papers[1:]
            ],
            # Titles only -- same reasoning as papers[]["citing_papers"]
            # above, insights.html resolves each against its own
            # already-fetched all_papers instead of getting a second
            # denormalized copy of every citer's year/venue/category/authors.
            "citing_papers": [c["title"] for c in citing_list],
            "citing_count": len(citing_list),
        })
    datasets.sort(key=lambda d: -d["citing_count"])

    # Citation-graph completeness -- NOT "how many papers have a citation
    # count" (a paper the graph never scanned as a citER and a paper it
    # scanned and found genuinely uncited both show 0/None citing_papers,
    # indistinguishable from citations_by_source.in_corpus alone). What IS
    # knowable and honest to show: how much of the corpus has actually had
    # its own reference list extracted and scanned for in-corpus citations
    # -- build_citation_graph.py's sources_scanned count, against the
    # denominator of papers that source could ever reach: CVF-hosted core
    # papers for the PDF path (cvf_done also counts a confirmed-404 paper as
    # done -- it will never be fetchable, so it shouldn't read as pending
    # forever), and only core papers with a known arXiv preprint for the
    # arXiv path (arxiv_eligible_total) -- a paper with no preprint at all
    # could never be reached this way, so counting it in the denominator
    # made 100% structurally unreachable even once every real preprint was
    # scanned. Surfaced on About so "no citation data" reads as "not yet
    # verifiable" rather than "confirmed zero."
    cvf_core_total = sum(1 for e in entries if (e.get("venue") or "") in CVF_CITATION_GRAPH_VENUES)
    arxiv_eligible_total = sum(1 for e in entries if e.get("arxiv_url"))
    sources_scanned = citation_graph.get("sources_scanned") or {}
    citation_graph_coverage = {
        "cvf_scanned": sources_scanned.get("cvf", 0),
        "cvf_permanent_failures": citation_graph.get("cvf_permanent_failures", 0),
        "cvf_core_total": cvf_core_total,
        "arxiv_scanned": sources_scanned.get("arxiv", 0),
        "arxiv_eligible_total": arxiv_eligible_total,
        "core_total": len(entries),
    }

    # Corpus-wide counts (unfiltered by av_relevance) for the overview banner --
    # "how many papers are indexed", broken down by venue and by year.
    venue_counts = defaultdict(int)
    year_counts = defaultdict(int)
    for e in all_entries:
        if e.get("venue"):
            venue_counts[e["venue"]] += 1
        if e.get("year"):
            year_counts[e["year"]] += 1

    # Which venues are common enough to list individually in the UI, vs.
    # getting folded into one "Other" bucket (user-requested: the venue
    # filter dropdown and the Venues page both used to list every distinct
    # venue string, which -- after backfill_citing_venues.py started filling
    # in real per-paper venues for ~68k citation-discovered papers -- meant
    # thousands of one-off venues, most represented by a single paper).
    # Counted over CORE (av_relevant) papers specifically, regardless of
    # which relevance view a page is currently showing, since "> 25 AV
    # relevant papers" is what was asked for -- a venue's standing here
    # doesn't change just because someone switched to browsing adjacent
    # papers. Shipped as a plain name list rather than re-derived client-side
    # so every page buckets the exact same way.
    BIG_VENUE_MIN_PAPERS = 25
    core_venue_counts = Counter(e["venue"] for e in entries if e.get("venue"))
    big_venues = sorted(
        (v for v, c in core_venue_counts.items() if c > BIG_VENUE_MIN_PAPERS),
        key=lambda v: -core_venue_counts[v],
    )

    # all_author_names itself is computed once, up front (see the top of
    # main()) -- reused here for the overview stat tile too, so "researchers
    # tracked" reflects the whole core corpus rather than being capped at
    # the top-50 leaderboard length. Imprecise (name-string dedup, no
    # disambiguation of same-named authors) but far more honest than a
    # number that's actually just "len(top_authors)".
    total_researchers = len(all_author_names)
    all_institutions_seen = set()
    for e in entries:
        details = e.get("authors_detail") or []
        co_author_names = {clean_author_name(x.get("name")) for x in details if x.get("name")}
        for a in details:
            all_institutions_seen.update(author_affiliations(a, all_author_names, co_author_names))
    total_institutions_all = len(all_institutions_seen)

    # "Best paper" must be chosen only among papers with at least one
    # in-corpus citation -- otherwise, for a year/venue where nothing is
    # cited in-corpus yet, this would silently pick whichever 0-citation
    # paper happens to sort first and label it "best", which is not a real
    # comparison.
    best_by_year = {}
    for p in papers:
        if not p["citations"]:
            continue
        y = p["year"]
        if y and (y not in best_by_year or p["citations"] > best_by_year[y]["citations"]):
            best_by_year[y] = p
    best_by_venue = {}
    for p in papers:
        if not p["citations"]:
            continue
        v = p["venue"]
        if v and (v not in best_by_venue or p["citations"] > best_by_venue[v]["citations"]):
            best_by_venue[v] = p

    category_stats = defaultdict(lambda: {"papers": 0, "citations": 0})
    for p in papers:
        cat = p["category"] or "uncategorized"
        category_stats[cat]["papers"] += 1
        category_stats[cat]["citations"] += p["citations"] or 0

    # Per-year/per-category timelines are computed client-side from all_papers
    # (see categories.html) so they respect whatever filters are active -- no
    # precomputed timeline data needed here.

    author_citations = defaultdict(int)
    author_papers = defaultdict(int)
    author_names_enriched = set()
    # name -> {institution: {"first_year", "last_year", "papers"}} -- tracking
    # the year lets the researcher page show an author's institutions in
    # chronological order (e.g. "Google 2016-2017, then Waymo 2018-") instead
    # of an unordered alphabetical list that can't distinguish "changed
    # employer" from "two simultaneous appointments."
    author_institutions = defaultdict(dict)
    # dict, not set -- needs first_year/last_year tracked per country the
    # same way author_institutions tracks it per institution (see below),
    # so "which country is this author's CURRENT one" can be answered by
    # chronological order instead of alphabetical order. Confirmed real bug
    # (user-flagged): an author whose most recent institution is in the
    # Netherlands was showing "United States" as their country on a
    # co-author's page, only because it sorts after "Netherlands"
    # alphabetically, not because it's more recent.
    author_countries = defaultdict(dict)
    inst_citations = defaultdict(int)
    inst_papers = defaultdict(int)
    # institution -> author name -> {"papers", "citations"} -- ONLY from that
    # specific author's OWN authors_detail affiliation entry, never from a
    # co-author's. Powers institution.html's "top authors here" precisely;
    # the paper-level `institutions` field above (every author's affiliation
    # unioned together) is correct for "does this paper involve institution
    # X" but was previously reused for "top authors AT X" too, which wrongly
    # credited every co-author on a shared paper with each other's employer
    # (confirmed on real data: an author who only ever appears with one
    # company's affiliation was showing as a top author of a different
    # company, solely because a co-author on some shared papers works there).
    institution_authors = defaultdict(lambda: defaultdict(
        lambda: {"papers": 0, "citations": 0, "first_year": None, "last_year": None}))
    country_citations = defaultdict(int)
    country_papers = defaultdict(int)

    n_excluded = 0
    n_with_author_detail = 0
    for e in entries:
        if e.get("author_verification") == "mismatch":
            n_excluded += 1
            continue

        details = e.get("authors_detail")
        if not details:
            continue  # venue-listing papers only have a plain author-name string, no affiliations
        n_with_author_detail += 1

        c = citation_count(e) or 0
        year = e.get("year")
        paper_institutions = set()
        paper_countries = set()
        # See the blanket_shared_papers comment above: real evidence the
        # PAPER involved these institutions (paper_institutions/paper_
        # countries just below are populated from own_affs regardless of
        # this flag), not reliable evidence of any INDIVIDUAL's own
        # affiliation, so author_institutions/institution_authors/
        # author_countries (this person's own page) skip crediting anyone
        # on a flagged paper.
        blanket_shared = id(e) in blanket_shared_papers
        co_author_names = {clean_author_name(x.get("name")) for x in details if x.get("name")}
        for a in details:
            name = clean_author_name(a.get("name"))
            if not name or not is_full_name(name):
                continue
            author_citations[name] += c
            author_papers[name] += 1
            author_names_enriched.add(name)
            own_affs = author_affiliations(a, all_author_names, co_author_names)
            if not blanket_shared:
                for aff in own_affs:
                    rec = author_institutions[name].setdefault(aff, {"first_year": year, "last_year": year, "papers": 0})
                    if year is not None:
                        rec["first_year"] = year if rec["first_year"] is None else min(rec["first_year"], year)
                        rec["last_year"] = year if rec["last_year"] is None else max(rec["last_year"], year)
                    rec["papers"] += 1
                    inst_authors_entry = institution_authors[aff][name]
                    inst_authors_entry["papers"] += 1
                    inst_authors_entry["citations"] += c
                    if year is not None:
                        inst_authors_entry["first_year"] = year if inst_authors_entry["first_year"] is None \
                            else min(inst_authors_entry["first_year"], year)
                        inst_authors_entry["last_year"] = year if inst_authors_entry["last_year"] is None \
                            else max(inst_authors_entry["last_year"], year)
            codes = author_country_codes(a)
            if not blanket_shared:
                for code in codes:
                    cname = COUNTRY_NAMES.get(code, code)
                    rec = author_countries[name].setdefault(cname, {"first_year": year, "last_year": year})
                    if year is not None:
                        rec["first_year"] = year if rec["first_year"] is None else min(rec["first_year"], year)
                        rec["last_year"] = year if rec["last_year"] is None else max(rec["last_year"], year)
            paper_institutions.update(own_affs)
            paper_countries.update(codes)
        # Same institution-name fallback as paper_countries_institutions()
        # above -- this loop builds the Countries leaderboard from author
        # country codes directly and has its own separate paper_countries
        # set, so the fallback has to be applied here too, not just once.
        # paper_countries holds raw 2-letter codes at this point (resolved
        # to display names below), so the raw-code fallback map is used, not
        # the display-name one paper_countries_institutions() uses.
        for aff in paper_institutions:
            if aff in institution_country_codes:
                paper_countries.add(institution_country_codes[aff])
        for aff in paper_institutions:
            inst_citations[aff] += c
            inst_papers[aff] += 1
        for code in paper_countries:
            name = COUNTRY_NAMES.get(code, code)
            country_citations[name] += c
            country_papers[name] += 1

    def top(citations_d, papers_d, n=50):
        # Sorted by total citations (papers this small a sample favors, so surface
        # both: the total and the per-paper average, plus the paper count itself
        # so a reader can judge whether either number is trustworthy here).
        ranked = sorted(citations_d.items(), key=lambda kv: kv[1], reverse=True)[:n]
        return [{"name": k, "citations": v, "papers": papers_d[k],
                  "avg_citations": round(v / papers_d[k])} for k, v in ranked]

    # Author ranking is deliberately NOT "total citations" -- that metric goes
    # up just by publishing more, regardless of any single paper's impact, so
    # it rewards paper count rather than research quality. Rank by average
    # citations per paper instead, restricted to authors with more than
    # MIN_AUTHOR_PAPERS papers in the (enriched-so-far) corpus -- otherwise a
    # single lucky highly-cited paper would dominate the "average" the same
    # way total citations let paper count dominate. 1 is the floor: an author
    # with exactly one paper has an average equal to that paper's own count,
    # which isn't a comparative signal at all.
    MIN_AUTHOR_PAPERS = 1

    def top_authors_by_avg(citations_d, papers_d, n=50, min_papers=None):
        floor = MIN_AUTHOR_PAPERS if min_papers is None else min_papers
        eligible = [(k, v) for k, v in citations_d.items() if papers_d[k] > floor]
        ranked = sorted(eligible, key=lambda kv: kv[1] / papers_d[kv[0]], reverse=True)[:n]
        return [{"name": k, "citations": v, "papers": papers_d[k],
                  "avg_citations": round(v / papers_d[k])} for k, v in ranked]

    n_verified = sum(1 for e in entries if e.get("author_verification") in ("verified_arxiv", "verified_crossref"))

    # Hand-maintained Scholar profile URL + hotlinked photo URL for a small
    # set of top-ranked authors, looked up manually via a real browser (no
    # scraping API, no downloading/copying the photo -- we just reference
    # Google's own hosted image URL). Not attempted for the full author list;
    # only worth the manual effort for names that actually surface on a
    # leaderboard. Missing entries are expected and fine.
    scholar_profiles_raw = {}
    if SCHOLAR_PROFILES_FILE.exists():
        scholar_profiles_raw = json.loads(SCHOLAR_PROFILES_FILE.read_text(encoding="utf-8"))
    # Keyed by clean_author_name(), not the raw JSON key, so a
    # scholar_profiles.json entry added under an old/uncleaned spelling
    # (an initials-and-surname variant, say, from before that name was
    # folded into its canonical form via KNOWN_NAME_FIXES) still lands on
    # the one canonical author instead of creating a second, stale-keyed entry.
    scholar_profiles = {}
    for raw_name, prof in scholar_profiles_raw.items():
        scholar_profiles.setdefault(clean_author_name(raw_name), prof)

    # ORCIDs (fetch_s2_author_ids.py + fetch_orcids.py, via Semantic
    # Scholar) are keyed by a letters-only normalized name rather than the
    # exact cleaned name used elsewhere -- those two scripts read the RAW
    # author string straight off papers_full.json, which can still carry a
    # leftover PDF-extraction artifact (a footnote marker, a DBLP
    # disambiguation suffix) that clean_author_name() strips later. Digits
    # and punctuation are exactly the kind of artifact that differs between
    # the raw and cleaned forms, so stripping down to bare letters is what
    # lets "Cheng-Zhong Xu 0001" (raw) and "Cheng-Zhong Xu" (cleaned) still
    # land on the same key.
    def normalize_name_letters(n):
        return re.sub(r"[^a-z]", "", (n or "").lower())

    orcids = {}
    if ORCIDS_FILE.exists():
        orcids = json.loads(ORCIDS_FILE.read_text(encoding="utf-8"))

    # Per-author enrichment for the researcher page: institution/country
    # affiliation (only known for the authors_detail-backed subset) plus a
    # hand-looked-up Scholar profile/photo where we have one. The researcher
    # page itself computes each author's paper list and citation timeline
    # client-side from all_papers (by matching the plain author-name string),
    # which covers their full paper set, not just the affiliation-enriched
    # subset -- this dict only supplies what can't be derived that way.
    author_detail = {}
    # Union with scholar_profiles's own keys, not just author_names_enriched
    # -- a manually-curated Scholar profile/photo is worth showing even for
    # an author whose papers all come from venues with no affiliation crawl
    # at all (title/authors-only venues, see About > Data sources), who
    # would otherwise never enter this loop and so could never pick up a
    # hand-added profile no matter what scholar_profiles.json says.
    for name in author_names_enriched | set(scholar_profiles.keys()):
        profile = scholar_profiles.get(name, {})
        # Chronological, not alphabetical -- earliest first_year first, so an
        # author who changed institutions reads as a timeline ("Google
        # 2016-2017, Waymo 2018-2026") rather than an arbitrary A-Z list that
        # can't tell a reader which came first.
        institutions = [
            {"name": inst, "first_year": rec["first_year"], "last_year": rec["last_year"], "papers": rec["papers"]}
            for inst, rec in sorted(author_institutions[name].items(),
                                     key=lambda kv: (kv[1]["first_year"] is None, kv[1]["first_year"]))
        ]
        # Chronological, not alphabetical, same reasoning and shape as
        # institutions above -- countries stays a plain array of names (not
        # {name, first_year, last_year} objects like institutions) since
        # several pages already consume it that way (e.g. `.join(' · ')`
        # for the tags row); only the ORDER changes here, from alphabetical
        # to earliest-first, so "the last entry" now actually means the most
        # recent country instead of whichever name happens to sort last.
        countries = [
            cname for cname, rec in sorted(author_countries[name].items(),
                                            key=lambda kv: (kv[1]["first_year"] is None, kv[1]["first_year"]))
        ]
        orcid = orcids.get(normalize_name_letters(name))
        if not institutions and not countries and not profile and not orcid:
            continue
        author_detail[name] = {
            "institutions": institutions,
            "countries": countries,
            "scholar_url": profile.get("scholar_url"),
            "photo_url": profile.get("photo_url"),
            # True only once this author has actually been manually looked up
            # (found -> scholar_url set, or confirmed absent -> not_found: true
            # in scholar_profiles.json) -- distinct from "never checked", so the
            # UI can say "not yet looked up" instead of implying a search
            # already came back empty.
            "scholar_checked": bool(profile.get("scholar_url") or profile.get("not_found")),
            "orcid": orcid,
            # True for a small, manually-reviewed set of names where a real
            # Scholar-profile search turned up multiple plausible different
            # people and no way to tell them apart (common name, no
            # institution/co-author data in this corpus to disambiguate with)
            # -- set in scholar_profiles.json, same place as a confirmed
            # profile, so a reader is warned rather than shown one person's
            # merged stats as if they were reliably one identity.
            "ambiguous": bool(profile.get("ambiguous")),
        }

    # "Most prolific" means paper count specifically, not the avg-citations
    # ranking top_authors_by_avg produces -- a separate small ranking just
    # for that one insight.
    top_authors_by_paper_count = sorted(
        ({"name": k, "papers": v, "citations": author_citations[k]} for k, v in author_papers.items()),
        key=lambda a: a["papers"], reverse=True,
    )[:5]

    # Per-author lifetime (first-to-last publication year) and citation
    # totals, computed from the FULL `papers` list's plain "authors" field
    # (the same one authors.html aggregates client-side from all_papers)
    # rather than the authors_detail-only subset above -- an author with no
    # affiliation enrichment yet must still be eligible to show up as a
    # "most promising young researcher" or in the lifetime distribution.
    author_lifetimes = {}
    for p in papers:
        year = p.get("year")
        for name in set(p.get("authors") or []):
            rec = author_lifetimes.setdefault(
                name, {"first_year": None, "last_year": None, "papers": 0, "citations": 0, "cited_papers": 0})
            rec["papers"] += 1
            if year is not None:
                rec["first_year"] = year if rec["first_year"] is None else min(rec["first_year"], year)
                rec["last_year"] = year if rec["last_year"] is None else max(rec["last_year"], year)
            if p.get("citations") is not None:
                rec["citations"] += p["citations"]
                rec["cited_papers"] += 1
    for rec in author_lifetimes.values():
        rec["lifetime"] = (rec["last_year"] - rec["first_year"]) if rec["first_year"] is not None else None
    # Only authors with a known year range have a computable lifetime --
    # drop the rest rather than let them collapse into a fake "lifetime 0".
    author_lifetimes = {k: v for k, v in author_lifetimes.items() if v["lifetime"] is not None}

    insights = compute_insights(
        papers, all_entries, citation_graph, category_stats,
        # min_papers=10, stricter than the Authors page's own min-1 floor --
        # "highest average impact" shouldn't be won by two papers and one
        # lucky hit (user-requested).
        top_authors_by_avg(author_citations, author_papers, n=5, min_papers=10),
        top_authors_by_paper_count,
        top(inst_citations, inst_papers, n=5),
        datasets,
        author_lifetimes,
    )

    # Not-AV-relevant paper count per author (user-requested, shown on
    # author.html and the Authors table) -- has to be computed here, not
    # derived client-side, because stats_adjacent.json deliberately carries
    # no author field at all (it's ~212k records; adding one would meaningfully
    # grow an already-58MB file for a client-side page that would then have
    # to fetch and scan all of it just to count matches for one name). A
    # plain {name: count} map is a few hundred KB at most and answers the
    # same question far cheaper. Adjacent entries still have their raw
    # "authors" string (this is server-side, reading the full corpus, not
    # the slimmed client file), so no separate enrichment pass is needed.
    adjacent_entries = [e for e in all_entries
                         if e.get("av_relevance") == "adjacent" and is_fully_processed(e)]
    non_av_paper_counts = defaultdict(int)
    # Same reasoning, same shape, for citations rather than paper count --
    # author.html's stat tiles pair "AV citations" with "Non-AV citations"
    # (user-requested), and that pairing needs both numbers available
    # synchronously on page load, not behind the lazy stats_adjacent.json
    # fetch the Papers table below only triggers once a reader actually
    # switches the SHOW filter.
    non_av_paper_citations = defaultdict(int)
    for e in adjacent_entries:
        cites = citation_count(e)
        for raw in (e.get("authors") or "").split(","):
            name = clean_author_name(raw)
            if name and is_full_name(name):
                non_av_paper_counts[name] += 1
                if cites:
                    non_av_paper_citations[name] += cites

    # Per-venue collection completeness for the About page's Data coverage
    # table -- computed from the actual corpus (all_entries, not just
    # av_relevance=="core", since this is about how completely each venue's
    # proceedings were collected, not which papers turned out AV-relevant),
    # so it can never drift out of sync the way a hand-typed table would.
    # arXiv is excluded: it's a keyword search against arXiv's own API, not
    # a complete-proceedings pull like every venue below, so "years covered"
    # doesn't mean the same thing for it (same reasoning insights.html's
    # venue-relevance list already excludes it for).
    # 500 papers cleanly separates the ~24 venues this site actually does a
    # complete-proceedings pull from (all in the thousands) from citation-
    # graph-discovered incidental venues (a handful of papers each, a steep
    # cliff below ~500) -- confirmed against the real distribution, not a
    # guess. Below the cliff isn't "wrong," just not one of this corpus's
    # target venues, and would make an unreadable 2,800+-row table if shown.
    VENUE_COVERAGE_MIN_PAPERS = 500
    venue_coverage_acc = defaultdict(lambda: {"years": set(), "with_abstract": 0, "total": 0})
    for e in all_entries:
        v = e.get("venue")
        if not v or v == "arXiv":
            continue
        rec = venue_coverage_acc[v]
        rec["total"] += 1
        if e.get("year"):
            rec["years"].add(e["year"])
        if e.get("abstract"):
            rec["with_abstract"] += 1
    venue_coverage = {
        v: {
            "years": sorted(rec["years"]),
            "papers": rec["total"],
            "abstract_coverage": (
                "full" if rec["with_abstract"] == rec["total"]
                else "none" if rec["with_abstract"] == 0
                else "partial"
            ),
        }
        for v, rec in sorted(venue_coverage_acc.items(), key=lambda kv: -kv[1]["total"])
        if rec["total"] >= VENUE_COVERAGE_MIN_PAPERS
    }

    stats = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "generated_from": len(all_entries),
        "core_relevant": len(entries),
        "corpus_stats": {
            "by_venue": dict(sorted(venue_counts.items(), key=lambda kv: -kv[1])),
            "venue_coverage": venue_coverage,
            "big_venues": big_venues,
            "by_year": {str(y): n for y, n in sorted(year_counts.items())},
            "venues_covered": len(venue_counts),
            "year_range": [min(year_counts), max(year_counts)] if year_counts else None,
            "total_researchers": total_researchers,
            "total_institutions": total_institutions_all,
            "citation_graph_coverage": citation_graph_coverage,
            # How many core papers came from each discovery path -- see the
            # "source" field on each paper for what these mean. Surfaced so
            # each path's contribution is auditable without having to dig
            # through raw data.
            "by_source": dict(Counter(p["source"] for p in papers)),
            # One sequential answer to "how many papers are where in the
            # pipeline" (user-requested), instead of having to piece it
            # together from several separately-shaped stats. Every core
            # paper passes through classify.py immediately on merge (stage
            # 1 == stage 0, always, by construction -- kept as its own
            # stage anyway so a reader doesn't have to know that), then
            # picks up an abstract and author/institution detail on its own
            # schedule depending on discovery path and which backfills have
            # reached it so far. (There's no "citations known" stage: every
            # paper now has a real in-corpus citation count, 0 included --
            # how complete the underlying reference-list crawl is shows in
            # the "Citation graph: ... reference lists" rows below.)
            "pipeline_stages": {
                "1_discovered": len(entries),
                "2_classified": len(entries),  # classify.py runs on every entry at merge time
                # Done once an abstract is found OR mine_abstracts.py has
                # confirmed (via a clean arXiv search that came back empty)
                # there isn't one to find -- both are a completed attempt,
                # only "never searched yet" should read as still pending.
                "3_abstract": sum(1 for e in entries if e.get("abstract") or e.get("abstract_search_exhausted")),
                "4_author_detail": n_with_author_detail,
            },
        },
        "verification": {
            "papers_with_author_detail": n_with_author_detail,
            "verified": n_verified,
            "excluded_mismatch": n_excluded,
        },
        "top_papers": papers[:50],
        "all_papers": papers,
        "top_authors": top_authors_by_avg(author_citations, author_papers, n=100),
        "top_institutions": top(inst_citations, inst_papers, n=100),
        "top_countries": top(country_citations, country_papers),
        "best_by_year": {str(y): p for y, p in sorted(best_by_year.items())},
        "best_by_venue": best_by_venue,
        "datasets": datasets,
        # Best-effort images from Wikipedia's public pageimages API (see
        # fetch_institution_logos.py / fetch_venue_logos.py) -- NOT
        # guaranteed to be an actual logo. Wikipedia's infobox image is
        # sometimes a company/campus photo instead (confirmed on real data:
        # Microsoft's and Meta's own articles return a building photo, not
        # their logo), so this is an illustrative image, not a verified
        # logo asset. Only entries with a real logo_url are included --
        # institution_logos.json also stores confirmed misses internally
        # (logo_url: null) so the fetch script doesn't retry them forever,
        # but there's no reason to ship those nulls to the client.
        "institution_images": {
            k: v["logo_url"] for k, v in (
                json.loads(INSTITUTION_LOGOS_FILE.read_text(encoding="utf-8"))
                if INSTITUTION_LOGOS_FILE.exists() else {}
            ).items() if v.get("logo_url")
        },
        "venue_images": {
            k: v["logo_url"] for k, v in (
                json.loads(VENUE_LOGOS_FILE.read_text(encoding="utf-8"))
                if VENUE_LOGOS_FILE.exists() else {}
            ).items() if v.get("logo_url")
        },
        # "academic" / "industry" per institution, see classify_institution_
        # sector's docstring for the method and its limits. Only classified
        # institutions are included -- categories.html looks this up per
        # paper's institutions[] to tally an Industry % column, treating an
        # institution absent here as simply not counted either way.
        "institution_sector": {
            inst: sector for inst in inst_citations
            for sector in [classify_institution_sector(inst)] if sector
        },
        "category_breakdown": [{"category": cat, **vals}
                                for cat, vals in sorted(category_stats.items(), key=lambda kv: -kv[1]["citations"])],
        "author_detail": author_detail,
        "non_av_paper_counts": dict(non_av_paper_counts),
        "non_av_paper_citations": dict(non_av_paper_citations),
        # Precise "who actually works here" per institution -- see
        # institution_authors' comment above for why this exists separately
        # from the paper-level `institutions` field on each paper.
        "institution_authors": {
            inst: [
                {"name": name, "papers": v["papers"], "citations": v["citations"],
                 "first_year": v["first_year"], "last_year": v["last_year"]}
                for name, v in sorted(authors.items(), key=lambda kv: -kv[1]["papers"])
            ]
            for inst, authors in institution_authors.items()
        },
        "insights": insights,
    }
    # No indent -- same reasoning as stats_adjacent.json/the abstract shards
    # just below (indent=2's per-key newline+spacing roughly doubled this
    # file's size at corpus scale, which is what pushed it over GitHub's
    # 100MB file limit and got a gh-pages push rejected outright).
    OUT_FILE.write_text(json.dumps(stats, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote {OUT_FILE}")
    print(f"  {len(all_entries)} total papers, {len(entries)} core AV-relevant")
    print(f"  {len(papers)} ranked papers, {len(author_citations)} authors, "
          f"{len(inst_citations)} institutions, {len(country_citations)} countries")
    print(f"  papers_with_author_detail={n_with_author_detail} verified={n_verified} excluded_mismatch={n_excluded}")

    # Sharded abstracts -- see ABSTRACTS_DIR's comment above. The shard set
    # is fixed (always exactly ABSTRACT_SHARD_COUNT files, fixed names) and
    # every shard is fully overwritten below, so there's no stale-file risk
    # from a paper that's since been dropped/retitled -- no need to rmtree
    # the directory first. That's not just tidiness: an rmtree here hit a
    # real OneDrive directory-lock PermissionError in practice (this repo
    # lives in a synced OneDrive folder), so avoiding it outright is safer
    # than a retry loop.
    ABSTRACTS_DIR.mkdir(parents=True, exist_ok=True)
    shards = [{} for _ in range(ABSTRACT_SHARD_COUNT)]
    n_abstracts = 0
    for e in entries:
        title, abstract = e.get("title"), e.get("abstract")
        if title and abstract:
            shards[shard_index(title)][title] = abstract
            n_abstracts += 1
    for i, shard in enumerate(shards):
        shard_path = ABSTRACTS_DIR / f"shard-{i:02d}.json"
        shard_path.write_text(json.dumps(shard, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote {ABSTRACTS_DIR}: {n_abstracts} abstracts across {ABSTRACT_SHARD_COUNT} shards")

    # Separate, lazily-fetched file for adjacent (not core-AV-relevant)
    # papers -- see ADJACENT_OUT_FILE's own comment for why this isn't part
    # of stats.json. No per-paper detail page link (user-requested: no
    # paper.html pages generated for these, "too many"). adjacent_entries
    # itself is computed further up, alongside non_av_paper_counts.
    #
    # authors/institutions/countries ARE included here (unlike an earlier
    # version of this file, which omitted them to save size) -- the Show
    # dropdown that switches to this dataset lives on every listing page,
    # including Authors/Institutions/Countries/Network, and those pages
    # aggregate client-side by exactly these three fields (see filterPapers/
    # aggregateByDimension in filters.js). Without them every one of those
    # pages silently showed "0 results" the moment a reader switched to
    # "Not AV relevant" -- the dropdown looked wired up but did nothing.
    # institutions/countries will legitimately come back empty for most
    # adjacent papers (affiliation enrichment targets core AV papers only,
    # so authors_detail is rarely present here) -- that's honest, not a bug.
    adjacent_papers = []
    for e in adjacent_entries:
        adj_countries, adj_institutions = paper_countries_institutions(e)
        adjacent_papers.append({
            "title": e.get("title"), "year": e.get("year"), "venue": e.get("venue"),
            "category": e.get("category"), "citations": citation_count(e),
            "doi": e.get("doi"), "arxiv_url": e.get("arxiv_url"),
            "source": e.get("source") or "venue_listing",
            "av_relevance": "adjacent",
            "authors": paper_authors(e),
            "countries": adj_countries,
            "institutions": adj_institutions,
        })
    ADJACENT_OUT_FILE.write_text(json.dumps(adjacent_papers, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote {ADJACENT_OUT_FILE}: {len(adjacent_papers)} adjacent papers "
          f"({ADJACENT_OUT_FILE.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
