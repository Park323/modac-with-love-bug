"""RecordSession — background-thread input recording controller.

Runs test_scenario_executor's InputRecorder on a daemon thread and saves
a replayable JSON when stopped. Supports optional auto-stop after duration_sec.

Threading safety:
- A single Lock guards state transitions.
- _finalized flag ensures recorder.save() runs exactly once even when a
  timer-triggered stop() races a manual stop().
- The timer thread uses _done_evt.wait() so manual stop() can cancel it
  promptly by setting the event — no lingering thread.
- stop() never holds the lock while calling into the recorder or joining the
  thread (deadlock prevention): it captures what it needs under the lock,
  releases, then does the heavy work outside the lock.
"""

from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Callable, Optional

from test_scenario_executor.input.logger import create_input_recorder
from test_scenario_executor.core.session_paths import (
    OUTPUT_ROOT,
    create_session_dir,
    session_paths,
    utc_now_iso,
)


class RecordSession:
    """Single-session input recording controller."""

    def __init__(
        self,
        backend: str = "polling",
        sample_hz: float = 120.0,
        recorder_factory: Callable = create_input_recorder,
        output_root=OUTPUT_ROOT,
    ):
        self._backend = backend
        self._sample_hz = sample_hz
        self._factory = recorder_factory
        self._output_root = output_root

        self._lock = threading.Lock()
        self._done_evt = threading.Event()  # set by stop(); wakes timer thread

        # Mutable state (all guarded by _lock)
        self._state: str = "idle"       # idle | recording | done | error
        self._recorder = None
        self._rec_thread: Optional[threading.Thread] = None
        self._input_path: Optional[Path] = None
        self._session_id: Optional[str] = None
        self._finalized: bool = False
        self._event_count: Optional[int] = None
        self._duration_sec: Optional[float] = None
        self._error: Optional[str] = None
        self._generation: int = 0  # incremented each start(); guards stale timers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self, duration_sec: float | None = None) -> None:
        """Start recording.  Raises RuntimeError if already recording."""
        with self._lock:
            if self._state == "recording":
                raise RuntimeError("already recording")

            # Reset all session state for a fresh recording.
            self._finalized = False
            self._event_count = None
            self._duration_sec = None
            self._error = None
            self._done_evt.clear()

            # Increment generation so any leftover timer from a previous session
            # will detect it is stale and abort before calling stop().
            self._generation += 1
            gen = self._generation

            # Build session directory + input_path.
            started_at = utc_now_iso()
            # Use a timestamp-based session_id so each run gets its own dir.
            session_id = f"record_{started_at.replace(':', '').replace('-', '')}"
            self._session_id = session_id
            session_dir = create_session_dir(session_id, self._output_root, started_at)
            paths = session_paths(session_dir)
            self._input_path = paths["input_path"]
            paths["input_dir"].mkdir(parents=True, exist_ok=True)

            # Create recorder via injected factory.
            recorder = self._factory(self._backend, self._sample_hz)
            self._recorder = recorder

        # Spawn recorder thread OUTSIDE the lock (start() blocks).
        rec_thread = threading.Thread(target=recorder.start, daemon=True, name="recorder-start")
        rec_thread.start()

        # Poll until recorder.is_recording (up to 2 s).
        for _ in range(400):
            if recorder.is_recording:
                break
            time.sleep(0.005)

        if not recorder.is_recording:
            msg = "recorder failed to start within 2s"
            with self._lock:
                self._state = "error"
                self._error = msg
            raise RuntimeError(msg)

        with self._lock:
            self._rec_thread = rec_thread
            self._state = "recording"

        # Optionally spawn auto-stop timer.
        if duration_sec and duration_sec > 0:
            def _timer_fn():
                # Wait for either done_evt (manual stop) or the timeout.
                self._done_evt.wait(duration_sec)
                # Guard against stale timers from a previous session: if the
                # generation has changed, a new session started — do not stop it.
                with self._lock:
                    if self._generation != gen:
                        return
                # Lock released by exiting the with-block; stop() acquires its own lock.
                self.stop()

            t = threading.Thread(target=_timer_fn, daemon=True, name="recorder-timer")
            t.start()

    def stop(self) -> None:
        """Stop recording and save.  Idempotent — safe to call multiple times."""
        # Phase 1: check state and mark finalized under the lock.
        with self._lock:
            if self._state != "recording":
                return
            if self._finalized:
                return
            self._finalized = True
            # Capture everything we need before releasing the lock.
            recorder = self._recorder
            rec_thread = self._rec_thread
            input_path = self._input_path
            session_id = self._session_id

        # Signal the done event so the timer thread wakes immediately.
        self._done_evt.set()

        # Phase 2: finalize outside the lock to avoid deadlock.
        try:
            recorder.stop()
            if rec_thread is not None:
                rec_thread.join(timeout=2.0)
            result = recorder.save(input_path, session_id=session_id)
            event_count = result["session"]["event_count"]
            duration_sec = result["session"]["duration_sec"]

            with self._lock:
                self._state = "done"
                self._event_count = event_count
                self._duration_sec = duration_sec
        except Exception as exc:
            with self._lock:
                self._state = "error"
                self._error = str(exc)

    def status(self) -> dict:
        """Return current status dict."""
        with self._lock:
            return {
                "state": self._state,
                "path": str(self._input_path) if self._input_path else None,
                "event_count": self._event_count,
                "duration_sec": self._duration_sec,
                "error": self._error,
            }

    @property
    def is_recording(self) -> bool:
        """True while a recording session is active (cross-guard hook)."""
        with self._lock:
            return self._state == "recording"
