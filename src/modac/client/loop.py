"""The agent loop: grab a frame, ask the server for an action, apply it.

Talks to the server over WebSocket by default (one persistent connection, one
Action per frame); HTTP is available via --transport http for debugging. The
game-side worker can run this as-is (crossfire adapter) or reimplement it in
another language against the same /stream contract.

    modac-agent --adapter mock --server ws://127.0.0.1:8000 --fps 20
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from modac.adapters.base import EnvAdapter
from modac.events import SessionRecorder
from modac.protocol import Action, encode_frame


def _ws_url(server: str) -> str:
    """Normalize a server address to a ws(s)://host:port/stream URL."""
    s = server.rstrip("/")
    if s.startswith("http://"):
        s = "ws://" + s[len("http://"):]
    elif s.startswith("https://"):
        s = "wss://" + s[len("https://"):]
    elif not s.startswith(("ws://", "wss://")):
        s = "ws://" + s
    if not s.endswith("/stream"):
        s += "/stream"
    return s


def run(adapter: EnvAdapter, server: str, fps: float = 20.0,
        max_steps: int | None = None, jpeg_quality: int = 80,
        record: str | None = None, transport: str = "ws") -> None:
    period = 1.0 / fps if fps > 0 else 0.0
    recorder = SessionRecorder(session_id="bot_run") if record else None
    session_start = time.time()

    def loop(infer) -> None:
        """Drive the adapter through `infer`, which maps frame bytes -> Action."""
        step = 0
        while max_steps is None or step < max_steps:
            t0 = time.time()
            frame = adapter.grab()
            action = infer(encode_frame(frame, quality=jpeg_quality))
            adapter.apply(action)
            if recorder is not None:
                recorder.record(action, t=t0 - session_start)
            step += 1
            elapsed = time.time() - t0
            if elapsed < period:
                time.sleep(period - elapsed)

    try:
        if transport == "ws":
            from websockets.sync.client import connect

            with connect(_ws_url(server), max_size=None) as ws:
                def infer(payload: bytes) -> Action:
                    ws.send(payload)
                    return Action(**json.loads(ws.recv()))

                loop(infer)
        else:
            import httpx

            url = server.rstrip("/") + "/act"
            if url.startswith(("ws://", "wss://")):
                url = url.replace("ws", "http", 1)
            client = httpx.Client(timeout=5.0)
            try:
                def infer(payload: bytes) -> Action:
                    resp = client.post(
                        url, content=payload, headers={"Content-Type": "image/jpeg"}
                    )
                    resp.raise_for_status()
                    return Action(**resp.json())

                loop(infer)
            finally:
                client.close()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        adapter.close()
        if recorder is not None:
            recorder.dump(
                record,
                recorded_at=datetime.now(timezone.utc).isoformat(),
                duration_sec=time.time() - session_start,
            )
            print(f"recorded {len(recorder.events)} events -> {record}")


def build_adapter(name: str) -> EnvAdapter:
    if name == "mock":
        from modac.adapters.mock_adapter import MockAdapter

        return MockAdapter()
    if name == "crossfire":
        from modac.adapters.crossfire_windows import CrossFireWindowsAdapter

        return CrossFireWindowsAdapter()
    raise SystemExit(f"Unknown adapter '{name}' (choose: mock, crossfire)")


def main() -> None:
    p = argparse.ArgumentParser(description="Run the MODAC agent loop.")
    p.add_argument("--adapter", default="mock", choices=["mock", "crossfire"])
    p.add_argument("--server", default="ws://127.0.0.1:8000")
    p.add_argument("--transport", default="ws", choices=["ws", "http"],
                   help="ws (default, persistent /stream) or http (/act, debug)")
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--jpeg-quality", type=int, default=80)
    p.add_argument("--record", default=None,
                   help="save the bot's actions to a tdm_run-format JSON at this path")
    args = p.parse_args()

    adapter = build_adapter(args.adapter)
    run(adapter, args.server, args.fps, args.max_steps, args.jpeg_quality,
        args.record, args.transport)


if __name__ == "__main__":
    main()
