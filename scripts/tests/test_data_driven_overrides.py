"""The per-name fixes (spelling fixes, identity splits, affiliation overrides,
OpenAlex id merges, joined-name splits) are read from data/*.json and applied
the same way to every name. These tests use synthetic names only."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aggregate as ag  # noqa: E402

BASE = Path(__file__).resolve().parent.parent.parent


class PatchGlobals:
    """Set module globals for one test and restore them afterwards."""
    def __init__(self, **values):
        self.values = values
        self.saved = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.saved[key] = getattr(ag, key)
            setattr(ag, key, value)

    def __exit__(self, *exc):
        for key, value in self.saved.items():
            setattr(ag, key, value)


class LoaderTests(unittest.TestCase):
    def test_reads_a_json_file_from_the_data_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "data").mkdir()
            (base / "data" / "x.json").write_text(json.dumps({"Jonny Doe": "Jane Doe"}), encoding="utf-8")
            self.assertEqual(ag.load_data_json("x.json", {}, base=base), {"Jonny Doe": "Jane Doe"})

    def test_missing_file_gives_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ag.load_data_json("nope.json", {"a": 1}, base=Path(tmp)), {"a": 1})

    def test_committed_data_files_have_the_expected_shape(self):
        for name in ("author_name_fixes.json", "openalex_id_merges.json", "author_name_splits.json"):
            data = json.loads((BASE / "data" / name).read_text(encoding="utf-8"))
            self.assertTrue(all(isinstance(k, str) and isinstance(v, (str, list)) for k, v in data.items()), name)
        over = json.loads((BASE / "data" / "author_affiliation_overrides.json").read_text(encoding="utf-8"))
        self.assertEqual(set(over), {"allowed_institution_substrings", "undated_institutions"})
        ids = json.loads((BASE / "data" / "author_identities.json").read_text(encoding="utf-8"))
        self.assertTrue(all(isinstance(v, dict) for v in ids.values()))

    def test_no_name_is_compared_with_a_literal_in_the_code(self):
        import re
        source = (BASE / "scripts" / "aggregate.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\bname\s*==\s*['\"]", source))


class NameFixTests(unittest.TestCase):
    def test_a_fix_from_the_data_is_applied(self):
        with PatchGlobals(KNOWN_NAME_FIXES={"Jonny Doe": "Jane Doe"}):
            self.assertEqual(ag.clean_author_name("Jonny Doe"), "Jane Doe")
            self.assertEqual(ag.clean_author_name("Someone Else"), "Someone Else")

    def test_a_joined_pair_from_the_data_is_split(self):
        with PatchGlobals(KNOWN_AUTHOR_SPLITS={"Ann Lee Bob Ray Cy Fox": ["Ann Lee", "Bob Ray Cy Fox"]}):
            self.assertEqual(ag.split_joined_authors("Ann Lee Bob Ray Cy Fox"), ["Ann Lee", "Bob Ray Cy Fox"])


class IdentityTests(unittest.TestCase):
    IDENTITIES = {"Pat Quill": {"Paper A": "main", "Paper B": "Pat Quill (Example University)"}}

    def test_every_name_in_the_data_is_split_the_same_way(self):
        with PatchGlobals(AUTHOR_IDENTITIES=self.IDENTITIES):
            self.assertEqual(ag.resolve_ambiguous_author_identity("Pat Quill", "Paper A"), "Pat Quill")
            self.assertEqual(ag.resolve_ambiguous_author_identity("Pat Quill", "Paper B"),
                             "Pat Quill (Example University)")
            self.assertEqual(ag.resolve_ambiguous_author_identity("Pat Quill", "Paper C"),
                             "Pat Quill (unidentified)")
            self.assertEqual(ag.resolve_ambiguous_author_identity("Other Name", "Paper C"), "Other Name")


class OpenAlexIdAndAffiliationTests(unittest.TestCase):
    def test_a_duplicate_openalex_id_is_merged(self):
        with PatchGlobals(KNOWN_DUPLICATE_OPENALEX_IDS={"A1": "A2"}):
            self.assertEqual(ag.canonical_openalex_id("A1"), "A2")
            self.assertEqual(ag.canonical_openalex_id("A3"), "A3")

    def test_an_affiliation_override_is_applied(self):
        with PatchGlobals(AUTHOR_AFFILIATION_ALLOWLIST={"Pat Quill": ("example univ",)}):
            affs = ["Example University", "Elsewhere Institute"]
            self.assertEqual(ag.filter_author_affiliations("Pat Quill", affs), ["Example University"])
            self.assertEqual(ag.filter_author_affiliations("Other Name", affs), affs)

    def test_override_file_is_turned_into_lookup_tables(self):
        self.assertIsInstance(ag.AUTHOR_AFFILIATION_ALLOWLIST, dict)
        for subs in ag.AUTHOR_AFFILIATION_ALLOWLIST.values():
            self.assertIsInstance(subs, tuple)
        for insts in ag.AUTHOR_UNDATED_AFFILIATIONS.values():
            self.assertIsInstance(insts, tuple)


if __name__ == "__main__":
    unittest.main()
