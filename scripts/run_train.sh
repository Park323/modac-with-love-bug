#!/usr/bin/env bash
# Train the minimap localizer. Run from the repo root (e.g. on a RunPod CUDA box).
# Assets it expects in CWD (committed): patched_map.png, ring.png, marker.png,
# sample_2..5.png. CUDA is auto-detected and AMP is enabled there.
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root

# 1) install deps (torch + torchvision + project, editable)
python -m pip install -e ".[train]"

# 2) linear-probe baseline: frozen ResNet50 backbone, train only the 4-d head
python -m modac.localize_net.train \
  --linear-probe --lr 1e-3 \
  --epochs 25 --steps-per-epoch 300 --val-steps 30 \
  --batch 128 --workers 8 --zoom 3.0 \
  --out checkpoints

# --- alternatives -----------------------------------------------------------
# Full fine-tune (unfreeze backbone) — higher accuracy, needs the GPU:
#   python -m modac.localize_net.train \
#     --lr 3e-4 --epochs 30 --steps-per-epoch 400 --val-steps 40 \
#     --batch 128 --workers 8 --zoom 3.0 --out checkpoints
#
# Resume from a checkpoint:
#   python -m modac.localize_net.train ... --resume checkpoints/last.pt
#
# Best weights land in checkpoints/best.pt (val-loss), last.pt every epoch.
