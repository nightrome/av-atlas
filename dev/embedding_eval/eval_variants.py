"""CPU post-hoc evaluation: Matryoshka truncation + int8 quantisation of saved embeddings.
usage: eval_variants.py <name> [dims,comma,separated]
Writes out/<name>/variants.json and, for the dims given, out/<name>/int8_<d>.npy (+ scale)."""
import json, sys, os
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_lib import evaluate  # noqa: E402

BASE = "/cluster/shared/embedding_eval"
name = sys.argv[1]
dims = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else []
out = f"{BASE}/out/{name}"
qdata = json.load(open(f"{BASE}/data/queries.json", encoding="utf-8"))
emb = np.load(f"{out}/emb.npy").astype(np.float32)
full = emb.shape[1]
res = {}


def norm(x):
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def int8(x):
    scale = 127.0 / np.abs(x).max()
    q = np.clip(np.round(x * scale), -127, 127).astype(np.int8)
    return q, scale


for d in [full] + [d for d in dims if d < full]:
    x = norm(emb[:, :d])
    r = evaluate(x, qdata)
    q, scale = int8(x)
    r8 = evaluate(q.astype(np.float32), qdata)
    res[str(d)] = {"fp": {k: r[k] for k in ("recall@10", "recall@50", "ndcg@10")},
                   "int8": {k: r8[k] for k in ("recall@10", "recall@50", "ndcg@10")},
                   "int8_bytes": int(q.nbytes), "fp16_bytes": int(q.size * 2)}
    if d in dims:
        np.save(f"{out}/int8_{d}.npy", q)
        json.dump({"scale": float(scale), "dim": d, "n": int(q.shape[0])}, open(f"{out}/int8_{d}.json", "w"))
    print(name, d, res[str(d)], flush=True)
json.dump(res, open(f"{out}/variants.json", "w"), indent=1)
