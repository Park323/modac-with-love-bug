"""Learned minimap localization: regress (x, y, yaw) from a radar crop.

This subpackage trains a small CNN (ResNet50) to replace the brittle classical
matcher in :mod:`modac.localize`. Because the minimap is a deterministic render
of a *fixed* world map, we can synthesize an unlimited, perfectly-labeled
dataset by inverting the localize geometry: pick (x, y, yaw), render the radar,
and we already know the answer.

Modules:
  render   — full_map + (x, y, yaw) -> radar image  (the data engine)
  dataset  — torch Dataset doing on-the-fly synthesis + augmentation
  model    — ResNet50 regression head + yaw (sin/cos) encoding and loss
  train    — training loop CLI
  infer    — load a checkpoint and predict (x, y, yaw); localize()-compatible
"""
