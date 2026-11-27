"""Embed all AV papers with one model, then evaluate. Usage: embed_eval.py <name>
Writes out/<name>/emb.npy (fp16), metrics.json, seeds.json under /cluster/shared/embedding_eval."""
import json, os, sys, time
import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_lib import evaluate, seed_neighbours  # noqa: E402

BASE = "/cluster/shared/embedding_eval"
name = sys.argv[1]
MAXLEN = 512
BS = int(os.environ.get("BS", "64"))

MODELS = {
    "minilm": ("sentence-transformers/all-MiniLM-L6-v2", "st"),
    "bge-base": ("BAAI/bge-base-en-v1.5", "st"),
    "specter2": ("allenai/specter2_base", "specter2"),
    "qwen3-8b": ("Qwen/Qwen3-Embedding-8B", "qwen3"),
    "nv-embed-v2": ("nvidia/NV-Embed-v2", "nv"),
    "e5-mistral": ("intfloat/e5-mistral-7b-instruct", "st7b"),
}
mid, kind = MODELS[name]
out = f"{BASE}/out/{name}"
os.makedirs(out, exist_ok=True)

rows = [json.loads(l) for l in open(f"{BASE}/data/av_pool.jsonl", encoding="utf-8")]
assert [r["id"] for r in rows] == list(range(len(rows)))
texts = [r["text"] for r in rows]
qdata = json.load(open(f"{BASE}/data/queries.json", encoding="utf-8"))
dev = "cuda"
print("model", mid, "kind", kind, "texts", len(texts), flush=True)

order = np.argsort([-len(t) for t in texts])  # longest first: early OOM, less padding
t0 = time.time()


def finish(emb_sorted):
    emb = np.zeros_like(emb_sorted)
    emb[order] = emb_sorted
    return emb


if kind in ("st", "st7b"):
    from sentence_transformers import SentenceTransformer
    kw = {"model_kwargs": {"torch_dtype": torch.bfloat16}} if kind == "st7b" else {}
    m = SentenceTransformer(mid, device=dev, **kw)
    m.max_seq_length = MAXLEN
    if kind == "st":
        m.half()
    dims = m.get_sentence_embedding_dimension()
    emb_s = m.encode([texts[i] for i in order], batch_size=BS, normalize_embeddings=True,
                     show_progress_bar=False, convert_to_numpy=True)
    emb = finish(emb_s.astype(np.float16))

elif kind == "specter2":
    from transformers import AutoTokenizer
    from adapters import AutoAdapterModel
    tok = AutoTokenizer.from_pretrained("allenai/specter2_base")
    model = AutoAdapterModel.from_pretrained("allenai/specter2_base")
    model.load_adapter("allenai/specter2", source="hf", load_as="proximity", set_active=True)
    model.to(dev).half().eval()
    # documented SPECTER2 input: title [SEP] abstract (split the stored "title. abstract" text back apart)
    titles = qdata["titles"]
    sp = [titles[i] + tok.sep_token + texts[i][len(titles[i]) + 2:] for i in range(len(texts))]
    chunks = []
    with torch.no_grad():
        for s in range(0, len(order), BS):
            b = [sp[i] for i in order[s:s + BS]]
            enc = tok(b, padding=True, truncation=True, max_length=MAXLEN, return_tensors="pt",
                      return_token_type_ids=False).to(dev)
            o = model(**enc).last_hidden_state[:, 0]
            chunks.append(torch.nn.functional.normalize(o.float(), dim=1).cpu().numpy())
    emb = finish(np.concatenate(chunks).astype(np.float16))

elif kind == "qwen3":
    from transformers import AutoTokenizer, AutoModel
    tok = AutoTokenizer.from_pretrained(mid, padding_side="left")
    model = AutoModel.from_pretrained(mid, torch_dtype=torch.bfloat16).to(dev).eval()
    chunks = []
    # documents side: no instruction. Last-token pooling (left padding), L2 normalised.
    with torch.no_grad():
        for s in range(0, len(order), BS):
            b = [texts[i] for i in order[s:s + BS]]
            enc = tok(b, padding=True, truncation=True, max_length=MAXLEN, return_tensors="pt").to(dev)
            h = model(**enc).last_hidden_state[:, -1]
            chunks.append(torch.nn.functional.normalize(h.float(), dim=1).cpu().numpy())
            if (s // BS) % 50 == 0:
                print(s, len(order), round(time.time() - t0), "s", flush=True)
    emb = finish(np.concatenate(chunks).astype(np.float16))

elif kind == "nv":
    from transformers import AutoModel
    model = AutoModel.from_pretrained(mid, trust_remote_code=True, torch_dtype=torch.float16).to(dev).eval()
    chunks = []
    with torch.no_grad():
        for s in range(0, len(order), BS):
            b = [texts[i] for i in order[s:s + BS]]
            e = model.encode(b, instruction="", max_length=MAXLEN)
            chunks.append(torch.nn.functional.normalize(e.float(), dim=1).cpu().numpy())
            if (s // BS) % 50 == 0:
                print(s, len(order), round(time.time() - t0), "s", flush=True)
    emb = finish(np.concatenate(chunks).astype(np.float16))

embed_s = time.time() - t0
print("embedded", emb.shape, round(embed_s), "s", flush=True)
assert np.isfinite(emb.astype(np.float32)).all()
np.save(f"{out}/emb.npy", emb)
met = evaluate(emb, qdata, device=dev)
met.update({"model": mid, "name": name, "dims": int(emb.shape[1]), "embed_seconds": round(embed_s, 1),
            "gpu": torch.cuda.get_device_name(0), "max_len": MAXLEN})
json.dump(met, open(f"{out}/metrics.json", "w"), indent=1)
json.dump(seed_neighbours(emb, qdata, device=dev), open(f"{out}/seeds.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(met, indent=1))
