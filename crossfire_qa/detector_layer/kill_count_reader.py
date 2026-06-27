from __future__ import annotations

import json
import math
import re
import base64
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

import cv2
import numpy as np


CountStatus = Literal[
    "ok",
    "low_confidence",
    "no_digit_candidate",
    "no_template",
    "ocr_unavailable",
    "parse_failed",
]


@dataclass(frozen=True)
class Box:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    def clip(self, width: int, height: int) -> "Box":
        x1 = max(0, min(self.x, width - 1))
        y1 = max(0, min(self.y, height - 1))
        x2 = max(x1 + 1, min(self.x2, width))
        y2 = max(y1 + 1, min(self.y2, height))
        return Box(x1, y1, x2 - x1, y2 - y1)

    def to_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


@dataclass
class DigitCandidate:
    digit: Optional[int]
    confidence: float
    bbox: list[int]
    status: str


@dataclass
class ScoreSideReading:
    side: str
    value: Optional[int]
    confidence: float
    raw_text: str
    digit_candidates: list[DigitCandidate] = field(default_factory=list)
    status: CountStatus = "parse_failed"


@dataclass
class FrameScoreReading:
    frame_index: int
    timestamp_sec: float
    crop_path: Optional[str]
    readings: dict[str, ScoreSideReading]


@dataclass
class StableCountPoint:
    timestamp_sec: float
    side: str
    value: Optional[int]
    confidence: float
    votes: int
    window_start_sec: float
    window_end_sec: float
    status: str


@dataclass
class CountChangeEvent:
    time: float
    event: str
    side: str
    from_value: int
    to_value: int
    confidence: float
    source: list[str]
    status: str
    matched_kill_feed: Optional[dict[str, Any]] = None
    notes: list[str] = field(default_factory=list)


DEFAULT_SCORE_SUB_ROIS: dict[str, dict[str, int]] = {
    # Coordinates are relative to top_score_bar crop, not full frame.
    # Keep these tight around the single score digits; wider crops also catch
    # the round target ("100"), team labels, and timer text.
    "left_score": {"x": 102, "y": 8, "w": 27, "h": 23},
    "right_score": {"x": 233, "y": 8, "w": 21, "h": 23},
}

DEFAULT_SIDE_MAP: dict[str, str] = {
    # Use logical names first. If your match uses fixed teams, change to GR/BL.
    "left_score": "left_score",
    "right_score": "right_score",
}


class DigitTemplateLibrary:
    """
    Loads digit templates from either layout:

      digit_templates/0.png
      digit_templates/1.png
      digit_templates/0/score_0_a.png
      digit_templates/1/score_1_a.png

    Templates are preprocessed to binary images before matching.
    """

    def __init__(self, template_dir: Optional[str | Path]) -> None:
        self.template_dir = Path(template_dir) if template_dir else None
        self.templates: dict[int, list[np.ndarray]] = defaultdict(list)
        if self.template_dir and self.template_dir.exists():
            self._load()

    @property
    def has_templates(self) -> bool:
        return any(len(v) > 0 for v in self.templates.values())

    @property
    def has_complete_digit_set(self) -> bool:
        return all(len(self.templates.get(d, [])) > 0 for d in range(10))

    def _load(self) -> None:
        assert self.template_dir is not None
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

        # Root-level templates, e.g. 0.png, digit_0.png
        for p in self.template_dir.iterdir():
            if not p.is_file() or p.suffix.lower() not in image_exts:
                continue
            m = re.search(r"([0-9])", p.stem)
            if m:
                self._register(int(m.group(1)), p)

        # Per-digit directories, e.g. 0/*.png
        for d in self.template_dir.iterdir():
            if not d.is_dir() or not re.fullmatch(r"[0-9]", d.name):
                continue
            digit = int(d.name)
            for p in d.iterdir():
                if p.is_file() and p.suffix.lower() in image_exts:
                    self._register(digit, p)

    def _register(self, digit: int, path: Path) -> None:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None or img.size == 0:
            return
        self.templates[digit].append(preprocess_digit_image(img, output_size=(24, 32)))

    def available_digits(self) -> list[int]:
        return sorted(d for d, imgs in self.templates.items() if imgs)


