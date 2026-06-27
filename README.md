# Modacthon QA Input Prototype

Practice workspace for the Smilegate Modacthon QA track.

```text
human/scripted input -> recording or scenario JSON -> replay -> repeatable QA action flow
```

## Structure

- `assets/`: shared CrossFire/TDM/map/scenario reference JSON files.
- `manager/`: QA PlayTest control server (FastAPI + WebSocket).
- `record_replay/`: 입력 기록 및 재생 프로토타입 (Python + FastAPI).
- `test_scenario_executor/`: 키보드/마우스 기록, 화면 녹화, JSON 액션 재생 모듈.
- `crossfire_qa/`: 영상 분석 파이프라인 (detector / report layer).
- `ui/`: QA 자동화 제어 대시보드 (Electron).

---

## Quick Start (Windows — 권장)

**처음 실행하는 환경에서도 venv 생성 및 패키지 설치를 자동으로 처리합니다.**

```
run_manager.bat  ← 더블클릭
```

실행 흐름:
1. `.venv` 가 없으면 자동 생성 (`python -m venv .venv`)
2. `requirements.txt` 의 모든 의존성 설치
3. `manager.control` 서버 기동 → `http://127.0.0.1:8765`

> **사전 조건**: Python 3.10 이상이 PATH에 등록되어 있어야 합니다.  
> 설치 확인: `python --version`

---

## 전체 의존성 한 번에 설치 (수동)

```bash
# 루트에서 실행
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### Optional: OCR 기능 (crossfire_qa)

EasyOCR 또는 PaddleOCR을 사용할 경우에만 설치합니다.

```bash
pip install -r crossfire_qa/requirements-optional.txt
```

---

## 모듈별 실행

### manager (control server)

```bash
python -m manager.control
# → http://127.0.0.1:8765/playtest/
```

### record_replay

```bash
python record_replay/main.py
```

### test_scenario_executor

```bash
python test_scenario_executor/local_runner.py
```

### crossfire_qa 분석 파이프라인

```bash
python crossfire_qa/run.py
```

### ui (Electron)

```bash
cd ui
npm install
npm start
```

---

## Notes

- `.venv/` is intentionally not tracked (`.gitignore`).
- Runtime recordings under `record_replay/recordings/*.json` are ignored by default.
- CrossFire fullscreen may block keyboard capture from user-mode Python; `record_replay` includes hook, polling, and raw-input recording attempts plus replay tooling.
- The `ui/` Electron app communicates with the Python control server via WebSocket (`ws://127.0.0.1:8765`).
