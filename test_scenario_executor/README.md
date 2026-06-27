# test_scenario_executor

Windows에서 키보드/마우스 입력을 기록하고, 화면을 저장하며, 외부앱이 전달한 JSON 액션을 재생하기 위한 독립 모듈입니다.

## 제공 기능

- `input/logger.py`: 실시간 키보드/마우스 입력을 기록하고 JSON으로 저장합니다.
- `screen/recorder.py`: 기능 시작부터 종료까지 30fps 스크린샷과 MP4 동영상을 저장합니다.
- `playback/player.py`: 외부앱에서 받은 JSON array를 키보드/마우스 입력으로 재생합니다.
- `core/session_paths.py`: 세션별 저장 폴더와 manifest 경로를 관리합니다.
- `api.py`: 외부앱과 통신하기 위한 FastAPI 엔드포인트를 제공합니다.


## 저장 위치

결과는 세션별 폴더에 모입니다.

```text
test_scenario_executor_output/
  {session_id}_{timestamp}/
    input_recording/
      input.json
    screen_recording/
      screenshots/
        screenshot_20260627_120000_000000_000000.png
        screenshot_20260627_120000_033333_000001.png
      screen.mp4
    manifest.json
```

`manifest.json`에는 `test_started_at` 필드로 테스트 시작 시간이 기록됩니다.


## 서버 실행

아래 명령은 테스트 시나리오를 제어하는 HTTP 서버를 실행합니다. 서버가 실행 중이어야 외부앱이 `/test/start`, `/test/stop`, `/player/play` 같은 API를 호출할 수 있습니다.

```bash
pip install -r test_scenario_executor/requirements.txt
python -m uvicorn test_scenario_executor.api:app --host 127.0.0.1 --port 8765
```

venv에서 실행하려면 프로젝트 루트에서 아래 순서대로 실행합니다.

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r test_scenario_executor/requirements.txt
python -m uvicorn test_scenario_executor.api:app --host 127.0.0.1 --port 8765
```

PowerShell에서 스크립트 실행이 막히면 현재 터미널에만 적용되도록 다음 명령을 먼저 실행한 뒤 venv를 활성화합니다.

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

Windows cmd:

```cmd
py -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r test_scenario_executor\requirements.txt
python -m uvicorn test_scenario_executor.api:app --host 127.0.0.1 --port 8765
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r test_scenario_executor/requirements.txt
python -m uvicorn test_scenario_executor.api:app --host 127.0.0.1 --port 8765
```

기본 API 주소는 `http://127.0.0.1:8765`입니다. 외부앱은 `Content-Type: application/json` 헤더로 HTTP 요청을 보내면 됩니다.


## 테스트 실행 API

`/test/start`는 입력 로깅과 화면 녹화를 동시에 시작합니다.

```http
POST /test/start
Content-Type: application/json

{
  "session_id": "tdm_run_001",
  "backend": "hook",
  "sample_hz": 120,
  "fps": 30,
  "screenshot_callback_url": "http://127.0.0.1:9000/screenshots"
}
```

응답에는 입력 로그 저장 예정 경로, 화면 녹화 세션 디렉터리, 동영상 저장 경로, callback 설정값이 포함됩니다. 개별 스크린샷 경로는 시작 응답에 미리 포함되지 않고, 스크린샷 파일이 생성되는 시점마다 `screenshot_callback_url`로 전송됩니다.

```json
{
  "status": "started",
  "session_id": "tdm_run_001",
  "input": {
    "status": "recording",
    "session_dir": "test_scenario_executor_output/tdm_run_001_20260627_120000",
    "test_started_at": "2026-06-27T03:00:00.000000+00:00",
    "save_path": "test_scenario_executor_output/tdm_run_001_20260627_120000/input_recording/input.json"
  },
  "screen": {
    "status": "recording",
    "session_dir": "test_scenario_executor_output/tdm_run_001_20260627_120000",
    "test_started_at": "2026-06-27T03:00:00.000000+00:00",
    "locations": {
      "session_dir": "test_scenario_executor_output/tdm_run_001_20260627_120000",
      "screenshots_dir": "test_scenario_executor_output/tdm_run_001_20260627_120000/screen_recording/screenshots",
      "video_path": "test_scenario_executor_output/tdm_run_001_20260627_120000/screen_recording/screen.mp4",
      "manifest_path": "test_scenario_executor_output/tdm_run_001_20260627_120000/manifest.json"
    },
    "screenshot_callback_url": "http://127.0.0.1:9000/screenshots"
  }
}
```

외부앱이 `screenshot_callback_url`에서 받는 요청 예시는 다음과 같습니다.