def preprocess_score_crop(crop_bgr: np.ndarray) -> np.ndarray:
    """Contrast-normalized grayscale image for segmentation/OCR."""
    if crop_bgr.ndim == 3:
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop_bgr.copy()
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    gray = clahe.apply(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return gray


def binarize_digit_foreground(gray: np.ndarray) -> np.ndarray:
    """Extract bright HUD text/digit foreground as a binary mask."""
    # Otsu works reasonably for white/yellow digits on dark HUD.
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Remove tiny noise and connect broken digit strokes.
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    binary = cv2.dilate(binary, np.ones((2, 2), np.uint8), iterations=1)
    return binary


def preprocess_digit_image(img_gray: np.ndarray, output_size: tuple[int, int] = (24, 32)) -> np.ndarray:
    """Normalize a single digit candidate/template to a fixed binary canvas."""
    gray = img_gray.copy()
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)
    gray = preprocess_score_crop(gray)
    binary = binarize_digit_foreground(gray)

    ys, xs = np.where(binary > 0)
    if len(xs) == 0 or len(ys) == 0:
        return np.zeros((output_size[1], output_size[0]), dtype=np.uint8)

    x1, x2 = int(xs.min()), int(xs.max()) + 1
    y1, y2 = int(ys.min()), int(ys.max()) + 1
    digit = binary[y1:y2, x1:x2]

    out_w, out_h = output_size
    h, w = digit.shape[:2]
    scale = min((out_w - 4) / max(1, w), (out_h - 4) / max(1, h))
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = cv2.resize(digit, (new_w, new_h), interpolation=cv2.INTER_AREA)

    canvas = np.zeros((out_h, out_w), dtype=np.uint8)
    x = (out_w - new_w) // 2
    y = (out_h - new_h) // 2
    canvas[y:y + new_h, x:x + new_w] = resized
    return canvas


def segment_digit_candidates(score_crop_bgr: np.ndarray) -> list[tuple[Box, np.ndarray]]:
    """
    Finds digit-like connected components in a score sub-crop.
    Returns sorted (bbox, digit_image_gray) pairs.
    """
    h, w = score_crop_bgr.shape[:2]
    gray = preprocess_score_crop(score_crop_bgr)
    binary = binarize_digit_foreground(gray)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[Box] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        area = bw * bh
        if bh < max(8, int(h * 0.25)):
            continue
        if bw < 3 or bw > int(w * 0.8):
            continue
        if area < 20:
            continue
        # Ignore very wide text fragments; score digits are compact.
        aspect = bw / max(1, bh)
        if aspect > 1.1:
            continue
        boxes.append(Box(x, y, bw, bh))

    # Merge boxes that are extremely close horizontally, useful for broken strokes.
    boxes = sorted(boxes, key=lambda b: b.x)
    merged: list[Box] = []
    for b in boxes:
        if not merged:
            merged.append(b)
            continue
        prev = merged[-1]
        gap = b.x - prev.x2
        vertical_overlap = max(0, min(prev.y2, b.y2) - max(prev.y, b.y))
        min_h = min(prev.h, b.h)
        if gap <= 2 and vertical_overlap >= 0.5 * min_h:
            x1 = min(prev.x, b.x)
            y1 = min(prev.y, b.y)
            x2 = max(prev.x2, b.x2)
            y2 = max(prev.y2, b.y2)
            merged[-1] = Box(x1, y1, x2 - x1, y2 - y1)
        else:
            merged.append(b)

    candidates: list[tuple[Box, np.ndarray]] = []
    for b in merged:
        pad = 2
        bp = Box(b.x - pad, b.y - pad, b.w + 2 * pad, b.h + 2 * pad).clip(w, h)
        candidates.append((bp, gray[bp.y:bp.y2, bp.x:bp.x2]))
    return candidates


