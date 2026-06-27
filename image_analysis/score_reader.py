from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np


class OCRReader(Protocol):
    def readtext(self, image: np.ndarray, **kwargs: Any) -> list[Any]: ...


@dataclass(frozen=True)
class Region:
    x1: float
    y1: float
    x2: float
    y2: float

    def crop(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        left = max(0, min(width, round(self.x1 * width)))
        top = max(0, min(height, round(self.y1 * height)))
        right = max(left + 1, min(width, round(self.x2 * width)))
        bottom = max(top + 1, min(height, round(self.y2 * height)))
        return image[top:bottom, left:right]


# Normalized against the supplied 392x72 HUD example. Keeping these as ratios
# allows the same fixed HUD to be processed at different capture resolutions.
REGIONS = {
    "my_team": Region(0.10, 0.02, 0.25, 0.49),
    "my_score": Region(0.23, 0.02, 0.38, 0.49),
    "max_kills": Region(0.37, 0.02, 0.56, 0.49),
    "opp_score": Region(0.55, 0.02, 0.70, 0.49),
    "opp_team": Region(0.68, 0.02, 0.83, 0.49),
    "match_time": Region(0.35, 0.47, 0.64, 0.97),
}


def _preprocess(crop: np.ndarray, scale: int = 5) -> list[np.ndarray]:
    enlarged = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4)).apply(gray)
    _, binary = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [enlarged, cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR), cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)]


def _ocr(reader: OCRReader, crop: np.ndarray, allowlist: str) -> str:
    candidates: list[str] = []
    for variant in _preprocess(crop):
        results = reader.readtext(
            variant,
            detail=0,
            paragraph=False,
            allowlist=allowlist,
            min_size=3,
        )
        candidates.extend(str(value).strip().upper() for value in results if str(value).strip())
    return max(candidates, key=len, default="")


def normalize_team(value: str) -> str | None:
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    substitutions = {"6R": "GR", "CR": "GR", "8L": "BL", "BI": "BL"}
    cleaned = substitutions.get(cleaned, cleaned)
    if "GR" in cleaned:
        return "GR"
    if "BL" in cleaned:
        return "BL"
    return None


def normalize_score(value: str) -> int | None:
    cleaned = value.upper().replace("O", "0").replace("I", "1").replace("L", "1")
    digits = re.sub(r"\D", "", cleaned)
    return int(digits) if digits else None


def extract_score(image_path: str | Path, reader: OCRReader | None = None) -> dict[str, dict[str, str | int]]:
    path = Path(image_path)
    image = cv2.imread(str(path))
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")

    if reader is None:
        try:
            import easyocr
        except ImportError as exc:
            raise RuntimeError("EasyOCR is not installed. Run: pip install easyocr") from exc
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    raw_my_team = _ocr(reader, REGIONS["my_team"].crop(image), "GRBL0123456789")
    raw_opp_team = _ocr(reader, REGIONS["opp_team"].crop(image), "GRBL0123456789")
    raw_my_score = _ocr(reader, REGIONS["my_score"].crop(image), "0123456789OIL")
    raw_opp_score = _ocr(reader, REGIONS["opp_score"].crop(image), "0123456789OIL")

    my_team = normalize_team(raw_my_team)
    opp_team = normalize_team(raw_opp_team)
    if my_team and not opp_team:
        opp_team = "BL" if my_team == "GR" else "GR"
    elif opp_team and not my_team:
        my_team = "BL" if opp_team == "GR" else "GR"

    my_score = normalize_score(raw_my_score)
    opp_score = normalize_score(raw_opp_score)
    if my_team is None or opp_team is None or my_score is None or opp_score is None:
        raise ValueError(
            "Could not parse scoreboard: "
            f"my_team={raw_my_team!r}, my_score={raw_my_score!r}, "
            f"opp_team={raw_opp_team!r}, opp_score={raw_opp_score!r}"
        )

    return {
        "my": {"team": my_team, "score": my_score},
        "opp": {"team": opp_team, "score": opp_score},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract my/opponent team scores from a fixed CrossFire HUD image.")
    parser.add_argument("image", help="Path to the HUD image")
    args = parser.parse_args()
    print(json.dumps(extract_score(args.image), ensure_ascii=False))


if __name__ == "__main__":
    main()
