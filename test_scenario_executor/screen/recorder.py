"""Capture screenshots and a video file during a session."""

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

from ..core.session_paths import create_session_dir, session_paths, utc_now_iso


class ScreenRecorder:
    def __init__(
        self,
        output_root: str | Path = "test_scenario_executor_output",
        screenshot_fps: float = 30.0,
        video_fps: float = 30.0,
        screenshot_callback_url: str | None = None,
        frame_callback=None,
    ) -> None:
        if screenshot_fps <= 0:
            raise ValueError("screenshot_fps must be greater than 0")
        if video_fps <= 0:
            raise ValueError("video_fps must be greater than 0")
        self.output_root = Path(output_root)
        self.screenshot_fps = screenshot_fps
        self.video_fps = video_fps
        self.screenshot_callback_url = screenshot_callback_url
        # in-process 콜백: 스샷 캡처(screenshot_fps)마다 BGR 프레임을 push.
        self._frame_callback = frame_callback
        self._running = False
        self._session_dir: Path | None = None
        self._screenshots_dir: Path | None = None
        self._video_path: Path | None = None
        self._manifest_path: Path | None = None
        self._screenshot_count = 0
        self._video_frame_count = 0
        self._started_at = 0.0
        self._test_started_at: str | None = None
        self._meta: dict[str, Any] = {}
        self._callback_queue: queue.Queue[Any] = queue.Queue(maxsize=300)
        self._callback_thread: threading.Thread | None = None
        self._latest_frame = None
        self._frame_lock = threading.Lock()

    @property
    def latest_frame(self):
        with self._frame_lock:
            return self._latest_frame

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

    def prepare(
        self,
        session_id: str,
        session_dir: str | Path | None = None,
        test_started_at: str | None = None,
    ) -> dict[str, str | None]:
        self._test_started_at = test_started_at or utc_now_iso()
        self._session_dir = Path(session_dir) if session_dir else create_session_dir(
            session_id, self.output_root, self._test_started_at
        )
        paths = session_paths(self._session_dir)
        self._screenshots_dir = paths["screenshots_dir"]
        self._video_path = paths["video_path"]
        self._manifest_path = paths["manifest_path"]
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
        self._screenshot_count = 0
        self._video_frame_count = 0
        self._started_at = time.perf_counter()
        self._meta = {
            "schema_version": "1.0",
            "session_id": session_id,
            "test_started_at": self._test_started_at or utc_now_iso(),
            "screenshot_fps": self.screenshot_fps,
            "video_fps": self.video_fps,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "locations": self.locations,
            "screenshot_callback_url": self.screenshot_callback_url,
        }

        writer: cv2.VideoWriter | None = None
        screenshot_interval = 1.0 / self.screenshot_fps
        video_interval = 1.0 / self.video_fps
        next_screenshot_at = time.perf_counter()
        next_video_at = next_screenshot_at
        self._start_callback_worker()

        try:
            with mss.mss() as sct:
                monitor = sct.monitors[1]
                while self._running:
                    now = time.perf_counter()
                    screenshot_due = now >= next_screenshot_at
                    video_due = now >= next_video_at
                    if not screenshot_due and not video_due:
                        time.sleep(min(next_screenshot_at, next_video_at) - now)
                        continue

                    raw = sct.grab(monitor)
                    frame = cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)
                    with self._frame_lock:
                        self._latest_frame = frame
                    if writer is None:
                        height, width = frame.shape[:2]
                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        writer = cv2.VideoWriter(
                            str(self._video_path), fourcc, self.video_fps, (width, height)
                        )
                        if not writer.isOpened():
                            raise RuntimeError(f"Could not open video writer: {self._video_path}")

                    if screenshot_due:
                        captured_at = datetime.now(timezone.utc)
                        timestamp = captured_at.strftime("%Y%m%d_%H%M%S_%f")
                        frame_path = (
                            self._screenshots_dir
                            / f"screenshot_{timestamp}_{self._screenshot_count:06d}.png"
                        )
                        cv2.imwrite(str(frame_path), frame)
                        self._notify_screenshot(
                            session_id, frame_path, self._screenshot_count, captured_at
                        )
                        if self._frame_callback is not None:
                            try:
                                self._frame_callback(frame)
                            except Exception:
                                pass
                        self._screenshot_count += 1
                        next_screenshot_at += screenshot_interval

                    if video_due:
                        writer.write(frame)
                        self._video_frame_count += 1
                        next_video_at += video_interval

                    now = time.perf_counter()
                    if next_screenshot_at <= now:
                        next_screenshot_at = now + screenshot_interval
                    if next_video_at <= now:
                        next_video_at = now + video_interval
        finally:
            self._running = False
            if writer is not None:
                writer.release()
            self._stop_callback_worker()
            self._summary()

    def stop(self) -> dict[str, Any]:
        if not self._running:
            return self._summary()
        self._running = False
        return self._summary()

    def _summary(self) -> dict[str, Any]:
        duration = time.perf_counter() - self._started_at if self._started_at else 0.0
        summary = {
            "screenshot_fps": self.screenshot_fps,
            "video_fps": self.video_fps,
            "started_at": self._meta.get("started_at"),
            "stopped_at": datetime.now(timezone.utc).isoformat(),
            "duration_sec": round(duration, 4),
            "screenshot_count": self._screenshot_count,
            "video_frame_count": self._video_frame_count,
            "screenshot_callback_url": self.screenshot_callback_url,
        }
        return summary

    def _notify_screenshot(
        self,
        session_id: str,
        frame_path: Path,
        frame_index: int,
        captured_at: datetime,
    ) -> None:
        if not self.screenshot_callback_url:
            return
        elapsed = time.perf_counter() - self._started_at if self._started_at else 0.0
        payload = {
            "event": "screenshot_saved",
            "session_id": session_id,
            "frame_index": frame_index,
            "t": round(elapsed, 4),
            "created_at": captured_at.isoformat(),
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
