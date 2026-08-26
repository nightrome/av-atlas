#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Regression tests for institution_extraction_llm.py's pure logic (candidate
shortlisting, response parsing, registry dedup) -- the parts with no network
dependency. extract_institutions() itself (the actual Ollama call) is tested
separately with call_ollama monkeypatched, never a real network call.

Usage: python -m unittest discover -s av-atlas/scripts/tests
   or: python av-atlas/scripts/tests/test_institution_extraction_llm.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import institution_extraction_llm as iel  # noqa: E402


class TestSignificantWords(unittest.TestCase):
    def test_excludes_generic_institutional_words(self):
        words = iel.significant_words("Karlsruhe Institute of Technology")
        self.assertIn("karlsruhe", words)
        self.assertNotIn("institute", words)
        self.assertNotIn("technology", words)
        self.assertNotIn("of", words)

    def test_excludes_short_words(self):
        self.assertNotIn("at", iel.significant_words("KTH at Stockholm"))

    def test_empty_text_returns_empty_set(self):
        self.assertEqual(iel.significant_words(""), set())
        self.assertEqual(iel.significant_words(None), set())


class TestBuildCandidateShortlist(unittest.TestCase):
    def test_ranks_by_word_overlap_descending(self):
        registry = [
            "KTH Royal Institute of Technology",
            "Royal Institute of Technology Stockholm",  # shares fewer distinctive words
            "Stanford University",
        ]
        shortlist = iel.build_candidate_shortlist(
            "Authors are with KTH Royal Institute of Technology, Stockholm, Sweden", registry)
        self.assertEqual(shortlist[0], "KTH Royal Institute of Technology")
        self.assertNotIn("Stanford University", shortlist)

    def test_bare_acronym_with_no_shared_words_returns_empty(self):
        # Token overlap alone can't bridge "MIT" to its spelled-out name
        # ("MIT" and "Massachusetts" share no substring) -- that's the LLM's
        # job (given this as context), not this function's. Note "KTH" is a
        # weaker example here since "KTH" IS a literal token inside "KTH
        # Royal Institute of Technology", so that one already shortlists
        # correctly on token overlap alone.
        registry = ["Massachusetts Institute of Technology"]
        self.assertEqual(iel.build_candidate_shortlist("MIT", registry), [])

    def test_respects_max_candidates_cap(self):
        registry = [f"Sample University {i}" for i in range(100)]
        shortlist = iel.build_candidate_shortlist("Sample University 1", registry, max_candidates=10)
        self.assertLessEqual(len(shortlist), 10)

    def test_no_significant_words_in_text_returns_empty(self):
        self.assertEqual(iel.build_candidate_shortlist("the of and", ["Some University"]), [])


class TestParseExtractionResponse(unittest.TestCase):
    def test_parses_well_formed_response(self):
        result = iel.parse_extraction_response(
            '{"institutions": [{"name": "KTH Royal Institute of Technology", "matched_existing": true}]}')
        self.assertEqual(result, [{"name": "KTH Royal Institute of Technology", "matched_existing": True}])

    def test_empty_institutions_list_is_valid_not_a_failure(self):
        # Distinct from a parse failure (None) -- an empty list is a real,
        # successful "this text names no institution" answer.
        self.assertEqual(iel.parse_extraction_response('{"institutions": []}'), [])

    def test_malformed_json_returns_none(self):
        self.assertIsNone(iel.parse_extraction_response("not json at all"))

    def test_missing_institutions_key_returns_none(self):
        self.assertIsNone(iel.parse_extraction_response('{"foo": "bar"}'))

    def test_drops_entries_with_no_name(self):
        result = iel.parse_extraction_response(
            '{"institutions": [{"name": "", "matched_existing": true}, '
            '{"name": "MIT", "matched_existing": false}]}')
        self.assertEqual(result, [{"name": "MIT", "matched_existing": False}])

    def test_skips_non_dict_entries_instead_of_crashing(self):
        result = iel.parse_extraction_response('{"institutions": ["MIT", 5, null]}')
        self.assertEqual(result, [])


class TestResolveAndRegister(unittest.TestCase):
    def test_matched_existing_true_is_used_as_is(self):
        registry = {"KTH Royal Institute of Technology"}
        extracted = [{"name": "KTH Royal Institute of Technology", "matched_existing": True}]
        resolved = iel.resolve_and_register(extracted, registry)
        self.assertEqual(resolved, ["KTH Royal Institute of Technology"])
        self.assertEqual(registry, {"KTH Royal Institute of Technology"})

    def test_new_institution_is_added_to_the_registry(self):
        registry = {"KTH Royal Institute of Technology"}
        extracted = [{"name": "Brand New University", "matched_existing": False}]
        resolved = iel.resolve_and_register(extracted, registry)
        self.assertEqual(resolved, ["Brand New University"])
        self.assertIn("Brand New University", registry)

    def test_hallucinated_matched_existing_still_gets_registered(self):
        # The model claimed matched_existing=True but the name isn't
        # actually in the registry (a subtly reworded "match") -- must not
        # be silently dropped or trusted as deduped; it gets added like any
        # other new name so it's at least consistent going forward.
        registry = {"KTH Royal Institute of Technology"}
        extracted = [{"name": "KTH Royal Inst. of Technology", "matched_existing": True}]
        resolved = iel.resolve_and_register(extracted, registry)
        self.assertEqual(resolved, ["KTH Royal Inst. of Technology"])
        self.assertIn("KTH Royal Inst. of Technology", registry)

    def test_second_call_in_same_run_can_match_a_just_added_institution(self):
        # The registry mutation is the point -- two authors on the same
        # paper (or two papers in the same batch) naming the same brand-new
        # institution must not each independently invent it.
        registry = set()
        first = iel.resolve_and_register(
            [{"name": "Brand New Lab", "matched_existing": False}], registry)
        self.assertIn("Brand New Lab", registry)
        second = iel.resolve_and_register(
            [{"name": "Brand New Lab", "matched_existing": True}], registry)
        self.assertEqual(first, second)

    def test_empty_extraction_resolves_to_empty(self):
        registry = {"KTH Royal Institute of Technology"}
        self.assertEqual(iel.resolve_and_register([], registry), [])


class TestExtractInstitutions(unittest.TestCase):
    def test_uses_call_ollama_and_parses_its_response(self):
        calls = []

        def fake_call_ollama(model, prompt, timeout=120):
            calls.append((model, prompt))
            return '{"institutions": [{"name": "MIT", "matched_existing": false}]}'

        original = iel.call_ollama
        iel.call_ollama = fake_call_ollama
        try:
            result = iel.extract_institutions("Authors are with MIT.", ["Stanford University"])
        finally:
            iel.call_ollama = original
        self.assertEqual(result, [{"name": "MIT", "matched_existing": False}])
        self.assertEqual(len(calls), 1)

    def test_raises_on_unparseable_response_rather_than_guessing(self):
        original = iel.call_ollama
        iel.call_ollama = lambda model, prompt, timeout=120: "not json"
        try:
            with self.assertRaises(ValueError):
                iel.extract_institutions("Some text", [])
        finally:
            iel.call_ollama = original


if __name__ == "__main__":
    unittest.main()
