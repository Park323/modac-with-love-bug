"""
Pass 1:
2~5 fps 샘플링
이벤트 후보 구간 탐색

Pass 2:
후보 이벤트 전후 ±2초 구간만 15~30 fps 재분석
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterator, Optional, Literal

import cv2
import numpy as np

ColorFormat = Literal["bgr", "rgb"]


@dataclass
class VideoInfo:
    video_path: str
    width: int
    height: int
    fps: float
    frame_count: int
    duration_sec: float


@dataclass
class FramePacket:
    frame: np.ndarray
    frame_index: int
    timestamp_sec: float
    source_fps: float
    width: int
    height: int


@dataclass
class SavedFrameRecord:
    frame_index: int
    timestamp_sec: float
    image_path: str
    width: int
    height: int


class VideoOpenError(RuntimeError):
    pass


class MP4FrameSampler:
    """
    MP4 decoder + frame sampler.

    This module intentionally stays independent from UI detection.
    Downstream modules consume FramePacket.frame and metadata.
    """

    def __init__(
        self,
        video_path: str | Path,
        sample_fps: Optional[float] = 5.0,
        resize_to: Optional[tuple[int, int]] = None,
        color_format: ColorFormat = "bgr",
    ) -> None:
        self.video_path = Path(video_path)
        self.sample_fps = sample_fps
        self.resize_to = resize_to
        self.color_format = color_format

        if not self.video_path.exists():
            raise FileNotFoundError(f"Video not found: {self.video_path}")

        self._cap = cv2.VideoCapture(str(self.video_path))
        if not self._cap.isOpened():
            raise VideoOpenError(f"Failed to open video: {self.video_path}")

        self.info = self._read_video_info()

    def _read_video_info(self) -> VideoInfo:
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(self._cap.get(cv2.CAP_PROP_FPS))
        frame_count = int(self._cap.get(cv2.CAP_PROP_FRAME_COUNT))

        if fps <= 0:
            fps = 30.0

        duration_sec = frame_count / fps if frame_count > 0 else 0.0
        return VideoInfo(
            video_path=str(self.video_path),
            width=width,
            height=height,
            fps=fps,
            frame_count=frame_count,
            duration_sec=duration_sec,
        )

    def iter_frames(self) -> Iterator[FramePacket]:
        source_fps = self.info.fps
        if self.sample_fps is None or self.sample_fps <= 0:
            sample_interval = 1
        else:
            sample_interval = max(1, round(source_fps / self.sample_fps))

        frame_index = 0
        while True:
            ok, frame = self._cap.read()
            if not ok:
                break

            if frame_index % sample_interval == 0:
                timestamp_sec = frame_index / source_fps

                if self.resize_to is not None:
                    target_w, target_h = self.resize_to
                    frame = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

                if self.color_format == "rgb":
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                height, width = frame.shape[:2]
                yield FramePacket(
                    frame=frame,
                    frame_index=frame_index,
                    timestamp_sec=timestamp_sec,
                    source_fps=source_fps,
                    width=width,
                    height=height,
                )

            frame_index += 1

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()

    def __enter__(self) -> "MP4FrameSampler":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def save_sampled_frames(
    video_path: str | Path,
    output_dir: str | Path,
    sample_fps: float = 5.0,
    resize_to: Optional[tuple[int, int]] = None,
    image_ext: str = "jpg",
    jpeg_quality: int = 95,
) -> dict:
    output_dir = Path(output_dir)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    records: list[SavedFrameRecord] = []
    with MP4FrameSampler(
        video_path=video_path,
        sample_fps=sample_fps,
        resize_to=resize_to,
        color_format="bgr",
    ) as sampler:
        video_info = sampler.info
        for packet in sampler.iter_frames():
            stem = f"frame_{packet.frame_index:06d}_t{packet.timestamp_sec:08.3f}"
            image_path = frames_dir / f"{stem}.{image_ext}"
            if image_ext.lower() in {"jpg", "jpeg"}:
                cv2.imwrite(str(image_path), packet.frame, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
            else:
                cv2.imwrite(str(image_path), packet.frame)
            records.append(
                SavedFrameRecord(
                    frame_index=packet.frame_index,
                    timestamp_sec=packet.timestamp_sec,
                    image_path=str(image_path),
                    width=packet.width,
                    height=packet.height,
                )
            )

    metadata = {
        "video_info": asdict(video_info),
        "sampling": {
            "sample_fps": sample_fps,
            "resize_to": resize_to,
            "image_ext": image_ext,
        },
        "num_saved_frames": len(records),
        "frames": [asdict(r) for r in records],
    }
    metadata_path = output_dir / "metadata.json"
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    return metadata


def parse_resize(value: Optional[str]) -> Optional[tuple[int, int]]:
    if value is None:
        return None
    value = value.lower().strip()
    if "x" not in value:
        raise ValueError("resize format must be like 1280x720")
    w_str, h_str = value.split("x", maxsplit=1)
    return int(w_str), int(h_str)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--sample-fps", type=float, default=5.0)
    parser.add_argument("--resize", type=str, default=None, help="e.g. 1280x720")
    parser.add_argument("--ext", type=str, default="jpg", choices=["jpg", "png"])
    args = parser.parse_args()

    metadata = save_sampled_frames(
        video_path=args.video,
        output_dir=args.out,
        sample_fps=args.sample_fps,
        resize_to=parse_resize(args.resize),
        image_ext=args.ext,
    )
    print(json.dumps({
        "video_info": metadata["video_info"],
        "num_saved_frames": metadata["num_saved_frames"],
        "metadata_path": str(Path(args.out) / "metadata.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
