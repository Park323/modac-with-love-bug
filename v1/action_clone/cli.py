from __future__ import annotations

import argparse
from pathlib import Path

from .recorder import record_session
from .replayer import replay_session


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="action_clone",
        description="Record and replay keyboard/mouse inputs for the QA input MVP.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Record keyboard/mouse input.")
    record.add_argument(
        "--output",
        default="recordings/tdm_run_001.json",
        help="Output recording JSON path.",
    )
    record.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Optional max recording duration in seconds.",
    )
    record.add_argument(
        "--countdown",
        type=float,
        default=3.0,
        help="Seconds to wait before recording starts.",
    )
    record.add_argument(
        "--start-hotkey",
        default=None,
        help="Optional global hotkey to start recording, for example F8.",
    )
    record.add_argument(
        "--no-beep",
        action="store_true",
        help="Disable audible countdown/start/stop cues.",
    )
    record.add_argument(
        "--backend",
        choices=["hook", "polling", "raw"],
        default="hook",
        help="Input capture backend. Use raw or polling when fullscreen blocks hooks.",
    )
    record.add_argument(
        "--session-id",
        default=None,
        help="Optional session id. Defaults to the output file stem.",
    )
    record.add_argument(
        "--window-title",
        default="CrossFire",
        help="Target window title stored in recording metadata.",
    )
    record.add_argument(
        "--mouse-sample-hz",
        type=float,
        default=60.0,
        help="Maximum mouse move samples per second.",
    )

    replay = subparsers.add_parser("replay", help="Replay a recorded input JSON.")
    replay.add_argument("recording", help="Recording JSON path.")
    replay.add_argument(
        "--window-title",
        default=None,
        help="Window title to focus before replay. Defaults to recording metadata.",
    )
    replay.add_argument(
        "--start-delay",
        type=float,
        default=3.0,
        help="Seconds to wait after focusing the window before replay starts.",
    )
    replay.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="Replay speed multiplier. 1.0 means original timing.",
    )
    replay.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Number of times to replay the recording.",
    )
    replay.add_argument(
        "--dry-run",
        action="store_true",
        help="Print replay events without sending input.",
    )
    replay.add_argument(
        "--no-focus",
        action="store_true",
        help="Do not try to focus the target window before replay.",
    )

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "record":
        output = Path(args.output)
        session_id = args.session_id or output.stem
        record_session(
            output_path=output,
            session_id=session_id,
            duration_sec=args.duration,
            countdown_sec=args.countdown,
            start_hotkey=args.start_hotkey,
            window_title=args.window_title,
            mouse_sample_hz=args.mouse_sample_hz,
            beep=not args.no_beep,
            backend=args.backend,
        )
        return

    if args.command == "replay":
        replay_session(
            recording_path=Path(args.recording),
            window_title=args.window_title,
            start_delay_sec=args.start_delay,
            speed=args.speed,
            repeat=args.repeat,
            dry_run=args.dry_run,
            focus_window=not args.no_focus,
        )
        return

    parser.error(f"Unknown command: {args.command}")
