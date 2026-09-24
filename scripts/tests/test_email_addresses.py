#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tests for email_addresses.py, plus two checks on real data: no tracked file
under data/ may contain an email address (the repo is public), and, when a
build has left data/stats.json and its shards in place, neither may anything
the site publishes. The second one is skipped on a fresh clone or in CI,
where those gitignored files don't exist.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_email_addresses.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import email_addresses as ea  # noqa: E402

# Metric notation and other "@" uses in abstracts and names that must never
# be read as an address.
NOT_ADDRESSES = [
    "58.11 mAP@0.5 for oriented object detection",
    "+22.9% mIoU vs. PointASNL@Sem.KITTI",
    "by an average of 10 points on nDCG@10.Moreover",
    "improves AP@0.5.In addition, it",
    "ViLCo R@1@0.5 of 29.6%",
    "Recall@k and Hits@n",
    "+9.6 CiDEr@0.5IoU",
    "+3.8% mIoU vs. SPoTr@S3DIS",
    "Energy Research Institute @ NTU",
    "Advanced Technology Group @ Samsung Smart Machines",
]


class TestStripEmailAddresses(unittest.TestCase):
    def check(self, text, expected, **kwargs):
        self.assertEqual(ea.strip_email_addresses(text, **kwargs), expected, text)
        # Stable: a key stripped once must look itself up again unchanged.
        self.assertEqual(ea.strip_email_addresses(expected, **kwargs), expected, expected)

    def test_address_becomes_its_domain(self):
        self.check("Tongji University, Shanghai, China x.y@tongji.edu.cn",
                   "Tongji University, Shanghai, China tongji.edu.cn")
        self.check("Correspondence to Xiang Bai <xbai@hust.edu.cn>.",
                   "Correspondence to Xiang Bai <hust.edu.cn>.")

    def test_grouped_local_parts_share_one_domain(self):
        for text in ("{mkoren, mykel}@stanford.edu", "[mkoren, mykel] @stanford.edu",
                     "(mkoren,mykel)@stanford.edu", "{firstname}.{lastname}@stanford.edu",
                     "{mkoren | mykel} @stanford.edu", "{mkoren; mykel}@ stanford.edu"):
            self.check(text, "stanford.edu")

    def test_repeated_domain_is_written_once(self):
        self.check("(jackjia@umich.edu, alfchen@umich.edu, zmao@umich.edu)", "(umich.edu)")
        self.check("Email: a.b@utexas.edu; c.d@utexas.edu.", "Email: utexas.edu.")

    def test_different_domains_are_all_kept(self):
        self.check("{a,b}@baidu.com {c,d}@buaa.edu.cn, wangsen1312@gmail.com",
                   "baidu.com buaa.edu.cn, gmail.com")

    def test_addresses_glued_together(self):
        self.check("Stellantis, France lina.achaji@stellantis.comjulien.moreau@stellantis.com",
                   "Stellantis, France stellantis.com")

    def test_address_glued_onto_a_word_keeps_a_space(self):
        self.check("Athens, GA 30602, USA{Penghao.Deng, Jidong.Yang}@uga.edu", "Athens, GA 30602, USA uga.edu")
        self.check("清华大学zhang@tsinghua.edu.cn", "清华大学 tsinghua.edu.cn")

    def test_partial_addresses_inside_a_group(self):
        self.check("Chinese Academy of Science{yuhaibao@air.,luoyz18@mails.}tsinghua.edu.cn",
                   "Chinese Academy of Science tsinghua.edu.cn")
        self.check("Tsinghua {xyzhang,wangli_thu@mail}.tsinghua.edu.cn", "Tsinghua tsinghua.edu.cn")

    def test_space_around_the_at_sign(self):
        self.check("With University of Pittsburgh nls71 @pitt.edu", "With University of Pittsburgh pitt.edu")
        self.check("Germany edmir.xhoxhi@ ikt.uni-hannover.de", "Germany ikt.uni-hannover.de")
        self.check("Know-Center{aremonda, skrebs} @ know-center.at", "Know-Center know-center.at")

    def test_capitalised_word_before_a_spaced_at_is_kept(self):
        self.check("Chalmers University of Technology @chalmers.se Zenseact",
                   "Chalmers University of Technology chalmers.se Zenseact")

    def test_zero_width_spaces_and_math_letters(self):
        self.check("USA, {\U0001D68A\U0001D697\U0001D692,\U0001D698\U0001D69B\U0001D698}\u200b@\u200b"
                   "\U0001D69E\U0001D696\U0001D692\U0001D68C\U0001D691.\U0001D68E\U0001D68D\U0001D69E",
                   "USA, umich.edu")

    def test_truncated_address(self):
        self.check("Technical University of Munich, name.lastname@tum", "Technical University of Munich, tum")
        self.check("Curtin University, Australia jm.andrew.yu@gmailcom", "Curtin University, Australia gmailcom")

    def test_author_names_drop_the_address_entirely(self):
        self.check("Wei-Chiu Ma Raquel Urtasun {weichiu,urtasun}@uber.com", "Wei-Chiu Ma Raquel Urtasun",
                   keep_domain=False)

    def test_other_uses_of_the_at_sign_are_left_alone(self):
        for text in NOT_ADDRESSES:
            self.assertEqual(ea.strip_email_addresses(text), text)

    def test_text_without_an_at_sign_is_returned_as_is(self):
        for text in (None, "", "KTH Royal Institute of Technology", "ETH Zürich \U0001D683\U0001D683"):
            self.assertIs(ea.strip_email_addresses(text), text)


