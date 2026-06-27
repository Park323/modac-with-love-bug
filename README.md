# Modacthon QA — CrossFire TDM 입력 자동화

CrossFire TDM (수송선 2.0) 맵에서 QA 입력 자동화를 구현하는 프로토타입입니다.

---

## 폴더 구조

```
modacthon/
├── assets/
│   ├── mapinfo.json             # 맵 폴리곤 데이터 (1980×654 픽셀 좌표계)
│   └── accomplish_snippet.json  # 웨이포인트 시나리오 예시
├── record_replay/
│   ├── hotkey_runner.py         # 수동 녹화 실행기 (F7~F10)
│   ├── auto_run.py              # 자동 경로 실행기
│   ├── src/
│   │   ├── recorder.py          # 키/마우스 입력 녹화
│   │   ├── replayer.py          # 녹화 파일 재생
│   │   ├── navigator.py         # A* 기반 자동 이동
│   │   ├── pathfinder.py        # A* 경로 탐색 (mapinfo 기반)
│   │   └── query_to_snippets.py # 자연어 → 웨이포인트 변환 (Claude API)
│   ├── recordings/              # 녹화 결과 JSON 저장 위치
│   └── requirements.txt
├── map_selector/                # 클라이언트 배포용 맵 UI 툴
│   ├── server.py
│   ├── mapinfo.json
│   ├── index.html / style.css / app.js
└── create_map_image.py          # 맵 미리보기 이미지 생성
```

---

## 환경 세팅

```bash
cd record_replay
python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt
```

주요 패키지: `mss`, `opencv-python`, `numpy`, `anthropic`, `pywin32`

자동 실행 기능 (`auto_run.py`) 사용 시 환경변수 필요:

```bash
set ANTHROPIC_API_KEY=sk-ant-...
```

---

## 1. 수동 녹화 — hotkey_runner.py

게임 안에서 직접 움직이며 키/마우스 입력을 녹화합니다.

```bash
python record_replay/hotkey_runner.py
```

| 키 | 동작 |
|----|------|
| F7 | 현재 화면 캡처 → `recordings/capture_001.png` |
| F8 | 녹화 시작 |
| F9 | 녹화 중지 & JSON 저장 |
| F10 | 프로그램 종료 |

저장 경로: `record_replay/recordings/tdm_run_{타임스탬프}.json`

---

## 2. 자동 경로 실행 — auto_run.py

웨이포인트 시나리오 파일을 읽어 A* 경로로 자동 이동하면서 입력을 녹화합니다.

```bash
python record_replay/auto_run.py assets/accomplish_snippet.json
```

실행 후 팀 선택:

| 키 | 동작 |
|----|------|
| F8 | GR팀으로 시작 (x=1901, y=123, rot=270) |
| F9 | BL팀으로 시작 (x=116, y=261, rot=90) |
| F10 | 실행 중단 & 현재까지 녹화 저장 |

3초 카운트다운 후 자동 이동 시작.
마지막 웨이포인트 도달 또는 F10 중단 시 `recordings/auto_{팀}_{타임스탬프}.json` 저장.

> 팀원 모듈(`get_map_position`)이 붙으면 팀 선택 없이 F8 한 번으로 바뀝니다.

---

## 3. 맵 좌표 선택 UI — map_selector

클라이언트 배포용 툴. 맵 위를 클릭+드래그해서 `x, y, rot`를 뽑아냅니다.

```bash
cd map_selector
python server.py
```

브라우저에서 `http://localhost:8080` 열기.

**사용법:**

1. 맵 위에서 **클릭 + 드래그** — 드래그 방향 = 캐릭터가 바라볼 방향(rot)
2. 마우스 놓으면 웨이포인트 추가
3. 오른쪽 패널에서 **Copy JSON** 또는 **Download** 로 추출

출력 형식:
```json
[
  {"x": 116.0, "y": 261.0, "rot": 90.0},
  {"x": 990.0, "y": 320.0, "rot": 45.0}
]
```

> `pip install` 불필요 — Python 표준 라이브러리만 사용합니다.

---

## 4. 맵 미리보기 이미지 생성 — create_map_image.py

`mapinfo.json` 폴리곤 데이터를 PNG로 시각화합니다.

```bash
# 기본 실행 (assets/map_preview.png 저장)
python create_map_image.py

# 경로 지정
python create_map_image.py assets/mapinfo.json assets/my_preview.png
```

`opencv-python`, `numpy` 필요.

---

## 좌표 체계

| 항목 | 값 |
|------|-----|
| 원점 | 좌측 상단 (0, 0) |
| +x | 오른쪽 |
| +y | 아래쪽 |
| rot 0° | 북쪽 (화면 위) |
| rot 90° | 동쪽 (화면 오른쪽) |
| 회전 방향 | 시계방향 |
| 맵 크기 | 1980 × 654 픽셀 |
| BL 스폰 | x=116, y=261, rot=90 |
| GR 스폰 | x=1901, y=123, rot=270 |
