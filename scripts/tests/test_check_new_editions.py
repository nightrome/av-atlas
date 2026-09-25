#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for check_new_editions.py. No network: every probe goes through a fake
Web whose pages are trimmed copies of what each site served on 2026-09-23
(CVF menu, PMLR index, virtual-site JSON, ecva.net, roboticsproceedings.org,
Crossref facets, the AAAI OAI feed, the hrjp list index).

Usage: python -m unittest discover -s scripts/tests
"""
import datetime
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_new_editions as cne

TODAY = datetime.date(2026, 9, 24)


def http_error(url, code):
    return urllib.error.HTTPError(url, code, "error", {}, None)


class FakeWeb(cne.Web):
    """A Web whose pages come from a dict. A missing URL is a 404; a value
    that's an int is that HTTP error."""

    def __init__(self, pages):
        def http_get(url):
            if url not in pages:
                raise http_error(url, 404)
            page = pages[url]
            if isinstance(page, int):
                raise http_error(url, page)
            return page if isinstance(page, str) else json.dumps(page)
        super().__init__(http_get=http_get, delay=0)


def cvf_listing(n):
    return "".join(f'<dt class="ptitle"><br><a href="/content/X/html/p{i}_paper.html">P{i}</a></dt>\n'
                   for i in range(n))


def rss_index(n):
    return "".join(f'<a href="p{i:03d}.html">Paper {i}</a>\n' for i in range(1, n + 1))


def virtual_dump(titles, group="https://openreview.net/group?id=ICLR.cc/2026/Conference"):
    return {"count": len(titles), "results": [{"name": t, "sourceurl": group} for t in titles]}


class WorkDir:
    """A temporary scripts/ and data/venues/ pair."""

    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.scripts = root / "scripts"
        self.venues = root / "venues"
        self.scripts.mkdir()
        self.venues.mkdir()

    def venue(self, name, n):
        (self.venues / name).write_text(json.dumps([{"title": f"T{i}"} for i in range(n)]), encoding="utf-8")

    def script(self, name, text="print('ok')\n"):
        (self.scripts / name).write_text(text, encoding="utf-8")

    def checker(self, pages, drift_years=0):
        return cne.Checker(FakeWeb(pages), TODAY, scripts_dir=self.scripts,
                           venues_dir=self.venues, drift_years=drift_years)

    def close(self):
        self.tmp.cleanup()


class LocalStateTests(unittest.TestCase):
    def test_candidate_years_annual_with_gap(self):
        self.assertEqual(cne.candidate_years([2022, 2024, 2025], 1, 2026), [2023, 2026])

    def test_candidate_years_keeps_biennial_rhythm(self):
        # ECCV is held in even years; ICCV in odd years.
        self.assertEqual(cne.candidate_years([2012, 2014, 2016, 2018], 2, 2026), [2020, 2022, 2024, 2026])
        self.assertEqual(cne.candidate_years([2021, 2023, 2025], 2, 2026), [])

    def test_candidate_years_for_a_venue_with_no_files(self):
        self.assertEqual(cne.candidate_years([], 1, 2014), [2012, 2013, 2014])

    def test_local_editions_reads_only_edition_files(self):
        w = WorkDir()
        try:
            for name in ("icra2025.json", "icra2025_github.json", "iros2024_github.json",
                         "tits_all.json", "arxiv_s2_citing.json", "cvpr2022.json.partial"):
                w.venue(name, 1)
            local = cne.local_editions(w.venues)
            self.assertEqual(local, {"icra": {2025: ["icra2025.json", "icra2025_github.json"]},
                                     "iros": {2024: ["iros2024_github.json"]}})
        finally:
            w.close()

    def test_script_literal_reads_a_table_without_importing(self):
        w = WorkDir()
        try:
            w.script("fetch_x.py", 'import no_such_module\nLISTINGS = {2025: "https://a/"}\n')
            self.assertEqual(cne.script_literal("fetch_x.py", "LISTINGS", w.scripts), {2025: "https://a/"})
            self.assertIsNone(cne.script_literal("fetch_x.py", "OTHER", w.scripts))
            self.assertIsNone(cne.script_literal("missing.py", "LISTINGS", w.scripts))
        finally:
            w.close()