class TestFindEmailAddresses(unittest.TestCase):
    def test_finds_plain_and_grouped_addresses(self):
        found = ea.find_email_addresses("Uni x a.b@tudelft.nl y {c, d} @kit.edu z")
        self.assertEqual(len(found), 2, found)

    def test_ignores_metric_notation_and_bare_domains(self):
        for text in NOT_ADDRESSES + ["Chalmers University of Technology @chalmers.se"]:
            self.assertEqual(ea.find_email_addresses(text), [], text)

    def test_agrees_with_strip(self):
        text = "A {a, b}@fzi.de B c@x.ac.uk C nls71 @pitt.edu D"
        self.assertEqual(len(ea.find_email_addresses(text)), 3)
        self.assertEqual(ea.find_email_addresses(ea.strip_email_addresses(text)), [])


class TestScrub(unittest.TestCase):
    def test_keys_that_become_equal_are_merged(self):
        # The key that had nothing to strip wins, then the most common value,
        # then the first one.
        self.assertEqual(ea.scrub({"Uni a@x.edu": [1], "Uni x.edu": [2], "Uni b@x.edu": [1]}), {"Uni x.edu": [2]})
        self.assertEqual(ea.scrub({"Uni a@x.edu": [1], "Uni b@x.edu": [3], "Uni c@x.edu": [3]}), {"Uni x.edu": [3]})
        self.assertEqual(ea.scrub({"Uni a@x.edu": [1], "Uni b@x.edu": [3]}), {"Uni x.edu": [1]})
        self.assertEqual(list(ea.scrub({"B b@x.edu": 1, "A": 2, "B c@x.edu": 1})), ["B x.edu", "A"])

    def test_authors_field_drops_the_address_and_other_fields_keep_the_domain(self):
        papers = [{"authors": "Ann Lee Bo Chen {ann,bo}@uber.com",
                   "abstract": "We drive. Corresponding author: ann@uber.com"}]
        self.assertEqual(ea.scrub(papers), [{"authors": "Ann Lee Bo Chen",
                                             "abstract": "We drive. Corresponding author: uber.com"}])

    def test_rewrite_keeps_the_file_layout(self):
        for layout, tail in ((dict(indent=2, ensure_ascii=False), ""),
                             (dict(indent=1, ensure_ascii=False, sort_keys=True), "\n")):
            before = {"b": "Zürich j.doe@ethz.ch", "a": [1, 2]}
            after = {"b": "Zürich ethz.ch", "a": [1, 2]}
            raw = (json.dumps(before, **layout) + tail).replace("\n", "\r\n")
            expected = (json.dumps(after, **layout) + tail).replace("\n", "\r\n")
            self.assertEqual(ea.scrub_json_text(raw), expected)

    def test_nothing_to_strip_means_no_rewrite(self):
        self.assertIsNone(ea.scrub_json_text('{"abstract": "58.11 mAP@0.5"}'))
        self.assertIsNone(ea.scrub_json_text('{"name": "ETH Zurich"}'))


class TestTrackedDataHasNoEmailAddresses(unittest.TestCase):
    def test_no_tracked_data_file_contains_an_email_address(self):
        try:
            files = ea.tracked_data_files()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("needs a git checkout to list the tracked files")
        self.assertGreater(len(files), 100)
        found = {}
        for path in files:
            hits = ea.find_email_addresses(path.read_text(encoding="utf-8"))
            if hits:
                found[path.relative_to(ea.BASE).as_posix()] = hits[:3]
        self.assertEqual(found, {}, "run `python scripts/email_addresses.py` to strip them")

    def test_affiliation_cache_keys_are_stored_stripped(self):
        # fetch_affiliations_arxiv.py looks keys up by their stripped form, so
        # a key that isn't stripped could never be hit again.
        cache_file = ea.BASE / "data" / "affiliations_llm_extracted.json"
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
        self.assertEqual([k for k in cache if ea.strip_email_addresses(k) != k], [])


class TestPublishedDataHasNoEmailAddresses(unittest.TestCase):
    # Everything under data/ that build_public_site.py copies into public/.
    SHARD_DIRS = ("abstracts", "citations", "author_detail", "institution_authors",
                  "non_av_author_stats", "non_av_papers")

    def test_built_stats_and_shards_contain_no_email_address(self):
        stats = ea.BASE / "data" / "stats.json"
        if not stats.exists():
            self.skipTest("needs data/stats.json, which is gitignored and rebuilt by the pipeline")
        paths = [stats] + [p for d in self.SHARD_DIRS for p in sorted((ea.BASE / "data" / d).glob("*.json"))]
        found = {}
        for path in paths:
            hits = ea.find_email_addresses(path.read_text(encoding="utf-8"))
            if hits:
                found[path.relative_to(ea.BASE).as_posix()] = hits[:3]
        self.assertEqual(found, {})


if __name__ == "__main__":
    unittest.main()
