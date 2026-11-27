"""Checks on the corpus that the embeddings make possible. Writes data/embedding_quality.md
for a person to read; nothing here changes the site or the data.

1. Possible duplicates: paper pairs with near-identical text but different titles
   (a title that came in two spellings, or a preprint and its published version).
2. Possible misclassified papers: AV papers whose nearest neighbours are all
   unusually far away, i.e. they read unlike the rest of the AV corpus.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
from build_similar_papers import nearest  # noqa: E402

DUPLICATE_THRESHOLD = 0.95
SHOW = 60


def main():
    vec_path, keys_path = BASE / "data/embeddings/int8_512.npy", BASE / "data/embeddings/keys.json"
    if not vec_path.exists() or not keys_path.exists():
        print("No embedding vectors, nothing to check.")
        return 0
    titles = json.loads(keys_path.read_text(encoding="utf-8"))
    stats = json.loads((BASE / "data/stats.json").read_text(encoding="utf-8"))
    meta = {p["title"]: p for p in stats["all_papers"]}
    idx, score = nearest(np.load(vec_path), k=10)

    def line(t):
        p = meta.get(t, {})
        return f"{t} ({p.get('venue', '?')} {p.get('year', '?')}, {p.get('citations', 0)} citations)"

    dups, seen = [], set()
    for i in range(len(titles)):
        j, s = int(idx[i][0]), float(score[i][0])
        if s >= DUPLICATE_THRESHOLD and (min(i, j), max(i, j)) not in seen:
            seen.add((min(i, j), max(i, j)))
            dups.append((s, titles[i], titles[j]))
    dups.sort(reverse=True)

    mean_sim = score.mean(axis=1)
    order = np.argsort(mean_sim)
    out = ["# Embedding quality report", "",
           f"## Possible duplicates (nearest neighbour similarity >= {DUPLICATE_THRESHOLD})", "",
           f"{len(dups)} pairs. Highest first, {min(SHOW, len(dups))} shown.", ""]
    for s, a, b in dups[:SHOW]:
        out += [f"- {s:.3f}", f"  - {line(a)}", f"  - {line(b)}"]
    out += ["", "## Papers that read unlike the rest of the AV corpus", "",
            "Lowest mean similarity to their 10 nearest neighbours. Check whether they really are about road vehicles.", ""]
    for i in order[:SHOW]:
        out.append(f"- {mean_sim[i]:.3f}  {line(titles[i])}  [{meta.get(titles[i], {}).get('category', '?')}]")
    target = BASE / "data" / "embedding_quality.md"
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {target}: {len(dups)} possible duplicate pairs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
