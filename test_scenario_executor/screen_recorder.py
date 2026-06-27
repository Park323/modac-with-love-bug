"""Capture 30 FPS screenshots and a video file during a session."""

from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request

import cv2
import mss
import numpy as np


class ScreenRecorder:
    def __init__(
        self,
        output_root: str | Path = "test_scenario_executor_output",
        fps: float = 30.0,
        screenshot_callback_url: str | None = None,
    ) -> None:
        self.output_root = Path(output_root)
        self.fps = fps
        self.screenshot_callback_url = screenshot_callback_url
        self._running = False
        self._session_dir: Path | None = None
        self._screenshots_dir: Path | None = None
        self._video_path: Path | None = None
        self._manifest_path: Path | None = None
        self._frame_count = 0
        self._started_at = 0.0
        self._meta: dict[str, Any] = {}
        self._callback_queue: queue.Queue[Any] = queue.Queue(maxsize=300)
        self._callback_thread: threading.Thread | None = None

    @property
    def is_recording(self) -> bool:
        return self._running

    @property
    def locations(self) -> dict[str, str | None]:
        return {
            "session_dir": str(self._session_dir) if self._session_dir else None,
            "screenshots_dir": str(self._screenshots_dir) if self._screenshots_dir else None,
            "video_path": str(self._video_path) if self._video_path else None,
            "manifest_path": str(self._manifest_path) if self._manifest_path else None,
        }

    def prepare(self, session_id: str) -> dict[str, str | None]:
        safe_id = "".join(c if c.isalnum() or c in "-_." else "_" for c in session_id)
        stamped = f"{safe_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self._session_dir = self.output_root / stamped
        self._screenshots_dir = self._session_dir / "screenshots"
        self._video_path = self._session_dir / "screen.mp4"
        self._manifest_path = self._session_dir / "manifest.json"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        return self.locations

    def start(self, session_id: str = "session") -> None:
        if self._running:
            raise RuntimeError("Screen recorder is already running")
        if self._session_dir is None:
            self.prepare(session_id)
        assert self._screenshots_dir is not None
        assert self._video_path is not None
        assert self._manifest_path is not None

        self._running = True
        self._frame_count = 0
        self._started_at = time.perf_counter()
        self._meta = {
            "schema_version": "1.0",
            "session_id": session_id,
            "fps": self.fps,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "locations": self.locations,
            "screenshot_callback_url": self.screenshot_callback_url,
        }

        writer: cv2.VideoWriter | None = None
        frame_interval = 1.0 / self.fps
        next_frame_at = time.perf_counter()
        self._start_callback_worker()

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                while self._running:
                    raw = sct.grab(monitor)
                    frame = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
                    if writer is None:
                        height, width = frame.shape[:2]
                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        writer = cv2.VideoWriter(str(self._video_path), fourcc, self.fps, (width, height))
                        if not writer.isOpened():
                            raise RuntimeError(f"Could not open video writer: {self._video_path}")

                    frame_path = self._screenshots_dir / f"frame_{self._frame_count:06d}.png"
                    cv2.imwrite(str(frame_path), frame)
                    writer.write(frame)
                    self._notify_screenshot(session_id, frame_path, self._frame_count)
                    self._frame_count += 1

                    next_frame_at += frame_interval
                    sleep_for = next_frame_at - time.perf_counter()
                    if sleep_for > 0:
                        time.sleep(sleep_for)
                    else:
                        next_frame_at = time.perf_counter()
        finally:
            self._running = False
            if writer is not None:
                writer.release()
            self._stop_callback_worker()
            self._write_manifest()

    def stop(self) -> dict[str, Any]:
        if not self._running:
            return self._write_manifest()
        self._running = False
        return self._write_manifest()

    def _write_manifest(self) -> dict[str, Any]:
        duration = time.perf_counter() - self._started_at if self._started_at else 0.0
        manifest = {
            **self._meta,
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": round(duration, 4),
            "frame_count": self._frame_count,
            "locations": self.locations,
        }
        if self._manifest_path:
            self._manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with self._manifest_path.open("w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
        return manifest

    def _notify_screenshot(self, session_id: str, frame_path: Path, frame_index: int) -> None:
        if not self.screenshot_callback_url:
            return
        elapsed = time.perf_counter() - self._started_at if self._started_at else 0.0
        payload = {
            "event": "screenshot_saved",
            "session_id": session_id,
            "frame_index": frame_index,
            "t": round(elapsed, 4),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "screenshot_path": str(frame_path),
            "locations": self.locations,
        }
        try:
            self._callback_queue.put_nowait(payload)
        except queue.Full:
            pass

    def _start_callback_worker(self) -> None:
        if not self.screenshot_callback_url or self._callback_thread:
            return

        def worker() -> None:
            while True:
                payload = self._callback_queue.get()
                if payload is None:
                    self._callback_queue.task_done()
                    break
                self._post_callback(payload)
                self._callback_queue.task_done()

        self._callback_thread = threading.Thread(target=worker, daemon=True)
        self._callback_thread.start()

    def _stop_callback_worker(self) -> None:
        if not self._callback_thread:
            return
        try:
            self._callback_queue.put_nowait(None)
        except queue.Full:
            try:
                self._callback_queue.get_nowait()
                self._callback_queue.task_done()
                self._callback_queue.put_nowait(None)
            except queue.Empty:
                pass
        self._callback_thread.join(timeout=2.0)
        self._callback_thread = None

    def _post_callback(self, payload: dict[str, Any]) -> None:
        if not self.screenshot_callback_url:
            return
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            self.screenshot_callback_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            request.urlopen(req, timeout=0.5).close()
        except Exception:
            pass
