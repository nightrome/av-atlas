import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fetch_orcids as fo  # noqa: E402


class DominantIdTests(unittest.TestCase):
    def test_profile_with_most_of_the_papers_wins(self):
        self.assertEqual(fo.dominant_id({"1": 3, "9": 17}), "9")

    def test_no_clear_owner_means_no_profile(self):
        # a common name spread over many people: no single profile holds half the papers
        self.assertIsNone(fo.dominant_id({"1": 3, "2": 3, "3": 4, "4": 3, "5": 3}))

    def test_exactly_half_still_counts(self):
        self.assertEqual(fo.dominant_id({"1": 5, "2": 5}), "1")


if __name__ == "__main__":
    unittest.main()
