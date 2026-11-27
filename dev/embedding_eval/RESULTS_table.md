| model | dims | recall@10 | recall@50 | nDCG@10 | embed GPU minutes (27.6k papers) | GPU |
|---|---|---|---|---|---|---|
| qwen3-8b | - | not run / failed | | | | |
| nv-embed-v2 | - | not run / failed | | | | |
| e5-mistral | - | not run / failed | | | | |
| BAAI/bge-base-en-v1.5 | 768 | 0.1002 | 0.2257 | 0.1685 | 1.1 | NVIDIA TITAN V |
| allenai/specter2_base | 768 | 0.0884 | 0.2055 | 0.1505 | 1.1 | NVIDIA TITAN V |
| sentence-transformers/all-MiniLM-L6-v2 | 384 | 0.0870 | 0.2019 | 0.1488 | 0.7 | NVIDIA TITAN V |

| model | dims | recall@10 (float) | nDCG@10 (float) | recall@10 (int8) | nDCG@10 (int8) | int8 size |
|---|---|---|---|---|---|---|
| bge-base | 768 | 0.1002 | 0.1685 | 0.1000 | 0.1683 | 21.2 MB |
| bge-base | 512 | 0.0979 | 0.1657 | 0.0974 | 0.1653 | 14.2 MB |
| bge-base | 256 | 0.0904 | 0.1556 | 0.0903 | 0.1555 | 7.1 MB |