```json
{
  "event": "screenshot_saved",
  "session_id": "tdm_run_001",
  "frame_index": 42,
  "t": 1.4,
  "created_at": "2026-06-27T03:00:01.400000+00:00",
  "screenshot_path": "test_scenario_executor_output/tdm_run_001_20260627_120000/screen_recording/screenshots/screenshot_20260627_120001_400000_000042.png",
  "locations": {
    "session_dir": "test_scenario_executor_output/tdm_run_001_20260627_120000",
    "screenshots_dir": "test_scenario_executor_output/tdm_run_001_20260627_120000/screen_recording/screenshots",
    "video_path": "test_scenario_executor_output/tdm_run_001_20260627_120000/screen_recording/screen.mp4",
    "manifest_path": "test_scenario_executor_output/tdm_run_001_20260627_120000/manifest.json"
  }
}
```

`/test/stop`은 입력 로깅을 종료해 JSON으로 저장하고, 화면 녹화도 종료해 MP4와 manifest 저장을 완료합니다.

```http
POST /test/stop
Content-Type: application/json

{
  "session_id": "tdm_run_001"
}
```

## 입력 로거

기록 시작:

```http
POST /input/record/start
{
  "session_id": "tdm_run_001",
  "backend": "hook",
  "sample_hz": 120
}
```

기록 종료 및 저장:

```http
POST /input/record/stop
{
  "session_id": "tdm_run_001"
}
```

입력 기록 JSON은 세션 폴더의 `input_recording/input.json`에 저장됩니다.

## 화면 녹화

녹화 시작:

```http
POST /screen/record/start
{
  "session_id": "tdm_run_001",
  "fps": 30,
  "screenshot_callback_url": "http://127.0.0.1:9000/screenshots"
}
```

시작 응답에는 `session_dir`, `screenshots_dir`, `video_path`, `manifest_path`가 포함됩니다. 이 값은 저장 루트 확인용입니다. 개별 스크린샷 파일 경로는 스크린샷이 생성될 때마다 `screenshot_callback_url`로 POST됩니다.

녹화 종료:

```http
POST /screen/record/stop
```

## 플레이어

JSON array를 직접 전달해서 재생합니다.

```http
POST /player/play
[
  {"t": 0.0, "type": "key_down", "key": "W"},
  {"t": 0.2, "type": "key_up", "key": "W"},
  {"t": 0.3, "type": "mouse_move", "dx": 40, "dy": -10},
  {"t": 0.4, "type": "mouse_click", "button": "left"}
]
```

플레이어는 기존 `record_replay` 스타일의 이벤트도 받을 수 있습니다.

```json
{"type": "mouse_button_down", "button": "left"}
```

또한 `kind/action` 형태의 이벤트도 지원합니다.

```json
{"kind": "mouse", "action": "raw_move", "dx": 20, "dy": 0}
```

## API별 테스트 방법

PowerShell에서는 `curl`이 PowerShell 자체 명령의 별칭일 수 있으므로 `-X`, `-H` 옵션이 실패할 수 있습니다. PowerShell에서는 `Invoke-RestMethod`를 쓰고, cmd에서는 `curl.exe`를 쓰면 됩니다.

로컬 커맨드는 HTTP 서버를 띄우지 않고 API 내부 동작을 직접 실행합니다. 단, `start`와 `stop`처럼 상태가 이어지는 API는 로컬 커맨드 하나가 `start -> 지정 시간 대기 -> stop` 흐름을 재현합니다.

### `GET /status`

