"""Precompute "similar papers" lists from stored embedding vectors.

The vectors come from an occasional GPU run (see dev/embedding_eval/); this
script only does the cheap part and runs fine on a CPU. For every AV paper it
finds the 20 nearest papers by cosine similarity and writes them to
data/similar/shard-NN.json, sharded by title like abstracts/ (paper.html
fetches one shard per page view).

Input:  data/embeddings/int8_512.npy + int8_512.json  (int8 vectors, one row per AV paper
        in corpus order, plus the quantisation scale)
Output: data/similar/shard-NN.json  {title: [[similar title, score], ...]}

data/embeddings/keys.json lists the paper title of every vector row, so the
corpus can grow without the vectors drifting out of step: papers without a
vector simply have no list until the next embedding run, and neighbour lists
only contain papers that have one. `--write-keys` creates keys.json for a fresh
vectors file (rows in "AV papers in papers_full.json order, one per normalised
title" order, the rule dev/embedding_eval/prep_data.py used) and refuses if the
row count does not match. Without a vectors file the script does nothing, so
the build still works on machines that have none.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

VECTORS = BASE / "data" / "embeddings" / "int8_512.npy"
KEYS = BASE / "data" / "embeddings" / "keys.json"
OUT_DIR = BASE / "data" / "similar"
TOP_K = 20


def av_pool_titles(papers, normalize_title):
    """Titles of AV papers, one per normalised title, in corpus order."""
    seen, titles = set(), []
    for p in papers:
        if p.get("av_relevance") != "AV":
            continue
        key = normalize_title((p.get("title") or "").strip())
        if not key or key in seen:
            continue
        seen.add(key)
        titles.append(p["title"])
    return titles


def nearest(vectors, k=TOP_K, chunk=2048):
    """Top-k neighbours (self excluded) by cosine similarity. Returns (idx, score)."""
    v = vectors.astype(np.float32)
    v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    n = len(v)
    idx = np.zeros((n, k), dtype=np.int32)
    score = np.zeros((n, k), dtype=np.float32)
    for start in range(0, n, chunk):
        sims = v[start:start + chunk] @ v.T
        rows = np.arange(sims.shape[0])
        sims[rows, rows + start] = -1.0
        part = np.argpartition(-sims, k, axis=1)[:, :k]
        part_scores = np.take_along_axis(sims, part, axis=1)
        order = np.argsort(-part_scores, axis=1)
        idx[start:start + chunk] = np.take_along_axis(part, order, axis=1)
        score[start:start + chunk] = np.take_along_axis(part_scores, order, axis=1)
    return idx, score


def main():
    write_keys = "--write-keys" in sys.argv
    if not VECTORS.exists():
        print(f"No embedding vectors at {VECTORS}, skipping similar papers.")
        return 0
    from aggregate import normalize_title, write_sharded_json

    vectors = np.load(VECTORS)
    if write_keys:
        papers = json.loads((BASE / "data" / "papers_full.json").read_text(encoding="utf-8"))
        titles = av_pool_titles(papers, normalize_title)
        if len(titles) != len(vectors):
            print(f"Embedding rows ({len(vectors)}) do not match AV papers ({len(titles)}); "
                  "not writing keys.json.")
            return 1
        KEYS.write_text(json.dumps(titles, ensure_ascii=False), encoding="utf-8")
        print(f"Wrote {KEYS} ({len(titles)} titles).")
    if not KEYS.exists():
        print(f"No {KEYS}; run once with --write-keys. Skipping similar papers.")
        return 0
    titles = json.loads(KEYS.read_text(encoding="utf-8"))
    if len(titles) != len(vectors):
        print(f"keys.json ({len(titles)}) and the vectors ({len(vectors)}) disagree; "
              "not writing similar papers.")
        return 1
    idx, score = nearest(vectors)
    mapping = {
        titles[i]: [[titles[j], round(float(s), 3)] for j, s in zip(idx[i], score[i])]
        for i in range(len(titles))
    }
    write_sharded_json(OUT_DIR, mapping, "similar-paper lists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
