# Modacthon QA Input Prototype

Practice workspace for the Smilegate Modacthon QA track.

The current `v1` prototype focuses on the input axis:

```text
human/scripted input -> recording or scenario JSON -> replay -> repeatable QA action flow
```

## Structure

- `assets/`: shared CrossFire/TDM/map/scenario reference JSON files.
- `v1/`: input recording and replay prototype.

## Notes

- `.venv/` is intentionally not tracked.
- Runtime recordings under `v1/recordings/*.json` are ignored by default.
- CrossFire fullscreen may block keyboard capture from user-mode Python; `v1` includes hook, polling, and raw-input recording attempts plus replay tooling.

---

# QA PlayTest Manager — 연동 개요

웹에서 JSON 시나리오를 골라 반복 재생하면, Manager가 이벤트를 한 개씩 실제 Play
모듈로 던져 OS 입력을 발생시키고, 동시에 Capture 모듈로 화면을 녹화한다. 아래는
**통신 방식 / 모듈 연동 / JSON 포맷** 요약.

## 전체 그림

```text
[브라우저 ui/playtest]
      │  HTTP REST (same-origin, 127.0.0.1:8765)
      ▼
[Manager 로컬 서버  manager/control/app.py  (FastAPI)]
      │  import (in-process 호출, HTTP 아님)
      ▼
[RunController] ── 이벤트 1개씩 ─▶ [RealPlayModule] ─▶ ActionPlayer._dispatch ─▶ 실제 키/마우스
      │                                                  (test_scenario_executor)
      └── 시작/종료 알림 ─────────▶ [RealCaptureModule] ─▶ ScreenRecorder ─▶ PNG + screen.mp4
```

## 통신 방식 (두 계층)

**웹 ↔ Manager : HTTP REST** — FastAPI `127.0.0.1:8765`. Manager가 `ui/`를
same-origin으로 서빙(CORS 없음). 진행 상황은 웹이 300ms 폴링.

| 메서드 | 경로 | 동작 |
|---|---|---|
| POST | `/scenario/browse` | 파일 다이얼로그 → 선택 경로 `{path}` |
| POST | `/run/start` | `{path, repeat}` 재생 시작 (경로 오류 400, 실행중 409) |
| GET | `/run/status` | `{state, repeat_index, repeat, item_index, total, error}` |
| POST | `/run/stop` | 중단 |

> 파일 다이얼로그(tkinter)는 별도 서브프로세스에서 실행 — FastAPI 스레드풀에서
> 직접 Tk 생성 시 크로스스레드 크래시(`Tcl_AsyncDelete`) 방지.

**Manager ↔ Play / Capture : import (in-process)** — HTTP 아님. 코드 복사도 아님.
같은 레포의 `test_scenario_executor` 패키지를 **직접 import**해 함수 호출.
그쪽 자체 서버(8765)는 안 띄움 → 포트 충돌 없음. 한 파이썬 프로세스에서 동작.

| 우리 어댑터 | 감싸는 실제 객체 | 핵심 호출 |
|---|---|---|
| `manager/play_real.py` `RealPlayModule` | `ActionPlayer` | `_dispatch(action)` = 액션 1개 즉시 실행 |
| `manager/capture_real.py` `RealCaptureModule` | `ScreenRecorder` | `prepare()` → `start()`(스레드) → `stop()` |

## 실행 흐름

```text
/run/start(path, repeat)
  └─ RunController (백그라운드 스레드)
       1. ScenarioReader.read(path) → events[] 리스트
       2. play.begin() + capture.begin()       ← Play·Capture 동시 시작 알림
       3. for _ in range(repeat):
            t_start = now
            for ev in events:
                (realtime) ev["t"] 만큼 대기      ← 원본 녹화 속도 재현
                play.dispatch(InputItem(raw=ev)) → ActionPlayer._dispatch(ev)
       4. play.end() + capture.end()            ← 둘 다 종료(중단/에러 시에도)
```

- **타이밍**: `event["t"]`(초, 시작 기준 절대시간)만큼 대기. 반복마다 기준 리셋.
  `_dispatch`는 즉시(이중 타이밍 방지).
- **중단**: 긴 대기 중에도 ≤50ms 반응.
- **상태**: `idle → running → done | stopped | error`. 동시 실행 1개.

## JSON 포맷

```json
{
  "events": [
    { "t": 0.114, "type": "mouse_move", "dx": -19, "dy": -32 },
    { "t": 0.300, "type": "key_down",  "key": "W", "scan": 17 },
    { "t": 0.450, "type": "key_up",    "key": "W", "scan": 17 }
  ]
}
```

- 최상위 `events` = 리스트, 한 원소 = 이벤트 1개.
- **Manager는 이벤트를 해석하지 않는다.** `ScenarioReader`가 `events[]`만 꺼내고
  각 dict를 그대로 `ActionPlayer._dispatch`로 투명 전달 → 포맷 호환 책임은 Player.
- 운반체 `InputItem(key, action, raw)`: `key=event["type"]`(표시용), `raw=원본`.

ActionPlayer가 아는 타입:

| type | 필드 | 동작 |
|---|---|---|
| `key_down` / `key_up` | `key` 또는 `scan`, `extended?` | 키 누름/뗌 |
| `key_press` | `key`, `duration_ms?` | 누름→대기→뗌 |
| `mouse_move` | `dx`, `dy` | 상대 이동 |
| `mouse_move_abs` | `x`, `y` | 절대 이동 |
| `mouse_button_down` / `_up` | `button` | 버튼 누름/뗌 |
| `mouse_click` | `button`, `duration_ms?` | 클릭 |
| (legacy) `kind`+`action` | record_replay 스타일 | 호환 |

- 모든 이벤트 `t`(초)는 시작 기준 절대 타이밍.
- `input-record`로 만든 `input_recording/input.json`이 그대로 재생 입력(변환 불필요).

## Capture 결과물

```text
test_scenario_executor_output/<session>_<timestamp>/
  screen_recording/screenshots/*.png
  screen_recording/screen.mp4
  manifest.json
```

현재 설정: 폴더 저장만(콜백 없음), screenshot_fps=5 / video_fps=30 기본.

## 실행 / 주의

```cmd
cd /d D:\modac-with-love-bug
.venv\Scripts\python.exe -m manager.control
```

→ 브라우저 `http://127.0.0.1:8765/playtest/` → "JSON 찾기" → 반복 횟수 → 시작.

> ⚠️ Play 모듈은 실제 마우스/키보드를 움직인다. 메모장 같은 안전한 창에서,
> 짧은 시나리오·반복 1회로 먼저 확인할 것.