class ParserTests(unittest.TestCase):
    def test_cvf_menu_skips_findings_workshops_and_demos(self):
        menu = ('<a href="CVPR2026">x</a><a href="CVPR2026_findings">x</a>'
                '<a href="/WACV2026_workshops/menu">x</a><a href="CVPR2022_demos">x</a>'
                '<a href="ICCV2019.py">x</a><a href="/ACCV2024">x</a>')
        self.assertEqual(cne.parse_cvf_menu(menu), {("CVPR", 2026), ("ICCV", 2019), ("ACCV", 2024)})

    def test_cvf_listing_count_and_broken_date_page(self):
        self.assertEqual(cne.count_cvf_listing(cvf_listing(3)), 3)
        self.assertIsNone(cne.count_cvf_listing("Error 1525: Incorrect DATE value"))
        self.assertIsNone(cne.count_cvf_listing(None))

    def test_pmlr_index_ignores_workshop_volumes(self):
        index = ('<li><a href="v305"><b>Volume 305</b></a> Proceedings of CoRL 2025</li>\n'
                 '<li><a href="v267"><b>Volume 267</b></a> Proceedings of ICML 2025</li>\n'
                 '<li><a href="v292"><b>Volume 292</b></a> TerraBytes at ICML 2025</li>\n'
                 '<li><a href="v251"><b>Volume 251</b></a> Proceedings of GRaM at ICML 2024</li>\n'
                 '<li><a href="v184"><b>Volume 184</b></a> Proceedings of ICML 2022 Workshop on Healthcare AI</li>\n')
        self.assertEqual(cne.parse_pmlr_index(index), {("CoRL", 2025): 305, ("ICML", 2025): 267})

    def test_virtual_site_counts_main_track_once_per_title(self):
        conf = "https://openreview.net/group?id=ICML.cc/2025/Conference"
        dump = {"results": [
            {"name": "A Paper", "sourceurl": conf},
            {"name": "A paper", "sourceurl": conf},  # the oral next to its poster
            {"name": "Position: B", "sourceurl": "https://openreview.net/group?id=ICML.cc/2025/Position_Paper_Track"},
            {"name": "A TMLR paper", "sourceurl": "https://openreview.net/group?id=TMLR"},
        ]}
        self.assertEqual(cne.count_virtual_site(dump, "ICML", 2025), 2)
        self.assertEqual(cne.count_virtual_site(dump, "ICLR", 2025), 1)
        self.assertEqual(cne.count_virtual_site({"count": 0, "results": []}, "NeurIPS", 2026), 0)
        self.assertEqual(cne.count_virtual_site(None, "ICLR", 2026), 0)

    def test_neurips_follows_the_main_conference_volume(self):
        year_page = '<a href="/paper_files/paper/2025/vol38-main-conference">Main</a>'
        self.assertEqual(cne.neurips_main_volume(year_page, 2025), "/paper_files/paper/2025/vol38-main-conference")
        self.assertIsNone(cne.neurips_main_volume("<a href='/x'>", 2024))
        listing = ('<a href="/paper_files/paper/2024/hash/00ab-Abstract-Conference.html">A</a>'
                   '<a href="/paper_files/paper/2024/hash/00ab-Abstract-Conference.html">A</a>'
                   '<a href="/paper_files/paper/2019/hash/ff01-Abstract.html">B</a>')
        self.assertEqual(cne.count_neurips_listing(listing), 2)

    def test_ecva_sections(self):
        page = ("<button>ECCV 2024 Papers</button>" + '<dt class="ptitle">' * 3 +
                "<button>ECCV 2022 Papers</button>" + '<dt class="ptitle">' * 2)
        self.assertEqual(cne.count_ecva_sections(page), {2024: 3, 2022: 2})

    def test_rss_and_bmvc_listing_counts(self):
        self.assertEqual(cne.count_rss_index(rss_index(4) + '<a href="p001.html">again</a><a href="../rss20/index.html">'), 4)
        page = '<a href="/proceedings/12/">A</a><a href="/proceedings/12/">A</a><a href="/proceedings/7/">B</a>'
        self.assertEqual(cne.count_bmvc_listing(page), 2)

    def test_aaai_oai_editions(self):
        page = ('<dc:source xml:lang="en-US">Proceedings of the AAAI Conference on Artificial Intelligence; '
                'Vol. 40 No. 1: AAAI-26 Technical Tracks 1; 101-109</dc:source>'
                '<dc:source>2159-5399</dc:source>'
                '<dc:source xml:lang="en-US">Proceedings ...; Vol. 35 No. 16: AAAI-21 Technical Tracks 16; 1-9</dc:source>')
        self.assertEqual(cne.parse_aaai_oai(page), {2026: 1, 2021: 1})

    def test_gcpr_books_pool_both_parts(self):
        message = {"items": [
            {"title": ["Pattern Recognition"], "ISBN": ["9783031851803", "9783031851810"],
             "subtitle": ["46th DAGM German Conference, DAGM GCPR 2024, Munich, Germany, Proceedings, Part I"]},
            {"title": ["Pattern Recognition"], "ISBN": ["9783031851865", "9783031851872"],
             "subtitle": ["46th DAGM German Conference, DAGM GCPR 2024, Munich, Germany, Proceedings, Part II"]},
            {"title": ["Pattern Recognition and Computer Vision"], "ISBN": ["9789819543946"], "subtitle": []},
        ]}
        self.assertEqual(cne.parse_gcpr_books(message),
                         {2024: ["9783031851803", "9783031851810", "9783031851865", "9783031851872"]})

    def test_ieee_facets(self):
        message = {"facets": {"container-title": {"values": {
            "2025 IEEE/RSJ International Conference on Intelligent Robots and Systems (IROS)": 1988,
            "2025 IEEE International Conference on Robotics and Automation (ICRA)": 1610,
            "2022 International Conference on Robotics and Automation (ICRA)": 944,
            "2025 IEEE 28th International Conference on Intelligent Transportation Systems (ITSC)": 677,
            "2026 IEEE Intelligent Vehicles Symposium (IV)": 296,
            "2026 IEEE Intelligent Vehicles Symposium Workshops (IV Workshops)": 30,
        }}}}
        self.assertEqual(cne.parse_ieee_facets(message), {
            ("IROS", 2025): 1988, ("ICRA", 2025): 1610, ("ICRA", 2022): 944,
            ("ITSC", 2025): 677, ("IV", 2026): 296})

    def test_list_index_keeps_only_allowlisted_owners(self):
        text = ("# ICRA Paper Lists\n| Year | Site | Repository |\n|---|---|---|\n"
                "| 2025 | x | [DoongLi/ICRA2025-Paper-List](https://github.com/DoongLi/ICRA2025-Paper-List) |\n"
                "| 2026 | x | [Stranger42/ICRA2026-Paper-List](https://github.com/Stranger42/ICRA2026-Paper-List) |\n"
                "# IROS Paper Lists\n"
                "| 2024 | x | [ryanbgriffiths/IROS2024PaperList](https://github.com/ryanbgriffiths/IROS2024PaperList) |\n")
        self.assertEqual(cne.parse_list_index(text), {
            ("ICRA", 2025): ["DoongLi/ICRA2025-Paper-List"],
            ("IROS", 2024): ["ryanbgriffiths/IROS2024PaperList"]})

    def test_markdown_table_rows(self):
        text = "# List\n| Title | Authors |\n|---|---|\n| A | x |\n| B | y |\n\n| Title | Authors |\n| :--- | --- |\n| C | z |\n"
        self.assertEqual(cne.count_markdown_table_rows(text), 3)
        self.assertEqual(cne.count_markdown_table_rows("- a bullet list\n- only\n"), 0)

    def test_dblp_status(self):
        self.assertEqual(cne.dblp_status("<title>Making sure you're not a bot!</title> Anubis"), "blocked")
        self.assertEqual(cne.dblp_status("<html>dblp: ICLR</html>"), "reachable")
        self.assertEqual(cne.dblp_status(None), "error")


