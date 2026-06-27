# auto_run_action

미니맵과 waypoints 를 받아 다음 액션을 반환하는 모듈.

```
frame + waypoints → next event
```

---

## `position.py` — 현재 위치 계산

```python
next_event = get_event(frame, waypoints)

# frame   : np.ndarray — shape=(900, 1600, 3), dtype=uint8, BGR
# waypoint : {"x": 549.6, "y": 295.2}
#
# 도착     : None
# 회전필요 : {"type": "mouse_move", "dx": 1380, "dy": 0}
# 전진     : {"type": "key_down",   "key": "W", "scan": 17, "extended": False}
```