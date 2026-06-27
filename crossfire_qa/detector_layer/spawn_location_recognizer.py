"""
Small image similarity

입력 frame과 reference frame
→ 192x108로 resize
→ grayscale 변환
→ histogram equalization
→ MSE similarity 계산
→ histogram correlation 계산
→ Canny edge similarity 계산
→ 세 점수를 weighted sum

ORB feature similarity (구조적 특징점)

frame과 reference를 적당히 축소
→ grayscale 변환
→ ORB keypoint/descriptor 추출
→ BFMatcher로 descriptor matching
→ distance가 충분히 낮은 good match 개수 계산
→ similarity로 변환

향후 고도화 방식

DINO / CLIP / MobileNet embedding similarity

"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Optional

import cv2
import numpy as np


DEFAULT_SPAWN_LOCATION_CONFIG: dict[str, Any] = {
    "allowed_locations": [
        "BL Base",
        "BL Deck",
        "GR Base",
        "GR Deck",
        "A Site",
        "B Site",
        "Mid",
    ],
    "expected_spawn": None,
    "sample_offsets_after_respawn_sec": [0.0, 0.4, 0.8],
    "ocr": {
        "backend": "easyocr",  # easyocr or none
        "allowlist": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_",
        "min_confidence": 0.25,
        "fuzzy_match_threshold": 0.55,
    },
    "thresholds": {
        "pass_threshold": 0.75,
        "fail_threshold": 0.45,
        "conflict_margin": 0.20,
        "min_signal_confidence": 0.25,
        "minimap_match_threshold": 0.50,
        "visual_match_threshold": 0.45,
    },
    "weights": {
        "location_text_match": 0.40,
        "minimap_position_match": 0.20,
        "visual_spawn_similarity": 0.30,
        "respawn_state_confidence": 0.10,
    },
    "reference": {
        "minimap_subdirs": ["minimap", "map", "minimap_refs"],
        "visual_subdirs": ["visual", "frames", "full_frame", "spawn_view"],
        "image_exts": [".png", ".jpg", ".jpeg", ".bmp", ".webp"],
    },
}


@dataclass
class LocationTextReading:
    raw_text: str
    normalized_text: str
    detected_location: Optional[str]
    confidence: float
    fuzzy_score: float
    status: str
    method: str = "easyocr"
    notes: list[str] = field(default_factory=list)


@dataclass
class ReferenceMatch:
    detected_spawn: Optional[str]
    confidence: float
    similarity: float
    reference_path: Optional[str]
    method: str
    status: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class SpawnFrameEvidence:
    frame_index: int
    timestamp_sec: float
    respawn_time: float
    offset_sec: float
    crop_paths: dict[str, str]
    location_text: LocationTextReading
    minimap_match: ReferenceMatch
    visual_match: ReferenceMatch
    respawn_confidence: float
    frame_score: float
    detected_spawn: Optional[str]
    status: str
    notes: list[str] = field(default_factory=list)


@dataclass
class SpawnLocationEvent:
    event: str
    respawn_time: float
    expected_spawn: Optional[str]
    detected_spawn: Optional[str]
    result: str
    status: str
    confidence: float
    final_spawn_score: float
    component_scores: dict[str, float]
    source: list[str]
    evidence_frames: list[SpawnFrameEvidence]
    notes: list[str] = field(default_factory=list)


class OptionalEasyOCRTextReader:
    """
    Optional EasyOCR adapter for location text under the minimap.

    This class is functional when easyocr is installed. If not installed, it
    returns ocr_unavailable instead of crashing the QA pipeline.
    """

    def __init__(
        self,
        allowlist: str = "",
        languages: Optional[list[str]] = None,
        model_storage_directory: Optional[str | Path] = None,
    ) -> None:
        self.reader = None
        self.error = ""
        self.allowlist = allowlist
        try:
            import easyocr  # type: ignore
        except Exception as exc:  # pragma: no cover
            self.error = str(exc)
            return
        try:
            kwargs = {}
            if model_storage_directory:
                model_dir = Path(model_storage_directory)
                model_dir.mkdir(parents=True, exist_ok=True)
                user_network_dir = model_dir / "user_network"
                user_network_dir.mkdir(parents=True, exist_ok=True)
                kwargs["model_storage_directory"] = str(model_dir)
                kwargs["user_network_directory"] = str(user_network_dir)
            self.reader = easyocr.Reader(languages or ["en"], gpu=False, verbose=False, **kwargs)
        except Exception as exc:  # pragma: no cover
            self.reader = None
            self.error = str(exc)

    @property
    def available(self) -> bool:
        return self.reader is not None

    def read_text(self, crop_bgr: np.ndarray) -> tuple[str, float, str]:
        if self.reader is None:
            return "", 0.0, "ocr_unavailable"
        if crop_bgr is None or crop_bgr.size == 0:
            return "", 0.0, "empty_crop"

        # Upscale tiny HUD text; OCR is much more stable after this.
        h, w = crop_bgr.shape[:2]
        scale = 3 if max(h, w) < 180 else 2
        up = cv2.resize(crop_bgr, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
        up = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        kwargs = {"detail": 1, "paragraph": False}
        if self.allowlist:
            kwargs["allowlist"] = self.allowlist
        try:
            result = self.reader.readtext(up, **kwargs)
        except Exception as exc:  # pragma: no cover
            return "", 0.0, f"ocr_error:{exc}"

        if not result:
            return "", 0.0, "parse_failed"

        best_text = ""
        best_conf = 0.0
        for _, text, conf in result:
            clean = str(text).strip()
            if clean and float(conf) > best_conf:
                best_text = clean
                best_conf = float(conf)
        if not best_text:
            return "", best_conf, "parse_failed"
        return best_text, best_conf, "ok"


class SpawnReferenceLibrary:
    """
    Loads reference images for spawn matching.

    Supported layouts:
      spawn_references/BL_Base/visual/*.jpg
      spawn_references/BL_Base/minimap/*.jpg
      spawn_references/BL_Base/*.jpg            # treated as visual reference

    Folder names are normalized, so BL_Base, BL Base, and bl-base can all map
    to the display name "BL Base" when compared by SpawnLocationRecognizer.
    """

    def __init__(self, root_dir: Optional[str | Path] = None, config: Optional[dict[str, Any]] = None) -> None:
        self.root_dir = Path(root_dir) if root_dir else None
        self.config = config or DEFAULT_SPAWN_LOCATION_CONFIG["reference"]
        self.visual_refs: dict[str, list[tuple[str, np.ndarray]]] = {}
        self.minimap_refs: dict[str, list[tuple[str, np.ndarray]]] = {}
        if self.root_dir and self.root_dir.exists():
            self._load()

    @staticmethod
    def normalize_spawn_name(name: str) -> str:
        name = name.strip().replace("_", " ").replace("-", " ")
        name = re.sub(r"\s+", " ", name)
        return name.title().replace("Bl ", "BL ").replace("Gr ", "GR ")

    def _read_image(self, path: Path) -> Optional[np.ndarray]:
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            return None
        return img

    def _image_files(self, directory: Path) -> list[Path]:
        exts = set(self.config.get("image_exts", [".png", ".jpg", ".jpeg", ".bmp", ".webp"]))
        if not directory.exists() or not directory.is_dir():
            return []
        return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in exts)

    def _register_many(self, spawn_name: str, target: dict[str, list[tuple[str, np.ndarray]]], files: list[Path]) -> None:
        for p in files:
            img = self._read_image(p)
            if img is not None:
                target.setdefault(spawn_name, []).append((str(p), img))

    def _load(self) -> None:
        assert self.root_dir is not None
        minimap_subdirs = set(self.config.get("minimap_subdirs", ["minimap"]))
        visual_subdirs = set(self.config.get("visual_subdirs", ["visual", "frames"]))

        for spawn_dir in sorted(p for p in self.root_dir.iterdir() if p.is_dir()):
            spawn_name = self.normalize_spawn_name(spawn_dir.name)

            # Direct images below spawn_dir are considered visual references.
            self._register_many(spawn_name, self.visual_refs, self._image_files(spawn_dir))

            for sub in sorted(p for p in spawn_dir.iterdir() if p.is_dir()):
                key = sub.name.strip().lower()
                files = self._image_files(sub)
                if key in minimap_subdirs:
                    self._register_many(spawn_name, self.minimap_refs, files)
                elif key in visual_subdirs:
                    self._register_many(spawn_name, self.visual_refs, files)

    def available_spawns(self) -> list[str]:
        return sorted(set(self.visual_refs.keys()) | set(self.minimap_refs.keys()))

    def get_visual(self) -> dict[str, list[tuple[str, np.ndarray]]]:
        return self.visual_refs

    def get_minimap(self) -> dict[str, list[tuple[str, np.ndarray]]]:
        return self.minimap_refs


class SpawnLocationRecognizer:
    """
    Recognizes whether a respawn happened at the expected CrossFire location.

    Signals:
      1. location_text OCR + dictionary matching
      2. minimap crop reference similarity
      3. full-frame visual spawn reference similarity
      4. respawn segment confidence

    It intentionally returns UNCERTAIN when evidence is insufficient.
    """

    def __init__(
        self,
        reference_dir: Optional[str | Path] = None,
        config: Optional[dict[str, Any]] = None,
        use_easyocr: Optional[bool] = None,
        easyocr_model_dir: Optional[str | Path] = None,
    ) -> None:
        cfg = json.loads(json.dumps(DEFAULT_SPAWN_LOCATION_CONFIG))
        if config:
            for key, value in config.items():
                if isinstance(value, dict) and isinstance(cfg.get(key), dict):
                    cfg[key].update(value)
                else:
                    cfg[key] = value
        self.config = cfg
        self.allowed_locations = [str(x) for x in cfg.get("allowed_locations", [])]
        self.thresholds = cfg.get("thresholds", {})
        self.weights = cfg.get("weights", {})
        self.reference_lib = SpawnReferenceLibrary(reference_dir, cfg.get("reference", {}))
        ocr_backend = str(cfg.get("ocr", {}).get("backend", "easyocr"))
        if use_easyocr is False or ocr_backend == "none":
            self.ocr_reader: Optional[OptionalEasyOCRTextReader] = None
        else:
            self.ocr_reader = OptionalEasyOCRTextReader(
                allowlist=str(cfg.get("ocr", {}).get("allowlist", "")),
                model_storage_directory=easyocr_model_dir,
            )

    @staticmethod
    def _clamp01(value: float) -> float:
        if value != value or value in {float("inf"), float("-inf")}:
            return 0.0
        return float(max(0.0, min(1.0, value)))

    @staticmethod
    def normalize_text(text: str) -> str:
        text = text.strip()
        text = re.sub(r"[^A-Za-z0-9 ]+", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text.title().replace("Bl ", "BL ").replace("Gr ", "GR ")

    def _best_location_match(self, text: str) -> tuple[Optional[str], float]:
        if not text:
            return None, 0.0
        norm = self.normalize_text(text)
        best_loc: Optional[str] = None
        best_score = 0.0
        for loc in self.allowed_locations:
            loc_norm = self.normalize_text(loc)
            seq = SequenceMatcher(None, norm.lower(), loc_norm.lower()).ratio()
            contains = 1.0 if loc_norm.lower() in norm.lower() or norm.lower() in loc_norm.lower() else 0.0
            score = max(seq, contains)
            if score > best_score:
                best_score = score
                best_loc = loc
        return best_loc, float(best_score)

    def read_location_text(self, location_crop_bgr: Optional[np.ndarray]) -> LocationTextReading:
        if location_crop_bgr is None or location_crop_bgr.size == 0:
            return LocationTextReading("", "", None, 0.0, 0.0, "missing_crop", "none")

        if self.ocr_reader is None:
            return LocationTextReading("", "", None, 0.0, 0.0, "ocr_disabled", "none")
        raw, ocr_conf, status = self.ocr_reader.read_text(location_crop_bgr)
        norm = self.normalize_text(raw)
        best_loc, fuzzy = self._best_location_match(norm)
        min_ocr = float(self.config.get("ocr", {}).get("min_confidence", 0.25))
        min_fuzzy = float(self.config.get("ocr", {}).get("fuzzy_match_threshold", 0.55))

        if status != "ok":
            return LocationTextReading(raw, norm, None, 0.0, fuzzy, status, "easyocr")
        if ocr_conf < min_ocr:
            return LocationTextReading(raw, norm, best_loc, self._clamp01(ocr_conf * fuzzy), fuzzy, "low_ocr_confidence", "easyocr")
        if fuzzy < min_fuzzy:
            return LocationTextReading(raw, norm, best_loc, self._clamp01(ocr_conf * fuzzy), fuzzy, "dictionary_match_low", "easyocr")
        return LocationTextReading(raw, norm, best_loc, self._clamp01(0.55 * ocr_conf + 0.45 * fuzzy), fuzzy, "ok", "easyocr")

    @staticmethod
    def _resize_for_similarity(img: np.ndarray, size: tuple[int, int]) -> np.ndarray:
        return cv2.resize(img, size, interpolation=cv2.INTER_AREA)

    @staticmethod
    def _gray_equalized(img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img.copy()
        return cv2.equalizeHist(gray)

    @classmethod
    def _small_image_similarity(cls, img_a: np.ndarray, img_b: np.ndarray, size: tuple[int, int] = (128, 128)) -> float:
        if img_a is None or img_b is None or img_a.size == 0 or img_b.size == 0:
            return 0.0
        a = cls._gray_equalized(cls._resize_for_similarity(img_a, size))
        b = cls._gray_equalized(cls._resize_for_similarity(img_b, size))
        mse = float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))
        mse_score = max(0.0, 1.0 - mse / (255.0**2))

        hist_a = cv2.calcHist([a], [0], None, [32], [0, 256])
        hist_b = cv2.calcHist([b], [0], None, [32], [0, 256])
        hist_score = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_CORREL))
        hist_score = max(0.0, min(1.0, (hist_score + 1.0) / 2.0))

        edge_a = cv2.Canny(a, 60, 140)
        edge_b = cv2.Canny(b, 60, 140)
        edge_mse = float(np.mean((edge_a.astype(np.float32) - edge_b.astype(np.float32)) ** 2))
        edge_score = max(0.0, 1.0 - edge_mse / (255.0**2))
        return float(0.35 * mse_score + 0.35 * hist_score + 0.30 * edge_score)

    @staticmethod
    def _orb_similarity(img_a: np.ndarray, img_b: np.ndarray, max_dim: int = 480) -> float:
        if img_a is None or img_b is None or img_a.size == 0 or img_b.size == 0:
            return 0.0

        def shrink(img: np.ndarray) -> np.ndarray:
            h, w = img.shape[:2]
            scale = min(1.0, max_dim / max(h, w))
            if scale < 1.0:
                img = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
            return img

        a = shrink(img_a)
        b = shrink(img_b)
        gray_a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        gray_b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        orb = cv2.ORB_create(nfeatures=700)
        kp1, des1 = orb.detectAndCompute(gray_a, None)
        kp2, des2 = orb.detectAndCompute(gray_b, None)
        if des1 is None or des2 is None or len(kp1) < 8 or len(kp2) < 8:
            return 0.0
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        matches = matcher.match(des1, des2)
        if not matches:
            return 0.0
        matches = sorted(matches, key=lambda m: m.distance)
        good = [m for m in matches if m.distance <= 64]
        denom = max(1, min(len(kp1), len(kp2)))
        return float(max(0.0, min(1.0, len(good) / denom * 2.0)))

    @classmethod
    def _visual_similarity(cls, img_a: np.ndarray, img_b: np.ndarray) -> float:
        small = cls._small_image_similarity(img_a, img_b, size=(192, 108))
        orb = cls._orb_similarity(img_a, img_b)
        return float(0.55 * small + 0.45 * orb)

    def _best_reference_match(
        self,
        query_img: Optional[np.ndarray],
        references: dict[str, list[tuple[str, np.ndarray]]],
        method: str,
        threshold: float,
    ) -> ReferenceMatch:
        if query_img is None or query_img.size == 0:
            return ReferenceMatch(None, 0.0, 0.0, None, method, "missing_query")
        if not references:
            return ReferenceMatch(None, 0.0, 0.0, None, method, "no_references")

        candidates: list[dict[str, Any]] = []
        best_spawn: Optional[str] = None
        best_path: Optional[str] = None
        best_score = 0.0
        for spawn, refs in references.items():
            for path, ref_img in refs:
                if method == "minimap_reference_similarity":
                    score = self._small_image_similarity(query_img, ref_img, size=(128, 128))
                else:
                    score = self._visual_similarity(query_img, ref_img)
                candidates.append({"spawn": spawn, "reference_path": path, "similarity": float(score)})
                if score > best_score:
                    best_score = float(score)
                    best_spawn = spawn
                    best_path = path
        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        status = "ok" if best_score >= threshold else "low_similarity"
        return ReferenceMatch(
            detected_spawn=best_spawn,
            confidence=self._clamp01(best_score),
            similarity=self._clamp01(best_score),
            reference_path=best_path,
            method=method,
            status=status,
            candidates=candidates[:5],
        )

    def match_minimap(self, minimap_crop_bgr: Optional[np.ndarray]) -> ReferenceMatch:
        threshold = float(self.thresholds.get("minimap_match_threshold", 0.50))
        return self._best_reference_match(
            minimap_crop_bgr,
            self.reference_lib.get_minimap(),
            method="minimap_reference_similarity",
            threshold=threshold,
        )

    def match_visual_spawn(self, frame_bgr: Optional[np.ndarray]) -> ReferenceMatch:
        threshold = float(self.thresholds.get("visual_match_threshold", 0.45))
        return self._best_reference_match(
            frame_bgr,
            self.reference_lib.get_visual(),
            method="visual_reference_similarity",
            threshold=threshold,
        )

    def _score_for_expected(self, detected: Optional[str], confidence: float, expected: Optional[str]) -> float:
        if expected is None:
            return confidence if detected else 0.0
        if detected is None:
            return 0.0
        return confidence if self.normalize_text(detected) == self.normalize_text(expected) else 0.0

    def _vote_detected_spawn(self, evidences: list[SpawnFrameEvidence]) -> Optional[str]:
        votes: dict[str, float] = {}
        for ev in evidences:
            for spawn, conf in [
                (ev.location_text.detected_location, ev.location_text.confidence),
                (ev.minimap_match.detected_spawn, ev.minimap_match.confidence),
                (ev.visual_match.detected_spawn, ev.visual_match.confidence),
            ]:
                if not spawn:
                    continue
                votes[spawn] = votes.get(spawn, 0.0) + float(conf)
        if not votes:
            return None
        return max(votes.items(), key=lambda item: item[1])[0]

    def _frame_score(
        self,
        expected_spawn: Optional[str],
        location: LocationTextReading,
        minimap: ReferenceMatch,
        visual: ReferenceMatch,
        respawn_confidence: float,
    ) -> tuple[float, dict[str, float], list[str]]:
        notes: list[str] = []
        location_score = self._score_for_expected(location.detected_location, location.confidence, expected_spawn)
        minimap_score = self._score_for_expected(minimap.detected_spawn, minimap.confidence, expected_spawn)
        visual_score = self._score_for_expected(visual.detected_spawn, visual.confidence, expected_spawn)
        respawn_score = self._clamp01(respawn_confidence)

        if expected_spawn is not None:
            for label, detected, conf in [
                ("location_text", location.detected_location, location.confidence),
                ("minimap", minimap.detected_spawn, minimap.confidence),
                ("visual", visual.detected_spawn, visual.confidence),
            ]:
                if detected and self.normalize_text(detected) != self.normalize_text(expected_spawn) and conf >= float(self.thresholds.get("min_signal_confidence", 0.25)):
                    notes.append(f"{label}_conflict:{detected}!={expected_spawn}@{conf:.3f}")

        components = {
            "location_text_match": location_score,
            "minimap_position_match": minimap_score,
            "visual_spawn_similarity": visual_score,
            "respawn_state_confidence": respawn_score,
        }

        # Dynamic weight normalization: do not penalize a missing optional signal
        # such as EasyOCR or reference DB. Penalize only signals that are present
        # and disagree with expected_spawn by giving them a 0 score above.
        available = {
            "location_text_match": location.detected_location is not None and location.status not in {"ocr_disabled", "ocr_unavailable", "missing_crop", "parse_failed"},
            "minimap_position_match": minimap.detected_spawn is not None and minimap.status != "no_references",
            "visual_spawn_similarity": visual.detected_spawn is not None and visual.status != "no_references",
            "respawn_state_confidence": respawn_score > 0.0,
        }
        weighted_sum = 0.0
        weight_sum = 0.0
        for key, value in components.items():
            if not available.get(key, False):
                continue
            weight = float(self.weights.get(key, 0.0))
            weighted_sum += weight * float(value)
            weight_sum += weight
        score = weighted_sum / weight_sum if weight_sum > 0 else 0.0
        return self._clamp01(score), components, notes

    def analyze_respawn_frame(
        self,
        frame_index: int,
        timestamp_sec: float,
        respawn_time: float,
        crops: dict[str, np.ndarray],
        crop_paths: Optional[dict[str, str]] = None,
        frame_bgr: Optional[np.ndarray] = None,
        expected_spawn: Optional[str] = None,
        respawn_confidence: float = 0.0,
    ) -> SpawnFrameEvidence:
        location = self.read_location_text(crops.get("location_text"))
        minimap = self.match_minimap(crops.get("minimap"))
        visual = self.match_visual_spawn(frame_bgr)
        score, _, notes = self._frame_score(expected_spawn, location, minimap, visual, respawn_confidence)

        # Frame-level detected spawn is a weighted local vote.
        local_votes: dict[str, float] = {}
        for spawn, conf in [
            (location.detected_location, location.confidence * float(self.weights.get("location_text_match", 0.40))),
            (minimap.detected_spawn, minimap.confidence * float(self.weights.get("minimap_position_match", 0.20))),
            (visual.detected_spawn, visual.confidence * float(self.weights.get("visual_spawn_similarity", 0.30))),
        ]:
            if spawn:
                local_votes[spawn] = local_votes.get(spawn, 0.0) + conf
        detected = max(local_votes.items(), key=lambda x: x[1])[0] if local_votes else None
        status = "ok" if detected or score > 0 else "no_spawn_signal"
        return SpawnFrameEvidence(
            frame_index=frame_index,
            timestamp_sec=float(timestamp_sec),
            respawn_time=float(respawn_time),
            offset_sec=float(timestamp_sec - respawn_time),
            crop_paths=crop_paths or {},
            location_text=location,
            minimap_match=minimap,
            visual_match=visual,
            respawn_confidence=float(respawn_confidence),
            frame_score=score,
            detected_spawn=detected,
            status=status,
            notes=notes,
        )

    def aggregate_event(
        self,
        respawn_event: dict[str, Any],
        evidences: list[SpawnFrameEvidence],
        expected_spawn: Optional[str] = None,
    ) -> SpawnLocationEvent:
        respawn_time = float(respawn_event.get("respawn_time", 0.0) or 0.0)
        respawn_conf = float(respawn_event.get("confidence", 0.0) or 0.0)
        if expected_spawn is None:
            expected_spawn = self.config.get("expected_spawn")

        notes: list[str] = []
        if not evidences:
            return SpawnLocationEvent(
                event="spawn_location_check",
                respawn_time=respawn_time,
                expected_spawn=expected_spawn,
                detected_spawn=None,
                result="UNCERTAIN",
                status="NO_EVIDENCE",
                confidence=0.0,
                final_spawn_score=0.0,
                component_scores={
                    "location_text_match": 0.0,
                    "minimap_position_match": 0.0,
                    "visual_spawn_similarity": 0.0,
                    "respawn_state_confidence": respawn_conf,
                },
                source=[],
                evidence_frames=[],
                notes=["no_frames_near_respawn"],
            )

        # Average component scores across frames. Each frame has the same respawn confidence, but visual/location/minimap can vary.
        components_sum = {
            "location_text_match": 0.0,
            "minimap_position_match": 0.0,
            "visual_spawn_similarity": 0.0,
            "respawn_state_confidence": 0.0,
        }
        for ev in evidences:
            _, components, frame_notes = self._frame_score(expected_spawn, ev.location_text, ev.minimap_match, ev.visual_match, ev.respawn_confidence)
            for key, value in components.items():
                components_sum[key] += value
            notes.extend(frame_notes)
        n = len(evidences)
        component_scores = {k: self._clamp01(v / n) for k, v in components_sum.items()}

        # Use the average dynamically-normalized frame score as the final score.
        # This avoids treating unavailable OCR/reference signals as hard failures.
        final_score = self._clamp01(float(np.mean([ev.frame_score for ev in evidences])))
        detected_spawn = self._vote_detected_spawn(evidences)

        source: list[str] = []
        if any(ev.location_text.status == "ok" for ev in evidences):
            source.append("location_text_ocr")
        if any(ev.minimap_match.status == "ok" for ev in evidences):
            source.append("minimap_reference_match")
        if any(ev.visual_match.status == "ok" for ev in evidences):
            source.append("visual_reference_match")
        if respawn_conf > 0:
            source.append("respawn_segment_confidence")

        pass_th = float(self.thresholds.get("pass_threshold", 0.75))
        fail_th = float(self.thresholds.get("fail_threshold", 0.45))
        conflict_margin = float(self.thresholds.get("conflict_margin", 0.20))

        if expected_spawn is None:
            result = "OBSERVED"
            status = "NO_EXPECTED_SPAWN"
            confidence = max(final_score, max((ev.frame_score for ev in evidences), default=0.0))
            notes.append("expected_spawn_not_provided")
        else:
            normalized_expected = self.normalize_text(expected_spawn)
            normalized_detected = self.normalize_text(detected_spawn or "") if detected_spawn else None
            has_conflict = normalized_detected is not None and normalized_detected != normalized_expected
            strong_wrong_signal = has_conflict and any(
                (
                    (ev.location_text.detected_location and self.normalize_text(ev.location_text.detected_location) != normalized_expected and ev.location_text.confidence >= conflict_margin)
                    or (ev.minimap_match.detected_spawn and self.normalize_text(ev.minimap_match.detected_spawn) != normalized_expected and ev.minimap_match.confidence >= conflict_margin)
                    or (ev.visual_match.detected_spawn and self.normalize_text(ev.visual_match.detected_spawn) != normalized_expected and ev.visual_match.confidence >= conflict_margin)
                )
                for ev in evidences
            )
            if final_score >= pass_th and not strong_wrong_signal:
                result = "PASS"
                status = "CONFIRMED"
                confidence = final_score
            elif final_score <= fail_th and strong_wrong_signal:
                result = "FAIL"
                status = "CONFLICT"
                confidence = max(1.0 - final_score, 0.50)
            elif final_score <= fail_th and not detected_spawn:
                result = "UNCERTAIN"
                status = "INSUFFICIENT_SIGNAL"
                confidence = 1.0 - final_score
            elif final_score <= fail_th:
                result = "FAIL"
                status = "LOW_EXPECTED_MATCH"
                confidence = 1.0 - final_score
            else:
                result = "UNCERTAIN"
                status = "AMBIGUOUS"
                confidence = max(final_score, 1.0 - abs(final_score - 0.5))

        return SpawnLocationEvent(
            event="spawn_location_check",
            respawn_time=respawn_time,
            expected_spawn=expected_spawn,
            detected_spawn=detected_spawn,
            result=result,
            status=status,
            confidence=self._clamp01(confidence),
            final_spawn_score=final_score,
            component_scores=component_scores,
            source=source,
            evidence_frames=evidences,
            notes=sorted(set(notes)),
        )


def load_spawn_location_config(config_path: Optional[str | Path]) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_SPAWN_LOCATION_CONFIG))
    if not config_path:
        return cfg
    with Path(config_path).open("r", encoding="utf-8") as f:
        user_cfg = json.load(f)
    for key, value in user_cfg.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def spawn_location_event_to_dict(event: SpawnLocationEvent) -> dict[str, Any]:
    return asdict(event)
