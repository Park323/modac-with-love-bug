"""AutoRunController — capture→analyze→play 자동주행 폐루프(백그라운드 스레드).

~fps 주기로 capture.next() 프레임을 analysis에 넘기고, 나온 InputItem을
play.dispatch 한다. waypoint 전부 도달 시 done. 입력 로거는 선택.
"""

from __future__ import annotations

import threading
import time

from manager.clock import Clock


class AutoRunController:
    def __init__(self, capture, analysis, play, clock: Clock,
                 logger=None, fps: float = 10.0,
                 max_consecutive_errors: int = 10) -> None:
        self._capture = capture
        self._analysis = analysis
        self._play = play
        self._clock = clock
        self._logger = logger
        self._period = (1.0 / fps) if fps > 0 else 0.1
        self._max_consec = max_consecutive_errors

        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._state = "idle"
        self._frames = 0
        self._dispatched = 0
        self._consec = 0
        self._error: str | None = None
        self._wp_total = 0

    def start(self, waypoints: list[dict]) -> None:
        with self._lock:
            if self._running:
                raise RuntimeError("already running")
            self._running = True
            self._state = "running"
            self._frames = 0
            self._dispatched = 0
            self._consec = 0
            self._error = None
            self._wp_total = len(waypoints)
        self._thread = threading.Thread(
            target=self._run, args=(list(waypoints),), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._running = False
            # state is set to "stopped" by the thread after cleanup finishes

    def status(self) -> dict:
        remaining = self._analysis.remaining
        with self._lock:
            return {
                "state": self._state,
                "wp_total": self._wp_total,
                "wp_done": self._wp_total - remaining,
                "frames": self._frames,
                "dispatched": self._dispatched,
                "consecutive_errors": self._consec,
                "error": self._error,
            }

    # ── internal ──────────────────────────────────────────────────────────

    def _run(self, waypoints: list[dict]) -> None:
        began: list[str] = []
        try:
            self._clock.start()
            self._play.begin(self._clock); began.append("play")
            self._capture.begin(self._clock); began.append("capture")
            if self._logger is not None:
                try:
                    self._logger.start(); began.append("logger")
                except Exception as e:
                    with self._lock:
                        self._error = f"logger start failed (continuing without logger): {e}"
            self._analysis.set_waypoints(waypoints)
        except Exception as e:
            with self._lock:
                self._state = "error"
                self._error = str(e)
                self._running = False
            self._safe_end(began)
            return
        try:
            self._loop()
        finally:
            self._safe_end(began)
            # Settle final state: if still "running" the controller was stopped()
            with self._lock:
                if self._state == "running":
                    self._state = "stopped"

    def _loop(self) -> None:
        next_tick = time.perf_counter()
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                frame = self._capture.next()
                items = self._analysis.analyze(frame)
                for it in items:
                    self._play.dispatch(it)
                with self._lock:
                    self._frames += 1
                    self._dispatched += len(items)
                    self._consec = 0
            except Exception as e:
                with self._lock:
                    self._consec += 1
                    self._error = str(e)
                    if self._consec > self._max_consec:
                        self._state = "error"
                        self._running = False
                        break

            if self._analysis.done:
                with self._lock:
                    if self._state == "running":
                        self._state = "done"
                    self._running = False
                break

            next_tick += self._period
            self._sleep_until(next_tick)

    def _sleep_until(self, target: float) -> None:
        while True:
            with self._lock:
                if not self._running:
                    return
            remaining = target - time.perf_counter()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    def _safe_end(self, began: list[str]) -> None:
        if "logger" in began and self._logger is not None:
            try:
                self._logger.stop()
            except Exception:
                pass
        if "capture" in began:
            try:
                self._capture.end()
            except Exception:
                pass
        if "play" in began:
            try:
                self._play.end()
            except Exception:
                pass
