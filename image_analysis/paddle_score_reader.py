from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .score_reader import REGIONS, normalize_score, normalize_team


MODEL_NAME = "en_PP-OCRv5_mobile_rec"
FIELD_ORDER = ("my_team", "my_score", "opp_score", "opp_team")


def _prepare_line(image: np.ndarray, scale: int = 5) -> np.ndarray:
    """Upscale one fixed HUD text field for recognition-only inference."""
    enlarged = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    # A small neutral border prevents edge characters from being clipped.
    return cv2.copyMakeBorder(enlarged, 8, 8, 12, 12, cv2.BORDER_CONSTANT, value=(32, 32, 32))


def _result_payload(result: Any) -> dict[str, Any]:
    """Normalize PaddleOCR/PaddleX result objects across 3.x releases."""
    if isinstance(result, dict):
        payload: Any = result
    else:
        payload = getattr(result, "json", None)
        if callable(payload):
            payload = payload()
        if payload is None:
            payload = getattr(result, "res", None)

    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise TypeError(f"Unsupported PaddleOCR result type: {type(result).__name__}")
    return payload.get("res", payload)


def _recognize_batch(model: Any, crops: list[np.ndarray]) -> list[str]:
    output = model.predict(input=crops, batch_size=len(crops))
    texts: list[str] = []
    for result in output:
        payload = _result_payload(result)
        texts.append(str(payload.get("rec_text", "")).strip().upper())
    return texts


def create_model() -> Any:
    os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
    try:
        from paddleocr import TextRecognition
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR is not installed. Follow image_analysis/README.md "
            "and install the Paddle version dependencies."
        ) from exc

    return TextRecognition(
        model_name=MODEL_NAME,
        device="cpu",
        engine="paddle_dynamic",
    )


def extract_score_paddle(
    image_path: str | Path,
    model: Any | None = None,
) -> dict[str, dict[str, str | int]]:
    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")

    model = model or create_model()
    crops = [_prepare_line(REGIONS[name].crop(image)) for name in FIELD_ORDER]
    texts = _recognize_batch(model, crops)
    values = dict(zip(FIELD_ORDER, texts, strict=True))

    my_team = normalize_team(values["my_team"])
    opp_team = normalize_team(values["opp_team"])
    if my_team and not opp_team:
        opp_team = "BL" if my_team == "GR" else "GR"
    elif opp_team and not my_team:
        my_team = "BL" if opp_team == "GR" else "GR"

    my_score = normalize_score(values["my_score"])
    opp_score = normalize_score(values["opp_score"])
    if my_team is None or opp_team is None or my_score is None or opp_score is None:
        raise ValueError(
            "Could not parse scoreboard: "
            f"my_team={values['my_team']!r}, my_score={values['my_score']!r}, "
            f"opp_team={values['opp_team']!r}, opp_score={values['opp_score']!r}"
        )

    return {
        "my": {"team": my_team, "score": my_score},
        "opp": {"team": opp_team, "score": opp_score},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract CrossFire HUD scores with PaddleOCR mobile recognition."
    )
    parser.add_argument("image", help="Path to the HUD image")
    args = parser.parse_args()
    print(json.dumps(extract_score_paddle(args.image), ensure_ascii=False))


if __name__ == "__main__":
    main()
