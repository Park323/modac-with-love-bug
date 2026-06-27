import uvicorn

from manager.control.app import app


def main() -> None:
    print("QA Manager control server: http://127.0.0.1:8765/playtest/")
    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
