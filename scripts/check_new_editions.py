#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Monthly check for conference editions the corpus doesn't have yet. For every
covered conference it takes the years already in data/venues/, works out
which editions should exist by now (the ones after the newest year, plus any
gap in between), and asks each venue's own proceedings site whether they're
out. It's cheap: most sources need one small request per venue, and a few
indexes (CVF's menu, PMLR's volume list, one Crossref facet query) cover
several venues at once.

What happens to an edition that turns up:
  - if this checkout has a fetcher for it, the fetcher is run right away
    (unless --no-fetch), and the summary says how many papers it wrote;
  - if not, the summary says what's missing (a fetcher, a URL in a
    fetcher's table, a policy call).

Where each venue is probed:
  CVPR, ICCV, WACV, ACCV  openaccess.thecvf.com /menu and /menu_other.html,
                          then the edition's ?day=all listing
  ECCV                    the eccv.ecva.net virtual-site JSON, plus the
                          ecva.net papers page (which has abstracts)
  NeurIPS                 proceedings.neurips.cc (the virtual-site JSON is
                          only reported, the proceedings are what we fetch)
  ICLR                    iclr.cc virtual-site JSON
  ICML                    the PMLR volume index, else icml.cc virtual JSON
  CoRL                    the PMLR volume index
  AAAI                    ojs.aaai.org OAI-PMH (no fetcher yet: report only)
  BMVC                    bmvc<year>.bmva.org proceedings page
  RSS                     roboticsproceedings.org/rss<NN>/index.html
  GCPR                    Crossref, Springer book whose subtitle says
                          "DAGM GCPR <year>"
  IV, ITSC                Crossref, IEEE container title
  ICRA, IROS              Crossref, plus community paper lists on GitHub
                          from the owners in LIST_OWNERS only

The journals (T-ITS, RA-L, ...) aren't editions; the monthly job pulls them
with fetch_crossref.py's date-range mode instead.

Two more checks run on every call:
  - Listing drift: for editions from the last few years (--drift-years),
    the paper count in our file is compared with the live listing. A file
    that's clearly short means a fetch stopped partway (CVPR 2022 sat at
    774 of 2,074 papers for months); a live listing that's clearly shorter
    than our file means papers were withdrawn. Drift is only reported,
    unless --fetch-drift is given.
  - Whether DBLP answers again without its bot challenge. This is a single
    page request and a status report, nothing more.

Politeness: one request per second, the fetch_common User-Agent (with a
mailto), and a host that answers 403 or 429 twice is not asked again in the
same run. No Google Scholar, no DBLP API, nothing that needs a login.

Output: --json writes the machine-readable summary (schema below) and
--markdown appends a readable version, so it can point straight at
$GITHUB_STEP_SUMMARY. Without either, the markdown goes to stdout.

  {"checked_at", "today", "venues": {venue: {"years": [...], "probed": [...]}},
   "new_editions": [{"venue", "year", "source", "url", "papers", "command",
                     "status", "note", "papers_written", "missing_script"}],
   "notes": [{"venue", "year", "message"}],
   "drift": [{"venue", "year", "file", "local", "live", "source", "kind",
              "command", "status", "papers_written"}],
   "dblp": {"status": "blocked" | "reachable" | "error", "url", "detail"},
   "errors": [{"url", "error"}],
   "needs_attention": ["one line per thing a person should look at"]}

