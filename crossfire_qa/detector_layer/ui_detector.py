"""
1. Fixed ROI 기반 UI crop
2. 1920x1080 기준 해상도 정규화
3. minimap / top_score_bar / weapon_ammo_area anchor 기반 ROI 보정
4. 선택적 template matching 기반 confidence 산출
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


# Base CrossFire HUD ROI layout. Coordinates are defined on 1920x1080 frames.
DEFAULT_BASE_RESOLUTION = (1920, 1080)  # width, height
DEFAULT_ROIS: dict[str, dict[str, int]] = {
    "top_score_bar": {"x": 780, "y": 0, "w": 390, "h": 68},
    "kill_feed_area": {"x": 1320, "y": 53, "w": 585, "h": 135},
    "kill_medal_area": {"x": 645, "y": 645, "w": 630, "h": 255},
    "death_killer_panel": {"x": 1598, "y": 450, "w": 315, "h": 600},
    "minimap": {"x": 0, "y": 30, "w": 225, "h": 225},
    "location_text": {"x": 53, "y": 218, "w": 195, "h": 53},
    "hp_ac_area": {"x": 0, "y": 975, "w": 285, "h": 105},
    "weapon_ammo_area": {"x": 1515, "y": 915, "w": 405, "h": 165},
    "crosshair": {"x": 915, "y": 495, "w": 90, "h": 90},
}

# Anchors are stable UI elements used to estimate global HUD shift.
# If a corresponding template exists, detector searches near this ROI.
DEFAULT_ANCHORS: dict[str, dict[str, Any]] = {
    "minimap": {"roi_name": "minimap", "search_padding": 68, "threshold": 0.55, "weight": 1.0},
    "top_score_bar": {"roi_name": "top_score_bar", "search_padding": 90, "threshold": 0.55, "weight": 1.0},
    "weapon_ammo_area": {"roi_name": "weapon_ammo_area", "search_padding": 105, "threshold": 0.50, "weight": 0.7},
}


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

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2.0, self.y + self.h / 2.0

    def clip(self, width: int, height: int) -> "Box":
        x1 = max(0, min(self.x, width - 1))
        y1 = max(0, min(self.y, height - 1))
        x2 = max(x1 + 1, min(self.x2, width))
        y2 = max(y1 + 1, min(self.y2, height))
        return Box(x1, y1, x2 - x1, y2 - y1)

    def expand(self, padding: int, width: int, height: int) -> "Box":
        return Box(
            self.x - padding,
            self.y - padding,
            self.w + 2 * padding,
            self.h + 2 * padding,
        ).clip(width, height)

    def shifted(self, dx: int, dy: int) -> "Box":
        return Box(self.x + dx, self.y + dy, self.w, self.h)

    def to_list(self) -> list[int]:
        return [self.x, self.y, self.w, self.h]


@dataclass
class TemplateMatch:
    name: str
    score: float
    bbox: list[int]
    method: str = "TM_CCOEFF_NORMED"


@dataclass
class AnchorCorrection:
    dx: int = 0
    dy: int = 0
    confidence: float = 0.0
    used_anchors: list[TemplateMatch] = field(default_factory=list)
    status: str = "not_applied"


@dataclass
class UIRegion:
    name: str
    bbox_base: list[int]
    bbox_corrected: list[int]
    bbox_original: list[int]
    confidence: float
    crop_path: Optional[str] = None
    template_match: Optional[TemplateMatch] = None
    status: str = "fixed_roi"


@dataclass
class UIDetectionResult:
    frame_index: Optional[int]
    timestamp_sec: Optional[float]
    original_resolution: tuple[int, int]
    processing_resolution: tuple[int, int]
    normalized_to_base: bool
    anchor_correction: AnchorCorrection
    regions: dict[str, UIRegion]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TemplateLibrary:
    """
    Loads optional templates from a directory.

    Supported layouts:
      templates/minimap.png
      templates/top_score_bar/*.png
      templates/region/top_score_bar.png
      templates/anchor/minimap.png

    The same get(name) call looks in all of these locations.
    """

    def __init__(self, template_dir: Optional[str | Path] = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else None
        self.templates: dict[str, list[tuple[str, np.ndarray]]] = {}
        if self.template_dir and self.template_dir.exists():
            self._load()

    def _load_image(self, path: Path) -> Optional[np.ndarray]:
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        return img if img is not None and img.size > 0 else None

    def _register(self, key: str, path: Path) -> None:
        img = self._load_image(path)
        if img is not None:
            self.templates.setdefault(key, []).append((path.stem, img))

    def _load(self) -> None:
        assert self.template_dir is not None
        image_exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

        # Root-level: templates/minimap.png -> key minimap
        for p in self.template_dir.iterdir():
            if p.is_file() and p.suffix.lower() in image_exts:
                self._register(p.stem, p)

        # Directory-level: templates/minimap/*.png -> key minimap
        for d in self.template_dir.iterdir():
            if d.is_dir():
                if d.name in {"anchor", "anchors", "region", "regions"}:
                    for p in d.iterdir():
                        if p.is_file() and p.suffix.lower() in image_exts:
                            self._register(p.stem, p)
                else:
                    for p in d.iterdir():
                        if p.is_file() and p.suffix.lower() in image_exts:
                            self._register(d.name, p)

    def get(self, key: str) -> list[tuple[str, np.ndarray]]:
        return self.templates.get(key, [])

    def available_keys(self) -> list[str]:
        return sorted(self.templates.keys())


class CrossFireUIDetector:
    """
    Fixed ROI + resolution normalization + anchor correction + template matching.

    Important behavior:
    - If normalize_to_base=True, input frames are resized to base_resolution first.
      All detection/crops are performed on the normalized frame.
    - bbox_original maps the normalized bbox back to the input frame coordinate system.
    - If no templates are provided, detector still returns fixed ROI crops.
    """

    def __init__(
        self,
        base_resolution: tuple[int, int] = DEFAULT_BASE_RESOLUTION,
        roi_config: Optional[dict[str, dict[str, int]]] = None,
        anchor_config: Optional[dict[str, dict[str, Any]]] = None,
        template_dir: Optional[str | Path] = None,
        normalize_to_base: bool = True,
        apply_anchor_correction: bool = True,
        verify_region_templates: bool = True,
    ) -> None:
        self.base_resolution = base_resolution
        self.roi_config = roi_config or DEFAULT_ROIS
        self.anchor_config = anchor_config or DEFAULT_ANCHORS
        self.template_lib = TemplateLibrary(template_dir)
        self.normalize_to_base = normalize_to_base
        self.apply_anchor_correction = apply_anchor_correction
        self.verify_region_templates = verify_region_templates

    @classmethod
    def from_json(cls, config_path: str | Path, **kwargs: Any) -> "CrossFireUIDetector":
        with Path(config_path).open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        base_resolution = tuple(cfg.get("base_resolution", DEFAULT_BASE_RESOLUTION))
        return cls(
            base_resolution=base_resolution,  # type: ignore[arg-type]
            roi_config=cfg.get("rois", DEFAULT_ROIS),
            anchor_config=cfg.get("anchors", DEFAULT_ANCHORS),
            **kwargs,
        )

    def _normalize_frame(self, frame_bgr: np.ndarray) -> tuple[np.ndarray, bool]:
        base_w, base_h = self.base_resolution
        h, w = frame_bgr.shape[:2]
        if self.normalize_to_base and (w, h) != (base_w, base_h):
            return cv2.resize(frame_bgr, (base_w, base_h), interpolation=cv2.INTER_AREA), True
        return frame_bgr, False

    def _base_box(self, name: str) -> Box:
        r = self.roi_config[name]
        return Box(int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"]))

    def _map_base_to_original(self, box: Box, original_w: int, original_h: int) -> Box:
        base_w, base_h = self.base_resolution
        sx = original_w / base_w
        sy = original_h / base_h
        return Box(
            int(round(box.x * sx)),
            int(round(box.y * sy)),
            int(round(box.w * sx)),
            int(round(box.h * sy)),
        ).clip(original_w, original_h)

    @staticmethod
    def _crop(frame: np.ndarray, box: Box) -> np.ndarray:
        return frame[box.y:box.y2, box.x:box.x2]

    @staticmethod
    def _match_template(
        search_img_bgr: np.ndarray,
        template_gray: np.ndarray,
        name: str,
    ) -> Optional[TemplateMatch]:
        if search_img_bgr.size == 0:
            return None

        search_gray = cv2.cvtColor(search_img_bgr, cv2.COLOR_BGR2GRAY)
        th, tw = template_gray.shape[:2]
        sh, sw = search_gray.shape[:2]
        if th > sh or tw > sw:
            return None

        # Normalize contrast lightly to reduce brightness sensitivity.
        search_gray = cv2.equalizeHist(search_gray)
        template_gray = cv2.equalizeHist(template_gray)

        result = cv2.matchTemplate(search_gray, template_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        x, y = max_loc
        return TemplateMatch(
            name=name,
            score=float(max_val),
            bbox=[int(x), int(y), int(tw), int(th)],
        )

    def _estimate_anchor_correction(self, frame_base: np.ndarray) -> AnchorCorrection:
        if not self.apply_anchor_correction:
            return AnchorCorrection(status="disabled")

        frame_h, frame_w = frame_base.shape[:2]
        dx_values: list[float] = []
        dy_values: list[float] = []
        weights: list[float] = []
        used: list[TemplateMatch] = []

        for anchor_name, cfg in self.anchor_config.items():
            roi_name = cfg.get("roi_name", anchor_name)
            if roi_name not in self.roi_config:
                continue
            templates = self.template_lib.get(anchor_name) or self.template_lib.get(roi_name)
            if not templates:
                continue

            expected_box = self._base_box(roi_name).clip(frame_w, frame_h)
            search_box = expected_box.expand(int(cfg.get("search_padding", 50)), frame_w, frame_h)
            search_crop = self._crop(frame_base, search_box)

            best: Optional[TemplateMatch] = None
            for tpl_name, tpl_gray in templates:
                match = self._match_template(search_crop, tpl_gray, name=f"{anchor_name}:{tpl_name}")
                if match is None:
                    continue
                if best is None or match.score > best.score:
                    best = match

            if best is None:
                continue

            threshold = float(cfg.get("threshold", 0.55))
            if best.score < threshold:
                continue

            # Convert matched bbox from search-crop coordinates to base-frame coordinates.
            bx, by, bw, bh = best.bbox
            matched_box = Box(search_box.x + bx, search_box.y + by, bw, bh)
            best.bbox = matched_box.to_list()

            # For a full-ROI template, expected top-left is ROI top-left.
            # For a smaller template, place its expected top-left around ROI center.
            expected_tpl_x = expected_box.x + int(round((expected_box.w - bw) / 2.0))
            expected_tpl_y = expected_box.y + int(round((expected_box.h - bh) / 2.0))
            dx = matched_box.x - expected_tpl_x
            dy = matched_box.y - expected_tpl_y

            weight = float(cfg.get("weight", 1.0)) * max(0.0, min(1.0, best.score))
            dx_values.append(dx)
            dy_values.append(dy)
            weights.append(weight)
            used.append(best)

        if not used:
            return AnchorCorrection(status="no_template_or_low_score")

        # Weighted average is stable enough for a first MVP. Median can be used later.
        dx = int(round(np.average(dx_values, weights=weights)))
        dy = int(round(np.average(dy_values, weights=weights)))
        confidence = float(np.average([m.score for m in used], weights=weights))
        return AnchorCorrection(dx=dx, dy=dy, confidence=confidence, used_anchors=used, status="applied")

    def _verify_region_template(self, frame_base: np.ndarray, region_name: str, box: Box) -> tuple[float, Optional[TemplateMatch]]:
        templates = self.template_lib.get(region_name)
        if not self.verify_region_templates or not templates:
            return 0.0, None

        crop = self._crop(frame_base, box)
        best: Optional[TemplateMatch] = None
        for tpl_name, tpl_gray in templates:
            match = self._match_template(crop, tpl_gray, name=f"{region_name}:{tpl_name}")
            if match is None:
                continue
            if best is None or match.score > best.score:
                best = match

        if best is None:
            return 0.0, None
        return best.score, best

    def detect(
        self,
        frame_bgr: np.ndarray,
        frame_index: Optional[int] = None,
        timestamp_sec: Optional[float] = None,
    ) -> UIDetectionResult:
        original_h, original_w = frame_bgr.shape[:2]
        frame_base, normalized = self._normalize_frame(frame_bgr)
        base_h, base_w = frame_base.shape[:2]

        anchor_correction = self._estimate_anchor_correction(frame_base)
        dx = anchor_correction.dx if anchor_correction.status == "applied" else 0
        dy = anchor_correction.dy if anchor_correction.status == "applied" else 0

        regions: dict[str, UIRegion] = {}
        for name in self.roi_config.keys():
            base_box = self._base_box(name).clip(base_w, base_h)
            corrected_box = base_box.shifted(dx, dy).clip(base_w, base_h)
            original_box = self._map_base_to_original(corrected_box, original_w, original_h)

            template_score, template_match = self._verify_region_template(frame_base, name, corrected_box)
            if template_match is not None:
                confidence = max(0.50, template_score)
                status = "template_verified"
            elif anchor_correction.status == "applied":
                confidence = max(0.50, min(0.90, anchor_correction.confidence))
                status = "anchor_corrected"
            else:
                confidence = 0.70
                status = "fixed_roi"

            regions[name] = UIRegion(
                name=name,
                bbox_base=base_box.to_list(),
                bbox_corrected=corrected_box.to_list(),
                bbox_original=original_box.to_list(),
                confidence=float(confidence),
                template_match=template_match,
                status=status,
            )

        return UIDetectionResult(
            frame_index=frame_index,
            timestamp_sec=timestamp_sec,
            original_resolution=(original_w, original_h),
            processing_resolution=(base_w, base_h),
            normalized_to_base=normalized,
            anchor_correction=anchor_correction,
            regions=regions,
        )

    def crop_regions(self, frame_bgr: np.ndarray, result: UIDetectionResult) -> dict[str, np.ndarray]:
        """
        Returns crops in normalized/base coordinates, which are ideal for OCR/classifiers.
        """
        frame_base, _ = self._normalize_frame(frame_bgr)
        crops: dict[str, np.ndarray] = {}
        for name, region in result.regions.items():
            box = Box(*region.bbox_corrected)
            crops[name] = self._crop(frame_base, box)
        return crops

    @staticmethod
    def draw_overlay(frame_bgr: np.ndarray, result: UIDetectionResult, draw_original_coords: bool = True) -> np.ndarray:
        overlay = frame_bgr.copy()
        h, w = overlay.shape[:2]
        for name, region in result.regions.items():
            if draw_original_coords:
                box = Box(*region.bbox_original).clip(w, h)
            else:
                box = Box(*region.bbox_corrected).clip(w, h)

            cv2.rectangle(overlay, (box.x, box.y), (box.x2, box.y2), (0, 255, 0), 2)
            label = f"{name} {region.confidence:.2f}"
            cv2.putText(
                overlay,
                label,
                (box.x, max(15, box.y - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )
        return overlay


def save_detection_artifacts(
    frame_bgr: np.ndarray,
    detector: CrossFireUIDetector,
    result: UIDetectionResult,
    output_dir: str | Path,
    frame_stem: str,
    save_crops: bool = True,
    save_overlay: bool = True,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if save_overlay:
        overlay_dir = output_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        overlay = detector.draw_overlay(frame_bgr, result, draw_original_coords=True)
        cv2.imwrite(str(overlay_dir / f"{frame_stem}_overlay.jpg"), overlay)

    if save_crops:
        crop_root = output_dir / "crops" / frame_stem
        crop_root.mkdir(parents=True, exist_ok=True)
        crops = detector.crop_regions(frame_bgr, result)
        for name, crop in crops.items():
            crop_path = crop_root / f"{name}.jpg"
            cv2.imwrite(str(crop_path), crop)
            result.regions[name].crop_path = str(crop_path)
