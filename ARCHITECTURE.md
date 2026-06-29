# Architecture

이 문서는 Lovebug QA 파이프라인의 **데이터 흐름**과 **확장 지점(인터페이스)** 을 정리합니다.
개념적 배경(QA를 3단계로 쪼갠 이유)은 [`README.md`](README.md)를 먼저 참고하세요.

핵심 설계 원칙은 하나입니다: **골격은 고정, 게임 특화 부분은 인터페이스 뒤로 격리.**
새 게임에 이식할 때는 아래 인터페이스의 구현체만 갈아끼웁니다.

---

## 전체 흐름

```text
 ① 타겟 분석                    ② 게임 실행 (manager loop @~10Hz)              ③ 결과 분석
┌──────────────┐   waypoints   ┌──────────────────────────────────────┐    ┌──────────────────┐
│ 자연어 시나리오 │ ───────────▶ │  capture ─▶ analyze ─▶ play           │    │ crossfire_qa     │
│      ↓        │              │  (screen)   (vision)   (key/mouse)     │    │  detector layer  │
│ scenario_to_  │              │     ▲          │           │           │    │       ↓          │
│  waypoints    │              │     └──────────┘ (다음 입력 결정)        │    │  report layer    │
│ (LLM)         │              │                                        │    │  PASS/FAIL/...   │
└──────────────┘              │  + 입력 로그 / 화면(mp4·png) 기록 ───────┼──▶ │  + 근거 리포트     │
                              └──────────────────────────────────────┘    └──────────────────┘
                                          ▲ HTTP/JSON                            ▲ run.py
                                  ┌───────┴────────┐                      녹화 영상 입력
                                  │ ui/ (Electron) │
                                  └────────────────┘
```

- ②의 출력(녹화 영상)이 ③의 입력이 됩니다. 두 단계는 파일(영상)로 느슨하게 결합되어 독립 실행도 가능합니다.
- `ui/`(Electron, `lovebug`)는 `manager.control` 서버를 자식 프로세스로 띄우고 HTTP로 제어합니다.

---

## ② 게임 실행 — 제어 루프와 인터페이스

`manager/`는 세 가지 추상 인터페이스(`manager/modules.py`)로 루프를 구성합니다.
실제 구현(`*_real.py`)과 테스트용 스텁(`*_stub.py`)이 동일 인터페이스를 만족하므로, 게임/플랫폼별 교체가 자유롭습니다.

| 인터페이스 | 역할 | 레퍼런스 구현 | 게임 이식 시 교체 대상 |
|---|---|---|---|
| `ICaptureModule` | `begin / next() → Frame / end` — 최신 화면 프레임 공급 | `capture_real.py` (`ScreenRecorder` 래핑) | 보통 그대로 사용 |
| `IAnalysisModule` | `analyze(frame) → [InputItem]` — 다음 입력 결정 | `analysis_autorun.py` → `auto_run_action` | **위치추정 비전 / 조향 로직** |
| `IPlayModule` | `begin / dispatch(item) / end` — 입력 실행 | `play_real.py` (`ActionPlayer` 래핑) | **입력 드라이버**(OS/주입 방식) |

오케스트레이터는 세 가지:

- `RunController` (`manager/runner.py`) — 녹화된 시나리오 JSON을 타임스탬프대로 재생
- `AutoRunController` (`manager/autorun_controller.py`) — `capture→analyze→play` 자율 주행 루프
- `RecordSession` (`manager/recorder_session.py`) — 배경 입력 기록

### 자율 주행 핵심 (`auto_run_action/`)

```text
frame ─▶ position.get_position(frame)   # radar.py: 미니맵 템플릿 매칭 → {x, y, rot}
            ↓
        step.next_event(position, waypoints)
            ↓
   {"type":"mouse_move", "dx":…}   회전
   {"type":"key_down","key":"W"}   전진
   None                            목표 도달
```

`pathfinder.py`(A*)가 `assets/mapinfo.json`의 벽/장애물을 피하는 경로를 만들고,
`step.py`가 현재 위치/방향에서 다음 단일 입력을 산출합니다.

> **공유 입력 코어:** 저수준 키/마우스 주입(`keys.py`, `win_input.py`)은 `record_replay/src/`에 단일 정본으로 두고,
> `auto_run_action`·`test_scenario_executor`는 이를 re-export하는 얇은 셰임을 둡니다(중복 제거). 정본만 수정하면 세 모듈에 반영됩니다.

---

## ③ 결과 분석 — 검출 → 통합 → 판정 → 근거

`crossfire_qa/run.py`가 영상 1개당 9단계를 순차 실행합니다.

```text
video ─▶ [detector_layer]                         ─▶ [report_layer]
         01 ui            화면 ROI 추출               07 global_temporal  타임라인 통합·링크
         02 kill_count    스코어 OCR/템플릿            08 qa_rules         규칙 판정 PASS/FAIL
         03 notifications  킬피드/데스 패널            09 evidence_report  클립·프레임·ROI 근거
         04 game_state    생존/사망/킬캠 상태
         05 respawn       사망→리스폰 매칭            ─▶ final_report.json / .md
         06 spawn_location 스폰 위치(옵션)
```

- 검출기는 모두 **config 주도**입니다(`crossfire_qa/configs/*.example.json`). ROI 좌표·임계값·규칙 토글이 코드 밖에 있습니다.
- report layer(타임라인 통합·근거 패키징)는 거의 게임 무관합니다. 게임별로 바뀌는 건 주로 ROI와 규칙 정의입니다.

---

## 데이터 계약 (핵심 포맷)

**입력 기록/시나리오 이벤트** (record/replay 공통):

```json
{ "schema_version": "1.0",
  "session": { "session_id": "...", "duration_sec": 42.5, "event_count": 250 },
  "events": [
    { "t": 0.0, "type": "key_down",   "key": "W", "scan": 17, "extended": false },
    { "t": 0.1, "type": "mouse_move", "dx": 20,  "dy": -5 }
  ] }
```

**waypoint** (① → ②):

```json
{ "x_map": 120.0, "y_map": 340.0, "rot": 90.0, "action": null, "label": "통제실 입구" }
```

**최종 리포트** (③): `final_report.json` — `events`, `qa_checks`, `decision_traces`, `evidence index` 포함.
예시는 `crossfire_qa/sample_report.json` 참고.

---

## 게임 이식 체크리스트

| 단계 | 손대는 곳 | 비고 |
|---|---|---|
| ① | `assets/mapinfo.json`, 좌표 보정, 시나리오 프롬프트 | 맵/좌표계 정의 |
| ② | `IAnalysisModule`(비전·조향), `IPlayModule`(입력) 구현 | 인터페이스만 만족하면 루프는 그대로 |
| ③ | `crossfire_qa/configs/*.json` (ROI·이벤트·규칙) | 검출기 코드는 대개 재사용 |

골격(`manager` 루프, 인터페이스, 리포트 포맷)은 **고정**입니다. 위 세 곳만 게임별로 정의하면 동일 파이프라인이 동작합니다.