class DigitTemplateOCR:
    """Pure OpenCV digit OCR using per-digit templates."""

    def __init__(self, template_dir: Optional[str | Path], min_digit_confidence: float = 0.55) -> None:
        self.library = DigitTemplateLibrary(template_dir)
        self.min_digit_confidence = min_digit_confidence

    def recognize_digit(self, digit_img_gray: np.ndarray) -> tuple[Optional[int], float]:
        if not self.library.has_templates:
            return None, 0.0

        sample = preprocess_digit_image(digit_img_gray, output_size=(24, 32))
        best_digit: Optional[int] = None
        best_score = -1.0

        for digit, templates in self.library.templates.items():
            for tpl in templates:
                # Both sample and tpl have same size, so matchTemplate returns one score.
                score = float(cv2.matchTemplate(sample, tpl, cv2.TM_CCOEFF_NORMED)[0, 0])
                if score > best_score:
                    best_score = score
                    best_digit = digit

        if best_score < self.min_digit_confidence:
            return None, max(0.0, best_score)
        return best_digit, max(0.0, min(1.0, best_score))

    def read_number(self, score_crop_bgr: np.ndarray) -> ScoreSideReading:
        if not self.library.has_templates:
            return ScoreSideReading(
                side="unknown",
                value=None,
                confidence=0.0,
                raw_text="",
                digit_candidates=[],
                status="no_template",
            )

        segments = segment_digit_candidates(score_crop_bgr)
        if not segments:
            return ScoreSideReading(
                side="unknown",
                value=None,
                confidence=0.0,
                raw_text="",
                digit_candidates=[],
                status="no_digit_candidate",
            )

        candidates: list[DigitCandidate] = []
        for box, img in segments:
            digit, conf = self.recognize_digit(img)
            candidates.append(
                DigitCandidate(
                    digit=digit,
                    confidence=conf,
                    bbox=box.to_list(),
                    status="ok" if digit is not None else "low_confidence",
                )
            )

        valid_candidates = [c for c in candidates if c.digit is not None]
        if not valid_candidates:
            return ScoreSideReading(
                side="unknown",
                value=None,
                confidence=float(np.mean([c.confidence for c in candidates])) if candidates else 0.0,
                raw_text="",
                digit_candidates=candidates,
                status="low_confidence",
            )

        best = max(valid_candidates, key=lambda c: c.confidence)
        raw = str(best.digit)
        return ScoreSideReading(
            side="unknown",
            value=int(raw),
            confidence=float(best.confidence),
            raw_text=raw,
            digit_candidates=candidates,
            status="ok",
        )


class OptionalEasyOCRReader:
    """
    Optional fallback. It is used only when easyocr is installed.
    This keeps the MVP dependency-light while allowing a quick baseline OCR.
    """

    def __init__(self, model_storage_directory: Optional[str | Path] = None) -> None:
        try:
            import easyocr  # type: ignore
        except Exception as exc:  # pragma: no cover
            self.reader = None
            self.error = str(exc)
            return
        kwargs = {}
        if model_storage_directory:
            model_dir = Path(model_storage_directory)
            model_dir.mkdir(parents=True, exist_ok=True)
            user_network_dir = model_dir / "user_network"
            user_network_dir.mkdir(parents=True, exist_ok=True)
            kwargs["model_storage_directory"] = str(model_dir)
            kwargs["user_network_directory"] = str(user_network_dir)
        self.reader = easyocr.Reader(["en"], gpu=False, verbose=False, **kwargs)
        self.error = ""

    @property
    def available(self) -> bool:
        return self.reader is not None

    def read_number(self, score_crop_bgr: np.ndarray) -> ScoreSideReading:
        if self.reader is None:
            return ScoreSideReading("unknown", None, 0.0, "", [], "ocr_unavailable")

        best_text = ""
        best_conf = 0.0
        for variant in make_score_ocr_variants(score_crop_bgr):
            result = self.reader.readtext(variant, allowlist="0123456789", detail=1, paragraph=False)
            for _, text, conf in result:
                digits = re.sub(r"\D", "", text)
                if digits and float(conf) > best_conf:
                    best_text = digits
                    best_conf = float(conf)

        if not best_text:
            return ScoreSideReading("unknown", None, best_conf, "", [], "parse_failed")
        return ScoreSideReading("unknown", int(best_text), best_conf, best_text, [], "ok")


class OptionalPaddleOCRReader:
    """
    Optional stronger OCR backend for small HUD digits.

    PaddleOCR is intentionally optional because it is much heavier than the
    OpenCV template path. When installed, it tends to be a better default for
    arbitrary 0-9 digits than a partial template library.
    """

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as exc:  # pragma: no cover
            self.reader = None
            self.error = str(exc)
            return
        try:
            self.reader = PaddleOCR(
                lang="en",
                use_angle_cls=False,
                show_log=False,
                det=False,
                rec=True,
            )
            self.error = ""
        except TypeError:
            # PaddleOCR changed constructor flags across releases. Keep a broad
            # fallback so users can try newer installs without code changes.
            self.reader = PaddleOCR(lang="en", use_angle_cls=False, show_log=False)
            self.error = ""
        except Exception as exc:  # pragma: no cover
            self.reader = None
            self.error = str(exc)

    @property
    def available(self) -> bool:
        return self.reader is not None

    def read_number(self, score_crop_bgr: np.ndarray) -> ScoreSideReading:
        if self.reader is None:
            return ScoreSideReading("unknown", None, 0.0, "", [], "ocr_unavailable")

        best_text = ""
        best_conf = 0.0
        for variant in make_score_ocr_variants(score_crop_bgr):
            result = self.reader.ocr(variant, det=False, cls=False)
            for text, conf in iter_paddle_text_conf(result):
                digits = re.sub(r"\D", "", text)
                if digits and float(conf) > best_conf:
                    best_text = digits
                    best_conf = float(conf)

        if not best_text:
            return ScoreSideReading("unknown", None, best_conf, "", [], "parse_failed")
        return ScoreSideReading("unknown", int(best_text), best_conf, best_text, [], "ok")


