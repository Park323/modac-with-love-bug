"""Generate representative sample I/O for the MODAC system.

INPUT  = one game frame (image). OUTPUT = the Action for that frame, shown both
as the model's per-frame Action JSON and as the tdm_run event-stream form.

The frame here comes from the cross-platform MockAdapter (a stand-in for a real
CrossFire capture); the shapes and the contract are identical to live play.
Deterministic (seeded) so the committed samples are stable.

    python samples/make_samples.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from modac.adapters.mock_adapter import MockAdapter
from modac.events import SessionRecorder, action_to_events
from modac.policy.random_policy import RandomPolicy
from modac.protocol import Action, encode_frame

OUT = Path(__file__).resolve().parent


def main() -> None:
    adapter = MockAdapter(width=640, height=360, log=False)
    policy = RandomPolicy(seed=7)  # seeded => reproducible samples

    # --- one representative frame ---
    for _ in range(8):  # advance a few steps so the frame isn't the t=0 edge case
        frame = adapter.grab()

    # INPUT, viewable form
    Image.fromarray(frame).save(OUT / "input_frame.png")
    # INPUT, exactly what goes on the wire (POST /act body)
    wire = encode_frame(frame, quality=80)
    (OUT / "input_frame.jpg").write_bytes(wire)

    # OUTPUT for that frame: the Action the policy/server returns
    action = policy.act(frame)
    (OUT / "output_action.json").write_text(json.dumps(action.model_dump(), indent=2))

    # OUTPUT as the recorder's event stream (tdm_run format) over a short run
    recorder = SessionRecorder(session_id="sample_seq", map="(mock)")
    prev = Action.idle()
    fps = 20.0
    frames = []
    for i in range(10):
        f = adapter.grab()
        a = policy.act(f)
        recorder.record(a, t=i / fps)
        frames.append((i, a))
        prev = a
    recorder.dump(
        OUT / "output_sequence_events.json",
        recorded_at="2026-06-27T00:00:00+00:00",
        duration_sec=len(frames) / fps,
    )

    # Console summary
    print(f"INPUT  frame: shape={frame.shape} dtype={frame.dtype} "
          f"| wire JPEG = {len(wire)} bytes")
    print("OUTPUT action (single frame):")
    print(json.dumps(action.model_dump(), indent=2))
    print(f"\nOUTPUT sequence: {len(recorder.events)} events over {len(frames)} frames")
    print("first events:")
    for e in recorder.events[:4]:
        print("  ", e)
    print(f"\nwrote: {', '.join(p.name for p in sorted(OUT.glob('*')) )}")


if __name__ == "__main__":
    main()
