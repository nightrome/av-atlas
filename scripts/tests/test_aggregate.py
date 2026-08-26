#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for aggregate.py: citation-count fallback, venue-name
normalization, the InternetLab/Brazil country-mislabel correction, and a
small end-to-end run of main() against fixture data.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_aggregate.py
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aggregate as ag


class TestIsValidInstitution(unittest.TestCase):
    def test_rejects_bare_country_and_city_names(self):
        for bad in ("China", "USA", "Germany", "Beijing", "Los Angeles"):
            self.assertFalse(ag.is_valid_institution(bad), bad)

    def test_rejects_generic_lab_names_with_qualifier_first(self):
        # User-flagged: unlike SUBUNIT_PREFIX_RE's noun-first form
        # ("Institute for AI"), these put the generic noun last.
        for bad in ("Robotics Institute", "Multimedia Laboratory"):
            self.assertFalse(ag.is_valid_institution(bad), bad)

    def test_rejects_url_leaked_into_the_name(self):
        self.assertFalse(ag.is_valid_institution("Carnegie Mellon Universityhttps://github.com/ywyeli/Place3D"))
        self.assertFalse(ag.is_valid_institution("UIUChttps://waabi.ai/flux4d"))

    def test_rejects_postal_addresses(self):
        self.assertFalse(ag.is_valid_institution("44227 Dortmund"))
        self.assertFalse(ag.is_valid_institution("Otto-Hahn-Str. 1"))

    def test_rejects_generic_department_or_person_name(self):
        self.assertFalse(ag.is_valid_institution("Department of Computer Science"))
        self.assertFalse(ag.is_valid_institution("Ye Li"))

    def test_rejects_subunit_names_with_no_parent_institution(self):
        for bad in ("College of Aerospace Science and Engineering", "Center for Robotics",
                    "School of Computing", "Institute for AI Safety", "Lab for Perception"):
            self.assertFalse(ag.is_valid_institution(bad), bad)

    def test_accepts_a_real_institution_name(self):
        self.assertTrue(ag.is_valid_institution("University of Oxford"))
        self.assertTrue(ag.is_valid_institution("Carnegie Mellon University"))
        # Starts with a subunit-style word but does carry a real parent name.
        self.assertTrue(ag.is_valid_institution("California Institute of Technology"))

    def test_rejects_garbled_affiliation_sentence_fragments(self):
        # Real case, confirmed by the user (Chen Lv's institution list):
        # a failed footnote parse leaked a whole author-affiliation SENTENCE,
        # a bare postal code, and a byline credit into the affiliations list
        # instead of just the institution name.
        self.assertFalse(ag.is_valid_institution(
            "Z. Huang and C. Lv are with the School of Mechanical and Aerospace Engineering"))
        self.assertFalse(ag.is_valid_institution(
            "and Chen Lv are with the School of Mechanical and Aerospace Engineering"))
        self.assertFalse(ag.is_valid_institution(
            "Singapore. This work was done during Z. Huang's visit to the University of California"))
        self.assertFalse(ag.is_valid_institution("Corresponding Author: Chen Lv"))
        self.assertFalse(ag.is_valid_institution("639798"))
        # The one real institution amid that same garbled list must still
        # be accepted -- this isn't a blanket "reject anything unusual" filter.
        self.assertTrue(ag.is_valid_institution("Nanyang Technological University"))

    def test_rejects_pdf_hosting_credit_line(self):
        # User-flagged: a proceedings/PDF-hosting credit line, not an author
        # affiliation.
        self.assertFalse(ag.is_valid_institution("provided by the Computer Vision Foundation"))

    def test_rejects_sentence_continuation_and_credit_fragments(self):
        # Found while auditing the full institution list alongside the CVF
        # case above: a footnote split into several strings, only one of
        # which is the actual affiliation -- the others are the leftover
        # sentence continuation ("and Diange Yang are with...") or a
        # byline/credit marker, not names.
        self.assertFalse(ag.is_valid_institution("and Diange Yang are with State Key Laboratory"))
        self.assertFalse(ag.is_valid_institution("Equal contribution Corresponding author"))
        self.assertFalse(ag.is_valid_institution("\\dagger indicates the corresponding author"))
        self.assertFalse(ag.is_valid_institution("Correspondence: foo@bar.com"))
        # "is with"/"are with" must be rejected even without a following
        # "the" -- the original regex required "with the", which missed
        # "A. Alizadeh is with Faculty of Mechatronics Engineering".
        self.assertFalse(ag.is_valid_institution("A. Alizadeh is with Faculty of Mechatronics Engineering"))

    def test_accepts_bare_and_as_a_substring_not_just_prefix(self):
        # LEADING_AND_RE only rejects a STRING that starts with "and" -- a
        # real institution containing "and" elsewhere must not be caught.
        self.assertTrue(ag.is_valid_institution("Science and Technology University"))