class OptionalVLMScoreReader:
    """OpenAI-compatible vision reader for the full top score bar crop."""

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_sec: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.cache: dict[str, dict[str, ScoreSideReading]] = {}
        self.error = "" if api_key else "missing_api_key"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _score_pair_image(self, crop_bgr: np.ndarray) -> np.ndarray:
        h, w = crop_bgr.shape[:2]
        left_box = Box(70, 0, 90, 42).clip(w, h)
        right_box = Box(210, 0, 95, 42).clip(w, h)
        left = crop_bgr[left_box.y:left_box.y2, left_box.x:left_box.x2]
        right = crop_bgr[right_box.y:right_box.y2, right_box.x:right_box.x2]
        left = cv2.resize(left, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        right = cv2.resize(right, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        row_h = max(left.shape[0], right.shape[0])
        row_w = max(left.shape[1], right.shape[1])
        canvas = np.zeros((row_h * 2 + 18, row_w, 3), dtype=np.uint8)
        canvas[0:left.shape[0], 0:left.shape[1]] = left
        canvas[row_h + 18:row_h + 18 + right.shape[0], 0:right.shape[1]] = right
        return canvas

    def _image_data_url(self, crop_bgr: np.ndarray) -> str:
        score_pair = self._score_pair_image(crop_bgr)
        ok, buf = cv2.imencode(".jpg", score_pair, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            raise RuntimeError("failed to encode score crop")
        data = base64.b64encode(buf.tobytes()).decode("ascii")
        return f"data:image/jpeg;base64,{data}"

    def _cache_key(self, crop_bgr: np.ndarray) -> str:
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY) if crop_bgr.ndim == 3 else crop_bgr
        h, w = gray.shape[:2]
        boxes = [
            Box(88, 4, 58, 31).clip(w, h),
            Box(218, 4, 58, 31).clip(w, h),
        ]
        parts = [gray[b.y:b.y2, b.x:b.x2] for b in boxes]
        score_only = np.hstack([cv2.resize(p, (48, 28), interpolation=cv2.INTER_AREA) for p in parts])
        small = cv2.resize(score_only, (64, 16), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        value = 0
        for bit in diff.flatten():
            value = (value << 1) | int(bool(bit))
        return f"{value:x}"

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            raise

    def _call_vlm(self, crop_bgr: np.ndarray) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("missing VLM API key")
        prompt = (
            "You are reading a cropped CrossFire scoreboard score image.\n"
            "The image contains two horizontal strips separated by black space.\n"
            "Top strip: our team score region from the LEFT side of the scoreboard.\n"
            "Bottom strip: enemy team score region from the RIGHT side of the scoreboard.\n"
            "Scores can be 0 to 999. Ignore team letters such as GR/BL and any decorative UI.\n"
            "Return strict JSON only with keys: our_score, enemy_score, confidence, visible, reason.\n"
            "our_score and enemy_score must be integers when visible, otherwise null.\n"
            "confidence must be a number from 0 to 1."
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": self._image_data_url(crop_bgr)}},
                    ],
                }
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"VLM HTTP error {exc.code}: {detail}") from exc
        content = raw["choices"][0]["message"]["content"]
        parsed = self._extract_json(content)
        parsed["_provider_response_id"] = raw.get("id")
        return parsed

    def read_score_bar(self, top_score_crop_bgr: np.ndarray) -> dict[str, ScoreSideReading]:
        key = self._cache_key(top_score_crop_bgr)
        if key in self.cache:
            return {
                side: ScoreSideReading(
                    side=r.side,
                    value=r.value,
                    confidence=r.confidence,
                    raw_text=r.raw_text,
                    digit_candidates=list(r.digit_candidates),
                    status=r.status,
                )
                for side, r in self.cache[key].items()
            }
        if not self.available:
            return {
                "our_score": ScoreSideReading("our_score", None, 0.0, "", [], "ocr_unavailable"),
                "enemy_score": ScoreSideReading("enemy_score", None, 0.0, "", [], "ocr_unavailable"),
            }

        try:
            data = self._call_vlm(top_score_crop_bgr)
            visible = bool(data.get("visible", True))
            conf = max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0)))
            our = data.get("our_score")
            enemy = data.get("enemy_score")
            status: CountStatus = "ok" if visible and our is not None and enemy is not None else "parse_failed"
            raw = json.dumps(
                {
                    "our_score": our,
                    "enemy_score": enemy,
                    "confidence": conf,
                    "visible": visible,
                    "reason": data.get("reason", ""),
                    "method": "vlm",
                    "model": self.model,
                    "provider_response_id": data.get("_provider_response_id"),
                },
                ensure_ascii=False,
            )
            readings = {
                "our_score": ScoreSideReading("our_score", int(our) if our is not None else None, conf, raw, [], status),
                "enemy_score": ScoreSideReading("enemy_score", int(enemy) if enemy is not None else None, conf, raw, [], status),
            }
            self.cache[key] = readings
            return readings
        except Exception as exc:
            self.error = str(exc)
            return {
                "our_score": ScoreSideReading("our_score", None, 0.0, str(exc), [], "parse_failed"),
                "enemy_score": ScoreSideReading("enemy_score", None, 0.0, str(exc), [], "parse_failed"),
            }


