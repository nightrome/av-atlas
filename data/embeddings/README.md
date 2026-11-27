# Embedding vectors

Text embeddings of every AV paper's title and abstract, made by hand on a GPU (Qwen3-Embedding-8B,
first 512 dimensions, stored as int8). The monthly build does not compute embeddings; it only reads
these files, so papers added since the last refresh simply have no similar papers, no place on the
map and no similarity entry until the next refresh.

- `int8_512.npy`: one row per paper, in the order of `keys.json`
- `keys.json`: the paper title of each row
- `map_xy.npy`: 2D map position of each row (t-SNE), used by map.html
- `int8_512.json`: the quantisation scale

Refreshing: embed the papers that are not in `keys.json` yet (see `dev/embedding_eval/` for the model
setup), append their rows and titles, run `python scripts/build_paper_map.py --recompute` and
`python scripts/find_duplicate_papers.py`, review `data/duplicate_papers.json`, then commit.
