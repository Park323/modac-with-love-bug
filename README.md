# Lovebug — 게임 무관(game-agnostic) QA 자동화 파이프라인

> **Smilegate Modacthon QA 트랙 최우수상 수상작**의 공개용 기반 코드입니다.
> 한 게임(CrossFire)을 레퍼런스 구현으로 두되, **어느 게임에도 이식 가능한 QA 파이프라인의 뼈대**로 쓰이도록 정리한 브랜치입니다.

![Demo](demo.gif)

---

## 핵심 아이디어 — QA를 "풀 수 있는 문제"로 쪼갠다

플레이 기반 QA는 막연히 보면 "사람이 게임을 잘 돌면서 버그를 찾는 일"이라 자동화가 어렵습니다.
우리는 이 작업을 **3단계로 분할**하고, 각 단계를 다시 **단순화된 하나의 문제로 환원**해서, 짧은 시간 안에 실제로 풀 수 있는 문제로 만들었습니다.

| QA 단계 | 단순화된 문제 | 한 줄 요약 |
|---|---|---|
| ① **QA 타겟 분석** | **맵 상의 목표 지점** | "무엇을 검증할지"를 맵 위 좌표(waypoint)의 나열로 환원 |
| ② **게임 실행** | **목표 지점 경유 이동 시스템** | "어떻게 실행할지"를 목표 지점들을 순서대로 경유하는 자율 주행으로 환원 |
| ③ **실행 결과 분석** | **영상 분석 QA 시스템** | "결과가 맞는지"를 녹화 영상에서 이벤트를 검출·판정하는 문제로 환원 |

이 추상화의 핵심은 **각 단계가 독립적으로 교체·고도화 가능하다**는 점입니다.
타겟 분석기를 바꾸고, 이동 시스템의 비전/입력 드라이버를 갈아끼우고, 영상 분석의 게임별 규칙만 맞추면
**동일한 골격으로 다른 게임의 QA 파이프라인**을 만들 수 있습니다. 이 브랜치는 바로 그 "기초 틀"을 제공하는 것을 목표로 합니다.

---

## 단계별 구성

### ① QA 타겟 분석 → 맵 상의 목표 지점

자연어 시나리오("침투 후 좌측 끝 통제실 진입")를 **맵 좌표 기반 waypoint 목록**으로 변환합니다.

- `record_replay/src/scenario_to_waypoints.py` — LLM으로 시나리오 → waypoint(`x_map, y_map, rot, action, label`) 변환
- `record_replay/src/query_to_snippets.py` — 자연어 질의 → waypoint 스니펫
- `assets/mapinfo.json` — 맵 기하(벽/장애물 폴리곤). 좌표계·경로 계획의 기준

> **일반화 포인트:** 맵 정의(`mapinfo.json`)와 좌표 보정값만 게임/맵별로 바꾸면 됩니다.

### ② 게임 실행 → 목표 지점 경유 이동 시스템

목표 지점들을 순서대로 경유하도록 캐릭터를 자율 주행시키고, 그 과정을 입력 로그·화면으로 기록합니다.
`capture → analyze → play` 루프가 약 10Hz로 돕니다.

- `manager/` — 제어 서버(FastAPI, `:8765`). `capture/analyze/play`를 추상 인터페이스(`ICaptureModule`/`IAnalysisModule`/`IPlayModule`)로 묶어 오케스트레이션
- `auto_run_action/` — 미니맵 위치추정(`radar.py`) + 조향 로직(`step.py`) + 경로계획(`pathfinder.py`). "현재 위치 → 다음 입력" 결정
- `test_scenario_executor/` — 입력 기록/재생 + 화면(영상·스크린샷) 캡처. 실제 액추에이터·레코더
- `record_replay/` — 입력 기록/재생 + 템플릿 매칭 검출 프로토타입
- `ui/` — Electron 제어 대시보드(`lovebug`). 서버를 띄우고 브라우저 UI로 제어

> **일반화 포인트:** 위치추정(비전)과 입력(키/마우스 드라이버)을 `IAnalysisModule`/`IPlayModule` 구현으로 교체하면 다른 게임에 이식됩니다. 인터페이스는 [`ARCHITECTURE.md`](ARCHITECTURE.md) 참고.

### ③ 실행 결과 분석 → 영상 분석 QA 시스템

