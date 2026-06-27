def pick_json_file() -> str | None:
    """OS 파일 다이얼로그로 JSON 경로 선택. 취소 시 None.

    핸들러마다 독립 Tk root 생성/파괴 (tkinter 스레드 안전성 회피).
    """
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    root.withdraw()
    try:
        path = filedialog.askopenfilename(
            title="시나리오 JSON 선택",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
    finally:
        root.destroy()
    return path or None
