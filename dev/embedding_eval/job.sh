#!/bin/bash
# usage: sbatch --job-name=emb-<name> job.sh <name> [venv]
#SBATCH --gres=gpu:a6000:1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --time=06:00:00
#SBATCH -o /cluster/shared/embedding_eval/logs/%x-%j.out
umask 002
BASE=/cluster/shared/embedding_eval
NAME=$1
VENV=${2:-$BASE/venv}
export HF_HOME=$BASE/hf
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export TOKENIZERS_PARALLELISM=false
echo "host $(hostname) job $SLURM_JOB_ID $(date)"
nvidia-smi -L
# internet check (information only)
curl -sI -m 6 https://huggingface.co | head -1 || echo "no internet"
cd $BASE
$VENV/bin/python $BASE/embed_eval.py $NAME
echo "end $(date)"
