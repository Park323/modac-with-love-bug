# Dashboard

결과 동영상을 분석하고 결과 디렉토리를 확인하는 화면입니다.

## 파일 구성

| 파일 | 역할 |
|---|---|
| `index.html` | 마크업. 분석 입력 폼, 진행 바, 결과 영역 |
| `dashboard.js` | 렌더러 로직. 버튼 이벤트, 상태 표시, 결과 수신 |

렌더러는 `window.LovebugBridge`(preload에서 주입)만 바라보며, Electron 여부나 mock 여부를 알지 못합니다.

## Python 분석 완료 알림 흐름

```
분석 시작 버튼 클릭
  │
  ▼
dashboard.js
  bridge.analyzeVideos(payload)     ← IPC invoke
  bridge.onAnalysisComplete(cb)     ← IPC on 등록
  │
  ▼
main.js  analyzeVideosReal()
  python3 analyze.py <directory> 실행
  stdout: JSON 수신 대기
  │
  ▼
analyze.py
  분석 완료 시 stdout에 JSON 출력
  {"resultDir": "/path/to/results/run_xxx"}
  exit(0)
  │
  ▼
main.js
  JSON 파싱 → event.sender.send("analysis-complete", result)
  │
  ▼
preload.js
  ipcRenderer.on("analysis-complete") → cb(data) 호출
  │
  ▼
dashboard.js  onAnalysisComplete 콜백
  resultDir 표시, 결과 폴더 열기 버튼 활성화
```

## Python 측 구현 가이드

`analyze.py`는 분석이 끝나면 결과 디렉토리 경로를 stdout에 JSON으로 출력하고 `exit(0)`으로 종료합니다.

```python
import json, sys

result_dir = "/path/to/results/run_20260627_143012"

# result_dir 구조 (Python이 직접 생성)
# result_dir/
#   package_manifest.json
#   assets/
#   data/
#   reports/

print(json.dumps({"resultDir": result_dir}))
sys.exit(0)
```

오류 시 `exit(0)` 이외의 코드로 종료하면 UI에 에러가 전달됩니다.

## 테스트 (mock 모드)

Python 없이 UI 전체 흐름을 테스트할 수 있습니다.

```bash
# ui/ 디렉토리에서
npx electron . --mock
```

`--mock` 플래그가 있으면 `main.js`의 `analyzeVideosMock()`이 호출됩니다.
1.5초 후 `{ resultDir: "ui/mock/results" }`를 IPC로 전송하며, `package_manifest.json`을 읽는 실제와 동일한 경로를 탑니다.
`dashboard.js`와 `index.html`은 mock/real 여부를 전혀 알지 못합니다.