class TestNormalizeInstitution(unittest.TestCase):
    def test_strips_trailing_country_parenthetical(self):
        self.assertEqual(ag.normalize_institution("Meta (Israel)"), "Meta")
        self.assertEqual(ag.normalize_institution("Google (United States)"), "Google")
        self.assertEqual(ag.normalize_institution("Baidu (China)"), "Baidu")

    def test_strips_trailing_period(self):
        self.assertEqual(ag.normalize_institution("Graz University of Technology."), "Graz University of Technology")
        self.assertEqual(ag.normalize_institution("China."), "China")

    def test_applies_known_aliases(self):
        self.assertEqual(ag.normalize_institution("PSI"), "KU Leuven")
        self.assertEqual(ag.normalize_institution("Cooperative Medianet Innovation Center"), "Shanghai Jiao Tong University")
        self.assertEqual(ag.normalize_institution("Baidu Inc."), "Baidu")
        self.assertEqual(ag.normalize_institution("Baidu Research"), "Baidu")
        self.assertEqual(ag.normalize_institution("Waymo LLC"), "Waymo")
        # Real case, confirmed by the user: OpenAlex itself mis-splits some
        # authors' "UC Berkeley"-shaped affiliation into two institution
        # records -- "Berkeley College" is a real but unrelated small
        # college, always confirmed alongside the correct UC Berkeley
        # record for the same author on the same paper.
        self.assertEqual(ag.normalize_institution("Berkeley College"), "University of California, Berkeley")

    def test_leaves_an_unrecognized_name_unchanged(self):
        self.assertEqual(ag.normalize_institution("University of Oxford"), "University of Oxford")

    def test_strips_corresponding_author_footnote_glued_to_a_real_institution(self):
        # User-flagged, confirmed as a systemic pattern (225+ entries): a
        # "Corresponding author" footnote glued onto a real institution
        # name with no separator, sometimes with no space at all.
        self.assertEqual(ag.normalize_institution("Tsinghua University Corresponding author"), "Tsinghua University")
        self.assertEqual(ag.normalize_institution("NVIDIA ResearchCorresponding authors:"), "NVIDIA Research")

    def test_strips_dagger_and_replacement_char_footnote_junk(self):
        self.assertEqual(
            ag.normalize_institution("Nankai University. �\\dagger: Corresponding authors: Diange Yang"),
            "Nankai University")

    def test_does_not_strip_when_nothing_substantial_remains(self):
        # "Indicates" alone (9 chars, below MIN_STRIPPED_INSTITUTION_LENGTH)
        # isn't a real institution -- must stay unchanged so
        # is_valid_institution()'s existing CREDIT_LINE_RE check still sees
        # "corresponding" and rejects the whole string, rather than a
        # stripped-down word that no longer matches anything.
        self.assertFalse(ag.is_valid_institution(ag.normalize_institution("Indicates corresponding author")))

    def test_author_affiliations_dedupes_after_normalization(self):
        # Same author crediting two variant spellings of the same real
        # institution should count as one, not two.
        a = {"affiliations": ["Baidu Inc.", "Baidu Research", "Baidu (China)"]}
        self.assertEqual(ag.author_affiliations(a), ["Baidu"])

    def test_fixes_mojibake_diacritics(self):
        # User-flagged: "Osnabru¨ck" -- a PDF-extraction artifact where the
        # diacritic's spacing glyph (U+00A8) lands right after the base
        # letter instead of composing into one accented character.
        self.assertEqual(ag.normalize_institution("Osnabru¨ck University"), "Osnabrück University")
        self.assertEqual(ag.normalize_institution("University of Mu¨nster"), "University of Münster")
        self.assertEqual(ag.normalize_institution("Koc¸ University"), "Koç University")
        self.assertEqual(ag.normalize_institution("O¨ rebro University"), "Örebro University")

    def test_strips_nextaff_latex_macro_prefix(self):
        self.assertEqual(ag.normalize_institution("\\NEXTAFFStanford University"), "Stanford University")

    def test_strips_latex_spacing_bracket_without_leading_zero(self):
        # "[.2cm]" (no digit before the decimal point), not just "[0.2cm]".
        self.assertEqual(ag.normalize_institution("Munich Center for Machine Learning[.2cm]"),
                          "Munich Center for Machine Learning")

    def test_strips_trailing_legal_suffixes(self):
        self.assertEqual(ag.normalize_institution("Aptiv Services Deutschland GmbH"), "Aptiv Services Deutschland")
        self.assertEqual(ag.normalize_institution("Zoox Inc"), "Zoox")
        # "Ltd"/"Co." are deliberately left alone (see the bare-"Ltd" comment
        # in INVALID_INSTITUTIONS) -- only LLC/Inc/GmbH are stripped.
        self.assertEqual(ag.normalize_institution("Shenzhen Forward Innovation Digital Technology Co. Ltd"),
                          "Shenzhen Forward Innovation Digital Technology Co. Ltd")

    def test_replaces_underscores_with_spaces(self):
        self.assertEqual(ag.normalize_institution("HKISI_CAS"), "Hong Kong Institute of Science & Innovation, CAS")

    def test_groups_company_lab_variants_under_one_canonical_name(self):
        # User-requested: fold every corporate-lab spelling of the same
        # company into one leaderboard row.
        for variant in ("Huawei Technologies", "Huawei Cloud Computing Technologies Co",
                         "Huawei Paris Research Center"):
            self.assertEqual(ag.normalize_institution(variant), "Huawei", variant)
        for variant in ("Robert Bosch", "Robert Bosch GmbH", "Bosch Research", "Bosch Mobility Solutions",
                        "Bosch Center for Artificial Intelligence", "Bosch Center for AI"):
            self.assertEqual(ag.normalize_institution(variant), "Bosch", variant)
        self.assertEqual(ag.normalize_institution("Mercedes-Benz Research & Development North America"),
                          "Mercedes-Benz")

    def test_groups_epfl_spellings(self):
        self.assertEqual(ag.normalize_institution("École Polytechnique Fédérale de Lausanne"), "EPFL")
        self.assertEqual(ag.normalize_institution("École Polytechnique Fédérale de Lausanne (EPFL)"), "EPFL")
        self.assertEqual(ag.normalize_institution("EPFL VITA lab"), "EPFL")

    def test_replaces_company_domain_with_company_name(self):
        self.assertEqual(ag.normalize_institution("valeo.ai"), "Valeo")

    def test_rejects_bare_city_names(self):
        # User-flagged (Eindhoven, Waterloo) -- same comma-split-affiliation
        # cause as the bare countries/cities already in INVALID_INSTITUTIONS.
        for city in ("Eindhoven", "Waterloo"):
            self.assertFalse(ag.is_valid_institution(city), city)


class TestCleanAuthorName(unittest.TestCase):
    def test_unicode_hyphen_variants_normalized_to_ascii(self):
        # Real case, confirmed by the user: one of Yi-Ting Chen's papers had
        # his name PDF-extracted with U+2010 HYPHEN instead of the ASCII
        # hyphen his other papers use, splitting him into two separate
        # leaderboard entries (4 papers found instead of the correct 5).
        self.assertEqual(ag.clean_author_name("Yi‐Ting Chen"), "Yi-Ting Chen")
        self.assertEqual(ag.clean_author_name("Yi-Ting Chen"), "Yi-Ting Chen")
        for dash in ("‐", "‑", "‒", "–", "−"):
            self.assertEqual(ag.clean_author_name(f"Ying{dash}Cong Chen"), "Ying-Cong Chen")


class TestIsFullyProcessed(unittest.TestCase):
    def test_complete_record_passes(self):
        self.assertTrue(ag.is_fully_processed({"title": "X", "abstract": "Y", "year": 2024}))

    def test_missing_abstract_still_passes(self):
        # DBLP-sourced venues (RSS/ICLR/AAAI, CVF gap-fills) never have an
        # abstract at all -- title + year alone is enough to be eligible.
        self.assertTrue(ag.is_fully_processed({"title": "X", "abstract": "", "year": 2024}))
        self.assertTrue(ag.is_fully_processed({"title": "X", "year": 2024}))

    def test_missing_year_fails(self):
        self.assertFalse(ag.is_fully_processed({"title": "X", "abstract": "Y", "year": None}))

    def test_missing_title_fails(self):
        self.assertFalse(ag.is_fully_processed({"title": "", "abstract": "Y", "year": 2024}))


