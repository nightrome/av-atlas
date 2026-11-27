#!/bin/bash
# Run on night-desktop (has internet). Creates the venv (uv, managed python in the shared folder
# so ted-old-desktop sees the same interpreter) and installs the packages.
umask 002
set -x
export PATH=$HOME/.local/bin:$PATH
BASE=/cluster/shared/embedding_eval
mkdir -p $BASE/logs $BASE/models $BASE/out $BASE/data
cd $BASE
export UV_PYTHON_INSTALL_DIR=$BASE/python
rm -rf venv
uv venv --python 3.12 --managed-python venv || exit 1
uv pip install --python venv/bin/python torch numpy "transformers>=4.51,<5" sentence-transformers adapters accelerate huggingface_hub || exit 1
venv/bin/python -c "import torch,transformers,sentence_transformers,adapters;print(torch.__version__,transformers.__version__,sentence_transformers.__version__,adapters.__version__, torch.version.cuda)"
echo SETUP_DONE
