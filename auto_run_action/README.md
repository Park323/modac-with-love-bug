# auto_run_action

매니저가 매 틱마다 두 함수를 호출해서 캐릭터를 자율 이동시키는 모듈.

```
frame → get_position() → position
position + waypoint → next_event() → event
```

---

## `position.py` — 현재 위치 계산

```python
from auto_run_action.position import get_position

position = get_position(frame)
# frame   : np.ndarray — shape=(900, 1600, 3), dtype=uint8, BGR
# 성공 시 : {"x": 271.5, "y": 123.7, "rot": 90.0}
# 실패 시 : None
```

---

## `step.py` — 다음 이벤트 결정

```python
from auto_run_action.step import next_event

event = next_event(position, waypoint)
# position : {"x": 271.5, "y": 123.7, "rot": 90.0}
# waypoint : {"x": 549.6, "y": 295.2}
#
# 도착     : None
# 회전필요 : {"type": "mouse_move", "dx": 1380, "dy": 0}
# 전진     : {"type": "key_down",   "key": "W", "scan": 17, "extended": False}
```

---

## 매니저 연동 예시

```python
from auto_run_action.position import get_position
from auto_run_action.step import next_event

# 매 틱
position = get_position(frame)
if position:
    event = next_event(position, waypoint)
    if event is None:
        pass  # 도착 → 다음 waypoint로
    else:
        pass  # event 실행
```

---

## 의존성

```
opencv-python
numpy
```
