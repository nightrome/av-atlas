import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_entity_similarity as bes  # noqa: E402


class EntityVectorTests(unittest.TestCase):
    def test_entities_below_the_paper_minimum_are_dropped(self):
        vec = bes.unit(np.array([[1, 0], [0, 1], [1, 1]], dtype=np.float32))
        names, mat, counts = bes.entity_vectors({"a": [0, 1, 2], "b": [0]}, vec, 2)
        self.assertEqual(names, ["a"])
        self.assertEqual(counts, [3])
        self.assertAlmostEqual(float(np.linalg.norm(mat[0])), 1.0, places=5)

    def test_group_lists_rank_by_cosine_and_skip_self(self):
        vec = bes.unit(np.array([[1, 0], [1, 0.1], [0, 1], [0.1, 1]], dtype=np.float32))
        lists = bes.group_lists({"x": [0], "y": [1], "z": [2], "w": [3]}, vec, 1, k=2)
        self.assertEqual(lists["x"][0][0], "y")
        self.assertEqual(lists["z"][0][0], "w")
        self.assertNotIn("x", [n for n, _, _ in lists["x"]])


class NetworkTests(unittest.TestCase):
    def papers(self):
        # a-b-c in a chain plus d-b: a and c (and d) share co-author b but never co-authored.
        return [{"authors": ["a", "b"]}, {"authors": ["b", "c"]}, {"authors": ["b", "d"]},
                {"authors": ["a", "b"]}, {"authors": ["c", "d"]}]

    def test_indirect_connections_need_two_shared_coauthors_and_no_joint_paper(self):
        papers = self.papers() + [{"authors": ["a", "e"]}, {"authors": ["c", "e"]}]
        ids, adj, joint = bes.coauthor_graph(papers, skip=set())
        out = bes.indirect_connections(ids, adj, joint, {})
        # a and c share b and e, never wrote together
        self.assertIn(["c", 2, None], out["a"])
        # a and b wrote together, so b is not an "indirect" connection of a
        self.assertNotIn("b", [r[0] for r in out["a"]])

    def test_skipped_names_and_huge_bylines_are_ignored(self):
        papers = [{"authors": ["a", "b"]}, {"authors": [f"x{i}" for i in range(40)]}]
        ids, _, _ = bes.coauthor_graph(papers, skip={"b"})
        self.assertNotIn("b", ids)
        self.assertNotIn("x0", ids)


if __name__ == "__main__":
    unittest.main()
