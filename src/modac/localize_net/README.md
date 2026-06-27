# localize_net — learned minimap localization

Regress `(x, y, yaw)` from a HUD radar crop with a ResNet50, replacing the
brittle classical matcher in `modac/localize.py`.

**Why this needs no real data collection:** the radar is a deterministic
composite of a *fixed* base map + fixed HUD overlays. So we invert the
`localize.py` geometry to synthesize unlimited, perfectly-labeled samples — pick
`(x, y, yaw)`, composite the radar, and the label is exact.

## Assets (the data engine inputs)
| asset | role |
|-------|------|
| `patched_map.png` (164×487) | base map, hand-drawn HUD style. **North = LEFT.** `x, y` labels are pixels here. |
| `ring.png` (126×135)        | radar circle border + baked-in **N marker**. Center ≈ (62.3, 69.4), r ≈ 62.6. Rotated by `yaw` so N rides to screen angle −yaw. |
| `marker.png`                | player chevron, composited at center, always pointing up (player-up radar). |

All geometry lives in `render.RadarSpec` (`PATCHED_SPEC`), so swapping maps/assets
is a one-liner. The one value still to **calibrate against the real radar** is
`zoom` (base-px per radar-px, uniform). Default `3.0`; preview alternatives with
`scripts/preview_synth.py --zoom N`.

## Pieces
| module | role |
|--------|------|
| `render.py`  | `RadarSpec` + `base map + (x,y,yaw)` → radar composite. `python -m modac.localize_net.render` dumps a yaw-sweep sanity grid. |
| `dataset.py` | torch `Dataset`: on-the-fly synthesis + domain-randomization aug; square `out_size` tensor + targets `(x/W, y/H, sin, cos)`. |
| `model.py`   | ResNet50, fc→4. SmoothL1 position + sin/cos MSE yaw loss; `decode()` → `(x_px, y_px, yaw_deg)`. |
| `train.py`   | training loop (AdamW + cosine + AMP), saves `best.pt`/`last.pt`. |
| `infer.py`   | `Localizer(ckpt).predict_path(crop)` → `(x, y, yaw)`, same convention as `localize.localize`. |

## Install + train
```bash
pip install "modac[train]"            # torch + torchvision (training box only)
modac-loc-train --zoom 3.0 --epochs 30 --batch 64 --out checkpoints
```
CUDA → AMP automatically; `--device mps` works on Apple Silicon (no AMP).

## Convention (matches `modac.localize`)
- `x, y`: player = radar center, in `patched_map.png` pixels.
- `yaw`: degrees clockwise from north (0 = facing north). patched_map north = left.

## Validation done
- Ring rotation sign locked: N marker tracks angle −yaw (round-trip check).
- `FULL_MAP_SPEC` round-tripped to <0.3° vs `localize.estimate_yaw` before the
  switch to patched_map.

## Sim-to-real TODO
- **Calibrate `zoom`** against a real `map_bound.png` crop (compare block sizes).
- Ensure the infer-time crop framing (circle-fills-frame ratio) matches the
  render canvas; adjust crop or `canvas_wh` if the real capture differs.
