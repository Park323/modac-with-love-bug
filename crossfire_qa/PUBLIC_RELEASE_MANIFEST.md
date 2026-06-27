# Public Release Manifest

This folder is a cleaned public-release copy of the CrossFire QA project.

## Included

- End-to-end QA pipeline: `run.py`
- Detector modules: `detector_layer/`
- Report/rule modules: `report_layer/`
- Example configs: `configs/`
- Asset preparation helpers:
  - `bootstrap.py`
  - `bootstrap_temporal_notifications.py`
  - `auto_promote_assets.py`
  - `prepare_assets.py`
- Synthetic data helper: `make_synthetic_dataset.py`
- Frontend handoff package builder:
  - `build_qa_review_package.py`
  - `build_qa_web_report.py`
- Example report schema: `sample_report.json`
- Dependency files:
  - `requirements.txt`
  - `requirements-optional.txt`

## Excluded

- Local API key files
- `.env`
- Generated `outputs/`
- Raw datasets and synthetic videos
- OCR/model caches
- Python bytecode/cache files
- VLM bootstrap script and VLM asset-labeling outputs

## Before Publishing To GitHub

Run these checks from the parent directory:

```bash
find cf_qa_public -type f \( -name "*.mp4" -o -name "*.mov" -o -name "*.mkv" -o -name "*.avi" \)
find cf_qa_public -type f \( -name ".openai_api_key" -o -name ".vlm_api_key" -o -name "*.pyc" \)
rg -n "sk-proj|/home/" cf_qa_public --glob '!PUBLIC_RELEASE_MANIFEST.md'
```

The first two commands should print nothing. The third command should not reveal secrets or private filesystem paths.
