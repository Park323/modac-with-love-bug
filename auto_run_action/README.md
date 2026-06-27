# auto_run_action

CrossFire 자율 주행 모듈. 매니저로부터 waypoints(목적지 목록)와 실시간 스크린 프레임을 받아 캐릭터를 자동으로 이동시키고, 그 과정에서 발생한 키/마우스 입력을 JSON으로 녹화한다.

녹화된 JSON은 매니저의 `RunController`가 그대로 재생할 수 있는 시나리오 포맷이라, 한 번 녹화하면 이후엔 CV 없이 반복 재생만 해도 된다.

```
snippet → [auto_run_action] → 캐릭터 이동 + 녹화 → scenario.json
                                                         ↓
                                              [RunController] 재생
```

---

## 패키지 구조

```
auto_run_action/
  assets/
    minimap_2.png       ← 기준 미니맵 (template matching용)
    north_marker.png    ← 북쪽 마커 (방향 계산용)
  radar.py       ← 미니맵 CV: frame → (x, y, yaw, score)
  locator.py     ← radar 래퍼: frame → {x, y, rot} | None  (10Hz 캐시)
  pathfinder.py  ← A*: (start, end) → 경유지 목록
  navigator.py   ← 이동 루프: W홀드 + 마우스 조향 + 리스폰 감지
  recorder.py    ← 키/마우스 이벤트 녹화 → JSON
  win_input.py   ← Windows SendInput 래퍼
  keys.py        ← 키 이름 ↔ VK ↔ 스캔코드 테이블
  runner.py      ← 진입점: AutoRunAction (start / stop / status)
```

---

## 모듈별 호출 & 입출력

### `runner.py` — 진입점

```python
from auto_run_action import AutoRunAction

action = AutoRunAction(
    mapinfo_path="assets/mapinfo.json",  # 없으면 직선 이동 fallback
)

# 시작 — 백그라운드 스레드로 실행됨
action.start(
    waypoints=[
        {"idx": 0, "x": 271.5, "y": 123.7, "rot": 90},
        {"idx": 1, "x": 549.6, "y": 295.2, "rot": 90},
    ],
    get_frame=lambda: capture.latest_frame,   # () -> np.ndarray (BGR)
    output_path="recordings/auto_run_001.json",
    session_id="run_001",  # 선택, 기본값: auto_run_<timestamp>
)

# 상태 조회
action.status()
# 실행 중: {"state": "running", "wp_index": 1, "total": 2, "elapsed_sec": 4.2,  "error": None}
# 완료:    {"state": "done",    "wp_index": 2, "total": 2, "elapsed_sec": 12.1, "error": None}
# 오류:    {"state": "error",   "wp_index": 0, "total": 2, "elapsed_sec": 0.0,  "error": "..."}
# 중단:    {"state": "stopped", "wp_index": 1, "total": 2, "elapsed_sec": 6.3,  "error": None}

# 중단 (녹화 저장 후 종료)
action.stop()
```

---

### `radar.py` — 미니맵 CV 엔진

frame에서 캐릭터 위치와 방향을 계산한다. 1600×900 캡처 기준으로 캘리브레이션됨.

```python
from auto_run_action.radar import locate

x, y, yaw, score = locate(frame)
# frame : np.ndarray — BGR 전체화면 캡처 (1600×900)
# x, y  : float      — 맵 픽셀 좌표 (1980×654 공간)
# yaw   : float      — 북쪽 기준 시계방향 각도 (0~360)
# score : float      — 매칭 신뢰도 (높을수록 정확)

# 예시 출력
# x=271.5, y=123.7, yaw=90.0, score=0.87
```

---

### `locator.py` — radar 래퍼 (TTL 캐시)

radar를 직접 쓰는 대신 이걸 쓰면 10Hz 캐시가 적용되어 locate() 과호출을 방지한다.