class TestPaperShortName(unittest.TestCase):
    def test_uses_text_before_colon(self):
        name = ag.paper_short_name(
            "nuScenes: A Multimodal Dataset for Autonomous Driving", ["Holger Caesar"], 2020)
        self.assertEqual(name, "nuScenes")

    def test_falls_back_to_surname_year_when_no_colon_or_dash(self):
        name = ag.paper_short_name("A Fully Convolutional Approach to Segmentation", ["Jane Wang"], 2021)
        self.assertEqual(name, "Wang21")

    def test_colon_preceded_by_a_full_sentence_falls_through_to_surname_year(self):
        # A sentence-starting word before the colon (long clause, not a
        # name) must not be mistaken for a short name just because a colon
        # happens to appear somewhere in the title.
        name = ag.paper_short_name(
            "A Survey of Motion Planning Approaches for Autonomous Vehicles: Recent Advances",
            ["John Smith"], 2023)
        self.assertEqual(name, "Smith23")

    def test_dash_used_only_when_short_and_name_like(self):
        name = ag.paper_short_name("PointPillars - Fast Encoders for Object Detection", ["Alex Lang"], 2019)
        self.assertEqual(name, "PointPillars")

    def test_ordinary_sentence_dash_does_not_produce_a_bad_short_name(self):
        # A dash inside a normal sentence-style title (long clause before
        # it) must fall through to the surname/year convention, not grab
        # the whole clause as if it were a name.
        name = ag.paper_short_name(
            "Improving Robustness of Object Detectors - A Data Augmentation Study", ["Mei Chen"], 2022)
        self.assertEqual(name, "Chen22")

    def test_hyphen_inside_a_compound_word_is_not_treated_as_a_separator(self):
        # "self-driving" has no surrounding spaces around its hyphen -- must
        # not be mistaken for a title-separating dash.
        name = ag.paper_short_name("Advances in Self-Driving Perception Systems", ["Kim Lee"], 2024)
        self.assertEqual(name, "Lee24")

    def test_no_author_falls_back_to_unknown(self):
        name = ag.paper_short_name("A Fully Convolutional Approach to Segmentation", [], 2021)
        self.assertEqual(name, "Unknown21")

    def test_abstract_supplies_a_coined_name_when_the_title_has_none(self):
        # Real case, user-flagged: "Planning-Oriented Autonomous Driving"
        # has no colon or dash, but the abstract introduces "Unified
        # Autonomous Driving (UniAD)" -- the name actually used to refer to
        # this paper (UniAD), not derivable from the title alone.
        abstract = (
            "We revisit the key components within perception and prediction. "
            "We introduce Unified Autonomous Driving (UniAD), a comprehensive "
            "framework up-to-date that incorporates full-stack driving tasks "
            "in one network. We instantiate UniAD on the challenging nuScenes benchmark."
        )
        name = ag.paper_short_name("Planning-Oriented Autonomous Driving", ["Yihan Hu"], 2023, abstract)
        self.assertEqual(name, "UniAD")

    def test_abstract_fallback_prefers_camelcase_over_a_generic_acronym(self):
        abstract = "Our bird's-eye-view (BEV) representation feeds into BEVFormer (BEVFormer), a new model."
        name = ag.paper_short_name("A Study of Perception Systems", ["Jane Doe"], 2023, abstract)
        self.assertEqual(name, "BEVFormer", "BEV is a generic term; BEVFormer is the paper's own CamelCase name")

    def test_abstract_fallback_rejects_a_generic_acronym_used_only_once(self):
        # "(GAN)" is a real term but not reused elsewhere in this abstract --
        # not this paper's own coined name, so the SurnameYY fallback below
        # it should win instead.
        abstract = "We use a generative adversarial network (GAN) as one component of our pipeline."
        name = ag.paper_short_name("A Study of Perception Systems", ["Jane Doe"], 2023, abstract)
        self.assertEqual(name, "Doe23")

    def test_no_abstract_falls_through_to_surname_year(self):
        name = ag.paper_short_name("A Study of Perception Systems", ["Jane Doe"], 2023, None)
        self.assertEqual(name, "Doe23")


class TestClassifyInstitutionSector(unittest.TestCase):
    def test_university_keyword_is_academic(self):
        self.assertEqual(ag.classify_institution_sector("Technical University of Munich"), "academic")

    def test_known_industry_lab_matches_override_despite_institute_in_name(self):
        # "Institute" alone would otherwise read as academic -- the override
        # exists specifically because this is a corporate R&D arm, not a
        # degree-granting or public-research institution.
        self.assertEqual(ag.classify_institution_sector("Toyota Research Institute"), "industry")

    def test_known_research_institute_without_university_in_name_is_academic(self):
        self.assertEqual(ag.classify_institution_sector("Max Planck Institute"), "academic")

    def test_case_insensitive(self):
        self.assertEqual(ag.classify_institution_sector("waymo"), "industry")
        self.assertEqual(ag.classify_institution_sector("WAYMO"), "industry")

    def test_unrecognized_institution_is_unclassified(self):
        self.assertIsNone(ag.classify_institution_sector("Some Regional Robotics Lab"))


class TestComputeDisruptionIndex(unittest.TestCase):
    def test_mixed_disruptive_and_consolidating_citers(self):
        # A cites R. citer1/citer2 cite only A (disruptive votes); citer3
        # cites A AND R (a consolidating vote). All citers' own reference
        # lists are themselves in `edges` (informative).
        edges = {
            "a": ["r"],
            "citer1": ["a"],
            "citer2": ["a"],
            "citer3": ["a", "r"],
        }
        result = ag.compute_disruption_index(edges)
        self.assertEqual(result["a"]["n_citers"], 3)
        self.assertAlmostEqual(result["a"]["cd_index"], 1 / 3, places=3)

    def test_all_disruptive_citers_score_1(self):
        edges = {"a": [], "c1": ["a"], "c2": ["a"], "c3": ["a"]}
        result = ag.compute_disruption_index(edges)
        self.assertEqual(result["a"]["cd_index"], 1.0)

    def test_all_consolidating_citers_score_negative_1(self):
        edges = {"a": ["r"], "c1": ["a", "r"], "c2": ["a", "r"], "c3": ["a", "r"]}
        result = ag.compute_disruption_index(edges)
        self.assertEqual(result["a"]["cd_index"], -1.0)

    def test_below_min_citers_threshold_excluded(self):
        # Only 2 informative citers -- below DISRUPTION_MIN_CITERS (3), a
        # +1/-1 average that coarse isn't meaningful.
        edges = {"a": [], "c1": ["a"], "c2": ["a"]}
        result = ag.compute_disruption_index(edges)
        self.assertNotIn("a", result)

    def test_target_with_no_scanned_references_still_scores(self):
        # A's own reference list was never scanned (not a key in edges) --
        # target_refs is then just empty, so every citer reads as
        # disruptive (there's nothing for them to also cite). Correct
        # behavior, not a bug: with no known references to compare against,
        # "didn't cite any of them" is vacuously true.
        edges = {"c1": ["a"], "c2": ["a"], "c3": ["a"]}
        result = ag.compute_disruption_index(edges)
        self.assertEqual(result["a"]["cd_index"], 1.0)


class TestShardIndex(unittest.TestCase):
    # paper.html re-implements this exact djb2-hash-mod-N algorithm in JS
    # (no shared module between Python and JS in this app) to pick the same
    # abstracts/shard-NN.json file client-side, with no index file needed.
    # These values were cross-checked against a real Node run of the JS copy
    # -- if this test ever needs updating, the JS copy in paper.html must be
    # updated to match, or every paper's abstract silently stops resolving.
    def test_matches_the_js_implementation_in_paper_html(self):
        cases = {
            "nuScenes: A multimodal dataset for autonomous driving": 54,
            "BEVFormer": 45,
            "End-to-End Object Detection with Transformers": 40,
            "": 5,
            "A": 38,
        }
        for title, expected_shard in cases.items():
            self.assertEqual(ag.shard_index(title), expected_shard, title)

    def test_stable_for_the_same_title(self):
        title = "Planning-Oriented Autonomous Driving"
        self.assertEqual(ag.shard_index(title), ag.shard_index(title))

    def test_in_range(self):
        for title in ("", "A", "A fairly long paper title about perception and planning"):
            self.assertGreaterEqual(ag.shard_index(title), 0)
            self.assertLess(ag.shard_index(title), ag.ABSTRACT_SHARD_COUNT)


class TestCitationCount(unittest.TestCase):
    def test_no_source_returns_none_not_zero(self):
        # This is the load-bearing distinction: "no data" must never become a
        # real 0, or every downstream average silently treats it as zero
        # impact instead of excluding it.
        self.assertIsNone(ag.citation_count({}))
        self.assertIsNone(ag.citation_count({"citations_by_source": {"in_corpus": {"count": None}}}))

    def test_reads_in_corpus_count(self):
        entry = {"citations_by_source": {"in_corpus": {"count": 2}}}
        self.assertEqual(ag.citation_count(entry), 2)

    def test_external_sources_are_never_used_even_when_only_source_present(self):
        # OpenAlex/Semantic Scholar/Scholar counts must never leak into the
        # site's citation number, ranking or displayed -- only the in-corpus
        # graph, computed entirely in-house, counts as "our own" data.
        entry = {
            "citations": 40, "citations_scholar": 35, "citations_openalex": 40,
            "citations_by_source": {"openalex": {"count": 40}, "semantic_scholar": {"count": 35}},
        }
        self.assertIsNone(ag.citation_count(entry))

    def test_a_genuine_zero_in_corpus_count_is_real(self):
        entry = {"citations_by_source": {"in_corpus": {"count": 0}}}
        self.assertEqual(ag.citation_count(entry), 0)


