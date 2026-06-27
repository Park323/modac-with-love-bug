# auto_run_action

미니맵과 waypoints 를 받아 다음 액션을 반환하는 모듈.

```
frame + waypoints → next event
```

---

## `step.py` — 다음 이벤트 결정

```python
from auto_run_action.step import get_event

event = get_event(frame, waypoints)
# frame     : np.ndarray — shape=(900, 1600, 3), dtype=uint8, BGR
# waypoints : [{"idx": int, "x": float, "y": float, "rot": int}, ...]
#
# 도착     : None
# 회전필요 : {"type": "mouse_move", "dx": 230, "dy": 0}   ← 최대 5° × 46px
# 전진     : {"type": "key_down",   "key": "W", "scan": 17, "extended": False}
```

---

## 의존성

```
opencv-python
numpy
```
