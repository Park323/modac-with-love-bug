import os
import subprocess
import sys

# tkinter는 스레드 안전하지 않다. FastAPI는 sync 핸들러를 스레드풀 워커에서
# 돌리므로, 워커 스레드에서 Tk()를 만들면 다른 스레드에서 파괴될 때
# "Tcl_AsyncDelete: async handler deleted by the wrong thread"로 프로세스가
# 통째로 죽는다. 그래서 다이얼로그는 별도 서브프로세스(자기 메인스레드)에서
# 실행한다 — 크로스스레드 없음, 서버 프로세스와 완전 격리.
_DIALOG_SCRIPT = (
    "import tkinter\n"
    "from tkinter import filedialog\n"
    "root = tkinter.Tk()\n"
    "root.withdraw()\n"
    "root.attributes('-topmost', True)\n"
    "p = filedialog.askopenfilename(\n"
    "    title='시나리오 JSON 선택',\n"
    "    filetypes=[('JSON', '*.json'), ('All files', '*.*')],\n"
    ")\n"
    "import sys\n"
    "sys.stdout.write(p or '')\n"
)

_DIALOG_DIR_SCRIPT = (
    "import tkinter\n"
    "from tkinter import filedialog\n"
    "root = tkinter.Tk()\n"
    "root.withdraw()\n"
    "root.attributes('-topmost', True)\n"
    "p = filedialog.askdirectory(title='결과 동영상 폴더 선택')\n"
    "import sys\n"
    "sys.stdout.write(p or '')\n"
)


def pick_directory() -> str | None:
    """OS 폴더 선택 다이얼로그. 취소 시 None."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _DIALOG_DIR_SCRIPT],
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    path = (proc.stdout or "").strip()
    return path or None


def pick_json_file() -> str | None:
    """OS 파일 다이얼로그로 JSON 경로 선택. 취소 시 None.

    별도 서브프로세스에서 tkinter 실행 (스레드 안전성 확보).
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-c", _DIALOG_SCRIPT],
        capture_output=True,
        encoding="utf-8",
        env=env,
    )
    path = (proc.stdout or "").strip()
    return path or None
