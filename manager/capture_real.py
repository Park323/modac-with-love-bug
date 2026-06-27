"""실제 Capture 모듈 어댑터 — test_scenario_executor.ScreenRecorder를 감쌈.

RunController가 begin할 때 화면 녹화를 시작하고, end할 때 종료한다.
결과(PNG 스크린샷 + screen.mp4)는 test_scenario_executor_output/<session>/에
저장된다(콜백 없음, 폴더 저장만).

ScreenRecorder.start(session_id)는 블로킹이라 별도 스레드에서 돌린다.
"""

import queue as _queue
import threading
import time

from manager.clock import Clock
from manager.frame import Frame
from manager.modules import ICaptureModule


class RealCaptureModule(ICaptureModule):
    def __init__(self, screenshot_fps: float = 10.0, video_fps: float = 30.0,
                 session_prefix: str = "playtest") -> None:
        self._screenshot_fps = screenshot_fps
        self._video_fps = video_fps
        self._session_prefix = session_prefix
        self._recorder = None
        self._clock = None
        self._thread: threading.Thread | None = None
        self._summary: dict | None = None
        # 스샷 콜백(screenshot_fps)으로 push되는 분석용 프레임. 최신만 유지(드롭).
        self._frame_q: "_queue.Queue" = _queue.Queue(maxsize=1)

    def _on_frame(self, frame) -> None:
        """ScreenRecorder가 스샷 캡처마다 호출. 큐에 최신 프레임만 보관."""
        try:
            self._frame_q.put_nowait(frame)
        except _queue.Full:
            try:
                self._frame_q.get_nowait()
            except _queue.Empty:
                pass
            try:
                self._frame_q.put_nowait(frame)
            except _queue.Full:
                pass

    def begin(self, clock: Clock) -> None:
        self._clock = clock
        # 지연 import: 이 모듈 import 시점에 cv2/mss 강제 로드 안 함
        from test_scenario_executor.screen.recorder import ScreenRecorder

        session_id = f"{self._session_prefix}_{int(time.time())}"
        self._recorder = ScreenRecorder(
            screenshot_fps=self._screenshot_fps,   # 분석용 push 주기(10fps)
            video_fps=self._video_fps,             # 비디오 녹화(30fps)
            screenshot_callback_url=None,          # 폴더 저장만
            frame_callback=self._on_frame,         # 스샷 캡처 → 분석 큐로 push
        )
        self._recorder.prepare(session_id)
        self._thread = threading.Thread(
            target=self._recorder.start, args=(session_id,), daemon=True)
        self._thread.start()

    def next(self) -> Frame:
        ts = self._clock.now_ms() if self._clock is not None else 0
        try:
            arr = self._frame_q.get(timeout=0.2)
        except _queue.Empty:
            arr = None
        return Frame(timestamp_ms=ts, bgr=arr)

    def end(self) -> None:
        if self._recorder is None:
            return
        self._summary = self._recorder.stop()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    @property
    def summary(self) -> dict | None:
        return self._summary