class WebTests(unittest.TestCase):
    def test_404_is_quiet_other_errors_are_kept(self):
        web = FakeWeb({"https://a.org/500": 500})
        self.assertIsNone(web.get("https://a.org/missing"))
        self.assertIsNone(web.get("https://a.org/500"))
        self.assertEqual(web.errors, [{"url": "https://a.org/500", "error": "HTTP 500"}])

    def test_host_that_refuses_twice_is_left_alone(self):
        web = FakeWeb({"https://b.org/1": 429, "https://b.org/2": 403, "https://b.org/3": "fine"})
        web.get("https://b.org/1")
        web.get("https://b.org/2")
        self.assertIsNone(web.get("https://b.org/3"))
        self.assertEqual(web.requests, 2)

    def test_pages_are_cached(self):
        web = FakeWeb({"https://c.org/": "x"})
        web.get("https://c.org/")
        web.get("https://c.org/")
        self.assertEqual(web.requests, 1)


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.w = WorkDir()

    def tearDown(self):
        self.w.close()

    def run_venues(self, venues, pages, drift_years=0):
        checker = self.w.checker(pages, drift_years)
        return checker.run(venues)

    def test_new_cvf_edition_with_a_fetcher(self):
        for y in (2012, 2014, 2016, 2018):
            self.w.venue(f"accv{y}.json", 10)
        self.w.script("fetch_cvf_history.py")
        pages = {f"{cne.CVF}/menu": '<a href="CVPR2026">', f"{cne.CVF}/menu_other.html":
                 '<a href="/ACCV2024">x</a><a href="/ACCV2022">x</a><a href="/ACCV2024_workshops/menu">',
                 f"{cne.CVF}/ACCV2022?day=all": cvf_listing(277),
                 f"{cne.CVF}/ACCV2024?day=all": cvf_listing(20)}
        s = self.run_venues(["ACCV"], pages)
        self.assertEqual(s["venues"]["ACCV"]["probed"], [2020, 2022, 2024, 2026])
        self.assertEqual([(e["year"], e["papers"], e["command"]) for e in s["new_editions"]],
                         [(2022, 277, ["fetch_cvf_history.py", "ACCV", "2022", "2022"])])
        self.assertIn("20 papers so far", s["notes"][0]["message"])

    def test_edition_without_its_script_is_reported_not_fetched(self):
        self.w.venue("rss2024.json", 150)
        s = self.run_venues(["RSS"], {f"{cne.RSS_BASE}/rss21/index.html": rss_index(163)})
        (e,) = s["new_editions"]
        self.assertEqual((e["year"], e["papers"], e["missing_script"]), (2025, 163, "fetch_rss.py"))
        self.assertIn("fetch_rss.py isn't in this checkout", e["note"])
        started = cne.run_fetches(s["new_editions"], self.w.venues, self.w.scripts, None, None,
                                  runner=lambda cmd: self.fail("must not run"))
        self.assertEqual((started, e["status"]), (0, "no_fetcher"))

    def test_run_fetches_checks_the_file_it_should_have_written(self):
        self.w.venue("rss2024.json", 150)
        self.w.script("fetch_rss.py")
        pages = {f"{cne.RSS_BASE}/rss21/index.html": rss_index(163),
                 f"{cne.RSS_BASE}/rss22/index.html": rss_index(210)}
        items = self.run_venues(["RSS"], pages)["new_editions"]
        self.assertEqual([i["command"] for i in items], [["fetch_rss.py", "2025"], ["fetch_rss.py", "2026"]])

        def runner(cmd):
            if cmd[1] == "2025":
                self.w.venue("rss2025.json", 163)
                return 0
            return 1  # 2026 fails and writes nothing
        cne.run_fetches(items, self.w.venues, self.w.scripts, None, None, runner=runner)
        self.assertEqual((items[0]["status"], items[0]["papers_written"]), ("fetched", 163))
        self.assertEqual(items[1]["status"], "fetch_failed")
        self.assertIn("rss2026.json not written", items[1]["note"])

    def test_max_fetches(self):
        items = [{"venue": "RSS", "year": y, "command": ["fetch_rss.py", str(y)]} for y in (2025, 2026)]
        ran = []
        cne.run_fetches(items, self.w.venues, self.w.scripts, 1, None, runner=lambda c: ran.append(c) or 1)
        self.assertEqual(len(ran), 1)
        self.assertEqual(items[1]["status"], "not_run")

    def test_icml_prefers_pmlr_and_falls_back_to_the_virtual_site(self):
        self.w.venue("icml2024.json", 10)
        pages = {f"{cne.PMLR}/": '<li><a href="v267"><b>Volume 267</b></a> Proceedings of ICML 2025</li>',
                 f"{cne.PMLR}/v267/": '<div class="paper">' * 60,
                 "https://icml.cc/static/virtual/data/icml-2026-orals-posters.json":
                     virtual_dump([f"P{i}" for i in range(70)], "https://openreview.net/group?id=ICML.cc/2026/Conference")}
        s = self.run_venues(["ICML"], pages)
        self.assertEqual([(e["year"], e["command"]) for e in s["new_editions"]], [
            (2025, ["fetch_pmlr.py", "ICML", "2025", "--volume", "267"]),
            (2026, ["fetch_virtual_site.py", "ICML", "2026", "--abstracts"])])

    def test_neurips_virtual_listing_alone_is_only_a_note(self):
        self.w.venue("neurips2025.json", 10)
        pages = {"https://neurips.cc/static/virtual/data/neurips-2026-orals-posters.json":
                 virtual_dump([f"P{i}" for i in range(80)], "https://openreview.net/group?id=NeurIPS.cc/2026/Conference")}
        s = self.run_venues(["NeurIPS"], pages)
        self.assertEqual(s["new_editions"], [])
        self.assertIn("waiting for proceedings", s["notes"][0]["message"])

    def test_placeholder_listing_is_not_an_edition(self):
        self.w.venue("iclr2025.json", 10)
        pages = {"https://iclr.cc/static/virtual/data/iclr-2026-orals-posters.json": {"count": 0, "results": []}}
        self.assertEqual(self.run_venues(["ICLR"], pages)["new_editions"], [])

    def test_robotics_uses_allowlisted_lists_and_the_repos_table(self):
        self.w.venue("icra2025_github.json", 10)
        self.w.script("fetch_github_paper_lists.py",
                      'REPOS = [(2026, "ICRA", "DoongLi/ICRA2026-Paper-List", "main", "table_semicolon")]\n')
        table = "| Title | Authors | Session |\n|---|---|---|\n" + "".join(f"| T{i} | A | S |\n" for i in range(120))
        pages = {cne.HRJP_INDEX: "# ICRA\n| 2026 | x | [Stranger42/ICRA2026-Paper-List](https://github.com/Stranger42/ICRA2026-Paper-List) |\n",
                 f"{cne.RAW_GITHUB}/Stranger42/ICRA2026-Paper-List/main/README.md": table,
                 f"{cne.RAW_GITHUB}/DoongLi/ICRA2026-Paper-List/main/README.md": table}
        web = FakeWeb(pages)
        checker = cne.Checker(web, TODAY, scripts_dir=self.w.scripts, venues_dir=self.w.venues, drift_years=0)
        (e,) = checker.run(["ICRA"])["new_editions"]
        self.assertEqual((e["source"], e["papers"], e["command"]),
                         ("GitHub list DoongLi/ICRA2026-Paper-List", 120, ["fetch_github_paper_lists.py"]))
        self.assertNotIn(f"{cne.RAW_GITHUB}/Stranger42/ICRA2026-Paper-List/main/README.md", web.cache)
        self.assertEqual(cne.expected_file("ICRA", 2026, e["command"]), "icra2026_github.json")

    def test_crossref_wins_for_ieee_conferences(self):
        self.w.venue("iv2025.json", 371)
        checker = self.w.checker({})
        checker.ieee_counts = lambda since_year=None: ("crossref-url", {("IV", 2026): 296})
        (e,) = checker.run(["IV"])["new_editions"]
        self.assertEqual((e["papers"], e["command"]), (296, ["fetch_crossref.py", "IV", "2026"]))

    def test_gcpr_isbns_known_or_not(self):
        self.w.venue("gcpr2022.json", 40)
        self.w.venue("gcpr2024.json", 40)
        self.w.script("fetch_crossref.py", 'PROCEEDINGS_ISBNS = {"GCPR": {2023: ["9783031546051"]}}\n')
        books = {"message": {"items": [
            {"ISBN": ["9783031546051"], "subtitle": ["45th DAGM German Conference, DAGM GCPR 2023, Proceedings"]},
            {"ISBN": ["9783032128409"], "subtitle": ["47th DAGM German Conference, DAGM GCPR 2025, Proceedings"]}]}}
        checker = self.w.checker({})
        checker.web.get_json = lambda url, quiet_errors=False: books
        s = checker.run(["GCPR"])
        self.assertEqual(s["venues"]["GCPR"]["probed"], [2023, 2025, 2026])
        by_year = {e["year"]: e for e in s["new_editions"]}
        self.assertEqual(by_year[2023]["command"], ["fetch_crossref.py", "GCPR", "2023"])
        self.assertIsNone(by_year[2025]["command"])
        self.assertIn("9783032128409", by_year[2025]["note"])

    def test_drift_short_file_and_withdrawn_papers(self):
        self.w.venue("cvpr2022.json", 774)
        self.w.venue("cvpr2026.json", 4068)
        self.w.venue("cvpr2025.json", 2871)
        self.w.script("fetch_cvf_history.py")
        pages = {f"{cne.CVF}/menu": '<a href="CVPR2026"><a href="CVPR2025"><a href="CVPR2022">',
                 f"{cne.CVF}/CVPR2022?day=all": cvf_listing(2074),
                 f"{cne.CVF}/CVPR2025?day=all": cvf_listing(2872),  # one dead link: not drift
                 f"{cne.CVF}/CVPR2026?day=all": cvf_listing(4042)}
        s = self.run_venues(["CVPR"], pages, drift_years=5)
        drift = {d["file"]: d for d in s["drift"]}
        self.assertEqual(set(drift), {"cvpr2022.json", "cvpr2026.json"})
        self.assertEqual((drift["cvpr2022.json"]["kind"], drift["cvpr2022.json"]["live"]), ("missing_papers", 2074))
        self.assertEqual(drift["cvpr2022.json"]["command"], ["fetch_cvf_history.py", "CVPR", "2022", "2022"])
        self.assertEqual(drift["cvpr2026.json"]["kind"], "listing_shrank")
        self.assertIsNone(drift["cvpr2026.json"]["command"])

    def test_drift_on_an_approximate_source_ignores_a_longer_file(self):
        # A community ICRA list carries RA-L papers too, so it's longer than
        # Crossref's proceedings; only a proceedings file is compared at all.
        self.w.venue("icra2022.json", 900)
        self.w.venue("icra2025_github.json", 2800)
        checker = self.w.checker({}, drift_years=5)
        checker.ieee_counts = lambda since_year=None: ("u", {("ICRA", 2022): 944, ("ICRA", 2025): 1610})
        s = checker.run(["ICRA"])
        self.assertEqual([(d["file"], d["local"], d["live"]) for d in s["drift"]], [("icra2022.json", 900, 944)])
        self.assertIn("Crossref now has ICRA 2025", s["notes"][0]["message"])

    def test_dblp_status_in_summary(self):
        s = self.run_venues([], {cne.DBLP_PAGE: "Making sure you're not a bot! Anubis"})
        self.assertEqual(s["dblp"]["status"], "blocked")


