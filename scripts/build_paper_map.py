"""2D map of all embedded papers for map.html.

Layout: t-SNE on the stored embedding vectors. t-SNE takes a few minutes, and a new
layout moves every dot, so the coordinates are cached in data/embeddings/map_xy.npy
and only recomputed with --recompute (do this after refreshing the embeddings). A
normal build just reads the cache and attaches the current paper metadata.

Input:  data/embeddings/int8_512.npy, keys.json, data/stats.json
Output: data/map/points.json  columns, not objects, to keep the file small:
        {"titles": [...], "x": [0..10000], "y": [0..10000], "year": [...],
         "cat": [index into "cats"], "cites": [...], "cats": [category slugs]}
        Papers that have no vector yet are simply not on the map.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
EMB = BASE / "data" / "embeddings"
XY = EMB / "map_xy.npy"


def layout(vectors):
    from sklearn.decomposition import PCA
    from sklearn.manifold import TSNE

    v = vectors.astype(np.float32)
    v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)
    v -= v.mean(axis=0)
    reduced = PCA(n_components=50, random_state=0).fit_transform(v)
    xy = TSNE(n_components=2, perplexity=40, init="pca", learning_rate="auto",
              n_jobs=-1, random_state=0).fit_transform(reduced)
    return xy.astype(np.float32)


def main():
    vectors_path, keys_path = EMB / "int8_512.npy", EMB / "keys.json"
    if not vectors_path.exists() or not keys_path.exists():
        print("No embedding vectors, skipping the paper map.")
        return 0
    keys = json.loads(keys_path.read_text(encoding="utf-8"))
    if "--recompute" in sys.argv or not XY.exists():
        print("Computing t-SNE layout (a few minutes)...")
        np.save(XY, layout(np.load(vectors_path)))
    xy = np.load(XY)
    if len(xy) != len(keys):
        print(f"map_xy.npy ({len(xy)}) does not match keys.json ({len(keys)}); run with --recompute.")
        return 1

    stats = json.loads((BASE / "data" / "stats.json").read_text(encoding="utf-8"))
    meta = {p["title"]: p for p in stats["all_papers"]}
    span = np.maximum(xy.max(axis=0) - xy.min(axis=0), 1e-9)
    scaled = ((xy - xy.min(axis=0)) / span * 10000).round().astype(int)
    cats, cat_index = [], {}
    out = {"titles": [], "x": [], "y": [], "year": [], "cat": [], "cites": []}
    for i, title in enumerate(keys):
        p = meta.get(title)
        if p is None:
            continue
        c = p.get("category") or "uncategorized"
        if c not in cat_index:
            cat_index[c] = len(cats)
            cats.append(c)
        out["titles"].append(title)
        out["x"].append(int(scaled[i, 0]))
        out["y"].append(int(scaled[i, 1]))
        out["year"].append(int(p["year"]) if str(p.get("year") or "").isdigit() else 0)
        out["cat"].append(cat_index[c])
        out["cites"].append(int(p.get("citations") or 0))
    out["cats"] = cats
    target = BASE / "data" / "map"
    target.mkdir(parents=True, exist_ok=True)
    (target / "points.json").write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                                        encoding="utf-8", newline="\n")
    print(f"Wrote {target / 'points.json'} ({len(out['titles'])} papers, {len(cats)} categories)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