def make_score_ocr_variants(score_crop_bgr: np.ndarray) -> list[np.ndarray]:
    """Create OCR-friendly variants of a tiny HUD digit crop."""
    if score_crop_bgr.ndim == 3:
        gray = cv2.cvtColor(score_crop_bgr, cv2.COLOR_BGR2GRAY)
    else:
        gray = score_crop_bgr.copy()

    up = cv2.resize(gray, None, fx=5, fy=5, interpolation=cv2.INTER_CUBIC)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(4, 4)).apply(up)
    sharp = cv2.addWeighted(clahe, 1.7, cv2.GaussianBlur(clahe, (0, 0), 1.0), -0.7, 0)
    _, otsu = cv2.threshold(sharp, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return [
        cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR),
        cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR),
    ]


def iter_paddle_text_conf(result: Any) -> Iterable[tuple[str, float]]:
    """Yield (text, confidence) from several PaddleOCR result shapes."""
    if result is None:
        return
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[0], str):
        yield result[0], float(result[1])
        return
    if isinstance(result, list):
        for item in result:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
                yield item[0], float(item[1])
            elif isinstance(item, list):
                yield from iter_paddle_text_conf(item)
            elif isinstance(item, tuple) and len(item) >= 2:
                yield from iter_paddle_text_conf(item[1])


