import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import duplicate_papers as dp  # noqa: E402

PAIR = {"primary": "A Paper", "duplicate": "A paper, reworded"}


class MergeEntriesTests(unittest.TestCase):
    def entries(self):
        return [
            {"title": "A Paper", "venue": "CVPR", "year": "2020", "abstract": ""},
            {"title": "A paper, reworded", "venue": "arXiv", "year": "2019", "abstract": "abs",
             "arxiv_url": "https://arxiv.org/abs/1"},
            {"title": "Other", "venue": "ICRA", "year": "2021"},
        ]

    def test_duplicate_dropped_and_noted_on_primary(self):
        kept = dp.merge_entries(self.entries(), [PAIR])
        self.assertEqual([e["title"] for e in kept], ["A Paper", "Other"])
        note = kept[0]["also_published_as"][0]
        self.assertEqual((note["title"], note["venue"], note["year"], note["url"]),
                         ("A paper, reworded", "arXiv", "2019", "https://arxiv.org/abs/1"))
        self.assertEqual(kept[0]["abstract"], "abs")          # missing fields come from the preprint
        self.assertEqual(kept[0]["arxiv_url"], "https://arxiv.org/abs/1")

    def test_pair_with_a_missing_side_is_ignored(self):
        entries = self.entries()[:1]
        self.assertEqual(dp.merge_entries(entries, [PAIR]), entries)


class FoldGraphTests(unittest.TestCase):
    def test_citations_of_the_duplicate_count_for_the_primary(self):
        graph = {"edges": {"x": ["apaperreworded"], "y": ["apaper", "apaperreworded"], "apaperreworded": ["z"]}}
        folded = dp.fold_graph(graph, dp.alias_map([PAIR]))
        self.assertEqual(folded["edges"]["x"], ["apaper"])
        self.assertEqual(folded["edges"]["y"], ["apaper"])        # one edge, not two
        self.assertEqual(folded["edges"]["apaper"], ["z"])        # its own references move too

    def test_no_pairs_returns_graph_untouched(self):
        graph = {"edges": {"x": ["y"]}}
        self.assertIs(dp.fold_graph(graph, {}), graph)


if __name__ == "__main__":
    unittest.main()