class TestCitationsBySourceForClient(unittest.TestCase):
    def test_exposes_in_corpus_count(self):
        entry = {"citations_by_source": {"in_corpus": {"count": 2}, "openalex": {"count": 40}}}
        result = ag.citations_by_source_for_client(entry)
        self.assertEqual(result, {"in_corpus": {"count": 2}})

    def test_external_only_data_returns_none(self):
        # Legacy ICRA/IROS entries (~379 papers) only ever got a flat
        # OpenAlex-sourced "citations" field, never an in-corpus count -- that
        # external data must not surface to the client at all.
        entry = {"citations": 40, "citations_by_source": {"openalex": {"count": 40}}}
        self.assertIsNone(ag.citations_by_source_for_client(entry))

    def test_no_citation_data_at_all_returns_none(self):
        self.assertIsNone(ag.citations_by_source_for_client({}))


class TestNormalizeVenue(unittest.TestCase):
    def test_known_aliases(self):
        self.assertEqual(ag.normalize_venue("Advances in Neural Information Processing Systems"), "NeurIPS")
        self.assertEqual(ag.normalize_venue("European Conference on Computer Vision"), "ECCV")
        self.assertEqual(
            ag.normalize_venue("IEEE Transactions on Pattern Analysis and Machine Intelligence"), "TPAMI"
        )
        self.assertEqual(ag.normalize_venue("arXiv preprint"), "arXiv")

    def test_unrecognized_venue_passes_through_unchanged(self):
        self.assertEqual(ag.normalize_venue("CVPR"), "CVPR")
        self.assertEqual(ag.normalize_venue("Some New Venue"), "Some New Venue")


class TestAuthorCountryCodes(unittest.TestCase):
    def test_internetlab_brazil_mislabel_is_stripped(self):
        author = {"affiliations": ["InternetLab", "Shanghai Artificial Intelligence Laboratory"],
                   "countries": ["BR", "CN"]}
        self.assertEqual(ag.author_country_codes(author), ["CN"])

    def test_real_brazil_affiliation_is_not_stripped(self):
        # The correction must be specific to InternetLab, not "any author
        # tagged BR" -- a real Brazilian affiliation must survive.
        author = {"affiliations": ["Universidade Federal do Ceara"], "countries": ["BR"]}
        self.assertEqual(ag.author_country_codes(author), ["BR"])

    def test_no_affiliation_data_does_not_crash(self):
        self.assertEqual(ag.author_country_codes({}), [])


