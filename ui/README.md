# lovebug UI

QA 자동화 테스트 실행과 결과 분석을 위한 정적 HTML 초안입니다.
두 화면을 서로 다른 사람이 병렬로 개발할 수 있도록 화면별 폴더와 공통 리소스를 분리했습니다.

## 폴더 구조

```text
ui/
  common/
    core.js          # 공통 유틸, 외부 브리지 접근, 공통 UI 헬퍼
    styles.css      # 공통 디자인 토큰, 레이아웃, 컴포넌트 스타일
  trigger/
    index.html      # 테스트 트리거 화면
    trigger.js      # 트리거 화면 전용 동작
  dashboard/
    index.html      # 결과 분석 대시보드 화면
    dashboard.js    # 대시보드 화면 전용 동작
  trigger.html      # 기존 URL 호환용 redirect
  dashboard.html    # 기존 URL 호환용 redirect
```

## 담당 영역

Trigger 담당자는 `ui/trigger/` 안에서 테스트 실행 설정, preset, 반복 횟수, 실행 상태 UI를 확장합니다.

Dashboard 담당자는 `ui/dashboard/` 안에서 동영상 디렉토리 입력, 분석 실행, 리포트 표시 영역을 확장합니다.

공통 레이아웃, 색상, 버튼, 패널, metric, progress, tag 스타일은 `ui/common/styles.css`에서 관리합니다. 두 화면에 동시에 영향을 주는 변경이므로 수정 전 화면 양쪽을 확인하는 것을 권장합니다.

## 외부 프로그램 연결 지점

각 화면은 `window.LovebugBridge`가 있으면 해당 함수를 호출하고, 없으면 콘솔에 payload를 출력합니다.

```js
window.LovebugBridge = {
  startTestRun(payload) {
    // Trigger 화면에서 테스트 시작 시 호출
  },
  analyzeVideos(payload) {
    // Dashboard 화면에서 분석 시작 시 호출
  }
};
```

Trigger payload:

```json
{
  "project": "lovebug",
  "preset": "tdm-smoke",
  "repeatCount": 3,
  "requestedAt": "ISO-8601 timestamp"
}
```

Dashboard payload:

```json
{
  "project": "lovebug",
  "videoDirectory": "/path/to/results/videos",
  "requestedAt": "ISO-8601 timestamp"
}
```

## 로컬 확인

프로젝트 루트에서 정적 서버를 실행한 뒤 아래 주소로 확인합니다.

```bash
python3 -m http.server 8123
```

- Trigger: `http://127.0.0.1:8123/ui/trigger/`
- Dashboard: `http://127.0.0.1:8123/ui/dashboard/`

기존 주소인 `/ui/trigger.html`, `/ui/dashboard.html`도 새 폴더 화면으로 redirect됩니다.

## 개발 규칙

- 화면 전용 로직은 각 화면 폴더의 JS에 둡니다.
- 두 화면에서 재사용할 유틸만 `common/core.js`로 이동합니다.
- 새 UI 컴포넌트가 두 화면 모두에 필요하면 `common/styles.css`에 추가합니다.
- 한 화면에만 쓰는 매우 특수한 스타일은 해당 HTML에 새 class를 붙이고, 충돌 가능성이 낮은 이름을 사용합니다.
