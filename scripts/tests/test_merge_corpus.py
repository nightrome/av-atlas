#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for merge_corpus.py -- title normalization/dedup, and the
authors_detail carry-over safety net (a real incident: re-running this script
used to silently discard OpenAlex author enrichment that a separate script
had patched directly onto papers_full.json, because this script rebuilds
that file from venues/*.json alone).

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_merge_corpus.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import merge_corpus as mc


class TestNormalizeTitle(unittest.TestCase):
    def test_case_and_punctuation_insensitive(self):
        self.assertEqual(mc.normalize_title("Depth Anything!"), mc.normalize_title("depth anything"))

    def test_different_titles_do_not_collide(self):
        self.assertNotEqual(mc.normalize_title("CARLA Simulator"), mc.normalize_title("CARLA 2.0 Simulator"))

    def test_none_title_does_not_crash(self):
        self.assertEqual(mc.normalize_title(None), "")

    def test_latex_and_superscript_notation_folds_together(self):
        # Same paper, one listing uses LaTeX math / a superscript glyph.
        for a, b in (
            ("A^2-Net: Molecular Structure Estimation", "A2-Net: Molecular Structure Estimation"),
            ("D$^3$RoMa: Disparity Diffusion Depth Sensing", "D3RoMa: Disparity Diffusion Depth Sensing"),
            ("VIENA²: A Driving Anticipation Dataset", "VIENA2: A Driving Anticipation Dataset"),
            ("$360+x$: A Panoptic Scene Dataset", "360+x: A Panoptic Scene Dataset"),
        ):
            self.assertEqual(mc.normalize_title(a), mc.normalize_title(b), (a, b))

    def test_known_duplicate_title_pair_folds_together(self):
        # A hand-verified pair (see KNOWN_DUPLICATE_TITLES' own comment):
        # same paper, retitled between its arXiv preprint and its IROS
        # camera-ready version, with a swapped leading qualifier none of
        # the other heuristics here catch.
        self.assertEqual(
            mc.normalize_title("End-to-end Learned Visual Odometry with Events and Frames"),
            mc.normalize_title("Deep Visual Odometry with Events and Frames"))

    def test_missing_space_after_acronym_colon_folds(self):
        self.assertEqual(mc.normalize_title("ADBA: Approximation Decision Boundary Approach"),
                         mc.normalize_title("ADBA:Approximation Decision Boundary Approach"))

    def test_spaced_dash_acronym_separator_folds_like_a_colon(self):
        # ICRA's community lists render "NAME: Subtitle" as "NAME - Subtitle".
        self.assertEqual(
            mc.normalize_title("MVX-Net - Multimodal VoxelNet for 3D Object Detection"),
            mc.normalize_title("MVX-Net: Multimodal VoxelNet for 3D Object Detection"))
        # ...but a plain descriptive lead-in, or a bare "2D"/"3D", is NOT a
        # coined name -- these must stay distinct.
        self.assertNotEqual(
            mc.normalize_title("2D - Object Detection in the Wild for Autonomous Cars"),
            mc.normalize_title("3D - Object Detection in the Wild for Autonomous Cars"))
        self.assertNotEqual(
            mc.normalize_title("Deep Learning - A Survey of Methods for Self Driving"),
            mc.normalize_title("Reinforcement Learning - A Survey of Methods for Self Driving"))

    def test_markdown_link_and_wrapping_quotes_are_stripped(self):
        self.assertEqual(
            mc.clean_title("[Learning Agile Locomotion on Risky Terrains](https://arxiv.org/abs/2311.10484)"),
            "Learning Agile Locomotion on Risky Terrains")
        self.assertEqual(mc.clean_title('"ShAPO: Implicit Representations"'), "ShAPO: Implicit Representations")
        self.assertEqual(
            mc.normalize_title("[ShAPO: Implicit Representations for Shape](https://arxiv.org/abs/2207.13691)"),
            mc.normalize_title("ShAPO: Implicit Representations for Shape"))

    def test_descriptive_colon_lead_in_is_still_untouched(self):
        self.assertNotEqual(mc.normalize_title("Learning to Drive: A Survey"),
                            mc.normalize_title("A Survey"))

    def test_decorative_emoji_in_the_title_is_ignored(self):
        for a, b in (
            ("🏘️ ProcTHOR: Large-Scale Embodied AI", "ProcTHOR: Large-Scale Embodied AI"),
            ("PooDLe🐩: Pooled dense self-supervised learning", "PooDLe: Pooled dense self-supervised learning"),
            ("⚡FLARES⚡: Fast LiDAR Semantic Segmentation", "FLARES: Fast LiDAR Semantic Segmentation"),
        ):
            self.assertEqual(mc.normalize_title(a), mc.normalize_title(b), (a, b))

    def test_final_word_singular_plural_folds(self):
        self.assertEqual(
            mc.normalize_title("Container: Context Aggregation Network"),
            mc.normalize_title("Container: Context Aggregation Networks"))
        self.assertEqual(
            mc.normalize_title("Feature Priors from Multi-View Image"),
            mc.normalize_title("Feature Priors from Multi-View Images"))
        # a real "-ss" word is not stemmed, so these stay distinct
        self.assertNotEqual(mc.normalize_title("Progress"), mc.normalize_title("Progres"))


class TestHtmlEntitiesInTitles(unittest.TestCase):
    def test_entities_are_unescaped_in_the_stored_title(self):
        self.assertEqual(mc.clean_title("RoadText-1K: Text Detection &amp; Recognition Dataset"),
                         "RoadText-1K: Text Detection & Recognition Dataset")
        self.assertEqual(mc.clean_title("Piecewise B&#233;zier Curve"), "Piecewise B\u00e9zier Curve")
        self.assertEqual(mc.clean_title("CHASE Algorithm: &#34;Ease of Driving&#34; Classification"),
                         'CHASE Algorithm: "Ease of Driving" Classification')

    def test_escaped_and_plain_copies_share_a_key(self):
        self.assertEqual(mc.normalize_title("Navya3DSeg - Dataset Design &#38; Split Generation"),
                         mc.normalize_title("Navya3DSeg - Dataset Design & Split Generation"))


class TestAuthorStrings(unittest.TestCase):
    def test_neurips_last_first_pairs_become_first_last(self):
        self.assertEqual(
            mc.normalize_author_string("Fan, Lue, Wang, Feng, Wang, Naiyan, ZHANG, ZHAO-XIANG", last_first=True),
            "Lue Fan, Feng Wang, Naiyan Wang, ZHAO-XIANG ZHANG")

    def test_two_word_given_names_stay_with_their_surname(self):
        # Split on commas alone, "Gim Hee" was a person on the site.
        self.assertEqual(mc.normalize_author_string("Lee, Gim Hee, Van Gool, Luc", last_first=True),
                         "Gim Hee Lee, Luc Van Gool")

    def test_nickname_in_parentheses_is_kept_for_the_name_cleaner(self):
        self.assertEqual(mc.normalize_author_string("Peng, Zhenghao (Mark), Zhou, Bolei", last_first=True),
                         "Zhenghao (Mark) Peng, Bolei Zhou")

    def test_an_odd_number_of_parts_is_left_alone(self):
        s = "Choo, XianJun, Davin, Jin, Billy"
        self.assertEqual(mc.normalize_author_string(s, last_first=True), s)

    def test_bibtex_and_form(self):
        self.assertEqual(mc.normalize_author_string("Tsoli, Aggeliki and Argyros, Antonis A."),
                         "Aggeliki Tsoli, Antonis A. Argyros")

    def test_plain_list_ending_in_and_loses_the_and(self):
        self.assertEqual(mc.normalize_author_string("Lisa Mais, Peter Hirsch and Dagmar Kainmueller"),
                         "Lisa Mais, Peter Hirsch, Dagmar Kainmueller")
        self.assertEqual(mc.normalize_author_string("Subeesh Vasu, Leonardo Citraro, and Pascal Fua"),
                         "Subeesh Vasu, Leonardo Citraro, Pascal Fua")

    def test_ordinary_strings_are_untouched(self):
        for s in ("Nathan Kallus, Ashok Cutkosky", "Christopher H. Lin, Mausam, Daniel S. Weld",
                  "Aakash, Indranil Saha 0001", "", None):
            self.assertEqual(mc.normalize_author_string(s), s)

    def test_file_level_detection(self):
        neurips = [{"authors": "Kallus, Nathan"}, {"authors": "Fan, Lue, Wang, Feng"},
                   {"authors": "Lee, Gim Hee, Tan, Robby"}]
        self.assertTrue(mc.uses_last_first_authors(neurips))
        eccv2018 = [{"authors": "Tsoli, Aggeliki and Argyros, Antonis A."}, {"authors": "Sekii, Taiki"}]
        self.assertTrue(mc.uses_last_first_authors(eccv2018))
        aaai = [{"authors": "Aakash, Indranil Saha 0001"}, {"authors": "Ligong Han, Ruijiang Gao"},
                {"authors": "Takuma Udagawa, Akiko Aizawa"}, {"authors": "Nathan Kallus"}]
        self.assertFalse(mc.uses_last_first_authors(aaai))
        self.assertFalse(mc.uses_last_first_authors([]))


class TestArxivId(unittest.TestCase):
    def test_url_forms(self):
        self.assertEqual(mc.arxiv_id("https://arxiv.org/abs/2003.08799"), "2003.08799")
        self.assertEqual(mc.arxiv_id("http://arxiv.org/abs/2003.08799v2"), "2003.08799")
        self.assertEqual(mc.arxiv_id("https://arxiv.org/pdf/2003.08799v1.pdf"), "2003.08799")
        self.assertIsNone(mc.arxiv_id("10.1109/CVPR.2021.00001"))
        self.assertIsNone(mc.arxiv_id(None))


class TestConferenceAndYearForFile(unittest.TestCase):
    def test_derives_conference_and_year_from_a_per_venue_year_filename(self):
        self.assertEqual(mc.conference_and_year_for_file("cvpr2024.json"), ("CVPR", 2024))
        self.assertEqual(mc.conference_and_year_for_file("neurips2025.json"), ("NeurIPS", 2025))

    def test_strips_a_github_suffix(self):
        self.assertEqual(mc.conference_and_year_for_file("icra2019_github.json"), ("ICRA", 2019))

    def test_a_continuous_journal_file_gets_conference_but_no_year(self):
        # "_all" files (e.g. ijcv_all.json) span many years -- the real year
        # has to come from each entry, not the filename.
        self.assertEqual(mc.conference_and_year_for_file("ijcv_all.json"), ("IJCV", None))

    def test_unrecognized_prefix_returns_no_conference(self):
        conference, year = mc.conference_and_year_for_file("arxiv_s2_citing.json")
        self.assertIsNone(conference)


class TestMergeCorpusEndToEnd(unittest.TestCase):
    def _run(self, venue_papers, prior_papers_full=None, venue_filename="cvpr2024.json", extra_files=None):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        base = Path(tmpdir.name)
        venues_dir = base / "venues"
        venues_dir.mkdir()
        (venues_dir / venue_filename).write_text(json.dumps(venue_papers), encoding="utf-8")
        for name, entries in (extra_files or {}).items():
            (venues_dir / name).write_text(json.dumps(entries), encoding="utf-8")

        categories_file = base / "categories.json"
        categories_file.write_text(json.dumps({"categories": []}), encoding="utf-8")

        out_file = base / "papers_full.json"
        if prior_papers_full is not None:
            out_file.write_text(json.dumps(prior_papers_full), encoding="utf-8")

        orig = (mc.VENUES_DIR, mc.OUT_FILE, mc.CATEGORIES_FILE)
        mc.VENUES_DIR, mc.OUT_FILE, mc.CATEGORIES_FILE = (venues_dir, out_file, categories_file)
        try:
            mc.main()
        finally:
            mc.VENUES_DIR, mc.OUT_FILE, mc.CATEGORIES_FILE = orig

        return json.loads(out_file.read_text(encoding="utf-8"))

    def test_carries_over_authors_detail_from_prior_run(self):
        prior = [{
            "title": "Planning-Oriented Autonomous Driving",
            "authors_detail": [{"name": "Chonghao Sima", "affiliations": ["HKU"], "countries": ["HK"]}],
        }]
        fresh_venue_papers = [{
            "title": "Planning-Oriented Autonomous Driving",
            "authors": "C Sima", "abstract": "Autonomous driving.", "conference": "CVPR", "year": 2023,
        }]
        papers = self._run(fresh_venue_papers, prior_papers_full=prior)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["authors_detail"][0]["name"], "Chonghao Sima")

    def test_carries_over_citations_by_source_from_prior_run(self):
        prior = [{
            "title": "Some Paper",
            "citations_by_source": {"openalex": {"count": 40, "updated": "2026-08-01"}},
        }]
        venue_papers = [{"title": "Some Paper", "authors": "S Name", "conference": "CVPR", "year": 2023}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertEqual(papers[0]["citations_by_source"]["openalex"]["count"], 40)

    def test_carries_over_authors_detail_source_from_prior_run(self):
        # A real bug, not hypothetical: authors_detail_source was missing
        # from CARRY_OVER_FIELDS, so it got silently dropped on every rebuild
        # even though authors_detail itself survived -- confirmed on real
        # data, every paper with authors_detail showed
        # authors_detail_source=None.
        prior = [{
            "title": "Some Paper",
            "authors_detail": [{"name": "Carried Name"}],
            "authors_detail_source": "openalex",
        }]
        venue_papers = [{"title": "Some Paper", "authors": "S Name", "conference": "CVPR", "year": 2023}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertEqual(papers[0]["authors_detail_source"], "openalex")

    def test_carries_over_a_confirmed_false_has_code_link(self):
        # has_code_link is a real 3-state field (True/False/never-checked) --
        # a bare truthy carry-over check would silently drop every confirmed
        # False (checked, no code link found), making it indistinguishable
        # from "never checked" on the very next rebuild. Regression test for
        # exactly that bug, caught before it shipped.
        prior = [{"title": "Checked, No Code", "has_code_link": False}]
        venue_papers = [{"title": "Checked, No Code", "authors": "A B", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertIn("has_code_link", papers[0])
        self.assertFalse(papers[0]["has_code_link"])

    def test_carries_over_abstract_search_exhausted_from_prior_run(self):
        # mine_abstracts.py sets this when a clean arXiv search confirms no
        # match exists, so a rerun of this script (which rebuilds the file
        # from venues/*.json alone) must not silently discard that -- the
        # same class of bug the authors_detail carry-over above already
        # guards against.
        prior = [{"title": "No Match Paper", "abstract_search_exhausted": True}]
        venue_papers = [{"title": "No Match Paper", "authors": "A B", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertTrue(papers[0]["abstract_search_exhausted"])

    def test_carried_over_detail_is_kept_when_no_fresher_source_exists(self):
        prior = [{"title": "Some Paper", "authors_detail": [{"name": "Carried Name"}]}]
        venue_papers = [{"title": "Some Paper", "authors": "S Name", "conference": "CVPR", "year": 2023}]
        papers = self._run(venue_papers, prior_papers_full=prior)
        self.assertEqual(papers[0]["authors_detail"][0]["name"], "Carried Name")

    def test_no_prior_file_does_not_crash(self):
        venue_papers = [{"title": "New Paper", "authors": "A B", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers, prior_papers_full=None)
        self.assertEqual(len(papers), 1)
        self.assertNotIn("authors_detail", papers[0])

    def test_venue_and_year_are_derived_from_filename_when_absent_from_entries(self):
        # The whole point of stripping "conference"/"year" from venues/*.json
        # (repo-size cleanup) -- a real per-venue-year file with neither
        # field on any entry must still produce the right venue/year.
        venue_papers = [{"title": "Stripped Fields Paper", "authors": "A B"}]
        papers = self._run(venue_papers, venue_filename="wacv2023.json")
        self.assertEqual(papers[0]["venue"], "WACV")
        self.assertEqual(papers[0]["year"], 2023)

    def test_explicit_per_entry_conference_and_year_still_win(self):
        # An entry that DOES carry its own conference/year (an "_all"
        # journal file's year, or a not-yet-migrated file) must not be
        # overridden by a filename-derived guess.
        venue_papers = [{"title": "Explicit Fields Paper", "authors": "A B",
                          "conference": "IJCV", "year": 2019}]
        papers = self._run(venue_papers, venue_filename="ijcv_all.json")
        self.assertEqual(papers[0]["venue"], "IJCV")
        self.assertEqual(papers[0]["year"], 2019)

    def test_every_paper_gets_classified(self):
        venue_papers = [{"title": "Autonomous Driving Survey", "authors": "A B",
                          "abstract": "A survey of autonomous driving.", "conference": "CVPR", "year": 2024}]
        papers = self._run(venue_papers)
        self.assertIn("category", papers[0])
        self.assertIn("av_relevance", papers[0])
        self.assertEqual(papers[0]["av_relevance"], "AV")

    def test_icra_and_iros_papers_are_included(self):
        venue_papers = [
            {"title": "An ICRA Paper", "authors": "A", "conference": "ICRA", "year": 2022},
            {"title": "An IROS Paper", "authors": "B", "conference": "IROS", "year": 2022},
            {"title": "A CVPR Paper", "authors": "C", "conference": "CVPR", "year": 2024},
        ]
        papers = self._run(venue_papers)
        self.assertEqual(len(papers), 3)

    def test_neurips_author_strings_are_rewritten(self):
        venue_papers = [
            {"title": "Fully Sparse 3D Object Detection", "authors": "Fan, Lue, Wang, Feng, Wang, Naiyan"},
            {"title": "Another Paper", "authors": "Kallus, Nathan"},
        ]
        papers = self._run(venue_papers, venue_filename="neurips2022.json")
        by_title = {p["title"]: p for p in papers}
        self.assertEqual(by_title["Fully Sparse 3D Object Detection"]["authors"],
                         "Lue Fan, Feng Wang, Naiyan Wang")
        self.assertEqual(by_title["Another Paper"]["authors"], "Nathan Kallus")

    def test_title_and_venue_entities_are_unescaped(self):
        venue_papers = [{"title": "Detection &amp; Recognition", "authors": "A B",
                          "conference": "Journal of Intelligent &amp; Robotic Systems", "year": 2020}]
        papers = self._run(venue_papers, venue_filename="ijrr_all.json")
        self.assertEqual(papers[0]["title"], "Detection & Recognition")
        self.assertEqual(papers[0]["venue"], "Journal of Intelligent & Robotic Systems")

    def test_renamed_preprint_folds_into_the_venue_record_by_arxiv_id(self):
        # The venue record's arxiv_url comes from the previous run (it's
        # stamped onto papers_full.json by apply_arxiv_links.py).
        prior = [{"title": "Generalizable Pedestrian Detection: The Elephant in the Room",
                  "arxiv_url": "https://arxiv.org/abs/2003.08799"}]
        venue_papers = [{"title": "Generalizable Pedestrian Detection: The Elephant in the Room",
                          "authors": ""}]
        arxiv_papers = [{"title": "Pedestrian Detection: The Elephant In The Room",
                          "authors": "Irtiza Hasan, Shengcai Liao", "abstract": "Pedestrians.",
                          "conference": "arXiv.org", "year": 2020,
                          "doi": "https://arxiv.org/abs/2003.08799v2"}]
        papers = self._run(venue_papers, prior_papers_full=prior, venue_filename="cvpr2021.json",
                           extra_files={"arxiv_s2_citing.json": arxiv_papers})
        self.assertEqual(len(papers), 1)
        p = papers[0]
        self.assertEqual(p["title"], "Generalizable Pedestrian Detection: The Elephant in the Room")
        self.assertEqual((p["venue"], p["year"], p["source"]), ("CVPR", 2021, "venue_listing"))
        # ...and takes only what it was missing from the arXiv copy.
        self.assertEqual(p["authors"], "Irtiza Hasan, Shengcai Liao")
        self.assertEqual(p["abstract"], "Pedestrians.")

    def test_two_venue_records_sharing_an_arxiv_id_are_both_kept(self):
        # A conference paper and its journal version are two listings.
        prior = [{"title": "Benchmarking the Robustness of Segmentation",
                  "arxiv_url": "https://arxiv.org/abs/1908.05005"},
                 {"title": "Benchmarking the Robustness of Segmentation Models Extended",
                  "arxiv_url": "https://arxiv.org/abs/1908.05005"}]
        papers = self._run(
            [{"title": "Benchmarking the Robustness of Segmentation", "authors": "A B"}],
            prior_papers_full=prior, venue_filename="cvpr2020.json",
            extra_files={"ijcv_all.json": [{"title": "Benchmarking the Robustness of Segmentation Models Extended",
                                            "authors": "A B", "year": 2021}]})
        self.assertEqual(len(papers), 2)

    def test_two_arxiv_records_sharing_an_arxiv_id_are_both_kept(self):
        # Semantic Scholar sometimes files a second paper by the same
        # authors under one arXiv id.
        arxiv_papers = [
            {"title": "Extend the Safety Horizon", "conference": "arXiv preprint", "year": 2026,
             "doi": "https://arxiv.org/abs/2608.14603"},
            {"title": "HMS-SCP: Task-Oriented Semantic Communication", "conference": "arXiv preprint",
             "year": 2026, "doi": "https://arxiv.org/abs/2608.14603"},
        ]
        papers = self._run([], extra_files={"arxiv_s2_citing.json": arxiv_papers})
        self.assertEqual(len(papers), 2)

    def test_dedupes_by_normalized_title(self):
        venue_papers = [
            {"title": "Same Paper", "authors": "A", "conference": "CVPR", "year": 2024},
            {"title": "same paper", "authors": "B", "conference": "CVPR", "year": 2024},
        ]
        papers = self._run(venue_papers)
        self.assertEqual(len(papers), 1)


if __name__ == "__main__":
    unittest.main()
