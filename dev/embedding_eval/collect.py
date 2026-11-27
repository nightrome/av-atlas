"""Collect metrics of all finished models into results.json, RESULTS_table.md and seeds_all.json (run on night)."""
import json, os, glob
BASE = "/cluster/shared/embedding_eval"
order = ["qwen3-8b", "nv-embed-v2", "e5-mistral", "bge-base", "specter2", "minilm"]
rows, seeds = [], {}
for n in order:
    f = f"{BASE}/out/{n}/metrics.json"
    if not os.path.exists(f):
        rows.append((n, None)); continue
    m = json.load(open(f)); rows.append((n, m))
    seeds[n] = json.load(open(f"{BASE}/out/{n}/seeds.json"))
lines = ["| model | dims | recall@10 | recall@50 | nDCG@10 | embed GPU minutes (27.6k papers) | GPU |", "|---|---|---|---|---|---|---|"]
for n, m in rows:
    if m is None:
        lines.append(f"| {n} | - | not run / failed | | | | |")
    else:
        lines.append(f"| {m['model']} | {m['dims']} | {m['recall@10']:.4f} | {m['recall@50']:.4f} | {m['ndcg@10']:.4f} | {m['embed_seconds']/60:.1f} | {m['gpu']} |")
var = []
for n, m in rows:
    f = f"{BASE}/out/{n}/variants.json"
    if m and os.path.exists(f):
        v = json.load(open(f))
        for d, r in v.items():
            var.append(f"| {n} | {d} | {r['fp']['recall@10']:.4f} | {r['fp']['ndcg@10']:.4f} | {r['int8']['recall@10']:.4f} | {r['int8']['ndcg@10']:.4f} | {r['int8_bytes']/1e6:.1f} MB |")
lines += ["", "| model | dims | recall@10 (float) | nDCG@10 (float) | recall@10 (int8) | nDCG@10 (int8) | int8 size |", "|---|---|---|---|---|---|---|"] + var
open(f"{BASE}/RESULTS_table.md", "w").write("\n".join(lines) + "\n")
json.dump(seeds, open(f"{BASE}/seeds_all.json", "w"), ensure_ascii=False, indent=1)
print("\n".join(lines))
