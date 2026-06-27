import sys

from manager.config import load_config
from manager.sources import FixedFileFrameSource
from manager.transport import WebSocketTransport
from manager.streamer import FrameStreamer


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "qa_manager.ini"
    cfg = load_config(config_path)

    if not cfg.server_url or not cfg.png_path:
        print(f"config missing server_url or png_path (file: {config_path})",
              file=sys.stderr)
        return 1

    try:
        source = FixedFileFrameSource(cfg.png_path)
    except FileNotFoundError as e:
        print(f"png not found: {e}", file=sys.stderr)
        return 3

    transport = WebSocketTransport()
    if not transport.connect(cfg.server_url):
        print(f"failed to connect: {cfg.server_url}", file=sys.stderr)
        return 2

    print(f"streaming {cfg.png_path} -> {cfg.server_url} at {cfg.fps} fps "
          f"(Ctrl+C to stop)")
    streamer = FrameStreamer(source, transport, cfg.fps)
    try:
        streamer.run()
    except KeyboardInterrupt:
        streamer.stop()
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
