"""실제 Capture 모듈 어댑터 — test_scenario_executor.ScreenRecorder를 감쌈.

RunController가 begin할 때 화면 녹화를 시작하고, end할 때 종료한다.
결과(PNG 스크린샷 + screen.mp4)는 test_scenario_executor_output/<session>/에
저장된다(콜백 없음, 폴더 저장만).

ScreenRecorder.start(session_id)는 블로킹이라 별도 스레드에서 돌린다.
"""

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

    def begin(self, clock: Clock) -> None:
        self._clock = clock
        # 지연 import: 이 모듈 import 시점에 cv2/mss 강제 로드 안 함
        from test_scenario_executor.screen.recorder import ScreenRecorder

        session_id = f"{self._session_prefix}_{int(time.time())}"
        self._recorder = ScreenRecorder(
            screenshot_fps=self._screenshot_fps,
            video_fps=self._video_fps,
            screenshot_callback_url=None,  # 폴더 저장만
        )
        self._recorder.prepare(session_id)
        self._thread = threading.Thread(
            target=self._recorder.start, args=(session_id,), daemon=True)
        self._thread.start()

    def next(self) -> Frame:
        arr = self._recorder.latest_frame if self._recorder is not None else None
        ts = self._clock.now_ms() if self._clock is not None else 0
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
