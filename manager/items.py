from dataclasses import dataclass


# 포맷 미정 — 우선 placeholder. 확정 시 이 타입만 수정.
@dataclass
class InputItem:
    key: str            # 예: "D"
    action: str         # 예: "Pressed"
    raw: dict | None = None   # 이벤트 원본 운반 (시나리오 한 줄)


@dataclass
class InputResult:
    item: InputItem
    timestamp_ms: int   # 공유 Clock 기준 절대 ts
    ok: bool
