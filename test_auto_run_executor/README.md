# test_auto_run_executor

클라이언트가 전달한 웨이포인트 JSON을 받아 **자동 경로 이동(auto-run)** 을 실행하고,
입력 녹화·화면 녹화 결과를 세션 폴더에 저장하는 모듈입니다.

`test_scenario_executor`의 모든 API를 포함하며, `/auto-run/*` 엔드포인트가 추가됩니다.

---

## 서버 실행

```bash
pip install -r test_auto_run_executor/requirements.txt
python -m uvicorn test_auto_run_executor.api:app --host 127.0.0.1 --port 8765
```

기본 API 주소: `http://127.0.0.1:8765`

---

## 저장 위치

```
test_auto_run_executor_output/
  {session_id}_{timestamp}/
    input_recording/
      input.json          ← 자동 이동 중 녹화된 키/마우스 입력
    screen_recording/
      screenshots/
      screen.mp4
    manifest.json
```

---

## auto-run API (신규)

### `POST /auto-run/start`

클라이언트 웨이포인트를 받아 자동 이동을 시작합니다.

```json
{
  "session_id": "run_001",
  "team": "BL",
  "waypoints": [
    {"idx": 0, "x": 227.4, "y": 217.3, "rot": 40.0},
    {"idx": 1, "x": 523.6, "y": 240.6, "rot": 75.6},
    {"idx": 2, "x": 819.8, "y": 251.0, "rot": 125.4}
  ],
  "screenshot_fps": 5,
  "video_fps": 30
}
```

| 필드 | 설명 |
|------|------|
| `team` | `"BL"` (x=116, y=261, rot=90) 또는 `"GR"` (x=1901, y=123, rot=270) |
| `waypoints` | `idx` 순서로 정렬되어 이동 |
| `screenshot_fps` / `video_fps` | 생략 시 화면 녹화 없이 입력만 녹화 |

### `POST /auto-run/stop`

실행 중인 auto-run을 중단하고 녹화 파일을 저장합니다.

```http
POST /auto-run/stop
```

### `GET /auto-run/status`

현재 auto-run 상태를 반환합니다.

```json
{"status": "running", "session_id": "run_001", "error": null}
```

`status` 값: `idle` / `running` / `done` / `stopped` / `error`

---

## 기존 API (test_scenario_executor 동일)

| Method | Endpoint | 설명 |
|--------|----------|------|
| `POST` | `/test/start` | 입력 + 화면 녹화 동시 시작 |
| `POST` | `/test/stop` | 둘 다 종료 및 저장 |
| `POST` | `/input/record/start` | 입력 녹화만 시작 |
| `POST` | `/input/record/stop` | 입력 녹화 종료 및 저장 |
| `GET`  | `/input/recordings` | 저장된 녹화 목록 |
| `POST` | `/screen/record/start` | 화면 녹화만 시작 |
| `POST` | `/screen/record/stop` | 화면 녹화 종료 |
| `GET`  | `/screen/record/status` | 화면 녹화 상태 |
| `POST` | `/player/play` | JSON action 배열 직접 재생 |
| `POST` | `/player/play-file` | JSON action 파일 재생 |
| `POST` | `/player/stop` | 재생 중단 |
| `GET`  | `/status` | 전체 상태 |

---

## 로컬 커맨드 (서버 없이 실행)

### auto-run

```bash
python -m test_auto_run_executor.local_runner auto-run <웨이포인트파일> --team BL --session-id run_001
```

화면 녹화 포함:
```bash
python -m test_auto_run_executor.local_runner auto-run waypoints.json --team BL --screenshot-fps 5 --video-fps 30
```

웨이포인트 파일 형식:
```json
[
  {"idx": 0, "x": 227.4, "y": 217.3, "rot": 40.0},
  {"idx": 1, "x": 523.6, "y": 240.6, "rot": 75.6}
]
```

### 그 외 커맨드

```bash
# 입력 + 화면 동시 녹화 (N초)
python -m test_auto_run_executor.local_runner test-session --duration-sec 10 --backend polling

# 입력만 녹화
python -m test_auto_run_executor.local_runner input-record --duration-sec 5

# 화면만 녹화
python -m test_auto_run_executor.local_runner screen-record --duration-sec 5 --screenshot-fps 5 --video-fps 30

# action 파일 재생
python -m test_auto_run_executor.local_runner player-file path/to/input.json
```

---

## 변수 대응 (OptimizedNavigator)

auto-run 중 아래 상황이 발생해도 자동으로 대응합니다.

| 상황 | 감지 방법 | 대응 |
|------|-----------|------|
| 적 공격으로 밀림 / 게임 버그 | walk timeout | 현재 위치에서 A* 재계산 후 재시도 |
| Killed & Respawn | 위치가 spawn으로 순간이동 감지 | snippet 처음부터 재시작 (최대 5회) |

> `_get_current_state()` — 팀원 모듈 연결 후 실시간 위치 반영 예정.
> 연결 전까지는 시작 시 입력한 팀 스폰 좌표를 고정값으로 사용합니다.
