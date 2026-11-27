#!/bin/bash
# Run on night-desktop. Downloads the models into the shared HF cache.
umask 002
export HF_HOME=/cluster/shared/embedding_eval/hf
BASE=/cluster/shared/embedding_eval
cd $BASE
for m in sentence-transformers/all-MiniLM-L6-v2 BAAI/bge-base-en-v1.5 allenai/specter2_base allenai/specter2 Qwen/Qwen3-Embedding-8B intfloat/e5-mistral-7b-instruct; do
  echo "== $m"
  venv/bin/python -c "
import sys
from huggingface_hub import snapshot_download
p = snapshot_download(sys.argv[1], allow_patterns=['*.json','*.txt','*.safetensors','*.model','*.py','*.bin'] if 'specter2' in sys.argv[1] else None, ignore_patterns=['*.onnx','*.h5','*.msgpack','*.ot','openvino/*','onnx/*','*.gguf'])
print(p)
" "$m" 2>&1 | tail -3
done
echo DOWNLOAD_DONE
