"""Codec between per-frame ``Action`` (model I/O) and the recorder's event
stream format (schema_version 0.1, as in tdm_run_*.json).

Two representations, one vocabulary:

  * Event stream  — edge-triggered (key_down/key_up, mouse_move, mouse_button_*),
    sparse, absolute timestamps. What the capture/injection worker speaks.
  * Frame Action  — level-triggered state per fixed-rate frame. What an
    image->action model emits and consumes.

They convert losslessly enough for record/replay:

    actions  --action_to_events-->  events        (bot output, replayable)
    events   --events_to_actions--> frame actions (training pairs from a demo)

Mouse note: ``Action.yaw/pitch`` are relative mouse counts == ``mouse_move``
``dx/dy`` (same unit the game and SendInput use). The dx/dy in a *polled*
recording are unusable in FPS raw-input mode — capture must use Raw Input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from modac.protocol import Action

SCHEMA_VERSION = "0.1"

# Action bool field -> (key label, set-1 scan code, extended-key flag).
# Scan codes match modac.adapters.win_input.SCAN and the recorder's output.
KEY_EVENTS: dict[str, tuple[str, int, bool]] = {
    "forward": ("W", 17, False),
    "back": ("S", 31, False),
    "left": ("A", 30, False),
    "right": ("D", 32, False),
    "jump": ("Space", 57, False),
    "crouch": ("LControl", 29, False),
    "sprint": ("LShift", 42, False),
    "reload": ("R", 19, False),
    "use": ("E", 18, False),
}

# Weapon slot -> (label, scan). Slots are tapped (down+up) per frame they appear.
WEAPON_KEYS: dict[int, tuple[str, int]] = {
    n: (str(n), 0x02 + (n - 1)) for n in range(1, 10)
}

# Reverse: scan code -> Action field, for decoding recordings back to actions.
_SCAN_TO_FIELD = {scan: field_ for field_, (_, scan, _) in KEY_EVENTS.items()}


def _key_event(t: float, down: bool, label: str, scan: int, extended: bool) -> dict:
    return {
        "t": round(t, 4),
        "type": "key_down" if down else "key_up",
        "key": label,
        "scan": scan,
        "extended": extended,
    }


def action_to_events(prev: Action, cur: Action, t: float) -> list[dict]:
    """Emit the edge events that take the input state from ``prev`` to ``cur``.

    ``prev`` is the action of the previous frame (use ``Action.idle()`` for the
    first frame). ``t`` is the timestamp of ``cur`` (seconds from session start).
    """
    events: list[dict] = []

    # Keyboard: emit on transitions only.
    for field_, (label, scan, ext) in KEY_EVENTS.items():
        was, now = getattr(prev, field_), getattr(cur, field_)
        if now and not was:
            events.append(_key_event(t, True, label, scan, ext))
        elif was and not now:
            events.append(_key_event(t, False, label, scan, ext))

    # Mouse look: one relative move per frame if there's any motion.
    dx, dy = round(cur.yaw), round(cur.pitch)
    if dx or dy:
        events.append({"t": round(t, 4), "type": "mouse_move", "dx": int(dx), "dy": int(dy)})

    # Mouse buttons: fire->left, aim->right, on transitions.
    for field_, button in (("fire", "left"), ("aim", "right")):
        was, now = getattr(prev, field_), getattr(cur, field_)
        if now and not was:
            events.append({"t": round(t, 4), "type": "mouse_button_down", "button": button})
        elif was and not now:
            events.append({"t": round(t, 4), "type": "mouse_button_up", "button": button})

    # Weapon select: tap the slot key.
    if cur.weapon and WEAPON_KEYS.get(cur.weapon):
        label, scan = WEAPON_KEYS[cur.weapon]
        events.append(_key_event(t, True, label, scan, False))
        events.append(_key_event(t, False, label, scan, False))

    return events


def events_to_actions(events: list[dict], fps: float) -> list[Action]:
    """Resample an event stream onto a fixed ``fps`` grid of frame Actions.

    Each frame covers the window (prev, f]: held keys/buttons reflect the state
    at the window's end, mouse motion is summed over the window, and a button
    that clicks-and-releases within one window still registers as pressed.
    Keys not in our action space (arrows, F9, …) are ignored.
    """
    if not events or fps <= 0:
        return []

    evs = sorted(events, key=lambda e: e["t"])
    duration = evs[-1]["t"]
    n_frames = int(duration * fps) + 1

    held: set[str] = set()
    btn = {"left": False, "right": False}
    actions: list[Action] = []
    idx = 0

    for i in range(n_frames):
        # Frame i represents the input state at time i/fps; its window is
        # (previous frame time, i/fps], so events at exactly i/fps land here.
        frame_end = i / fps
        dx = dy = 0
        pressed_this_frame = {"left": False, "right": False}

        while idx < len(evs) and evs[idx]["t"] <= frame_end:
            e = evs[idx]
            idx += 1
            etype = e["type"]
            if etype == "key_down":
                field_ = _SCAN_TO_FIELD.get(e.get("scan"))
                if field_:
                    held.add(field_)
            elif etype == "key_up":
                field_ = _SCAN_TO_FIELD.get(e.get("scan"))
                if field_:
                    held.discard(field_)
            elif etype == "mouse_move":
                dx += e.get("dx", 0)
                dy += e.get("dy", 0)
            elif etype == "mouse_button_down":
                btn[e["button"]] = True
                pressed_this_frame[e["button"]] = True
            elif etype == "mouse_button_up":
                btn[e["button"]] = False

        kwargs = {field_: True for field_ in held}
        actions.append(
            Action(
                **kwargs,
                yaw=float(dx),
                pitch=float(dy),
                fire=btn["left"] or pressed_this_frame["left"],
                aim=btn["right"] or pressed_this_frame["right"],
            )
        )

    return actions


@dataclass
class SessionRecorder:
    """Accumulates bot (or human) actions into the recorder's session schema.

    Feed it one Action per frame via ``record(action, t)``; ``to_dict`` /
    ``dump`` produce JSON byte-for-byte compatible with tdm_run_*.json.
    """

    session_id: str
    game: str = "CrossFire"
    mode: str = "Team Deathmatch"
    map: str = ""
    backend: str = "raw_input"
    note: str = "mouse_move dx/dy are relative raw-input counts"
    events: list[dict] = field(default_factory=list)
    _prev: Action = field(default_factory=Action.idle)

    def record(self, action: Action, t: float) -> None:
        self.events.extend(action_to_events(self._prev, action, t))
        self._prev = action

    def to_dict(self, recorded_at: str, duration_sec: float) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "session": {
                "session_id": self.session_id,
                "game": self.game,
                "mode": self.mode,
                "map": self.map,
                "recorded_at": recorded_at,
                "duration_sec": round(duration_sec, 4),
                "event_count": len(self.events),
            },
            "environment": {"backend": self.backend, "note": self.note},
            "events": self.events,
        }

    def dump(self, path: str | Path, recorded_at: str, duration_sec: float) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(recorded_at, duration_sec), indent=2)
        )


def load_recording(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def main() -> None:
    """Quick inspector: convert a recording to frame actions and summarize."""
    import argparse

    p = argparse.ArgumentParser(description="Inspect/convert a session recording.")
    p.add_argument("recording", help="path to a tdm_run_*.json recording")
    p.add_argument("--fps", type=float, default=20.0)
    args = p.parse_args()

    rec = load_recording(args.recording)
    actions = events_to_actions(rec["events"], args.fps)
    total_yaw = sum(abs(a.yaw) for a in actions)
    total_pitch = sum(abs(a.pitch) for a in actions)
    print(f"{rec['session']['session_id']}: {len(rec['events'])} events "
          f"-> {len(actions)} frames @ {args.fps} fps")
    print(f"summed |yaw|={total_yaw:.0f}  |pitch|={total_pitch:.0f}  "
          f"(near zero => mouse capture was cursor-locked, not raw input)")


if __name__ == "__main__":
    main()
