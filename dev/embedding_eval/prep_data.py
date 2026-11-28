"""Build the compact evaluation set: data/av_pool.jsonl (all AV papers) and
data/queries.json (sampled queries + neighbour ids + seed papers).
Reads the repo's data/ read-only. Output stays under dev/embedding_eval/data (gitignored)."""
import json, random, re, sys, html
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path("C:/Users/hcaesar/Claude/av-atlas")
sys.path.insert(0, str(ROOT / "scripts"))
from aggregate import normalize_title, rekey_citation_graph  # noqa: E402

OUT = ROOT / "dev/embedding_eval/data"
OUT.mkdir(parents=True, exist_ok=True)

papers = json.loads((ROOT / "data/papers_full.json").read_text(encoding="utf-8"))
graph = rekey_citation_graph(json.loads((ROOT / "data/citation_graph.json").read_text(encoding="utf-8")))

pool, key2id = [], {}
for p in papers:
    if p.get("av_relevance") != "AV":
        continue
    title = html.unescape((p.get("title") or "").strip())
    abstract = (p.get("abstract") or "").strip()
    if abstract in ("None", "null"):
        abstract = ""
    k = normalize_title(title)
    if not k or k in key2id:
        continue
    i = len(pool)
    key2id[k] = i
    pool.append({"id": i, "title": title, "abstract": abstract, "year": p.get("year"),
                 "authors": p.get("authors") or "", "text": title + ". " + abstract})
print("AV pool", len(pool), "with abstract", sum(1 for p in pool if p["abstract"]))

nb = {i: set() for i in range(len(pool))}
for citer, cited in graph["edges"].items():
    a = key2id.get(citer)
    if a is None:
        continue
    for c in cited:
        b = key2id.get(c)
        if b is not None and b != a:
            nb[a].add(b)
            nb[b].add(a)

eligible = [i for i in nb if pool[i]["abstract"] and len(nb[i]) >= 3]
print("eligible queries", len(eligible))
rng = random.Random(42)
sample = sorted(rng.sample(eligible, min(3000, len(eligible))))

# seed papers: a set of AV papers + well-known ones
def find(sub):
    sub = sub.lower()
    return [p["id"] for p in pool if sub in p["title"].lower()]

seeds = []
hc = [p["id"] for p in pool if "holger caesar" in p["authors"].lower() and p["abstract"]]
hc.sort(key=lambda i: -len(nb[i]))
seeds += hc[:6]
for name in ["nuScenes: A multimodal dataset", "Are we ready for Autonomous Driving? The KITTI",
             "End-to-end Autonomous Driving: Challenges and Frontiers", "Planning-oriented Autonomous Driving",
             "BEVFormer", "PointPillars", "Lift, Splat, Shoot", "Waymo Open Dataset", "VAD: Vectorized",
             "CARLA: An Open Urban Driving Simulator", "Argoverse", "Trajectron++", "MotionLM",
             "Occupancy", "UniAD", "nuPlan"]:
    ids = [i for i in find(name) if pool[i]["abstract"]]
    ids.sort(key=lambda i: -len(nb[i]))
    if ids and ids[0] not in seeds:
        seeds.append(ids[0])
seeds = seeds[:20]
# top up with random popular papers if short
while len(seeds) < 20:
    c = rng.choice(eligible)
    if c not in seeds:
        seeds.append(c)
print("seeds", [(i, pool[i]["title"][:60]) for i in seeds])

with open(OUT / "av_pool.jsonl", "w", encoding="utf-8") as f:
    for p in pool:
        f.write(json.dumps({"id": p["id"], "text": p["text"]}, ensure_ascii=False) + "\n")
(OUT / "queries.json").write_text(json.dumps({
    "queries": [{"id": i, "nb": sorted(nb[i])} for i in sample],
    "seeds": seeds,
    "titles": [p["title"] for p in pool],
    "years": [p["year"] for p in pool],
}, ensure_ascii=False), encoding="utf-8")
print("avg neighbours per sampled query", sum(len(nb[i]) for i in sample) / len(sample))
