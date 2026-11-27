#!/bin/bash
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=01:00:00
#SBATCH --constraint=titanv
#SBATCH -o /cluster/shared/embedding_eval/logs/finalize-%j.out
umask 002
cd /cluster/shared/embedding_eval
PY=venv/bin/python
for n in bge-base specter2 minilm nv-embed-v2 e5-mistral; do [ -f out/$n/emb.npy ] && $PY eval_variants.py $n 512; done
[ -f out/qwen3-8b/emb.npy ] && $PY eval_variants.py qwen3-8b 256,512,1024
$PY collect.py
