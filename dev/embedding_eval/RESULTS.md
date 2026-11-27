# Embedding model comparison on the AV Atlas corpus

Status 2026-11-27: four of six models were run and Qwen3-Embedding-8B is the best by a clear margin. NV-Embed-v2
and e5-mistral-7b were started but failed (a missing package and a CUDA start-up error) and are skipped on
purpose: they are unlikely to beat Qwen3 clearly, and the comparison is good enough to decide. The site uses
Qwen3-Embedding-8B at 512 dims stored as int8.

Qwen3-8B truncated (Matryoshka) and stored as int8 keeps almost all of its quality:
256 dims 0.1060 recall@10 (7.1 MB), 512 dims 0.1126 (14.2 MB), 1024 dims 0.1156 (28.3 MB),
full 4096 dims 0.1175 (113 MB). The 512-dim int8 version beats every small model at the same size.

## Setup
- Pool: all 27,653 AV papers (title-deduplicated); text = title + ". " + abstract, max 512 tokens.
- Queries: 3,000 random (seed 42) of the 13,882 AV papers with an abstract and >= 3 citation
  neighbours (cited or citing) that are themselves in the AV pool. Mean 21.7 neighbours per query.
- Metrics: recall@10 (hits / number of neighbours, so the ceiling is below 1), nDCG@10 (binary relevance),
  cosine similarity, self excluded. Document side / no retrieval instruction for every model.
  SPECTER2 uses its documented "title [SEP] abstract" input and the proximity adapter.
- Citation neighbours are a noisy proxy for "similar paper", so absolute numbers are low; use the ranking.

## Results

| model | dims | recall@10 | recall@50 | nDCG@10 | embed minutes (TITAN V) |
|---|---|---|---|---|---|
| BAAI/bge-base-en-v1.5 | 768 | 0.1002 | 0.2257 | 0.1685 | 1.1 |
| allenai/specter2_base + proximity | 768 | 0.0884 | 0.2055 | 0.1505 | 1.1 |
| all-MiniLM-L6-v2 | 384 | 0.0870 | 0.2019 | 0.1488 | 0.7 |
| Qwen3-Embedding-8B | 4096 | **0.1175** | 0.2678 | 0.1971 | 30.0 (RTX A6000) |
| NV-Embed-v2 | 4096 | skipped | | | |
| e5-mistral-7b-instruct | 4096 | skipped | | | |

int8 (global scale) loses under 0.001 recall@10 for bge-base. int8 size for 27,653 papers:
512 dims = 14.2 MB, 768 = 21.2 MB, 1024 = 28.3 MB.

Paths: everything big is in `/cluster/shared/embedding_eval/` on iv-slurm (embeddings in `out/<model>/emb.npy`).
