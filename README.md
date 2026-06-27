# Modacthon QA Input Prototype

Practice workspace for the Smilegate Modacthon QA track.

```text
human/scripted input -> recording or scenario JSON -> replay -> repeatable QA action flow
```

## Structure

- `assets/`: shared CrossFire/TDM/map/scenario reference JSON files.
- `record_replay/`: 입력 기록 및 재생 프로토타입 (Python + FastAPI).
- `test_scenario_executor/`: 키보드/마우스 기록, 화면 녹화, JSON 액션 재생 모듈 (Python + FastAPI).
- `ui/`: QA 자동화 제어 대시보드 (Electron).

## Setup & Run

### record_replay

```bash
cd record_replay
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### test_scenario_executor

```bash
cd test_scenario_executor
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python local_runner.py
```

### ui (Electron)

```bash
cd ui
npm install
npm start
```

## Notes

- `.venv/` is intentionally not tracked.
- Runtime recordings under `record_replay/recordings/*.json` are ignored by default.
- CrossFire fullscreen may block keyboard capture from user-mode Python; `record_replay` includes hook, polling, and raw-input recording attempts plus replay tooling.
- The `ui/` Electron app communicates with a Python analyzer via `window.LovebugBridge`; the Python script path is configured in `ui/main.js`.
