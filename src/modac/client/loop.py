"""The agent loop: grab a frame, ask the server for an action, apply it.

This is the reference client the game-side worker can run as-is (with the
crossfire adapter) or reimplement in another language against the same /act
contract.

    modac-agent --adapter mock --server http://127.0.0.1:8000 --fps 20
"""

from __future__ import annotations

import argparse
import time

import httpx

from modac.adapters.base import EnvAdapter
from modac.protocol import Action, encode_frame


def run(adapter: EnvAdapter, server: str, fps: float = 20.0,
        max_steps: int | None = None, jpeg_quality: int = 80) -> None:
    url = server.rstrip("/") + "/act"
    period = 1.0 / fps if fps > 0 else 0.0
    client = httpx.Client(timeout=5.0)
    step = 0
    try:
        while max_steps is None or step < max_steps:
            t0 = time.time()
            frame = adapter.grab()
            payload = encode_frame(frame, quality=jpeg_quality)
            resp = client.post(
                url,
                content=payload,
                headers={"Content-Type": "image/jpeg", "X-Frame-Id": str(step)},
            )
            resp.raise_for_status()
            adapter.apply(Action(**resp.json()))
            step += 1
            elapsed = time.time() - t0
            if elapsed < period:
                time.sleep(period - elapsed)
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        adapter.close()
        client.close()


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
    p.add_argument("--server", default="http://127.0.0.1:8000")
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--max-steps", type=int, default=None)
    p.add_argument("--jpeg-quality", type=int, default=80)
    args = p.parse_args()

    adapter = build_adapter(args.adapter)
    run(adapter, args.server, args.fps, args.max_steps, args.jpeg_quality)


if __name__ == "__main__":
    main()