```python
from auto_run_action.locator import Locator

locator = Locator()  # radar_path 기본값: auto_run_action/radar.py

result = locator.locate(frame)
# frame  : np.ndarray — BGR 스크린 캡처
# 성공 시: {"x": 271.5, "y": 123.7, "rot": 90.0}
# 실패 시: None

# 0.1초 이내 재호출 시 캐시 반환 (locate() 중복 실행 안 함)
```

---

### `pathfinder.py` — A* 경로 탐색

mapinfo.json의 장애물을 피해 시작점에서 목적지까지 경유지를 계산한다.

```python
from auto_run_action.pathfinder import MapPathfinder

pf = MapPathfinder("assets/mapinfo.json")

path = pf.find_path(start=(271.5, 123.7), end=(549.6, 295.2))
# start, end : (x, y) 픽셀 좌표
# 반환값     : list[(x, y)] — 장애물 회피 경유지 (line-of-sight 스무딩 적용)

# 경로 있음:  [(271.5, 123.7), (310.0, 180.0), (549.6, 295.2)]
# 경로 없음:  [(549.6, 295.2)]  ← 목적지 직행 fallback
```

---

### `recorder.py` — 입력 이벤트 녹화

auto_run 실행 중 발생하는 모든 키보드/마우스 이벤트를 캡처해 JSON으로 저장한다.
각 이벤트에 `position` 메타데이터가 붙는다.

```python
from auto_run_action.recorder import HookRecorder

rec = HookRecorder(
    get_position_fn=lambda: {"x": 271.5, "y": 123.7, "rot": 90.0}
    # 각 이벤트에 붙일 position을 반환하는 콜백
)

# 별도 스레드에서 실행 (블로킹)
import threading
t = threading.Thread(target=rec.start, daemon=True)
t.start()

rec.stop()
t.join()

result = rec.save("recordings/out.json", session_id="run_001")
# {
#   "schema_version": "0.2",
#   "session": {
#     "session_id":   "run_001",
#     "duration_sec": 12.3,
#     "event_count":  847
#   },
#   "events": [
#     {"t": 0.00, "type": "key_down",  "key": "W", "scan": 17, "extended": false,
#      "position": {"x": 271.5, "y": 123.7, "rot": 90.0}},
#     {"t": 0.05, "type": "mouse_move","dx": 23, "dy": 0,
#      "position": {"x": 275.0, "y": 126.0, "rot": 91.0}},
#     {"t": 12.3, "type": "key_up",   "key": "W", "scan": 17, "extended": false,
#      "position": {"x": 549.6, "y": 295.2, "rot": 90.0}},
#     ...
#   ]
# }
```

---

### `win_input.py` — OS 입력 주입

Windows SendInput API로 키보드/마우스 이벤트를 게임에 직접 전달한다.
스캔코드 기반이라 게임의 raw-input 모드와 호환된다.

```python
from auto_run_action import win_input as wi

wi.send_keyboard_scan(scan=17, extended=False, is_up=False)  # W 누름
wi.send_keyboard_scan(scan=17, extended=False, is_up=True)   # W 뗌

wi.send_mouse_relative(dx=23, dy=0)   # 오른쪽으로 23px 회전
wi.send_mouse_relative(dx=-10, dy=0)  # 왼쪽으로 10px 회전

wi.send_mouse_button(flag=wi.MOUSEEVENTF_LEFTDOWN)  # 좌클릭 누름
wi.send_mouse_button(flag=wi.MOUSEEVENTF_LEFTUP)    # 좌클릭 뗌

# 모든 함수 반환값 없음
```

---

### `keys.py` — 키 매핑

키 이름 ↔ VK 코드 ↔ 스캔코드 변환.

```python
from auto_run_action.keys import NAME_TO_VK, scan_code_for_vk

vk   = NAME_TO_VK["W"]        # 0x57
scan = scan_code_for_vk(0x57) # 17

# NAME_TO_VK 주요 키
# "W" "A" "S" "D" → 이동
# "F1"~"F12"      → 기능키
# "Shift" "Ctrl" "Alt" "Space" "Esc" "Enter"
```

---

## 의존성

```
opencv-python
numpy
```
