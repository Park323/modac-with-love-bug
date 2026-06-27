# CrossFire QA

CrossFire QA is a computer-vision QA pipeline for gameplay videos. It detects UI state, kill-feed events, score/count changes, death/respawn behavior, and produces structured PASS/FAIL/UNCERTAIN reports with traceable evidence.

The project is designed for low-cost, reproducible QA: most checks are based on fixed ROI sampling, image processing, OCR/template matching, temporal event aggregation, and deterministic rules rather than sending every frame to a large vision model.

## What It Checks

- **Kill/count consistency**: verifies that score/count changes are linked to kill-feed evidence when available.
- **Kill/death notification presence**: checks whether relevant kill-feed notifications appear around gameplay events.
- **Respawn operation after death**: checks whether a death state is followed by a return to playable HUD state.

Spawn-location OCR is disabled by default in the final QA flow. The current recommended public workflow uses respawn/playable-state signals instead of spawn text recognition.

## Repository Layout

```text
.
├── run.py                         # End-to-end dataset pipeline
├── detector_layer/                # UI, OCR, notification, game-state, respawn detectors
├── report_layer/                  # Timeline aggregation, QA rules, evidence report generation
├── configs/                       # Example configs
├── bootstrap.py                   # Conservative asset bootstrap helper
├── bootstrap_temporal_notifications.py
├── auto_promote_assets.py
├── prepare_assets.py
├── make_synthetic_dataset.py      # Synthetic occlusion dataset generator
├── build_qa_review_package.py     # Curated handoff package for visualization/frontends
├── build_qa_web_report.py         # Shared media/summary helpers for review packages
└── sample_report.json             # Example final report schema
```

Large videos, generated outputs, OCR model caches, and local API key files are intentionally excluded from this public package.

## Install

Python 3.10+ is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For OCR-based score reading:

```bash
pip install -r requirements-optional.txt
```

EasyOCR/PaddleOCR are optional. The pipeline can still run in template/heuristic modes, but OCR generally improves score-reading behavior on FHD videos.

## Run The Full QA Pipeline

Prepare a dataset directory containing gameplay videos:

```text
dataset/
  match_001.mp4
  match_002.mp4
```

Run:

```bash
python run.py \
  --dataset dataset \
  --out outputs/final_qa \
  --score-backend easyocr \
  --score-sample-interval-sec 1.0 \
  --evidence-config configs/evidence_report_config.fast_final.json \
  --keep-going
```

Main outputs:

```text
outputs/final_qa/
  final_report.json
  final_report.md
  <video_name>/
    01_ui/ui_detection_report.json
    02_kill_count/kill_count_report.json
    03_notifications/notification_report.json
    04_game_state/game_state_report.json
    05_respawn/respawn_segment_report.json
    07_global_timeline/global_event_timeline.json
    08_qa_rules/qa_rule_report.json
    09_evidence_report/report.json
```

The root `final_report.json` is the main machine-readable report. It includes:

- `overall_result`
- `summary`
- `events`
- `qa_checks`
- `decision_trace`
- `trace_links`
- `run_reproducibility`

## Build A Frontend Handoff Package

After running QA, create a compact package for a web/frontend team:

```bash
python build_qa_review_package.py \
  --run-dir outputs/final_qa \
  --out outputs/qa_review_package \
  --include-pass-details \
  --pass-sample-limit-per-type 3
```

This package contains:

```text
outputs/qa_review_package/
  package_manifest.json            # Main frontend entry point
  data/checks/*.json               # FAIL/UNCERTAIN/NEED_REVIEW checks
  data/pass_checks.json            # Compact PASS reasons and decision traces
  data/pass_examples/*.json        # Representative PASS examples with media
  assets/<check_id>/               # thumbnails, short clips, evidence crops
  reports/<check_id>/              # selected module reports for drill-down
```

`package_manifest.json` is the recommended source of truth for visualization.

## Create Synthetic QA Data

You can generate synthetic occlusion cases from a clean FHD dataset:

```bash
python make_synthetic_dataset.py \
  --source-dataset fhd_dataset \
  --out synthetic_fhd_dataset_lovebug \
  --cases all \
  --occluder-image path/to/occluder.jpg
```

Synthetic cases include:

- `control_pass`
- `score_hidden_fail`
- `kill_feed_hidden_observe`
- `hud_hidden_respawn_fail`

This is useful for checking whether the QA rules produce FAIL/UNCERTAIN on intentionally corrupted videos.

## Asset Preparation

For new gameplay footage, start conservatively:

```bash
python bootstrap.py \
  --dataset dataset \
  --out outputs/bootstrap_assets \
  --all \
  --digit-label-source none \
  --save-unlabeled-digits
```

Review candidates manually before promoting them into template folders. The final QA path is deterministic; asset bootstrap is only a setup helper.

## Notes For Public Use

- Do not commit datasets, generated outputs, model caches, or API keys.
- Use FHD 1920x1080 gameplay videos when possible; the ROI defaults are calibrated for that ratio.
- `configs/qa_rule_config.example.json` disables spawn-location text requirements by default.
- The review-package builder excludes `spawn_location_recognizer` reports because spawn text recognition is not part of the recommended final QA decision path.

## Minimal Smoke Test

```bash
python run.py \
  --dataset dataset/sample.mp4 \
  --out outputs/smoke \
  --max-frames 30 \
  --score-backend template \
  --keep-going
```

This verifies that the pipeline stages can execute without processing a full dataset.
