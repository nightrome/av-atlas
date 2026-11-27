"""Similar authors, institutions, venues and countries, from the paper embeddings.

Runs after aggregate.py and build_similar_papers.py. An entity's vector is the
average of the (unit-length) vectors of its papers, so two authors are similar
when their papers read alike. Only papers that have a stored vector count.

Also builds a network view for authors: people who share collaborators with an
author but never co-authored a paper with them ("connected through
collaborators"). Direct co-authors already have their own table on author.html.

Input:  data/stats.json (all_papers), data/embeddings/int8_512.npy + keys.json
Output: data/similar_authors/shard-NN.json       {name: {"s": [[name, score, joint papers, papers]],
                                                         "n": [[name, shared co-authors, score]]}}
        data/similar_institutions/shard-NN.json  {name: [[name, score, papers]]}
        data/similar_groups.json                  {"venues": {...}, "countries": {...}}

Does nothing when there are no vectors. Names with several people behind them
(identity_conflicted_authors and the ambiguous Scholar profiles) are skipped,
because an average over different people describes nobody.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy import sparse

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))

VECTORS = BASE / "data" / "embeddings" / "int8_512.npy"
KEYS = BASE / "data" / "embeddings" / "keys.json"
TOP_K = 20
MIN_AUTHOR_PAPERS = 3
MIN_INSTITUTION_PAPERS = 5
MIN_VENUE_PAPERS = 30
MIN_COUNTRY_PAPERS = 20
MAX_BYLINE_FOR_NETWORK = 30   # consortium papers would link thousands of strangers


def unit(v):
    v = v.astype(np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def entity_vectors(members, paper_vec, min_papers):
    """members: {name: [paper row indexes]}. Returns (names, unit matrix, paper counts)."""
    names, rows, counts = [], [], []
    for name, idxs in members.items():
        if len(idxs) < min_papers:
            continue
        names.append(name)
        rows.append(paper_vec[idxs].mean(axis=0))
        counts.append(len(idxs))
    if not names:
        return [], np.zeros((0, paper_vec.shape[1]), np.float32), []
    return names, unit(np.array(rows)), counts


def top_similar(mat, k=TOP_K, chunk=2048):
    """Top-k cosine neighbours (self excluded) for every row of a unit-length matrix."""
    n = len(mat)
    k = min(k, n - 1)
    if k < 1:
        return np.zeros((n, 0), np.int32), np.zeros((n, 0), np.float32)
    idx = np.zeros((n, k), np.int32)
    score = np.zeros((n, k), np.float32)
    for s in range(0, n, chunk):
        sims = mat[s:s + chunk] @ mat.T
        r = np.arange(sims.shape[0])
        sims[r, r + s] = -1.0
        part = np.argpartition(-sims, k - 1, axis=1)[:, :k]
        ps = np.take_along_axis(sims, part, axis=1)
        order = np.argsort(-ps, axis=1)
        idx[s:s + chunk] = np.take_along_axis(part, order, axis=1)
        score[s:s + chunk] = np.take_along_axis(ps, order, axis=1)
    return idx, score


def group_lists(members, paper_vec, min_papers, k=TOP_K):
    names, mat, counts = entity_vectors(members, paper_vec, min_papers)
    idx, score = top_similar(mat, k)
    return {names[i]: [[names[j], round(float(s), 3), counts[j]] for j, s in zip(idx[i], score[i])]
            for i in range(len(names))}


def coauthor_graph(papers, skip):
    """Binary co-author matrix over authors (bylines up to MAX_BYLINE_FOR_NETWORK), and joint-paper counts."""
    ids, joint = {}, defaultdict(int)
    for p in papers:
        authors = [a for a in dict.fromkeys(p.get("authors") or []) if a not in skip]
        if not 2 <= len(authors) <= MAX_BYLINE_FOR_NETWORK:
            continue
        for a in authors:
            ids.setdefault(a, len(ids))
        for i, a in enumerate(authors):
            for b in authors[i + 1:]:
                joint[(ids[a], ids[b])] += 1
                joint[(ids[b], ids[a])] += 1
    if not ids:
        return ids, sparse.csr_matrix((0, 0)), joint
    rows, cols = zip(*joint.keys())
    n = len(ids)
    adj = sparse.csr_matrix((np.ones(len(rows), np.float32), (rows, cols)), shape=(n, n))
    return ids, adj, joint


def indirect_connections(ids, adj, joint, author_vec, k=TOP_K, chunk=1000):
    """For each author: others sharing >= 2 co-authors with them but no joint paper."""
    names = [None] * len(ids)
    for name, i in ids.items():
        names[i] = name
    out = {}
    for s in range(0, adj.shape[0], chunk):
        two = (adj[s:s + chunk] @ adj).tocsr()
        for r in range(two.shape[0]):
            me = s + r
            lo, hi = two.indptr[r], two.indptr[r + 1]
            cand = [(two.indices[t], int(two.data[t])) for t in range(lo, hi)
                    if two.indices[t] != me and two.data[t] >= 2 and (me, two.indices[t]) not in joint]
            if not cand:
                continue
            cand.sort(key=lambda x: -x[1])
            rows = []
            for j, shared in cand[:k]:
                sim = None
                a, b = author_vec.get(names[me]), author_vec.get(names[j])
                if a is not None and b is not None:
                    sim = round(float(a @ b), 3)
                rows.append([names[j], shared, sim])
            out[names[me]] = rows
    return out


def main():
    if not VECTORS.exists() or not KEYS.exists():
        print("No embedding vectors, skipping entity similarity.")
        return 0
    from aggregate import write_sharded_json

    stats = json.loads((BASE / "data" / "stats.json").read_text(encoding="utf-8"))
    papers = stats["all_papers"]
    vec = unit(np.load(VECTORS))
    # Every AV paper shares a big common direction, so averages of many papers all
    # look alike (cosine above 0.97). Subtract the corpus mean first so the scores
    # spread out and mean something.
    vec = unit(vec - vec.mean(axis=0))
    row_of = {t: i for i, t in enumerate(json.loads(KEYS.read_text(encoding="utf-8")))}

    skip = set(stats.get("identity_conflicted_authors") or [])
    profiles_path = BASE / "data" / "scholar_profiles.json"
    if profiles_path.exists():
        skip |= {n for n, p in json.loads(profiles_path.read_text(encoding="utf-8")).items() if p.get("ambiguous")}

    authors, insts, venues, countries = (defaultdict(list) for _ in range(4))
    for p in papers:
        i = row_of.get(p["title"])
        if i is None:
            continue
        for a in dict.fromkeys(p.get("authors") or []):
            if a not in skip:
                authors[a].append(i)
        for x in dict.fromkeys(p.get("institutions") or []):
            insts[x].append(i)
        for c in dict.fromkeys(p.get("countries") or []):
            countries[c].append(i)
        if p.get("venue"):
            venues[p["venue"]].append(i)

    # Authors: similar by text, plus the network view.
    a_names, a_mat, a_counts = entity_vectors(authors, vec, MIN_AUTHOR_PAPERS)
    a_vec = {n: a_mat[i] for i, n in enumerate(a_names)}
    ids, adj, joint = coauthor_graph(papers, skip)
    joint_by_name = defaultdict(dict)
    for (i, j), n in joint.items():
        joint_by_name[i][j] = n
    idx, score = top_similar(a_mat)
    result = {}
    for i, name in enumerate(a_names):
        me = ids.get(name)
        rows = []
        for j, s in zip(idx[i], score[i]):
            other = a_names[j]
            joint_n = joint_by_name[me].get(ids.get(other), 0) if me is not None else 0
            rows.append([other, round(float(s), 3), joint_n, a_counts[j]])
        result[name] = {"s": rows}
    for name, rows in indirect_connections(ids, adj, joint, a_vec).items():
        result.setdefault(name, {})["n"] = rows
    write_sharded_json(BASE / "data" / "similar_authors", result, "author similarity lists")

    write_sharded_json(BASE / "data" / "similar_institutions",
                       group_lists(insts, vec, MIN_INSTITUTION_PAPERS), "institution similarity lists")
    groups = {"venues": group_lists(venues, vec, MIN_VENUE_PAPERS, 10),
              "countries": group_lists(countries, vec, MIN_COUNTRY_PAPERS, 10)}
    (BASE / "data" / "similar_groups.json").write_text(
        json.dumps(groups, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"Wrote data/similar_groups.json ({len(groups['venues'])} venues, {len(groups['countries'])} countries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
