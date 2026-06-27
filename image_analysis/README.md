# Score Reader

Extracts the left-side player team/score and right-side opponent team/score
from the fixed CrossFire HUD layout.

## Install

```powershell
..\.venv\Scripts\python.exe -m pip install -r image_analysis\requirements.txt
```

## Run

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m image_analysis.score_reader path\to\scoreboard.png
```

Expected output:

```json
{"my": {"team": "GR", "score": 0}, "opp": {"team": "BL", "score": 0}}
```

The left area is always treated as `my`; OCR does not infer ownership from
team color or team name.

## PaddleOCR version

PaddleOCR is not necessarily a smaller framework install, but this version
uses only the lightweight English recognition model and skips text detection.
It is intended to be faster after one-time model initialization.

Install the Windows CPU inference engine and PaddleOCR:

```powershell
.\.venv\Scripts\python.exe -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.\.venv\Scripts\python.exe -m pip install -r image_analysis\requirements_paddle.txt
```

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m image_analysis.paddle_score_reader path\to\scoreboard.png
```

The first run downloads `en_PP-OCRv5_mobile_rec`; reuse the same process/model
for repeated frames so model startup is paid only once. The implementation uses
the dynamic Paddle engine to avoid the heavier static-engine startup path.
