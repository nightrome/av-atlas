import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import numpy as np
    import build_similar_papers as bsp  # noqa: E402
except ImportError:   # the CI test job has no third-party packages; these tests then skip
    np = None
    bsp = None

NEEDS_NUMPY = unittest.skipIf(np is None, "needs numpy and scipy")


@NEEDS_NUMPY
class NearestTests(unittest.TestCase):
    def test_neighbours_ranked_by_cosine_and_exclude_self(self):
        v = np.array([[10, 0], [9, 1], [0, 10], [1, 9]], dtype=np.int8)
        idx, score = bsp.nearest(v, k=2)
        self.assertEqual(idx[0].tolist(), [1, 3])
        self.assertEqual(idx[2].tolist(), [3, 1])
        for i in range(4):
            self.assertNotIn(i, idx[i].tolist())
        self.assertTrue((score[:, 0] >= score[:, 1]).all())

    def test_scale_of_vectors_does_not_matter(self):
        v = np.array([[1, 0], [2, 0], [0, 3]], dtype=np.int8)
        idx, _ = bsp.nearest(v, k=1)
        self.assertEqual(idx[0, 0], 1)
        self.assertEqual(idx[1, 0], 0)


@NEEDS_NUMPY
class PoolTests(unittest.TestCase):
    def test_only_av_papers_one_per_normalised_title(self):
        norm = lambda t: "".join(c for c in t.lower() if c.isalnum())
        papers = [
            {"title": "A Paper", "av_relevance": "AV"},
            {"title": "a  paper!", "av_relevance": "AV"},
            {"title": "Other", "av_relevance": "not_av"},
            {"title": "Third", "av_relevance": "AV"},
        ]
        self.assertEqual(bsp.av_pool_titles(papers, norm), ["A Paper", "Third"])


if __name__ == "__main__":
    unittest.main()