class TopScoreCountReader:
    """
    Reads the score/count values inside top_score_bar crop.

    The reader intentionally separates:
      - where to read: score_sub_rois
      - what logical side it means: side_map
      - how to read digits: template OCR or optional EasyOCR
    """

    def __init__(
        self,
        digit_template_dir: Optional[str | Path] = None,
        score_sub_rois: Optional[dict[str, dict[str, int]]] = None,
        side_map: Optional[dict[str, str]] = None,
        backend: Literal["auto", "template", "easyocr", "paddleocr", "vlm"] = "auto",
        min_digit_confidence: float = 0.55,
        easyocr_model_dir: Optional[str | Path] = None,
        vlm_api_key: Optional[str] = None,
        vlm_model: str = "gpt-4o-mini",
        vlm_base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.score_sub_rois = score_sub_rois or DEFAULT_SCORE_SUB_ROIS
        self.side_map = side_map or DEFAULT_SIDE_MAP
        self.backend = backend
        self.easyocr_model_dir = easyocr_model_dir
        self.template_ocr = DigitTemplateOCR(digit_template_dir, min_digit_confidence=min_digit_confidence)
        self.easyocr: Optional[OptionalEasyOCRReader] = None
        self.paddleocr: Optional[OptionalPaddleOCRReader] = None
        self.vlm: Optional[OptionalVLMScoreReader] = None

        if backend == "vlm":
            self.vlm = OptionalVLMScoreReader(vlm_api_key, model=vlm_model, base_url=vlm_base_url)
        if backend == "paddleocr" or (backend == "auto" and not self.template_ocr.library.has_complete_digit_set):
            self.paddleocr = OptionalPaddleOCRReader()
        if backend == "easyocr" or (backend == "auto" and not self.template_ocr.library.has_complete_digit_set):
            self.easyocr = OptionalEasyOCRReader(model_storage_directory=self.easyocr_model_dir)

    @classmethod
    def from_json(cls, config_path: str | Path, **kwargs: Any) -> "TopScoreCountReader":
        with Path(config_path).open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        return cls(
            score_sub_rois=cfg.get("score_sub_rois", DEFAULT_SCORE_SUB_ROIS),
            side_map=cfg.get("side_map", DEFAULT_SIDE_MAP),
            **kwargs,
        )

    def _read_sub_score(self, sub_crop: np.ndarray) -> ScoreSideReading:
        if self.backend == "template":
            return self.template_ocr.read_number(sub_crop)
        if self.backend == "easyocr":
            if self.easyocr is None:
                self.easyocr = OptionalEasyOCRReader(model_storage_directory=self.easyocr_model_dir)
            return self.easyocr.read_number(sub_crop)
        if self.backend == "paddleocr":
            if self.paddleocr is None:
                self.paddleocr = OptionalPaddleOCRReader()
            return self.paddleocr.read_number(sub_crop)

        # auto
        if self.template_ocr.library.has_complete_digit_set:
            return self.template_ocr.read_number(sub_crop)
        if self.paddleocr is not None and self.paddleocr.available:
            reading = self.paddleocr.read_number(sub_crop)
            if reading.status == "ok":
                return reading
        if self.easyocr is not None and self.easyocr.available:
            reading = self.easyocr.read_number(sub_crop)
            if reading.status == "ok":
                return reading
        if self.template_ocr.library.has_templates:
            return self.template_ocr.read_number(sub_crop)
        return ScoreSideReading("unknown", None, 0.0, "", [], "ocr_unavailable")

    def read_top_score_bar(
        self,
        top_score_crop_bgr: np.ndarray,
        frame_index: int,
        timestamp_sec: float,
        crop_path: Optional[str] = None,
    ) -> FrameScoreReading:
        h, w = top_score_crop_bgr.shape[:2]
        readings: dict[str, ScoreSideReading] = {}

        if self.backend == "vlm":
            if self.vlm is None:
                self.vlm = OptionalVLMScoreReader(None)
            return FrameScoreReading(
                frame_index=frame_index,
                timestamp_sec=timestamp_sec,
                crop_path=crop_path,
                readings=self.vlm.read_score_bar(top_score_crop_bgr),
            )

        for slot_name, cfg in self.score_sub_rois.items():
            box = Box(int(cfg["x"]), int(cfg["y"]), int(cfg["w"]), int(cfg["h"])).clip(w, h)
            sub_crop = top_score_crop_bgr[box.y:box.y2, box.x:box.x2]
            reading = self._read_sub_score(sub_crop)
            side = self.side_map.get(slot_name, slot_name)
            reading.side = side
            readings[side] = reading

        return FrameScoreReading(
            frame_index=frame_index,
            timestamp_sec=timestamp_sec,
            crop_path=crop_path,
            readings=readings,
        )


def load_score_reader_config(config_path: Optional[str | Path]) -> tuple[dict[str, dict[str, int]], dict[str, str]]:
    if config_path is None:
        return DEFAULT_SCORE_SUB_ROIS, DEFAULT_SIDE_MAP
    with Path(config_path).open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    return cfg.get("score_sub_rois", DEFAULT_SCORE_SUB_ROIS), cfg.get("side_map", DEFAULT_SIDE_MAP)


def load_kill_feed_events(path: Optional[str | Path]) -> list[dict[str, Any]]:
    if path is None:
        return []
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data.get("temporal_aggregation"), dict):
        return [
            ev for ev in data.get("temporal_aggregation", {}).get("events", [])
            if ev.get("event") == "kill_notification" or ev.get("class_name") == "kill_feed"
        ]
    return data.get("events", []) or data.get("kill_feed_events", []) or []


