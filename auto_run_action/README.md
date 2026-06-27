# auto_run_action

CrossFire 자율 주행 모듈. waypoints와 실시간 스크린 프레임을 받아 캐릭터를 자동으로 이동 및 입력 이벤트를 녹화.

---

## 연동 방법

```python
from auto_run_action import AutoRunAction

action = AutoRunAction()

# 시작
action.start(
    waypoints=[{"idx": 0, "x": 271.5, "y": 123.7, "rot": 90}, ...],
    get_frame=capture.latest_frame,   # () -> np.ndarray (BGR)
    output_path="recordings/out.json",
)

# 상태 확인
action.status()
# → {"state": "running", "wp_index": 1, "total": 3, "elapsed_sec": 4.2, "error": None}

# 중지
action.stop()
```

---

## 파일별 역할

### `runner.py` — 진입점
매니저가 직접 사용하는 클래스.

| | |
|---|---|
| **클래스** | `AutoRunAction` |
| **INPUT** | `waypoints: list[dict]` — `[{idx, x, y, rot}, ...]` (snippet 내용 직접) |
| | `get_frame: Callable[[], np.ndarray]` — 매니저가 제공하는 최신 BGR 프레임 콜백 |
| | `output_path: str` — 녹화 결과 저장 경로 (`.json`) |
| | `session_id: str` — 선택, 기본값 `auto_run_<timestamp>` |
| **OUTPUT** | `status()` → `{state, wp_index, total, elapsed_sec, error}` |
| **메서드** | `start(waypoints, get_frame, output_path)` — 백그라운드 스레드로 실행 |
| | `stop()` — 즉시 중단 후 녹화 저장 |
| | `status()` — 현재 상태 조회 |

---

### `navigator.py` — 이동 로직
A* 경로를 따라 W키 홀드 + 마우스 회전으로 캐릭터를 이동시킨다.

| | |
|---|---|
| **클래스** | `OptimizedNavigator` (내부 사용) |
| **INPUT** | `get_frame: Callable` — 프레임 콜백 |
| | `locator: Locator` — 위치 계산기 |
| | `mapinfo_path: str` — A* 맵 파일 경로 |
| **동작** | 각 waypoint까지 A* 경로 계산 → W키 홀드로 전진 → 15° 이상 편차 시 마우스로 보정 |
| | 리스폰 감지 시 처음 waypoint부터 재시작 (최대 5회) |
| | 30초 초과 시 자동 종료 |

---

### `locator.py` — 위치 계산
받은 프레임으로 `radar.locate()`를 호출해 캐릭터 좌표를 반환한다.

| | |
|---|---|
| **클래스** | `Locator` |
| **INPUT** | `frame: np.ndarray` — BGR 스크린 캡처 |
| **OUTPUT** | `{x: float, y: float, rot: float}` 또는 `None` (감지 실패 시) |
| **특이사항** | 10Hz TTL 캐시 — 같은 프레임을 0.1초 이내에 다시 요청하면 캐시 반환 |

---

### `recorder.py` — 입력 이벤트 녹화
auto_run 실행 중 발생하는 모든 키보드/마우스 이벤트를 녹화한다.

| | |
|---|---|
| **클래스** | `HookRecorder`, `PollingRecorder` |
| **INPUT** | `get_position_fn: Callable[[], dict]` — 각 이벤트에 position 메타데이터를 붙이기 위한 콜백 |
| **OUTPUT** | `save(path, session_id)` → JSON 파일 (`schema_version 0.2` 형식) |
| **원본과 차이** | `mss.grab()` 제거 — position은 주입된 콜백으로 가져옴 |

---

### `pathfinder.py` — A* 경로 탐색
mapinfo.json의 장애물을 피해 시작점→목적지 경로를 계산한다.

| | |
|---|---|
| **클래스** | `MapPathfinder` |
| **INPUT** | `start: (x, y)`, `end: (x, y)` — 픽셀 좌표 |
| **OUTPUT** | `list[(x, y)]` — 장애물 회피 경유지 목록 (line-of-sight 스무딩 적용) |
| **특이사항** | mapinfo.json 없으면 직선 이동 fallback |

---

### `win_input.py` — OS 입력 주입
Windows SendInput API로 키보드/마우스 이벤트를 게임에 전달한다.

| | |
|---|---|
| **함수** | `send_keyboard_scan(scan, extended, is_up)` — 스캔코드 기반 키 입력 (게임 호환) |
| | `send_mouse_relative(dx, dy)` — FPS raw-input 마우스 이동 |
| | `send_mouse_button(flag)` — 마우스 버튼 |

---

### `keys.py` — 키 매핑
키 이름 ↔ VK 코드 ↔ 스캔코드 변환 테이블.

| | |
|---|---|
| **상수** | `NAME_TO_VK` — `{"W": 0x57, ...}` |
| **함수** | `scan_code_for_vk(vk)` → 스캔코드 정수 |

---

### `radar.py` — 미니맵 위치 계산 엔진
미니맵 이미지를 template matching해 캐릭터의 맵 좌표와 방향을 계산한다.

| | |
|---|---|
| **함수** | `locate(frame: np.ndarray) → (x, y, yaw, score)` |
| **INPUT** | `frame` — BGR 전체화면 캡처 (1600×900 기준) |
| **OUTPUT** | `x, y` — 맵 픽셀 좌표 (1980×654 공간), `yaw` — 북쪽 기준 시계방향 각도, `score` — 매칭 신뢰도 |
| **assets** | `auto_run_action/assets/minimap_2.png`, `auto_run_action/assets/north_marker.png` (패키지 내 포함) |

---

## 패키지 구조

```
auto_run_action/
  assets/
    minimap_2.png 
    north_marker.png  
  __init__.py
  radar.py
  locator.py
  navigator.py
  pathfinder.py
  recorder.py
  runner.py
  win_input.py
  keys.py
```

완전 standalone — 외부 프로젝트 모듈 의존 없음.

---

## 의존성

```
opencv-python
numpy
```