녹화된 게임플레이 영상을 분석해 킬/데스/스코어/리스폰 등 이벤트를 검출하고, **PASS/FAIL/UNCERTAIN** 으로 판정한 뒤 근거(클립·프레임·ROI)를 묶은 리포트를 생성합니다.

- `crossfire_qa/detector_layer/` — UI/스코어/알림/게임상태/리스폰/스폰위치 등 단계별 검출기
- `crossfire_qa/report_layer/` — 타임라인 통합(`global_temporal_aggregator`) → 규칙 판정(`qa_rule_engine`) → 근거 리포트(`evidence_report_generator`)
- `crossfire_qa/run.py` — 9단계 파이프라인 엔드투엔드 오케스트레이터

> **일반화 포인트:** 검출기는 이미 config 기반입니다. HUD ROI 좌표(`configs/roi_config.*.json`), 이벤트 분류, 판정 규칙(`configs/qa_rule_config.*.json`)만 게임별로 정의하면 됩니다.

---

## 디렉터리 구조

```text
.
├── manager/                 ② 제어 서버 (FastAPI, capture→analyze→play 오케스트레이션)
├── auto_run_action/         ② 미니맵 위치추정 + 조향 + 경로계획
├── test_scenario_executor/  ② 입력 기록/재생 + 화면 캡처
├── record_replay/           ①② 시나리오→waypoint 변환 + 입력 기록/재생
├── crossfire_qa/            ③ 영상 분석 QA 파이프라인 (detector / report)
├── ui/                      ② Electron 제어 대시보드 (lovebug)
├── assets/                  맵/시나리오/컨트롤 등 레퍼런스 데이터
└── tests/                   pytest 스위트
```

전체 데이터 흐름과 모듈 인터페이스는 [`ARCHITECTURE.md`](ARCHITECTURE.md)에 정리되어 있습니다.

---

## 빠른 시작

> 게임 입력 주입은 Windows 전용입니다(Win32 `SendInput`). 영상 분석(`crossfire_qa`)은 OS 무관하게 동작합니다.

### Windows (권장) — 더블클릭 한 번

```text
run_manager.bat
```

`.venv` 자동 생성 → `requirements.txt` 설치 → 제어 서버 기동(`http://127.0.0.1:8765`).
사전 조건: Python 3.10+ 가 PATH에 등록되어 있어야 합니다.

### 수동 설치

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

OCR 기능(`crossfire_qa`, 선택)은 필요할 때만:

```bash
pip install -r crossfire_qa/requirements-optional.txt
```

### 모듈별 실행

```bash
# ② 제어 서버 — PlayTest/Dashboard/Record UI 제공
python -m manager.control          # → http://127.0.0.1:8765/playtest/

# ② Electron 대시보드 (서버를 자동 기동)
cd ui && npm install && npm start

# ③ 영상 분석 파이프라인
python crossfire_qa/run.py --dataset <videos_dir> --out outputs/final_qa

# 입력 기록/재생 (개별 실행)
python -m test_scenario_executor.local_runner test-session --duration-sec 5
python record_replay/main.py
```

---

## 다른 게임에 적용하려면

이 브랜치는 "CrossFire용 완제품"이 아니라 **"새 게임용 QA 파이프라인을 빠르게 만드는 출발점"** 입니다.
단계별로 손대야 하는 곳은 다음과 같습니다.

| 단계 | 게임별로 정의/교체할 것 |
|---|---|
| ① 타겟 분석 | `assets/mapinfo.json`(맵 기하), 좌표 보정값, 시나리오 프롬프트 |
| ② 게임 실행 | 위치추정 비전(`IAnalysisModule`), 입력 드라이버(`IPlayModule`), 화면 캡처(`ICaptureModule`) |
| ③ 결과 분석 | HUD ROI 좌표, 이벤트 분류, 판정 규칙(`crossfire_qa/configs/*.json`) |

골격(루프·인터페이스·리포트 포맷)은 그대로 두고, 위의 **게임 특화 값과 드라이버만 교체**하는 것이 설계 의도입니다.

---

## 라이선스 / 크레딧

Smilegate Modacthon QA 트랙 출품작. 게임 자산(CrossFire 관련)의 권리는 각 권리자에게 있으며,
이 저장소는 QA 자동화 파이프라인의 구조와 코드를 공개·재사용 가능한 형태로 정리한 것입니다.
