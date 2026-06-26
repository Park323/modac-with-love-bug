# Game QA Clone

Windows 게임 QA용 키보드/마우스 시나리오 녹화/재생 도구입니다.

외부 패키지 없이 Python 3 표준 라이브러리와 Windows API만 사용합니다.

## 사용법

PowerShell 또는 CMD에서 실행합니다.

```powershell
python game_qa_clone.py record scenarios\login_run.json
```

게임을 사람이 플레이한 뒤 `Ctrl+Shift+F12`를 누르면 녹화가 종료되고 JSON 파일이 저장됩니다.

```powershell
python game_qa_clone.py inspect scenarios\login_run.json
```

저장된 이벤트 개수, 길이, 키보드/마우스 이벤트 수를 확인합니다.

```powershell
python game_qa_clone.py play scenarios\login_run.json
```

3초 카운트다운 동안 게임 창을 포커스하면 녹화된 입력과 입력 간격이 재현됩니다.

## 옵션

```powershell
python game_qa_clone.py record scenarios\run.json --move-interval 0.02
python game_qa_clone.py play scenarios\run.json --speed 1.5 --countdown 5
```

- `--move-interval`: 마우스 이동 이벤트 저장 간격입니다. 기본값은 `0.01`초입니다.
- `--speed`: 재생 속도입니다. `2.0`은 2배속, `0.5`는 절반 속도입니다.
- `--countdown`: 재생 전 대기 시간입니다.

## 주의사항

- Windows에서만 실행됩니다.
- 관리자 권한으로 실행 중인 게임에 입력하려면 이 프로그램도 관리자 권한으로 실행해야 할 수 있습니다.
- 일부 게임, 특히 안티치트가 있는 게임은 보안 정책상 합성 입력을 차단할 수 있습니다.
- 재생은 화면 좌표 기반입니다. 해상도, 모니터 배치, 게임 창 위치가 녹화 시점과 같을수록 안정적입니다.
