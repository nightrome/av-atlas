"""Retrieval metrics shared by the GPU job and the CPU post-hoc evaluation."""
import json
import numpy as np


def load_queries(path):
    d = json.load(open(path, encoding="utf-8"))
    return d


def evaluate(emb, qdata, device="cpu", ks=(10, 50), bs=256):
    """emb: (N, D) float array, rows normalised or not (normalised here).
    Returns mean recall@k and nDCG@10 over the sampled queries, cosine similarity,
    self excluded, candidates = all N AV papers."""
    import torch
    E = torch.tensor(np.asarray(emb), dtype=torch.float32, device=device)
    E = torch.nn.functional.normalize(E, dim=1)
    qs = qdata["queries"]
    rec = {k: [] for k in ks}
    ndcg = []
    disc = 1.0 / np.log2(np.arange(2, 12))
    for s in range(0, len(qs), bs):
        chunk = qs[s:s + bs]
        ids = torch.tensor([q["id"] for q in chunk], device=device)
        sim = E[ids] @ E.T
        sim[torch.arange(len(chunk), device=device), ids] = -1e9
        top = sim.topk(max(ks), dim=1).indices.cpu().numpy()
        for row, q in zip(top, chunk):
            rel = set(q["nb"])
            for k in ks:
                rec[k].append(len(rel.intersection(row[:k].tolist())) / len(rel))
            gains = np.array([1.0 if t in rel else 0.0 for t in row[:10]])
            dcg = float((gains * disc).sum())
            idcg = float(disc[:min(len(rel), 10)].sum())
            ndcg.append(dcg / idcg)
    out = {f"recall@{k}": float(np.mean(v)) for k, v in rec.items()}
    out["ndcg@10"] = float(np.mean(ndcg))
    out["n_queries"] = len(qs)
    out["n_pool"] = int(E.shape[0])
    return out


def seed_neighbours(emb, qdata, device="cpu", k=10):
    import torch
    E = torch.tensor(np.asarray(emb), dtype=torch.float32, device=device)
    E = torch.nn.functional.normalize(E, dim=1)
    res = {}
    titles, years = qdata["titles"], qdata["years"]
    for sid in qdata["seeds"]:
        sim = E @ E[sid]
        sim[sid] = -1e9
        v, i = sim.topk(k)
        res[str(sid)] = {
            "title": titles[sid], "year": years[sid],
            "neighbours": [{"title": titles[j], "year": years[j], "cos": round(float(c), 4)}
                           for c, j in zip(v.tolist(), i.tolist())],
        }
    return res
