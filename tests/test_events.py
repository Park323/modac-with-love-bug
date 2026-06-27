from modac.events import (
    action_to_events,
    events_to_actions,
    load_recording,
    SessionRecorder,
)
from modac.protocol import Action


def test_action_to_events_emits_on_transition_only():
    a0 = Action.idle()
    a1 = Action(forward=True, fire=True, yaw=10, pitch=-3)
    a2 = Action(forward=True, fire=True)  # forward+fire held, mouse stops

    e1 = action_to_events(a0, a1, t=0.0)
    types = {(e["type"], e.get("key") or e.get("button") or e["type"]) for e in e1}
    assert ("key_down", "W") in types
    assert ("mouse_button_down", "left") in types
    assert any(e["type"] == "mouse_move" and e["dx"] == 10 and e["dy"] == -3 for e in e1)

    # No transition, no motion => no events.
    assert action_to_events(a1, a2, t=0.1) == []


def test_roundtrip_keys_survive_action_to_events_to_action():
    frames = [
        Action(forward=True),
        Action(forward=True, right=True),
        Action(right=True, fire=True),
        Action.idle(),
    ]
    fps = 10.0
    events = []
    prev = Action.idle()
    for i, a in enumerate(frames):
        events.extend(action_to_events(prev, a, t=i / fps))
        prev = a

    decoded = events_to_actions(events, fps=fps)
    # The held-key state at each frame boundary should be recoverable.
    assert decoded[0].forward is True
    assert decoded[1].forward is True and decoded[1].right is True
    assert decoded[2].right is True and decoded[2].fire is True


def test_recorder_matches_schema():
    rec = SessionRecorder(session_id="t", map="Test", backend="raw_input")
    rec.record(Action(forward=True), t=0.0)
    rec.record(Action(forward=True, fire=True), t=0.1)
    out = rec.to_dict(recorded_at="2026-06-27T00:00:00+00:00", duration_sec=0.2)
    assert out["schema_version"] == "0.1"
    assert set(out["session"]) == {
        "session_id", "game", "mode", "map", "recorded_at", "duration_sec", "event_count"
    }
    assert out["session"]["event_count"] == len(out["events"])
    assert {"backend", "note"} == set(out["environment"])


def test_decode_real_recording_keys(tmp_path):
    # The committed sample lives at repo root; skip cleanly if absent.
    import pathlib

    sample = pathlib.Path(__file__).resolve().parents[1] / "tdm_run_001.json"
    if not sample.exists():
        return
    rec = load_recording(sample)
    actions = events_to_actions(rec["events"], fps=20.0)
    assert len(actions) > 0
    # WASD movement is present somewhere in the decoded stream.
    assert any(a.forward for a in actions)