HTTP 테스트:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/status" -Method Get
```

cmd:

```cmd
curl.exe http://127.0.0.1:8765/status
```

로컬 커맨드: 서버 상태 API이므로 별도 로컬 동작은 없습니다. 로컬 실행 가능 명령 목록은 아래로 확인합니다.

```powershell
python -m test_scenario_executor.local_runner --help
```

### `POST /test/start` + `POST /test/stop`

입력 로깅과 화면 녹화를 함께 시작하고, 종료 시 입력 JSON, 스크린샷, MP4, manifest를 저장합니다. 로컬 테스트에서는 hook 설치 문제를 피하기 위해 먼저 `polling` 백엔드를 권장합니다.

HTTP 테스트:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/test/start" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"session_id":"local_test_001","backend":"polling","sample_hz":120,"fps":30}'
```

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/test/stop" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"session_id":"local_test_001"}'
```

cmd:

```cmd
curl.exe -X POST http://127.0.0.1:8765/test/start -H "Content-Type: application/json" -d "{\"session_id\":\"local_test_001\",\"backend\":\"polling\",\"sample_hz\":120,\"fps\":30}"
curl.exe -X POST http://127.0.0.1:8765/test/stop -H "Content-Type: application/json" -d "{\"session_id\":\"local_test_001\"}"
```

로컬 커맨드:

```powershell
python -m test_scenario_executor.local_runner test-session --session-id local_test_001 --backend polling --duration-sec 5 --fps 30
```

callback까지 확인하려면 외부앱 역할을 하는 서버를 먼저 띄운 뒤 callback URL을 넘깁니다.

```powershell
python -m test_scenario_executor.local_runner test-session --session-id local_test_001 --backend polling --duration-sec 5 --fps 30 --screenshot-callback-url http://127.0.0.1:9000/screenshots
```

### `POST /input/record/start` + `POST /input/record/stop`

입력 로깅만 시작하고, 종료 시 JSON으로 저장합니다.

HTTP 테스트:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/input/record/start" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"session_id":"local_input_001","backend":"polling","sample_hz":120}'
```

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/input/record/stop" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"session_id":"local_input_001"}'
```

cmd:

```cmd
curl.exe -X POST http://127.0.0.1:8765/input/record/start -H "Content-Type: application/json" -d "{\"session_id\":\"local_input_001\",\"backend\":\"polling\",\"sample_hz\":120}"
curl.exe -X POST http://127.0.0.1:8765/input/record/stop -H "Content-Type: application/json" -d "{\"session_id\":\"local_input_001\"}"
```

로컬 커맨드:

```powershell
python -m test_scenario_executor.local_runner input-record --session-id local_input_001 --backend polling --duration-sec 5
```

### `POST /screen/record/start` + `POST /screen/record/stop`

화면 녹화만 시작하고, 종료 시 스크린샷, MP4, manifest를 저장합니다.

HTTP 테스트:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/screen/record/start" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"session_id":"local_screen_001","fps":30}'
```

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/screen/record/stop" `
  -Method Post
```

cmd:

```cmd
curl.exe -X POST http://127.0.0.1:8765/screen/record/start -H "Content-Type: application/json" -d "{\"session_id\":\"local_screen_001\",\"fps\":30}"
curl.exe -X POST http://127.0.0.1:8765/screen/record/stop
```

로컬 커맨드:

```powershell
python -m test_scenario_executor.local_runner screen-record --session-id local_screen_001 --duration-sec 5 --fps 30
```

### `GET /screen/record/status`

HTTP 테스트:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/screen/record/status" -Method Get
```

cmd:

```cmd
curl.exe http://127.0.0.1:8765/screen/record/status
```

로컬 커맨드: 서버에 떠 있는 화면 녹화 상태를 조회하는 API라 별도 로컬 동작은 없습니다. 화면 녹화 동작 자체는 `screen-record` 커맨드로 확인합니다.

### `POST /player/play`

JSON array를 받아 실제 키보드/마우스 입력을 발생시킵니다. 메모장 같은 안전한 창을 열어 둔 상태에서 짧게 테스트하세요.

HTTP 테스트:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/player/play" `
  -Method Post `
  -ContentType "application/json" `
  -Body '[{"t":0.0,"type":"key_press","key":"W","duration_ms":50}]'
```

cmd:

```cmd
curl.exe -X POST http://127.0.0.1:8765/player/play -H "Content-Type: application/json" -d "[{\"t\":0.0,\"type\":\"key_press\",\"key\":\"W\",\"duration_ms\":50}]"
```

로컬 커맨드:

```powershell
python -m test_scenario_executor.local_runner player-sample --key W --delay-sec 3
```

### `POST /player/play-file`

JSON action 파일을 읽어 재생합니다.

HTTP 테스트:

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8765/player/play-file" `
  -Method Post `
  -ContentType "application/json" `
  -Body '{"path":"test_scenario_executor_output/local_test_001_20260627_120000/input_recording/input.json"}'
```

cmd:

```cmd
curl.exe -X POST http://127.0.0.1:8765/player/play-file -H "Content-Type: application/json" -d "{\"path\":\"test_scenario_executor_output/local_test_001_20260627_120000/input_recording/input.json\"}"
```

로컬 커맨드:

```powershell
python -m test_scenario_executor.local_runner player-file test_scenario_executor_output/local_test_001_20260627_120000/input_recording/input.json
```

### `POST /player/stop`

현재 실행 중인 player를 중지합니다.

HTTP 테스트:

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8765/player/stop" -Method Post
```

cmd:

```cmd
curl.exe -X POST http://127.0.0.1:8765/player/stop
```

로컬 커맨드: 로컬 player 커맨드는 짧은 동기 실행이라 별도 stop 명령이 없습니다. 긴 action 파일을 중단하려면 실행 중인 터미널에서 `Ctrl+C`를 누릅니다.

### 저장 파일 확인

PowerShell:

```powershell
Get-ChildItem test_scenario_executor_output
Get-ChildItem test_scenario_executor_output\local_test_001_20260627_120000
```

cmd:

```cmd
dir test_scenario_executor_output
dir test_scenario_executor_output\local_test_001_20260627_120000
```