class TestAggregateEndToEnd(unittest.TestCase):
    """Runs aggregate.main() against small fixture data by redirecting its
    IN_FILE/OUT_FILE module-level paths, then inspects the written stats.json."""

    def _run(self, entries, citation_graph=None):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        in_file = Path(tmpdir.name) / "papers_full.json"
        out_file = Path(tmpdir.name) / "stats.json"
        # is_fully_processed requires an abstract -- default one in here so
        # every existing fixture above (written before that gate existed)
        # doesn't need to restate "abstract": "..." on every single entry
        # just to pass a precondition unrelated to what each test actually
        # checks.
        for e in entries:
            e.setdefault("abstract", "placeholder abstract for testing")
        in_file.write_text(json.dumps(entries), encoding="utf-8")
        graph_file = Path(tmpdir.name) / "citation_graph.json"
        if citation_graph is not None:
            graph_file.write_text(json.dumps(citation_graph), encoding="utf-8")

        orig_in, orig_out, orig_adjacent, orig_profiles, orig_graph, orig_abstracts = (
            ag.IN_FILE, ag.OUT_FILE, ag.ADJACENT_OUT_FILE, ag.SCHOLAR_PROFILES_FILE,
            ag.CITATION_GRAPH_FILE, ag.ABSTRACTS_DIR)
        ag.IN_FILE, ag.OUT_FILE = in_file, out_file
        # Patched to a tmp path same as OUT_FILE -- without this, every test
        # run would silently overwrite the real ~34MB data/stats_adjacent.json
        # with whatever tiny fixture the current test happens to pass.
        ag.ADJACENT_OUT_FILE = Path(tmpdir.name) / "stats_adjacent.json"
        ag.SCHOLAR_PROFILES_FILE = Path(tmpdir.name) / "scholar_profiles.json"  # deliberately absent
        ag.CITATION_GRAPH_FILE = graph_file  # absent unless citation_graph was passed
        # Same reasoning as ADJACENT_OUT_FILE above -- without this, every
        # test run would rmtree+rewrite the real data/abstracts/ directory.
        ag.ABSTRACTS_DIR = Path(tmpdir.name) / "abstracts"
        try:
            ag.main()
            self.last_adjacent_papers = json.loads(ag.ADJACENT_OUT_FILE.read_text(encoding="utf-8"))
        finally:
            ag.IN_FILE, ag.OUT_FILE, ag.ADJACENT_OUT_FILE, ag.SCHOLAR_PROFILES_FILE, \
                ag.CITATION_GRAPH_FILE, ag.ABSTRACTS_DIR = (
                orig_in, orig_out, orig_adjacent, orig_profiles, orig_graph, orig_abstracts)

        return json.loads(out_file.read_text(encoding="utf-8"))

    def test_adjacent_papers_file_excludes_core_and_incomplete_entries(self):
        self._run([
            {"title": "A Core Paper", "year": 2023, "venue": "CVPR", "av_relevance": "core"},
            {"title": "An Adjacent Paper", "year": 2022, "venue": "NeurIPS", "av_relevance": "adjacent",
             "category": "general-cv-ml-method"},
            {"title": "", "year": 2021, "venue": "ICML", "av_relevance": "adjacent"},  # no title
            {"title": "No Year Adjacent", "venue": "ICML", "av_relevance": "adjacent"},  # no year
        ])
        titles = [p["title"] for p in self.last_adjacent_papers]
        self.assertEqual(titles, ["An Adjacent Paper"])
        p = self.last_adjacent_papers[0]
        self.assertEqual(p["av_relevance"], "adjacent")
        self.assertEqual(p["venue"], "NeurIPS")
        self.assertEqual(p["category"], "general-cv-ml-method")

    def test_best_by_year_omits_years_with_no_citation_data(self):
        stats = self._run([
            {"title": "Uncited AV Paper", "year": 2015, "venue": "CVPR", "av_relevance": "core"},
            {"title": "Cited AV Paper", "year": 2023, "venue": "CVPR", "av_relevance": "core",
             "citations_by_source": {"in_corpus": {"count": 50}}},
        ])
        self.assertNotIn("2015", stats["best_by_year"])
        self.assertIn("2023", stats["best_by_year"])
        self.assertEqual(stats["best_by_year"]["2023"]["title"], "Cited AV Paper")

    def test_best_by_year_ranks_by_citations(self):
        stats = self._run([
            {"title": "Fewer citations", "year": 2024, "venue": "CVPR",
             "av_relevance": "core", "citations_by_source": {"in_corpus": {"count": 200}}},
            {"title": "More citations", "year": 2024, "venue": "CVPR",
             "av_relevance": "core", "citations_by_source": {"in_corpus": {"count": 300}}},
        ])
        self.assertEqual(stats["best_by_year"]["2024"]["title"], "More citations")

    def test_venue_names_normalized_in_output(self):
        stats = self._run([
            {"title": "A", "year": 2024, "venue": "Advances in Neural Information Processing Systems",
             "av_relevance": "core", "citations": 10},
        ])
        self.assertIn("NeurIPS", stats["corpus_stats"]["by_venue"])
        self.assertNotIn("Advances in Neural Information Processing Systems", stats["corpus_stats"]["by_venue"])

    def test_adjacent_papers_excluded_from_leaderboards(self):
        stats = self._run([
            {"title": "Not AV at all", "year": 2024, "venue": "CVPR", "av_relevance": "adjacent",
             "citations": 99999},
        ])
        self.assertEqual(stats["core_relevant"], 0)
        self.assertEqual(len(stats["all_papers"]), 0)

    def test_citing_papers_lists_who_cites_a_paper_within_the_corpus(self):
        stats = self._run(
            [
                {"title": "Cited Paper", "year": 2020, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Citer One", "year": 2022, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Citer Two", "year": 2021, "venue": "CVPR", "av_relevance": "core"},
            ],
            citation_graph={"generated_at": "2026-01-01", "edges": {
                "citerone": ["citedpaper"], "citertwo": ["citedpaper"],
            }},
        )
        by_title = {p["title"]: p for p in stats["all_papers"]}
        # Just a list of titles -- each citer is already its own top-level
        # all_papers entry, see aggregate.py's citing_papers comment.
        citers = set(by_title["Cited Paper"]["citing_papers"])
        self.assertEqual(citers, {"Citer One", "Citer Two"})
        # Sorted oldest first, for a citation-timeline chart: Citer Two is
        # 2021, Citer One is 2022.
        self.assertEqual(by_title["Cited Paper"]["citing_papers"], ["Citer Two", "Citer One"])

    def test_insights_highest_impact_author_requires_more_than_10_papers(self):
        # User-requested: a two-paper lucky hit shouldn't win "highest
        # average impact" on the Insights page, even though it legitimately
        # can on the Authors page's own (much looser) ranking.
        entries = []
        for i in range(2):
            entries.append({"title": f"Lucky Hit {i}", "year": 2024, "venue": "CVPR", "av_relevance": "core",
                             "citations": 1000, "authors_detail": [{"name": "Lucky Author", "affiliations": []}]})
        for i in range(11):
            entries.append({"title": f"Steady Paper {i}", "year": 2024, "venue": "CVPR", "av_relevance": "core",
                             "citations": 10, "authors_detail": [{"name": "Steady Author", "affiliations": []}]})
        stats = self._run(entries)
        self.assertEqual(stats["insights"]["highest_impact_author"]["name"], "Steady Author")

    def test_drops_authors_detail_from_a_wrong_paper_enrichment_match(self):
        # Real case, user-flagged: a 7-author paper carrying 100 unrelated
        # authors_detail entries (a different paper's full author list),
        # which drove bogus "80 institutions on one paper" leaderboard
        # entries. The mismatched detail must be dropped so it contributes
        # no institutions/countries at all, not partially trusted.
        many_fake_authors = [{"name": f"Fake Author {i}", "affiliations": ["Nowhere University"]}
                              for i in range(100)]
        stats = self._run([
            {"title": "Real Small Paper", "year": 2025, "venue": "CoRL", "av_relevance": "core",
             "authors": "Rachel Luo, Heng Yang, Michael Watson", "authors_detail": many_fake_authors},
        ])
        paper = stats["all_papers"][0]
        self.assertEqual(paper["institutions"], [])

    def test_keeps_authors_detail_when_the_count_is_plausible(self):
        # A genuinely large-team paper (e.g. a big collaborative report)
        # must not get its real data thrown out just for having many
        # authors -- only a count wildly out of proportion to the paper's
        # own author-string length is suspect.
        real_authors = [{"name": f"Real Author {i}", "affiliations": ["MIT"]} for i in range(12)]
        raw_names = ", ".join(f"Real Author {i}" for i in range(12))
        stats = self._run([
            {"title": "Big Team Paper", "year": 2025, "venue": "CoRL", "av_relevance": "core",
             "authors": raw_names, "authors_detail": real_authors},
        ])
        paper = stats["all_papers"][0]
        self.assertEqual(paper["institutions"], ["MIT"])

    def test_drops_authors_detail_that_names_completely_different_people(self):
        # Real case, user-flagged: a 6-Chinese-author AAAI paper carried 8
        # authors_detail entries that were the real author list of an
        # unrelated ~2012 urban-mobility paper -- close enough in COUNT (8 vs
        # 6) that the count-only guard above didn't catch it, driving a bogus
        # "most international paper" insight for a paper that's really only
        # from one country. Comparing surnames catches what count alone
        # can't: authors_detail naming none of the paper's own authors.
        wrong_paper_authors = [
            {"name": "Michael Batty", "affiliations": ["University College London"],
             "countries": ["GB"]},
            {"name": "Kay Axhausen", "affiliations": ["ETH Zurich"], "countries": ["CH"]},
        ]
        stats = self._run([
            {"title": "Driving with Advice", "year": 2026, "venue": "AAAI", "av_relevance": "core",
             "authors": "Junyin Wang, Jinlei Yu, Hao Lin", "authors_detail": wrong_paper_authors},
        ])
        paper = stats["all_papers"][0]
        self.assertEqual(paper["institutions"], [])
        self.assertEqual(paper["countries"], [])

    def test_citing_papers_includes_self_citations_but_tracks_them_separately(self):
        # User-requested: self-citations (citer shares an author with the
        # cited paper) count toward the total like any other citation now --
        # a separate per-author Self-citation % metric (self_citations)
        # covers "how much of this is self-citation" instead of the total
        # silently excluding them.
        stats = self._run(
            [
                {"title": "Cited Paper", "year": 2020, "venue": "CVPR", "av_relevance": "core",
                 "authors": "Alice Smith, Bob Jones"},
                {"title": "Self Citer", "year": 2022, "venue": "CVPR", "av_relevance": "core",
                 "authors": "Alice Smith, Carol White"},
                {"title": "Independent Citer", "year": 2021, "venue": "CVPR", "av_relevance": "core",
                 "authors": "Dave Green"},
            ],
            citation_graph={"edges": {
                "selfciter": ["citedpaper"], "independentciter": ["citedpaper"],
            }},
        )
        by_title = {p["title"]: p for p in stats["all_papers"]}
        cited = by_title["Cited Paper"]
        citers = set(cited["citing_papers"])
        self.assertEqual(citers, {"Self Citer", "Independent Citer"})
        self.assertEqual(cited["self_citations"], 1)

    def test_citing_papers_absent_when_nothing_cites_it(self):
        stats = self._run(
            [{"title": "Uncited Paper", "year": 2020, "venue": "CVPR", "av_relevance": "core"}],
            citation_graph={"edges": {}},
        )
        self.assertNotIn("citing_papers", stats["all_papers"][0])

    def test_citing_papers_ignores_a_citer_not_in_the_ranked_corpus(self):
        # A citer key with no matching core paper (e.g. it cites something
        # adjacent/excluded) must not crash the lookup.
        stats = self._run(
            [{"title": "Cited Paper", "year": 2020, "venue": "CVPR", "av_relevance": "core"}],
            citation_graph={"edges": {"someunmatchedpaper": ["citedpaper"]}},
        )
        self.assertNotIn("citing_papers", stats["all_papers"][0])

    def test_cvf_permanent_failures_counted_as_done_not_pending(self):
        # A confirmed-404 CVF paper can never be fetched no matter how many
        # times build_citation_graph.py reruns -- the coverage stat should
        # count it as done (nothing left to do), not still-pending, or the
        # "Citation graph: CVPR/ICCV/WACV reference lists" row on About can
        # never reach 100% even once every real paper has been scanned.
        stats = self._run(
            [{"title": "A", "year": 2024, "venue": "CVPR", "av_relevance": "core"},
             {"title": "B", "year": 2024, "venue": "CVPR", "av_relevance": "core"}],
            citation_graph={"edges": {}, "sources_scanned": {"cvf": 1, "arxiv": 0},
                             "cvf_permanent_failures": 1},
        )
        cov = stats["corpus_stats"]["citation_graph_coverage"]
        self.assertEqual(cov["cvf_scanned"], 1)
        self.assertEqual(cov["cvf_permanent_failures"], 1)
        self.assertEqual(cov["cvf_core_total"], 2)

    def test_arxiv_eligible_total_only_counts_papers_with_a_known_preprint(self):
        # A paper with no arXiv preprint at all could never be reached by
        # the arXiv reference-list path -- the denominator this row shows on
        # About must exclude it, or 100% is structurally unreachable even
        # once every real preprint has been scanned.
        stats = self._run([
            {"title": "Has Preprint", "year": 2024, "venue": "CVPR", "av_relevance": "core",
             "arxiv_url": "https://arxiv.org/abs/2401.00001"},
            {"title": "No Preprint", "year": 2024, "venue": "CVPR", "av_relevance": "core"},
        ])
        cov = stats["corpus_stats"]["citation_graph_coverage"]
        self.assertEqual(cov["arxiv_eligible_total"], 1)

    def test_abstract_stage_counts_a_confirmed_no_match_as_done(self):
        # mine_abstracts.py sets abstract_search_exhausted when a clean
        # arXiv search confirms no match exists -- that's a completed
        # attempt, same as finding one, and must count as done on the
        # "Abstract search complete" row so a genuinely-unavailable abstract
        # doesn't read as still pending forever.
        stats = self._run([
            {"title": "Has Abstract", "year": 2024, "venue": "CVPR", "av_relevance": "core",
             "abstract": "Some abstract text."},
            {"title": "Confirmed No Match", "year": 2024, "venue": "CVPR", "av_relevance": "core",
             "abstract": "", "abstract_search_exhausted": True},
            # Explicit "" defeats _run's own placeholder-abstract default
            # (see _run's setdefault comment) -- this fixture needs a real
            # "genuinely has neither" case, not the auto-filled one.
            {"title": "Not Yet Searched", "year": 2024, "venue": "CVPR", "av_relevance": "core", "abstract": ""},
        ])
        self.assertEqual(stats["corpus_stats"]["pipeline_stages"]["3_abstract"], 2)

    def test_early_citations_counts_only_citers_within_the_window(self):
        # Paper published 2015 (long-closed window). Citers at 2015 and
        # 2017 (year + EARLY_CITATION_WINDOW_YEARS=2) count; 2018 doesn't.
        stats = self._run(
            [
                {"title": "Old Paper", "year": 2015, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Same Year Citer", "year": 2015, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Within Window Citer", "year": 2017, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Outside Window Citer", "year": 2018, "venue": "CVPR", "av_relevance": "core"},
            ],
            citation_graph={"edges": {
                "sameyearciter": ["oldpaper"],
                "withinwindowciter": ["oldpaper"],
                "outsidewindowciter": ["oldpaper"],
            }},
        )
        by_title = {p["title"]: p for p in stats["all_papers"]}
        self.assertEqual(by_title["Old Paper"]["early_citations"], 2)

    def test_early_citations_omitted_when_window_not_yet_closed(self):
        # A paper from the current year hasn't had 2 years to accumulate
        # early citations yet -- must not show 0 (which would read as
        # "confirmed no early citations" rather than "too soon to tell").
        current_year = datetime.now(timezone.utc).year
        stats = self._run([{"title": "Brand New Paper", "year": current_year, "venue": "CVPR", "av_relevance": "core"}])
        self.assertNotIn("early_citations", stats["all_papers"][0])

    def test_bare_surname_authors_excluded_from_researcher_stats(self):
        # OpenAlex occasionally returns a bare surname ("Wang", "Li", ...)
        # instead of a full name -- seen on real data attached to 20-100+
        # papers each, clearly not one identifiable person. A surname alone
        # must never accumulate researcher-leaderboard/detail stats.
        stats = self._run([
            {"title": "Paper A", "year": 2024, "venue": "CVPR", "av_relevance": "core",
             "citations": 10,
             "authors_detail": [
                 {"name": "Wang", "affiliations": ["Some University"]},
                 {"name": "Xin Wang", "affiliations": ["Some University"]},
             ]},
        ])
        self.assertNotIn("Wang", stats["author_detail"])
        self.assertIn("Xin Wang", stats["author_detail"])

    def test_bare_surname_excluded_from_paper_level_authors_list(self):
        # researchers.html aggregates its leaderboard client-side from each
        # paper's "authors" list in all_papers (see filters.js), not from
        # top_authors/author_detail -- this is the field that actually needs
        # filtering for a bare surname to disappear from the Researchers page.
        stats = self._run([
            {"title": "Paper B", "year": 2024, "venue": "CVPR", "av_relevance": "core",
             "authors_detail": [{"name": "Li"}, {"name": "Wei Li"}]},
            {"title": "Paper C", "year": 2024, "venue": "CVPR", "av_relevance": "core",
             "authors": "Yu, Full Name"},
        ])
        all_authors = {a for p in stats["all_papers"] for a in p["authors"]}
        self.assertNotIn("Li", all_authors)
        self.assertIn("Wei Li", all_authors)
        self.assertNotIn("Yu", all_authors)
        self.assertIn("Full Name", all_authors)

    def test_institution_top_authors_only_credits_the_authors_own_affiliation(self):
        # Real case, confirmed by the user: institution.html's "top authors
        # here" used to credit EVERY author on a paper with each OTHER
        # author's institution too -- Dragomir Anguelov (Waymo-only) was
        # showing as a top author of Google purely because a co-author on a
        # shared paper is Google-affiliated. institution_authors must only
        # ever attribute an institution to the specific author whose OWN
        # authors_detail entry names it.
        stats = self._run([
            {"title": "Shared Paper", "year": 2020, "venue": "CVPR", "av_relevance": "core", "citations": 10,
             "authors_detail": [
                 {"name": "Waymo Person", "affiliations": ["Waymo"]},
                 {"name": "Google Person", "affiliations": ["Google"]},
             ]},
        ])
        waymo_names = {a["name"] for a in stats["institution_authors"].get("Waymo", [])}
        google_names = {a["name"] for a in stats["institution_authors"].get("Google", [])}
        self.assertIn("Waymo Person", waymo_names)
        self.assertNotIn("Waymo Person", google_names)
        self.assertIn("Google Person", google_names)
        self.assertNotIn("Google Person", waymo_names)

    def test_dataset_with_two_introducing_papers_merges_citations(self):
        # Real case: KITTI's citable contribution splits across two papers
        # (the original 2012 benchmark-suite paper and a fuller 2013
        # writeup) -- a citing paper referencing EITHER one must count
        # toward the same "KITTI" dataset entry, not just whichever paper
        # happened to be seeded first.
        stats = self._run(
            [
                {"title": "Are we ready for autonomous driving? The KITTI vision benchmark suite",
                 "year": 2012, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Vision meets robotics: The KITTI dataset",
                 "year": 2013, "venue": "IJRR", "av_relevance": "core"},
                {"title": "Cites The 2012 Paper", "year": 2015, "venue": "CVPR", "av_relevance": "core"},
                {"title": "Cites The 2013 Paper", "year": 2016, "venue": "CVPR", "av_relevance": "core"},
            ],
            citation_graph={"edges": {
                "citesthe2012paper": ["arewereadyforautonomousdrivingthekittivisionbenchmarksuite"],
                "citesthe2013paper": ["visionmeetsroboticsthekittidataset"],
            }},
        )
        kitti = next(d for d in stats["datasets"] if d["name"] == "KITTI")
        self.assertEqual(kitti["citing_count"], 2)
        citer_titles = set(kitti["citing_papers"])
        self.assertEqual(citer_titles, {"Cites The 2012 Paper", "Cites The 2013 Paper"})
        self.assertEqual(
            [a["title"] for a in kitti["also_introduced_in"]], ["Vision meets robotics: The KITTI dataset"])


class TestComputeInsights(unittest.TestCase):
    def _paper(self, title, year, citations=None, category=None, institutions=None,
               countries=None, authors=None):
        return {
            "title": title, "year": year, "citations": citations, "category": category,
            "institutions": institutions or [], "countries": countries or [], "authors": authors or [],
        }

    def test_flags_the_latest_year_as_partial_and_excludes_it_from_yoy(self):
        papers = [
            self._paper("A", 2023), self._paper("B", 2023),
            self._paper("C", 2024), self._paper("D", 2024), self._paper("E", 2024), self._paper("F", 2024),
            self._paper("G", 2025),  # a lone paper in the newest year -- must not look like a crash
        ]
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        # 2025 (the latest year, one lone paper) must not appear ANYWHERE in
        # a year-based figure -- not just flagged, excluded outright.
        self.assertNotIn(2025, insights["corpus_growth"]["years"])
        self.assertEqual(insights["corpus_growth"]["years"], [2023, 2024])
        self.assertEqual(insights["avg_annual_growth"]["from_year"], 2023)
        self.assertEqual(insights["avg_annual_growth"]["to_year"], 2024)
        self.assertEqual(insights["avg_annual_growth"]["pct"], 100.0)  # 2 -> 4 papers, one interval

    def test_avg_annual_growth_is_compounded_not_averaged_across_multiple_years(self):
        # 4 complete years, doubling every year: 10 -> 20 -> 40 -> 80. A
        # naive average of the three 100% jumps would also say 100%, but so
        # would (wrongly) averaging in a wildly different single bad year --
        # CAGR over the whole window is the number that actually describes
        # "doubling every year" correctly and stays robust to one outlier.
        papers = (
            [self._paper(f"A{i}", 2021) for i in range(10)]
            + [self._paper(f"B{i}", 2022) for i in range(20)]
            + [self._paper(f"C{i}", 2023) for i in range(40)]
            + [self._paper(f"D{i}", 2024) for i in range(80)]
            + [self._paper("Partial", 2025)]  # excluded as the latest/partial year
        )
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        self.assertEqual(insights["avg_annual_growth"]["from_year"], 2021)
        self.assertEqual(insights["avg_annual_growth"]["to_year"], 2024)
        self.assertEqual(insights["avg_annual_growth"]["pct"], 100.0)

    def test_self_citation_rate_matches_the_same_exclusion_rule_used_elsewhere(self):
        papers = [
            self._paper("Cited Paper", 2020, authors=["Alice Smith", "Bob Jones"]),
            self._paper("Self Citer", 2021, authors=["Alice Smith", "Carol White"]),
            self._paper("Independent Citer", 2021, authors=["Dave Green"]),
        ]
        graph = {"edges": {"selfciter": ["citedpaper"], "independentciter": ["citedpaper"]}}
        insights = ag.compute_insights(papers, [], graph, {}, [], [], [])
        self.assertEqual(insights["self_citation_counts"], {"total_edges": 2, "self_edges": 1})
        self.assertEqual(insights["self_citation_rate_pct"], 50.0)

    def test_citation_concentration_on_a_simple_known_distribution(self):
        # 10 papers, one with 91 citations and nine with 1 each (100 total) --
        # the single top paper (the top 10%, rounding 1 paper) holds 91%.
        papers = [self._paper(f"P{i}", 2020, citations=1) for i in range(9)]
        papers.append(self._paper("Big", 2020, citations=91))
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        conc = insights["citation_concentration"]
        self.assertEqual(conc["cited_papers"], 10)
        self.assertEqual(conc["top10pct_share_pct"], 91.0)

    def test_picks_the_actual_extreme_papers(self):
        papers = [
            self._paper("Low", 2020, citations=5, authors=["A Person"]),
            self._paper("High", 2021, citations=500, authors=["Someone Famous"]),
            self._paper("Solo author", 2020, institutions=["MIT"], countries=["United States"],
                        authors=["A Person"]),
            self._paper("Big collab", 2022, institutions=["MIT", "Google", "Waymo"],
                        countries=["United States", "Germany", "China"], authors=["Someone Else"]),
        ]
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        self.assertEqual(insights["most_cited_paper"]["title"], "High")
        self.assertEqual(insights["most_cited_paper"]["short_title"], "Famous21")
        self.assertEqual(insights["most_collaborative_paper"]["title"], "Big collab")
        self.assertEqual(insights["most_collaborative_paper"]["institution_count"], 3)
        self.assertEqual(insights["most_international_paper"]["title"], "Big collab")
        self.assertEqual(insights["most_international_paper"]["country_count"], 3)

    def test_researcher_lifetime_distribution_buckets_and_caps_at_14(self):
        author_lifetimes = {
            "Zero Year": {"first_year": 2020, "last_year": 2020, "lifetime": 0, "papers": 1, "citations": 0, "cited_papers": 0},
            "Two Years": {"first_year": 2018, "last_year": 2020, "lifetime": 2, "papers": 3, "citations": 10, "cited_papers": 2},
            "Long Career": {"first_year": 2005, "last_year": 2025, "lifetime": 20, "papers": 30, "citations": 500, "cited_papers": 20},
        }
        insights = ag.compute_insights([], [], {"edges": {}}, {}, [], [], [], author_lifetimes=author_lifetimes)
        dist = {row["lifetime"]: row["researchers"] for row in insights["researcher_lifetime_distribution"]}
        self.assertEqual(dist[0], 1)
        self.assertEqual(dist[2], 1)
        # A 20-year lifetime is lumped into the capped 14 bucket, not its own
        # long tail of mostly-empty buckets.
        self.assertEqual(dist[14], 1)
        self.assertEqual(len(insights["researcher_lifetime_distribution"]), 15)  # 0..14 inclusive

    def test_citations_by_lifetime_requires_a_minimum_sample_and_excludes_uncited_authors(self):
        author_lifetimes = {}
        # 5 authors at lifetime=1, all cited -- enough for a real bucket.
        for i, citations in enumerate([10, 20, 30, 40, 50]):
            author_lifetimes[f"Cited{i}"] = {
                "first_year": 2020, "last_year": 2021, "lifetime": 1,
                "papers": 2, "citations": citations, "cited_papers": 1,
            }
        # An uncited author at the same lifetime must not pull the mean down --
        # they have no real citation figure, not a citation figure of 0.
        author_lifetimes["NoCitations"] = {
            "first_year": 2020, "last_year": 2021, "lifetime": 1,
            "papers": 2, "citations": 0, "cited_papers": 0,
        }
        # Only 2 authors at lifetime=5 -- below the minimum sample, must be
        # excluded from the output entirely rather than shown with a
        # misleadingly precise stdev off 2 data points.
        author_lifetimes["Sparse1"] = {"first_year": 2015, "last_year": 2020, "lifetime": 5,
                                        "papers": 1, "citations": 5, "cited_papers": 1}
        author_lifetimes["Sparse2"] = {"first_year": 2015, "last_year": 2020, "lifetime": 5,
                                        "papers": 1, "citations": 15, "cited_papers": 1}
        insights = ag.compute_insights([], [], {"edges": {}}, {}, [], [], [], author_lifetimes=author_lifetimes)
        by_lifetime = {row["lifetime"]: row for row in insights["citations_by_researcher_lifetime"]}
        self.assertIn(1, by_lifetime)
        self.assertEqual(by_lifetime[1]["researchers"], 5, "the uncited author must not be counted in this bucket")
        self.assertEqual(by_lifetime[1]["mean_citations"], 30.0)
        self.assertNotIn(5, by_lifetime, "a 2-author bucket is below the minimum sample size and must be omitted")

    def test_most_promising_young_researchers_filters_and_ranks_by_avg_citations(self):
        author_lifetimes = {
            # Short career, well-cited, enough papers, active in the last
            # complete year -- should be selected.
            "Rising Star": {"first_year": 2023, "last_year": 2025, "lifetime": 2,
                             "papers": 5, "citations": 500, "cited_papers": 5},
            # Long career -- not "young", excluded regardless of impact.
            "Veteran": {"first_year": 2005, "last_year": 2025, "lifetime": 20,
                        "papers": 50, "citations": 5000, "cited_papers": 50},
            # Short career but below the min-papers floor -- excluded.
            "Too Few Papers": {"first_year": 2024, "last_year": 2025, "lifetime": 1,
                                "papers": 4, "citations": 1000, "cited_papers": 4},
            # Short career, enough papers, but zero citation data -- excluded
            # (cited_papers > 0 is a technical floor against a division by
            # zero when computing avg_citations, not a business rule).
            "No Citation Data": {"first_year": 2024, "last_year": 2025, "lifetime": 1,
                                  "papers": 6, "citations": 0, "cited_papers": 0},
            # Short career, well-cited, enough papers, but not active in the
            # last complete year -- excluded (user-requested: only list
            # people still active in the most recent complete year).
            "Stopped Publishing": {"first_year": 2021, "last_year": 2023, "lifetime": 2,
                                    "papers": 5, "citations": 500, "cited_papers": 5},
        }
        # Two distinct years so complete_years (which excludes the single
        # latest, still-partial year, 2026) is non-empty and its last entry,
        # 2025, is the recency cutoff the filter above checks against.
        papers = [
            self._paper("Anchor 2025", 2025, citations=1, authors=["Someone"]),
            self._paper("Anchor 2026", 2026, citations=1, authors=["Someone Else"]),
        ]
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [], author_lifetimes=author_lifetimes)
        names = [a["name"] for a in insights["most_promising_young_researchers"]]
        self.assertEqual(names, ["Rising Star"])
        self.assertEqual(insights["young_researchers_cutoff_year"], 2025)

    def test_most_cited_paper_uses_the_known_dataset_short_name_when_one_exists(self):
        papers = [self._paper("nuScenes: A Multimodal Dataset for Autonomous Driving", 2020, citations=300,
                               authors=["Holger Caesar"])]
        datasets = [{"name": "nuScenes", "paper_title": "nuScenes: A Multimodal Dataset for Autonomous Driving",
                     "also_introduced_in": []}]
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [], datasets)
        self.assertEqual(insights["most_cited_paper"]["short_title"], "nuScenes")

    def test_highest_impact_author_respects_the_min_papers_floor_passed_in(self):
        # Insights passes a stricter min_papers than the Authors page's own
        # default (1) -- verify compute_insights just surfaces whatever list
        # it's given as-is (the floor itself is enforced by the caller via
        # top_authors_by_avg's min_papers param, tested separately below).
        top_authors_avg = [{"name": "Prolific Author", "papers": 15, "citations": 300, "avg_citations": 20}]
        insights = ag.compute_insights([], [], {"edges": {}}, {}, top_authors_avg, [], [])
        self.assertEqual(insights["highest_impact_author"]["name"], "Prolific Author")

    def test_venue_relevance_excludes_small_venues_and_arxiv(self):
        all_entries = (
            [{"venue": "CVPR", "av_relevance": "core"}] * 20
            + [{"venue": "CVPR", "av_relevance": "adjacent"}] * 80
            + [{"venue": "TinyWorkshop", "av_relevance": "core"}] * 5  # under the 50-paper floor
            + [{"venue": "arXiv", "av_relevance": "core"}] * 90  # excluded: a cherry-picked sample, not a real venue
            + [{"venue": "arXiv", "av_relevance": "adjacent"}] * 10
        )
        insights = ag.compute_insights([], all_entries, {"edges": {}}, {}, [], [], [])
        # venue_relevance ships the full list (not just top/bottom 3) -- the
        # client's own Min. papers dropdown (default 80) does the highest/
        # lowest-3 slicing at whatever threshold the reader picks.
        venues_seen = {v["venue"] for v in insights["venue_relevance"]}
        self.assertIn("CVPR", venues_seen)
        self.assertNotIn("TinyWorkshop", venues_seen)
        self.assertNotIn("arXiv", venues_seen)

    def test_disruption_index_summary_ranks_and_averages_scored_papers(self):
        papers = [self._paper("Disruptive Paper", 2020), self._paper("Consolidating Paper", 2020)]
        papers[0]["cd_index"] = 1.0
        papers[1]["cd_index"] = -1.0
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        d = insights["disruption_index"]
        self.assertEqual(d["scored_papers"], 2)
        self.assertEqual(d["mean_cd_index"], 0.0)
        self.assertEqual(d["most_disruptive"][0]["title"], "Disruptive Paper")
        self.assertEqual(d["most_consolidating"][0]["title"], "Consolidating Paper")

    def test_disruption_index_absent_when_no_papers_scored(self):
        papers = [self._paper("Unscored Paper", 2020)]
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        self.assertNotIn("disruption_index", insights)

    def test_open_source_compares_citations_between_checked_groups_only(self):
        papers = []
        for i in range(15):
            p = self._paper(f"With code {i}", 2021, citations=10)
            p["has_code_link"] = True
            papers.append(p)
        for i in range(10):
            p = self._paper(f"Without code {i}", 2021, citations=2)
            p["has_code_link"] = False
            papers.append(p)
        # Never checked at all -- must not be pulled into either group or
        # the checked_papers count.
        papers.append(self._paper("Never checked", 2021, citations=1000))
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        o = insights["open_source"]
        self.assertEqual(o["checked_papers"], 25)
        self.assertEqual(o["with_code_pct"], 60.0)
        self.assertEqual(o["avg_citations_with_code"], 10.0)
        self.assertEqual(o["avg_citations_without_code"], 2.0)

    def test_open_source_absent_below_min_checked_threshold(self):
        papers = []
        for i in range(5):
            p = self._paper(f"Paper {i}", 2021, citations=1)
            p["has_code_link"] = True
            papers.append(p)
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        self.assertNotIn("open_source", insights)

    def test_open_source_by_year_excludes_the_latest_partial_year(self):
        papers = []
        for i in range(20):
            p = self._paper(f"2022 Paper {i}", 2022)
            p["has_code_link"] = i < 10
            papers.append(p)
        for i in range(20):
            p = self._paper(f"2023 Paper {i}", 2023)  # latest year -- partial, excluded
            p["has_code_link"] = True
            papers.append(p)
        insights = ag.compute_insights(papers, [], {"edges": {}}, {}, [], [], [])
        years = {row["year"] for row in insights["open_source"]["with_code_pct_by_year"]}
        self.assertIn(2022, years)
        self.assertNotIn(2023, years)


if __name__ == "__main__":
    unittest.main()
