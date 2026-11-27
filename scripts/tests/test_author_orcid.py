import sys
import unittest
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import aggregate  # noqa: E402

A, B, C = "0000-0001-5099-6297", "0000-0002-0000-0001", "0000-0003-0000-0001"


class AuthorOrcidsTests(unittest.TestCase):
    def test_single_orcid(self):
        self.assertEqual(aggregate.author_orcids(Counter({A: 5}), None), (A, [{"orcid": A, "papers": 5}]))

    def test_several_orcids_are_kept_and_the_most_used_is_main(self):
        main, every = aggregate.author_orcids(Counter({A: 2, B: 9, C: 1}), None)
        self.assertEqual(main, B)
        self.assertEqual([e["orcid"] for e in every], [B, A, C])

    def test_tie_goes_to_the_semantic_scholar_orcid(self):
        main, _ = aggregate.author_orcids(Counter({A: 3, B: 3}), B)
        self.assertEqual(main, B)

    def test_semantic_scholar_orcid_is_added_when_openalex_never_saw_it(self):
        main, every = aggregate.author_orcids(Counter({A: 4}), C)
        self.assertEqual(main, A)
        self.assertEqual(every[-1], {"orcid": C, "papers": 0})

    def test_semantic_scholar_alone(self):
        self.assertEqual(aggregate.author_orcids(None, C), (C, [{"orcid": C, "papers": 0}]))

    def test_nothing_known(self):
        self.assertEqual(aggregate.author_orcids(None, None), (None, []))

    def test_orcid_format(self):
        self.assertTrue(aggregate.ORCID_RE.fullmatch("0000-0002-1825-009X"))
        self.assertFalse(aggregate.ORCID_RE.fullmatch("https://orcid.org/0000-0001-5099-6297"))


if __name__ == "__main__":
    unittest.main()