class KillCountTemporalAggregator:
    def __init__(
        self,
        window_sec: float = 0.8,
        min_read_confidence: float = 0.45,
        min_votes: int = 2,
        max_valid_jump: int = 1,
        min_event_gap_sec: float = 0.4,
        kill_feed_match_window_sec: float = 2.0,
    ) -> None:
        self.window_sec = window_sec
        self.min_read_confidence = min_read_confidence
        self.min_votes = min_votes
        self.max_valid_jump = max_valid_jump
        self.min_event_gap_sec = min_event_gap_sec
        self.kill_feed_match_window_sec = kill_feed_match_window_sec

    def build_stable_series(self, frame_readings: list[FrameScoreReading]) -> list[StableCountPoint]:
        if not frame_readings:
            return []

        sides = sorted({side for fr in frame_readings for side in fr.readings.keys()})
        stable: list[StableCountPoint] = []
        half = self.window_sec / 2.0

        for fr in frame_readings:
            t = fr.timestamp_sec
            for side in sides:
                values: list[tuple[int, float]] = []
                for other in frame_readings:
                    if abs(other.timestamp_sec - t) > half:
                        continue
                    r = other.readings.get(side)
                    if r is None or r.value is None or r.confidence < self.min_read_confidence:
                        continue
                    values.append((r.value, r.confidence))

                if not values:
                    stable.append(
                        StableCountPoint(t, side, None, 0.0, 0, t - half, t + half, "no_valid_reading")
                    )
                    continue

                counter = Counter(v for v, _ in values)
                value, votes = counter.most_common(1)[0]
                confs = [conf for v, conf in values if v == value]
                confidence = float(np.mean(confs)) * min(1.0, votes / max(1, self.min_votes))
                status = "stable" if votes >= self.min_votes else "weak_votes"
                stable.append(
                    StableCountPoint(t, side, value, confidence, votes, t - half, t + half, status)
                )

        # Remove exact duplicate values at near-identical timestamps if input had duplicates.
        stable.sort(key=lambda p: (p.side, p.timestamp_sec))
        return stable

    def _match_kill_feed(
        self,
        time_sec: float,
        side: str,
        kill_feed_events: list[dict[str, Any]],
    ) -> Optional[dict[str, Any]]:
        if not kill_feed_events:
            return None

        best: Optional[dict[str, Any]] = None
        best_dt = math.inf
        for e in kill_feed_events:
            et = e.get("time", e.get("timestamp_sec", e.get("time_sec")))
            if et is None:
                continue
            dt = abs(float(et) - time_sec)
            if dt > self.kill_feed_match_window_sec:
                continue
            relation = e.get("team_relation") if isinstance(e.get("team_relation"), dict) else {}
            event_side = (
                e.get("expected_score_side")
                or relation.get("expected_score_side")
                or e.get("side")
                or e.get("team")
                or e.get("killer_side")
            )
            if event_side is not None and str(event_side) != side:
                continue
            if dt < best_dt:
                best = e
                best_dt = dt
        return best

    def detect_count_changes(
        self,
        stable_series: list[StableCountPoint],
        kill_feed_events: Optional[list[dict[str, Any]]] = None,
    ) -> list[CountChangeEvent]:
        kill_feed_events = kill_feed_events or []
        events: list[CountChangeEvent] = []

        by_side: dict[str, list[StableCountPoint]] = defaultdict(list)
        for p in stable_series:
            by_side[p.side].append(p)

        for side, points in by_side.items():
            points = sorted(points, key=lambda p: p.timestamp_sec)
            last_value: Optional[int] = None
            last_event_time = -math.inf

            for p in points:
                if p.value is None or p.status not in {"stable", "weak_votes"}:
                    continue
                if p.confidence < self.min_read_confidence:
                    continue

                if last_value is None:
                    last_value = p.value
                    continue

                if p.value == last_value:
                    continue

                diff = p.value - last_value
                notes: list[str] = []
                status = "CONFIRMED"
                source = ["top_score_ocr"]

                if p.timestamp_sec - last_event_time < self.min_event_gap_sec:
                    last_value = p.value
                    continue

                if diff <= 0:
                    status = "NEED_REVIEW"
                    notes.append("score decreased or round reset candidate")
                elif diff > self.max_valid_jump:
                    status = "NEED_REVIEW"
                    notes.append(f"abnormal jump: +{diff}")

                matched = self._match_kill_feed(p.timestamp_sec, side, kill_feed_events)
                confidence = p.confidence
                if matched is not None:
                    source.append("kill_feed_crosscheck")
                    confidence = min(1.0, confidence * 0.85 + 0.15)
                elif kill_feed_events:
                    # kill feed data was supplied but no match was found.
                    status = "UNCERTAIN" if status == "CONFIRMED" else status
                    notes.append("no matching kill feed event within window")

                events.append(
                    CountChangeEvent(
                        time=p.timestamp_sec,
                        event="count_change",
                        side=side,
                        from_value=last_value,
                        to_value=p.value,
                        confidence=float(confidence),
                        source=source,
                        status=status,
                        matched_kill_feed=matched,
                        notes=notes,
                    )
                )
                last_event_time = p.timestamp_sec
                last_value = p.value

        events.sort(key=lambda e: e.time)
        return events

    def detect_kill_feed_anchored_changes(
        self,
        stable_series: list[StableCountPoint],
        kill_feed_events: list[dict[str, Any]],
    ) -> list[CountChangeEvent]:
        if not kill_feed_events:
            return []
        by_side: dict[str, list[StableCountPoint]] = defaultdict(list)
        for p in stable_series:
            if p.value is None or p.confidence < self.min_read_confidence:
                continue
            by_side[p.side].append(p)
        for side in by_side:
            by_side[side].sort(key=lambda p: p.timestamp_sec)

        events: list[CountChangeEvent] = []
        seen: set[tuple[str, float]] = set()
        for feed in kill_feed_events:
            side = (
                feed.get("expected_score_side")
                or feed.get("side")
                or feed.get("team")
                or feed.get("killer_side")
            )
            if side is None:
                relation = feed.get("team_relation") if isinstance(feed.get("team_relation"), dict) else {}
                side = relation.get("expected_score_side")
            if side is None:
                continue
            side = str(side)
            points = by_side.get(side, [])
            if not points:
                continue
            t = float(feed.get("time", feed.get("timestamp_sec", 0.0)) or 0.0)
            before = [
                p for p in points
                if 0.0 <= t - p.timestamp_sec <= self.kill_feed_match_window_sec
            ]
            after = [
                p for p in points
                if 0.0 <= p.timestamp_sec - t <= self.kill_feed_match_window_sec
            ]
            if not before or not after:
                continue
            b = max(before, key=lambda p: (p.timestamp_sec, p.confidence))
            a = min(after, key=lambda p: (p.timestamp_sec, -p.confidence))
            if a.value is None or b.value is None or a.value <= b.value:
                continue
            diff = int(a.value - b.value)
            status = "CONFIRMED" if diff <= self.max_valid_jump else "NEED_REVIEW"
            key = (side, round(a.timestamp_sec, 2))
            if key in seen:
                continue
            seen.add(key)
            confidence = min(1.0, 0.45 * b.confidence + 0.45 * a.confidence + 0.10 * float(feed.get("confidence", 0.7) or 0.7))
            events.append(
                CountChangeEvent(
                    time=a.timestamp_sec,
                    event="count_change",
                    side=side,
                    from_value=int(b.value),
                    to_value=int(a.value),
                    confidence=float(confidence),
                    source=["top_score_ocr", "kill_feed_anchor"],
                    status=status,
                    matched_kill_feed=feed,
                    notes=[
                        f"kill_feed_expected_score_side:{side}",
                        f"anchored_before_time:{b.timestamp_sec:.3f}",
                        f"anchored_after_time:{a.timestamp_sec:.3f}",
                    ] + ([f"abnormal jump: +{diff}"] if diff > self.max_valid_jump else []),
                )
            )
        events.sort(key=lambda e: e.time)
        return events

    def aggregate(
        self,
        frame_readings: list[FrameScoreReading],
        kill_feed_events: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        stable_series = self.build_stable_series(frame_readings)
        events = self.detect_count_changes(stable_series, kill_feed_events=kill_feed_events)
        anchored = self.detect_kill_feed_anchored_changes(stable_series, kill_feed_events or [])
        existing = {(e.side, round(e.time, 2)) for e in events}
        for ev in anchored:
            if (ev.side, round(ev.time, 2)) not in existing:
                events.append(ev)
        events.sort(key=lambda e: e.time)
        return {
            "params": {
                "window_sec": self.window_sec,
                "min_read_confidence": self.min_read_confidence,
                "min_votes": self.min_votes,
                "max_valid_jump": self.max_valid_jump,
                "min_event_gap_sec": self.min_event_gap_sec,
                "kill_feed_match_window_sec": self.kill_feed_match_window_sec,
            },
            "stable_series": [asdict(p) for p in stable_series],
            "events": [asdict(e) for e in events],
        }


def frame_readings_to_dict(frame_readings: list[FrameScoreReading]) -> list[dict[str, Any]]:
    return [asdict(fr) for fr in frame_readings]


def frame_readings_from_dict(items: Iterable[dict[str, Any]]) -> list[FrameScoreReading]:
    out: list[FrameScoreReading] = []
    for item in items:
        readings = {
            side: ScoreSideReading(
                side=r.get("side", side),
                value=r.get("value"),
                confidence=float(r.get("confidence", 0.0)),
                raw_text=r.get("raw_text", ""),
                digit_candidates=[DigitCandidate(**c) for c in r.get("digit_candidates", [])],
                status=r.get("status", "parse_failed"),
            )
            for side, r in item.get("readings", {}).items()
        }
        out.append(
            FrameScoreReading(
                frame_index=int(item.get("frame_index", -1)),
                timestamp_sec=float(item.get("timestamp_sec", 0.0)),
                crop_path=item.get("crop_path"),
                readings=readings,
            )
        )
    return out