class ReportTests(unittest.TestCase):
    def summary(self):
        return {"today": "2026-09-24", "notes": [], "errors": [],
                "new_editions": [
                    {"venue": "RSS", "year": 2025, "source": "roboticsproceedings.org", "papers": 163,
                     "command": ["fetch_rss.py", "2025"], "status": "fetched", "papers_written": 163, "note": ""},
                    {"venue": "AAAI", "year": 2027, "source": "ojs.aaai.org OAI-PMH", "papers": None,
                     "command": None, "status": "no_fetcher", "note": "there's no AAAI fetcher yet"}],
                "drift": [{"venue": "CVPR", "year": 2022, "file": "cvpr2022.json", "local": 774, "live": 2074,
                           "source": "CVF", "kind": "missing_papers", "command": ["fetch_cvf_history.py", "CVPR", "2022", "2022"]}],
                "dblp": {"status": "reachable"}}

    def test_needs_attention_lists_what_a_person_should_look_at(self):
        lines = cne.needs_attention(self.summary())
        self.assertEqual(len(lines), 3)
        self.assertIn("AAAI 2027", lines[0])
        self.assertIn("cvpr2022.json is missing papers: 774 here, 2074", lines[1])
        self.assertIn("DBLP", lines[2])

    def test_markdown(self):
        md = cne.to_markdown(self.summary())
        self.assertIn("| RSS | 2025 | 163 | roboticsproceedings.org | fetched (163 written) | `python scripts/fetch_rss.py 2025` |", md)
        self.assertIn("| cvpr2022.json | 774 | 2074 |", md)
        self.assertIn("DBLP: reachable.", md)


if __name__ == "__main__":
    unittest.main()