new_editions[].status is one of: fetched, fetch_failed, no_fetcher,
not_run (--no-fetch or --max-fetches reached). "command" is the fetcher that
would get the edition, relative to scripts/; "missing_script" is set when
that script isn't in this checkout (status no_fetcher). Exit status is 1 when a
fetch it started failed, 0 otherwise (probe errors don't fail the run: a
site being down for a day isn't something the monthly job can fix).

Usage:
  python scripts/check_new_editions.py --no-fetch            # report only
  python scripts/check_new_editions.py --json out.json --markdown "$GITHUB_STEP_SUMMARY"
  python scripts/check_new_editions.py --venues CVPR,RSS --drift-years 0
  python scripts/check_new_editions.py --fetch-drift --max-fetches 3
"""
import argparse
import ast
import datetime
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
from pathlib import Path

from fetch_common import OUT_DIR, fetch

SCRIPTS_DIR = Path(__file__).resolve().parent
FIRST_YEAR = 2012  # the corpus window

REQUEST_DELAY = 1.0
RETRY = dict(max_retries=2, retry_status=(429, 500, 502, 503, 504), backoff=10)

# A listing with fewer papers than this is a placeholder or a partial upload,
# not a new edition (the NeurIPS virtual JSON answers 200 with count 0 months
# before the conference).
MIN_PAPERS = 50
# Drift is reported when our file is short by more than both of these. A
# listing that shrank (withdrawn papers) is reported past DRIFT_MIN_MISSING
# alone, and only for sources whose count should match ours exactly.
DRIFT_TOLERANCE = 0.02
DRIFT_MIN_MISSING = 5

CVF = "https://openaccess.thecvf.com"
PMLR = "https://proceedings.mlr.press"
NEURIPS_PROC = "https://proceedings.neurips.cc"
ECVA_PAPERS = "https://www.ecva.net/papers.php"
RSS_BASE = "https://www.roboticsproceedings.org"
CROSSREF = "https://api.crossref.org/works"
AAAI_OAI = "https://ojs.aaai.org/index.php/AAAI/oai"
RAW_GITHUB = "https://raw.githubusercontent.com"
HRJP_INDEX = f"{RAW_GITHUB}/hrjp/ICRA-IROS-PaperList/main/README.md"
DBLP_PAGE = "https://dblp.org/db/conf/iclr/index.html"
VIRTUAL_HOSTS = {"ICLR": "iclr.cc", "ICML": "icml.cc", "NeurIPS": "neurips.cc", "ECCV": "eccv.ecva.net"}

# GitHub accounts whose ICRA/IROS paper lists we've read and trust. Search
# results are never used: a look-alike repo from an unknown account showed up
# for ICRA 2026 within days of the real one.
LIST_OWNERS = {"DoongLi", "ryanbgriffiths", "PaoPaoRobot", "dectrfov", "gonultasbu"}

# IEEE container titles on Crossref, one pattern per conference. The year in
# the title is the edition year.
IEEE_CONTAINERS = {
    "IV": re.compile(r"^(\d{4}) IEEE Intelligent Vehicles Symposium \(IV\)$"),
    "ITSC": re.compile(r"^(\d{4}) IEEE \d+\w\w International Conference on Intelligent Transportation Systems \(ITSC\)$"),
    "ICRA": re.compile(r"^(\d{4}) (?:IEEE )?International Conference on Robotics and Automation \(ICRA\)$"),
    "IROS": re.compile(r"^(\d{4}) IEEE/RSJ International Conference on Intelligent Robots and Systems \(IROS\)$"),
}

# venue -> (file prefix, years between editions, probe kind)
VENUES = {
    "CVPR": ("cvpr", 1, "cvf"),
    "ICCV": ("iccv", 2, "cvf"),
    "WACV": ("wacv", 1, "cvf"),
    "ACCV": ("accv", 2, "cvf"),
    "ECCV": ("eccv", 2, "eccv"),
    "NeurIPS": ("neurips", 1, "neurips"),
    "ICLR": ("iclr", 1, "iclr"),
    "ICML": ("icml", 1, "icml"),
    "CoRL": ("corl", 1, "corl"),
    "AAAI": ("aaai", 1, "aaai"),
    "BMVC": ("bmvc", 1, "bmvc"),
    "RSS": ("rss", 1, "rss"),
    "GCPR": ("gcpr", 1, "gcpr"),
    "IV": ("iv", 1, "ieee"),
    "ITSC": ("itsc", 1, "ieee"),
    "ICRA": ("icra", 1, "robotics"),
    "IROS": ("iros", 1, "robotics"),
}

_VENUE_FILE_RE = re.compile(r"^([a-z]+)(\d{4})(_github)?\.json$")


# ---------------------------------------------------------------------------
# Local state

def local_editions(venues_dir=OUT_DIR):
    """{prefix: {year: [file names]}} for every <prefix><year>[_github].json
    in data/venues. A proceedings file sorts before its _github twin."""
    out = {}
    for path in sorted(Path(venues_dir).glob("*.json")):
        m = _VENUE_FILE_RE.match(path.name)
        if m:
            out.setdefault(m.group(1), {}).setdefault(int(m.group(2)), []).append(path.name)
    return out


def paper_count(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return len(data) if isinstance(data, list) else None


def candidate_years(present, step, this_year, first_year=FIRST_YEAR):
    """Editions that should exist by this_year but have no file: every
    step-th year from the first one we have (so a biennial venue keeps its
    odd/even rhythm), skipping the ones present."""
    if not present:
        return list(range(first_year, this_year + 1))
    return [y for y in range(min(present), this_year + 1, step) if y not in present]


def script_literal(script, name, scripts_dir=SCRIPTS_DIR):
    """The value of a top-level literal assignment (a dict, list or tuple of
    plain values) in another script, read without importing it, so a
    fetcher's lookup tables can be checked without its import side effects.
    None when the script or the name isn't there."""
    path = Path(scripts_dir) / script
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            try:
                return ast.literal_eval(node.value)
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# HTTP

class Web:
    """Cached GETs with a pause between requests. A host that answers 403
    or 429 twice is left alone for the rest of the run. get() returns the
    body, or None for a 404 or a failure (failures are kept in .errors)."""

    def __init__(self, http_get=None, delay=REQUEST_DELAY):
        self.http_get = http_get or (lambda url: fetch(url, timeout=60, **RETRY))
        self.delay = delay
        self.cache = {}
        self.errors = []
        self.refusals = {}
        self.requests = 0

    def get(self, url, quiet_errors=False):
        if url in self.cache:
            return self.cache[url]
        host = urllib.parse.urlsplit(url).netloc
        if self.refusals.get(host, 0) >= 2:
            return None
        body = None
        self.requests += 1
        try:
            body = self.http_get(url)
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                self.refusals[host] = self.refusals.get(host, 0) + 1
            if e.code != 404 and not quiet_errors:
                self.errors.append({"url": url, "error": f"HTTP {e.code}"})
        except Exception as e:  # DNS failure, timeout, connection reset
            if not quiet_errors:
                self.errors.append({"url": url, "error": f"{type(e).__name__}: {e}"})
        if self.delay:
            time.sleep(self.delay)
        self.cache[url] = body
        return body

    def get_json(self, url, quiet_errors=False):
        body = self.get(url, quiet_errors)
        if body is None:
            return None
        try:
            return json.loads(body)
        except ValueError:
            self.errors.append({"url": url, "error": "not JSON"})
            return None


# ---------------------------------------------------------------------------
# Parsers (pure functions of a page body; the tests feed them fixtures)

def parse_cvf_menu(page):
    """{(conf, year)} for each main-conference link on CVF's /menu or
    /menu_other.html. Findings, workshops and demos have a suffix and are
    left out."""
    return {(c, int(y)) for c, y in re.findall(
        r'href="/?(CVPR|ICCV|WACV|ACCV)(\d{4})(?:\.py)?"', page or "")}


def count_cvf_listing(page):
    """Papers on an openaccess.thecvf.com ?day=all page, or None when the
    page is CVF's broken-date error (CVPR 2018-2020 need per-day pages)."""
    if page is None or "Error 1525" in page or "Incorrect DATE value" in page:
        return None
    return page.count('<dt class="ptitle">')


def parse_pmlr_index(page):
    """{("ICML"|"CoRL", year): volume} from the proceedings.mlr.press index.
    Only exact "Proceedings of ICML 2025" lines count; workshop volumes
    ("Proceedings of ICML 2022 Workshop on ...", "GRaM at ICML 2024") don't."""
    out = {}
    for vol, venue, year in re.findall(
            r'<a href="v(\d+)"><b>Volume \d+</b></a>\s*Proceedings of (ICML|CoRL) (\d{4})\s*</li>', page or ""):
        out[(venue, int(year))] = int(vol)
    return out


def count_pmlr_volume(page):
    return (page or "").count('<div class="paper">')


def count_virtual_site(dump, venue, year):
    """Unique main-track papers in a miniconf orals-posters dump. Orals are
    listed next to their poster, so titles are deduplicated. When no event
    names its OpenReview group (older dumps), every event counts."""
    if not isinstance(dump, dict):
        return 0
    tracks = ("Conference", "Position_Paper_Track") if venue == "ICML" else ("Conference",)
    results = dump.get("results") or []
    with_group = [r for r in results if "openreview.net/group" in (r.get("sourceurl") or "")]
    if with_group:
        results = [r for r in with_group
                   if any((r.get("sourceurl") or "").endswith(f"/{year}/{t}") for t in tracks)]
    titles = {re.sub(r"[^a-z0-9]", "", (r.get("name") or "").lower()) for r in results}
    titles.discard("")
    return len(titles)


def neurips_main_volume(page, year):
    """The "vol<N>-main-conference" link on a NeurIPS year page (2025 on),
    or None for the older layout where the year page is the listing."""
    m = re.search(rf'href="(/paper_files/paper/{year}/vol\d+-main-conference)"', page or "")
    return m.group(1) if m else None


def count_neurips_listing(page):
    return len(set(re.findall(r'/paper_files/paper/\d+/hash/[0-9a-f]+-Abstract(?:-Conference)?\.html', page or "")))


def count_ecva_sections(page):
    """{year: papers} for every "ECCV <year> Papers" section on ecva.net."""
    out = {}
    for m in re.finditer(r"ECCV (\d{4}) Papers(.*?)(?=ECCV \d{4} Papers|\Z)", page or "", re.S):
        out[int(m.group(1))] = m.group(2).count('<dt class="ptitle">')
    return out


def count_rss_index(page):
    return len(set(re.findall(r'<a href="(p\d+\.html)"', page or "")))


def count_bmvc_listing(page):
    return len(set(re.findall(r'href="[^"]*/proceedings/(\d+)/"', page or "")))


def parse_aaai_oai(page):
    """{year: records} for the AAAI-<yy> editions named in an OAI-PMH
    ListRecords page (dc:source reads "...: AAAI-26 Technical Tracks 1; ...")."""
    out = {}
    for yy in re.findall(r"<dc:source[^>]*>[^<]*\bAAAI-(\d\d)\b", page or ""):
        year = 2000 + int(yy)
        out[year] = out.get(year, 0) + 1
    return out


def parse_gcpr_books(message):
    """{year: [ISBNs]} from a Crossref book search: the GCPR proceedings are
    titled "Pattern Recognition" with the edition in the subtitle ("46th DAGM
    German Conference, DAGM GCPR 2024, ..."). Parts I and II of one year are
    separate books, so their ISBNs are pooled."""
    out = {}
    for item in (message or {}).get("items") or []:
        m = re.search(r"\bDAGM GCPR (\d{4})\b", " ".join(item.get("subtitle") or []))
        if m:
            isbns = out.setdefault(int(m.group(1)), [])
            isbns.extend(i for i in item.get("ISBN") or [] if i not in isbns)
    return out


def parse_ieee_facets(message):
    """{(venue, year): records} from a Crossref container-title facet."""
    values = (((message or {}).get("facets") or {}).get("container-title") or {}).get("values") or {}
    out = {}
    for title, count in values.items():
        for venue, pattern in IEEE_CONTAINERS.items():
            m = pattern.match(title.strip())
            if m:
                out[(venue, int(m.group(1)))] = count
    return out


def parse_list_index(text):
    """{(venue, year): ["owner/repo", ...]} from the hrjp ICRA/IROS index
    README, keeping only repos whose owner is in LIST_OWNERS."""
    out = {}
    venue = None
    for line in (text or "").splitlines():
        heading = re.match(r"#+\s*(ICRA|IROS)\b", line)
        if heading:
            venue = heading.group(1)
            continue
        row = re.match(r"\|\s*(\d{4})\s*\|", line)
        if not (venue and row):
            continue
        for owner, repo in re.findall(r"\[([\w.-]+)/([\w.-]+)\]\(https://github\.com/", line):
            if owner in LIST_OWNERS:
                out.setdefault((venue, int(row.group(1))), []).append(f"{owner}/{repo}")
    return out


def count_markdown_table_rows(text):
    """Data rows across the markdown tables in a paper-list README (header
    and separator rows not counted)."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip().startswith("|")]
    separators = sum(1 for l in lines if re.match(r"^\|\s*:?-{3,}", l))
    return max(0, len(lines) - 2 * separators)


def dblp_status(body):
    if body is None:
        return "error"
    low = body.lower()
    if "anubis" in low or "not a bot" in low:
        return "blocked"
    return "reachable" if "dblp" in low else "error"


# ---------------------------------------------------------------------------
# Probes. Each returns a list of findings for the candidate years:
# {"venue", "year", "source", "url", "papers", "command", "note"}, where a
# command of None means there's nothing here that can fetch it.

def _finding(venue, year, source, url, papers, command=None, note=""):
    return {"venue": venue, "year": year, "source": source, "url": url,
            "papers": papers, "command": command, "note": note}


class Checker:
    def __init__(self, web, today, scripts_dir=SCRIPTS_DIR, venues_dir=OUT_DIR, drift_years=5):
        self.web = web
        self.today = today
        self.scripts_dir = Path(scripts_dir)
        self.venues_dir = Path(venues_dir)
        self.drift_years = drift_years
        self.local = local_editions(venues_dir)
        self.notes = []

    # -- helpers --------------------------------------------------------------

    def has_script(self, name):
        return (self.scripts_dir / name).exists()

    def command(self, script, *args):
        """[script, args...]. run() marks the ones whose script isn't in this
        checkout, so the summary still says what would fetch the edition."""
        return [script, *[str(a) for a in args]]

    def note(self, venue, year, message):
        self.notes.append({"venue": venue, "year": year, "message": message})

    def cvf_editions(self):
        found = set()
        for page in ("menu", "menu_other.html"):
            found |= parse_cvf_menu(self.web.get(f"{CVF}/{page}"))
        return found

    def cvf_count(self, conf, year):
        url = f"{CVF}/{conf}{year}?day=all"
        return url, count_cvf_listing(self.web.get(url))

    def pmlr_volumes(self):
        return parse_pmlr_index(self.web.get(f"{PMLR}/"))

    def pmlr_count(self, volume):
        url = f"{PMLR}/v{volume}/"
        return url, count_pmlr_volume(self.web.get(url))

    def virtual_count(self, venue, year):
        url = f"https://{VIRTUAL_HOSTS[venue]}/static/virtual/data/{venue.lower()}-{year}-orals-posters.json"
        return url, count_virtual_site(self.web.get_json(url), venue, year)

    def neurips_count(self, year):
        url = f"{NEURIPS_PROC}/paper_files/paper/{year}"
        page = self.web.get(url)
        vol = neurips_main_volume(page, year)
        if vol:
            url = f"{NEURIPS_PROC}{vol}"
            page = self.web.get(url)
        return url, count_neurips_listing(page)

    def ecva_sections(self):
        return count_ecva_sections(self.web.get(ECVA_PAPERS))

    def ieee_counts(self, since_year=None):
        """{(venue, year): records} for the IEEE conferences. One Crossref
        facet query covers all four; the start year is the same for every
        caller unless an older gap needs probing, so it's usually one request."""
        since_year = min(since_year or 9999, self.today.year - max(self.drift_years - 1, 1))
        params = {
            "filter": f"prefix:10.1109,type:proceedings-article,from-pub-date:{since_year}-01-01",
            "rows": 0,
            "facet": "container-title:*",
            "query.container-title": "Robotics Automation Intelligent Robots Vehicles Transportation Systems",
        }
        url = f"{CROSSREF}?{urllib.parse.urlencode(params)}"
        data = self.web.get_json(url)
        return url, parse_ieee_facets((data or {}).get("message"))

    # -- per-venue probes -----------------------------------------------------

    def probe_cvf(self, venue, years):
        listed = self.cvf_editions()
        out = []
        for year in years:
            if (venue, year) not in listed:
                continue
            url, n = self.cvf_count(venue, year)
            if n is None or n < MIN_PAPERS:
                self.note(venue, year, f"CVF lists {venue} {year} but its listing has {n or 0} papers so far")
                continue
            out.append(_finding(venue, year, "CVF open access", url, n,
                                self.command("fetch_cvf_history.py", venue, year, year)))
        return out

    def probe_eccv(self, venue, years):
        out = []
        ecva = self.ecva_sections() if years else {}
        for year in years:
            url, n = self.virtual_count("ECCV", year)
            if n >= MIN_PAPERS:
                note = ""
                if ecva.get(year):
                    note = (f"ecva.net also has this edition ({ecva[year]} papers, with abstracts); "
                            f"add {year} to YEARS in fetch_ecva_history.py for the abstracts")
                out.append(_finding(venue, year, "ECCV virtual site", url, n,
                                    self.command("fetch_virtual_site.py", "ECCV", year), note))
            elif ecva.get(year):
                out.append(_finding(venue, year, "ecva.net", ECVA_PAPERS, ecva[year], None,
                                    f"add {year} to YEARS in fetch_ecva_history.py"))
        return out

    def probe_neurips(self, venue, years):
        out = []
        for year in years:
            url, n = self.neurips_count(year)
            if n >= MIN_PAPERS:
                out.append(_finding(venue, year, "NeurIPS proceedings", url, n,
                                    self.command("fetch_neurips_history.py", year, year)))
                continue
            vurl, vn = self.virtual_count("NeurIPS", year)
            if vn >= MIN_PAPERS:
                self.note(venue, year, f"neurips.cc lists {vn} accepted papers; waiting for "
                                       f"proceedings.neurips.cc, which has the abstracts")
        return out

    def probe_iclr(self, venue, years):
        out = []
        for year in years:
            url, n = self.virtual_count("ICLR", year)
            if n >= MIN_PAPERS:
                out.append(_finding(venue, year, "ICLR virtual site", url, n,
                                    self.command("fetch_virtual_site.py", "ICLR", year, "--abstracts")))
        return out

    def probe_icml(self, venue, years):
        volumes = self.pmlr_volumes() if years else {}
        out = []
        for year in years:
            vol = volumes.get(("ICML", year))
            if vol:
                url, n = self.pmlr_count(vol)
                if n >= MIN_PAPERS:
                    out.append(_finding(venue, year, f"PMLR volume {vol}", url, n,
                                        self.command("fetch_pmlr.py", "ICML", year, "--volume", vol)))
                    continue
            url, n = self.virtual_count("ICML", year)
            if n >= MIN_PAPERS:
                out.append(_finding(venue, year, "ICML virtual site", url, n,
                                    self.command("fetch_virtual_site.py", "ICML", year, "--abstracts"),
                                    "PMLR has no volume for it yet"))
        return out

    def probe_corl(self, venue, years):
        volumes = self.pmlr_volumes() if years else {}
        out = []
        for year in years:
            vol = volumes.get(("CoRL", year))
            if not vol:
                continue
            url, n = self.pmlr_count(vol)
            if n >= MIN_PAPERS:
                out.append(_finding(venue, year, f"PMLR volume {vol}", url, n,
                                    self.command("fetch_pmlr.py", "CoRL", year, "--volume", vol)))
        return out

    def probe_aaai(self, venue, years):
        if not years:
            return []
        since = (self.today - datetime.timedelta(days=62)).isoformat()
        url = f"{AAAI_OAI}?verb=ListRecords&metadataPrefix=oai_dc&from={since}"
        found = parse_aaai_oai(self.web.get(url))
        return [_finding(venue, year, "ojs.aaai.org OAI-PMH", url, None, None,
                         "there's no AAAI fetcher yet (it came from DBLP); an OAI-PMH harvester "
                         "would also bring abstracts")
                for year in years if found.get(year)]

    def probe_bmvc(self, venue, years):
        listings = script_literal("fetch_bmvc.py", "LISTINGS", self.scripts_dir) or {}
        out = []
        for year in years:
            url = listings.get(year) or f"https://bmvc{year}.bmva.org/proceedings/conference-proceedings/"
            # Next year's site often doesn't exist yet, so a DNS failure here is normal.
            n = count_bmvc_listing(self.web.get(url, quiet_errors=True))
            if n < MIN_PAPERS:
                continue
            if year in listings:
                out.append(_finding(venue, year, "BMVC proceedings", url, n, self.command("fetch_bmvc.py", year)))
            else:
                out.append(_finding(venue, year, "BMVC proceedings", url, n, None,
                                    "BMVC's site layout changes every year: add the URL to LISTINGS in "
                                    "fetch_bmvc.py and check the parser against it"))
        return out

    def probe_rss(self, venue, years):
        out = []
        for year in years:
            url = f"{RSS_BASE}/rss{year - 2004:02d}/index.html"
            n = count_rss_index(self.web.get(url))
            if n >= MIN_PAPERS:
                out.append(_finding(venue, year, "roboticsproceedings.org", url, n,
                                    self.command("fetch_rss.py", year)))
        return out

    def probe_gcpr(self, venue, years):
        if not years:
            return []
        params = {"query.bibliographic": "DAGM German Conference on Pattern Recognition GCPR",
                  "filter": f"type:book,prefix:10.1007,from-pub-date:{min(years)}-01-01",
                  "select": "DOI,title,subtitle,ISBN", "rows": 40}
        url = f"{CROSSREF}?{urllib.parse.urlencode(params)}"
        books = parse_gcpr_books(((self.web.get_json(url)) or {}).get("message"))
        known = (script_literal("fetch_crossref.py", "PROCEEDINGS_ISBNS", self.scripts_dir) or {}).get("GCPR", {})
        out = []
        for year in years:
            if year not in books:
                continue
            if year in known:
                out.append(_finding(venue, year, "Crossref (Springer)", url, None,
                                    self.command("fetch_crossref.py", "GCPR", year)))
            else:
                out.append(_finding(venue, year, "Crossref (Springer)", url, None, None,
                                    f"add {year}: {books[year]} to PROCEEDINGS_ISBNS['GCPR'] in fetch_crossref.py"))
        return out

    def probe_ieee(self, venue, years):
        if not years:
            return []
        url, counts = self.ieee_counts(min(years))
        out = []
        for year in years:
            n = counts.get((venue, year), 0)
            if n >= MIN_PAPERS:
                out.append(_finding(venue, year, "Crossref (IEEE)", url, n,
                                    self.command("fetch_crossref.py", venue, year)))
        return out

    def probe_robotics(self, venue, years):
        """ICRA/IROS: Crossref once IEEE has deposited the proceedings, a
        community list before that."""
        out = []
        crossref = self.probe_ieee(venue, years)
        by_year = {f["year"]: f for f in crossref}
        repos = script_literal("fetch_github_paper_lists.py", "REPOS", self.scripts_dir) or []
        configured = {(r[0], r[1]): r[2] for r in repos if len(r) >= 3}
        index = None
        for year in years:
            if year in by_year:
                out.append(by_year[year])
                continue
            if index is None:
                index = parse_list_index(self.web.get(HRJP_INDEX))
            candidates = list(dict.fromkeys(
                index.get((venue, year), []) + [f"DoongLi/{venue}{year}-Paper-List"]))
            for repo in candidates:
                url = f"{RAW_GITHUB}/{repo}/main/README.md"
                n = count_markdown_table_rows(self.web.get(url, quiet_errors=True))
                if n < MIN_PAPERS:
                    continue
                if configured.get((year, venue)) == repo:
                    cmd = self.command("fetch_github_paper_lists.py")
                    out.append(_finding(venue, year, f"GitHub list {repo}", url, n, cmd))
                else:
                    out.append(_finding(venue, year, f"GitHub list {repo}", url, n, None,
                                        f'add ({year}, "{venue}", "{repo}", "main", "table_semicolon") to REPOS '
                                        f"in fetch_github_paper_lists.py after checking the table's author format"))
                break
        return out

    PROBES = {
        "cvf": probe_cvf, "eccv": probe_eccv, "neurips": probe_neurips, "iclr": probe_iclr,
        "icml": probe_icml, "corl": probe_corl, "aaai": probe_aaai, "bmvc": probe_bmvc,
        "rss": probe_rss, "gcpr": probe_gcpr, "ieee": probe_ieee, "robotics": probe_robotics,
    }

    # -- drift ----------------------------------------------------------------

    def drift_window(self, prefix):
        first = self.today.year - self.drift_years + 1
        return sorted(y for y in self.local.get(prefix, {}) if y >= first) if self.drift_years > 0 else []

    def _drift_entry(self, venue, year, file_name, live, source, exact, command):
        local = paper_count(self.venues_dir / file_name)
        if local is None or live is None or live < MIN_PAPERS:
            return None
        missing = live - local
        if missing > max(DRIFT_MIN_MISSING, DRIFT_TOLERANCE * live):
            kind = "missing_papers"
        elif exact and -missing > DRIFT_MIN_MISSING:
            kind = "listing_shrank"
            command = None  # withdrawn papers: a person decides whether to drop them
        else:
            return None
        return {"venue": venue, "year": year, "file": file_name, "local": local, "live": live,
                "source": source, "kind": kind, "command": command}

    def check_drift(self, venues):
        out = []
        ecva = None
        volumes = None
        ieee = None
        for venue in venues:
            prefix, _, kind = VENUES[venue]
            for year in self.drift_window(prefix):
                files = self.local[prefix][year]
                main = next((f for f in files if not f.endswith("_github.json")), None)
                if main is None:
                    # A community-list year: compare nothing, but say when the
                    # official record has arrived for the latest ones.
                    if kind == "robotics" and year >= self.today.year - 1:
                        if ieee is None:
                            ieee = self.ieee_counts()[1]
                        n = ieee.get((venue, year), 0)
                        if n >= MIN_PAPERS:
                            self.note(venue, year, f"Crossref now has {venue} {year} ({n} papers); "
                                                   f"fetch_crossref.py {venue} {year} would add DOIs and affiliations")
                    continue
                entry = None
                if kind == "cvf":
                    url, n = self.cvf_count(venue, year)
                    entry = self._drift_entry(venue, year, main, n, url, True,
                                              self.command("fetch_cvf_history.py", venue, year, year))
                elif kind == "eccv":
                    if ecva is None:
                        ecva = self.ecva_sections()
                    if ecva.get(year):
                        entry = self._drift_entry(venue, year, main, ecva[year], ECVA_PAPERS, True, None)
                    else:
                        url, n = self.virtual_count("ECCV", year)
                        entry = self._drift_entry(venue, year, main, n, url, False,
                                                  self.command("fetch_virtual_site.py", "ECCV", year, "--force"))
                elif kind == "neurips":
                    url, n = self.neurips_count(year)
                    entry = self._drift_entry(venue, year, main, n, url, True,
                                              self.command("fetch_neurips_history.py", year, year))
                elif kind in ("icml", "corl"):
                    if volumes is None:
                        volumes = self.pmlr_volumes()
                    vol = volumes.get((venue, year))
                    if vol:
                        url, n = self.pmlr_count(vol)
                        entry = self._drift_entry(venue, year, main, n, url, True,
                                                  self.command("fetch_pmlr.py", venue, year, "--volume", vol, "--force"))
                elif kind == "iclr":
                    url, n = self.virtual_count("ICLR", year)
                    entry = self._drift_entry(venue, year, main, n, url, False,
                                              self.command("fetch_virtual_site.py", "ICLR", year, "--abstracts", "--force"))
                elif kind == "rss":
                    url = f"{RSS_BASE}/rss{year - 2004:02d}/index.html"
                    entry = self._drift_entry(venue, year, main, count_rss_index(self.web.get(url)) or None,
                                              url, True, self.command("fetch_rss.py", year))
                elif kind in ("ieee", "robotics"):
                    if ieee is None:
                        ieee = self.ieee_counts()[1]
                    entry = self._drift_entry(venue, year, main, ieee.get((venue, year)), "Crossref (IEEE)", False,
                                              self.command("fetch_crossref.py", venue, year))
                if entry:
                    out.append(entry)
        return out

    # -- the whole run --------------------------------------------------------

    def run(self, venues):
        summary = {"venues": {}, "new_editions": [], "drift": []}
        for venue in venues:
            prefix, step, kind = VENUES[venue]
            present = sorted(self.local.get(prefix, {}))
            years = candidate_years(present, step, self.today.year)
            summary["venues"][venue] = {"years": present, "probed": years}
            if years:
                summary["new_editions"].extend(self.PROBES[kind](self, venue, years))
        summary["drift"] = self.check_drift(venues)
        body = self.web.get(DBLP_PAGE, quiet_errors=True)
        summary["dblp"] = {"status": dblp_status(body), "url": DBLP_PAGE,
                           "detail": "bot challenge still up" if dblp_status(body) == "blocked" else ""}
        for item in summary["new_editions"] + summary["drift"]:
            cmd = item.get("command")
            if cmd and not self.has_script(cmd[0]):
                item["missing_script"] = cmd[0]
                item["note"] = "; ".join(filter(None, [item.get("note"), f"{cmd[0]} isn't in this checkout"]))
        summary["notes"] = self.notes
        summary["errors"] = self.web.errors
        return summary


# ---------------------------------------------------------------------------
# Fetching and reporting

def expected_file(venue, year, command):
    prefix = VENUES[venue][0]
    suffix = "_github" if command and command[0] == "fetch_github_paper_lists.py" else ""
    return f"{prefix}{year}{suffix}.json"


def run_fetches(items, venues_dir, scripts_dir, max_fetches, timeout, runner=None):
    """Runs each item's command in turn and records the outcome on the item.
    Returns how many were started."""
    runner = runner or (lambda cmd: subprocess.run(
        [sys.executable, str(Path(scripts_dir) / cmd[0]), *cmd[1:]], cwd=scripts_dir, timeout=timeout).returncode)
    started = 0
    for item in items:
        if not item.get("command") or item.get("missing_script"):
            item["status"] = "no_fetcher"
            continue
        if max_fetches is not None and started >= max_fetches:
            item["status"] = "not_run"
            continue
        started += 1
        print(f"\n--- {item['venue']} {item['year']}: {' '.join(item['command'])}", flush=True)
        try:
            code = runner(item["command"])
        except subprocess.TimeoutExpired:
            code = "timeout"
        out_file = Path(venues_dir) / expected_file(item["venue"], item["year"], item["command"])
        written = paper_count(out_file) if out_file.exists() else None
        item["papers_written"] = written
        if code == 0 and written:
            item["status"] = "fetched"
        else:
            item["status"] = "fetch_failed"
            item["note"] = " ".join(filter(None, [item.get("note"), f"exit status {code}, "
                                                  f"{out_file.name} {'has ' + str(written) + ' papers' if written else 'not written'}"]))
    return started


def needs_attention(summary):
    lines = []
    for e in summary["new_editions"]:
        what = f"{e['venue']} {e['year']} ({e['papers'] or '?'} papers, {e['source']})"
        if e["status"] == "no_fetcher":
            lines.append(f"New edition {what}: {e['note'] or 'no fetcher in this checkout'}")
        elif e["status"] == "fetch_failed":
            lines.append(f"New edition {what}: fetch failed, {e['note']}")
        elif e["status"] == "not_run":
            lines.append(f"New edition {what}: not fetched in this run")
    for d in summary["drift"]:
        if d.get("status") == "fetched":
            continue
        verb = "is missing papers" if d["kind"] == "missing_papers" else "has papers the live listing dropped"
        lines.append(f"{d['file']} {verb}: {d['local']} here, {d['live']} on {d['source']}")
    if summary["dblp"]["status"] == "reachable":
        lines.append("DBLP answers without its bot challenge again")
    return lines


def _cmd(c):
    return f"`python scripts/{' '.join(c)}`" if c else ""


def to_markdown(summary):
    out = [f"## New-edition check, {summary['today']}", ""]
    news = summary["new_editions"]
    if news:
        out += ["| Venue | Year | Papers | Source | Status | Command / what's needed |",
                "| --- | --- | --- | --- | --- | --- |"]
        for e in news:
            detail = "; ".join(filter(None, [_cmd(e.get("command")), e.get("note")]))
            status = e["status"] + (f" ({e['papers_written']} written)" if e.get("papers_written") else "")
            out.append(f"| {e['venue']} | {e['year']} | {e['papers'] or '?'} | {e['source']} | {status} | {detail} |")
    else:
        out.append("No new editions.")
    if summary["notes"]:
        out += ["", "Notes:", ""] + [f"- {n['venue']} {n['year']}: {n['message']}" for n in summary["notes"]]
    out.append("")
    if summary["drift"]:
        out += ["Listing drift:", "", "| File | Here | Live | Source | Command |", "| --- | --- | --- | --- | --- |"]
        for d in summary["drift"]:
            status = f" ({d['status']})" if d.get("status") else ""
            out.append(f"| {d['file']} | {d['local']} | {d['live']} | {d['source']} | {_cmd(d.get('command'))}{status} |")
    else:
        out.append("No listing drift in the checked years.")
    out += ["", f"DBLP: {summary['dblp']['status']}."]
    if summary["errors"]:
        out += ["", "Probe errors (the venue is checked again next month):", ""]
        out += [f"- {e['url']}: {e['error']}" for e in summary["errors"]]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Find (and fetch) conference editions the corpus doesn't have yet.")
    ap.add_argument("--venues", help="comma-separated subset, e.g. CVPR,RSS (default: all)")
    ap.add_argument("--no-fetch", action="store_true", help="report only, never run a fetcher")
    ap.add_argument("--fetch-drift", action="store_true", help="also rerun the fetcher for files that are short")
    ap.add_argument("--max-fetches", type=int, help="run at most this many fetchers")
    ap.add_argument("--fetch-timeout", type=int, help="seconds before a fetcher is stopped")
    ap.add_argument("--drift-years", type=int, default=5, help="compare listing counts for this many recent years (0 = off)")
    ap.add_argument("--json", help="write the summary as JSON to this file")
    ap.add_argument("--markdown", help="append the summary as markdown to this file")
    ap.add_argument("--today", help="pretend it's this date (YYYY-MM-DD)")
    args = ap.parse_args(argv)

    today = datetime.date.fromisoformat(args.today) if args.today else datetime.date.today()
    venues = list(VENUES)
    if args.venues:
        wanted = {v.strip().lower() for v in args.venues.split(",")}
        venues = [v for v in VENUES if v.lower() in wanted]
        unknown = wanted - {v.lower() for v in venues}
        if unknown:
            raise SystemExit(f"unknown venue(s): {', '.join(sorted(unknown))}; known: {', '.join(VENUES)}")

    checker = Checker(Web(), today, drift_years=args.drift_years)
    summary = checker.run(venues)
    summary = {"checked_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "today": today.isoformat(), **summary}
    print(f"{checker.web.requests} requests; {len(summary['new_editions'])} new editions, "
          f"{len(summary['drift'])} drift entries, DBLP {summary['dblp']['status']}", flush=True)

    to_fetch = summary["new_editions"] + (summary["drift"] if args.fetch_drift else [])
    if args.no_fetch:
        for item in to_fetch:
            item["status"] = "not_run" if item.get("command") and not item.get("missing_script") else "no_fetcher"
    else:
        run_fetches(to_fetch, OUT_DIR, SCRIPTS_DIR, args.max_fetches, args.fetch_timeout)
    for e in summary["new_editions"]:
        e.setdefault("status", "no_fetcher")
    summary["needs_attention"] = needs_attention(summary)

    if args.json:
        Path(args.json).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md = to_markdown(summary)
    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as f:
            f.write(md)
    if not args.json and not args.markdown:
        print(md)
    failed = [i for i in to_fetch if i.get("status") == "fetch_failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
