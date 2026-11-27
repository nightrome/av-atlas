"""Writes data/duplicate_papers.json: preprint / published pairs found with the embeddings.

Rule (chosen by reading pairs at every similarity band): the two papers' text must be at
least 0.95 similar, one must be the arXiv record and the other a published one, and they
must be at most 3 years apart. Two published papers are never paired (a conference paper and
its journal extension, or Part I and Part II, are different publications). The published
record becomes the primary. Review the file, then commit it; the build only reads it.
Needs the embedding vectors, so run it where they are.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE / "scripts"))
import duplicate_papers  # noqa: E402
from build_similar_papers import nearest  # noqa: E402

THRESHOLD = 0.95
MAX_YEAR_GAP = 3


def year(p):
    try:
        return int(p.get("year"))
    except (TypeError, ValueError):
        return None


def find_pairs(titles, idx, score, meta):
    best = {}
    for i in range(len(titles)):
        for j, s in zip(idx[i], score[i]):
            if s < THRESHOLD:
                continue
            a, b = sorted((i, int(j)))
            best[(a, b)] = max(best.get((a, b), 0.0), float(s))
    out = []
    for (a, b), s in best.items():
        pa, pb = meta.get(titles[a]), meta.get(titles[b])
        if not pa or not pb:
            continue
        arxiv_a, arxiv_b = pa.get("venue") == "arXiv", pb.get("venue") == "arXiv"
        ya, yb = year(pa), year(pb)
        if arxiv_a == arxiv_b or not ya or not yb or abs(ya - yb) > MAX_YEAR_GAP:
            continue
        primary, dup = (titles[b], titles[a]) if arxiv_a else (titles[a], titles[b])
        out.append({"primary": primary, "duplicate": dup, "similarity": round(s, 3)})
    # A preprint can match several published versions; keep its closest one.
    seen, kept = set(), []
    for pair in sorted(out, key=lambda x: -x["similarity"]):
        if pair["duplicate"] in seen or pair["primary"] in seen:
            continue
        seen.update((pair["duplicate"], pair["primary"]))
        kept.append(pair)
    return sorted(kept, key=lambda x: x["primary"])


def main():
    vec, keys = BASE / "data/embeddings/int8_512.npy", BASE / "data/embeddings/keys.json"
    if not vec.exists() or not keys.exists():
        print("No embedding vectors; cannot look for duplicates.")
        return 1
    titles = json.loads(keys.read_text(encoding="utf-8"))
    stats = json.loads((BASE / "data/stats.json").read_text(encoding="utf-8"))
    meta = {p["title"]: p for p in stats["all_papers"]}
    idx, score = nearest(np.load(vec), k=5)
    found = find_pairs(titles, idx, score, meta)
    # Keep pairs already on file: once merged, the preprint is gone from stats.json and
    # would never be found again, which would silently un-merge it.
    pairs = {p["duplicate"]: p for p in duplicate_papers.load_pairs()}
    pairs.update({p["duplicate"]: p for p in found})
    pairs = sorted(pairs.values(), key=lambda x: x["primary"])
    (BASE / "data" / "duplicate_papers.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote data/duplicate_papers.json: {len(pairs)} pairs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
