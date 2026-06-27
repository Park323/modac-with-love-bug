"""AutoRunAnalysis — auto_run_action(get_position + next_event)을 IAnalysisModule로 래핑.

frame.bgr(BGR ndarray)를 받아 다음 입력 이벤트 1개를 InputItem으로 반환.
- frame.bgr 없음 / 위치 미검출 → [] (에러 아님)
- waypoints는 첫 analyze에서만 next_event에 주입(이후 None — 전역 리셋 방지)
- 완료 = 주입한 waypoint 리스트가 모두 pop 되어 빈 것
"""

from __future__ import annotations

from manager.frame import Frame
from manager.items import InputItem
from manager.modules import IAnalysisModule


class AutoRunAnalysis(IAnalysisModule):
    def __init__(self) -> None:
        self._wps: list[dict] = []
        self._injected = False

    def set_waypoints(self, waypoints: list[dict]) -> None:
        self._wps = sorted(waypoints, key=lambda w: w.get("idx", 0))
        self._injected = False

    def analyze(self, frame: Frame) -> list[InputItem]:
        if frame.bgr is None:
            return []
        from auto_run_action.position import get_position
        from auto_run_action.step import next_event

        position = get_position(frame.bgr)
        if position is None:
            print("[autorun] pos=None (detection failed)")  # [임시] 진단 로깅
            return []

        waypoints = self._wps if not self._injected else None
        self._injected = True
        event = next_event(position, waypoints)
        # [임시] 진단 로깅 — run마다 rot/event/잔여 wp 추적(마우스 회전 원인 격리)
        print(f"[autorun] pos=({position.get('x'):.0f},{position.get('y'):.0f},"
              f"rot={position.get('rot'):.0f}) remaining={len(self._wps)} event={event}")
        if event is None:
            return []
        return [InputItem(key=event.get("key", ""),
                          action=event.get("type", ""),
                          raw=event)]

    @property
    def remaining(self) -> int:
        return len(self._wps)

    @property
    def done(self) -> bool:
        return self._injected and len(self._wps) == 0
