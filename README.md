# MODAC — image in, action out

A framework-agnostic system for FPS-style game bots: **input = a game frame,
output = the action for that frame**. The two halves are decoupled over a tiny
HTTP/WebSocket API, so the model never needs to know which game it's driving.

```
   [ game adapter ]                              [ policy server ]
    grab() ─────────── image bytes (JPEG) ───────▶  image → Action
    apply() ◀───────── Action (JSON) ─────────────  (this repo serves it)

   swap the adapter ⇒ retarget to a different game; the server is unchanged
```

- **Policy server** (`modac.server`) — receives a frame, returns an `Action`.
  Plug your model in by implementing one `Policy` class.
- **Protocol** (`modac.protocol`) — the single source of truth for the
  `Action` schema and frame encoding. The integration contract lives here.
- **Adapters** (`modac.adapters`) — the game side. `mock` (runs anywhere) and
  `crossfire` (Windows: dxcam capture + SendInput injection) ship in the box.
- **Agent loop** (`modac.client`) — reference client tying it together.

> CrossFire runs on **Windows** and is handled by a separate worker. This repo
> is the system around the game: the API contract + policy server + a reference
> Windows adapter they can build on (or reimplement against the WS `/stream`).

## Quickstart (works on this Mac — no game needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# terminal 1 — policy server with the placeholder random policy
modac-server --policy random

# terminal 2 — agent loop driving the mock game (WebSocket by default)
modac-agent --adapter mock --server ws://127.0.0.1:8000 --fps 20 --max-steps 100
```

You'll see the mock adapter log the actions the server returns each frame.

## On the game machine (Windows)

```powershell
pip install "modac[windows]"            # dxcam, pydirectinput, mss
modac-server --policy random            # or your model: pkg.module:ClassName
modac-agent --adapter crossfire --server ws://<policy-host>:8000 --fps 30
```

## Plug in a real model

Implement `Policy.act(frame, info) -> Action`:

```python
# my_model.py
import numpy as np
from modac.policy.base import Policy
from modac.protocol import Action

class MyPolicy(Policy):
    def __init__(self):
        self.model = ...  # load weights once

    def act(self, frame: np.ndarray, info=None) -> Action:
        logits = self.model(frame)        # your inference
        return Action(forward=True, yaw=float(logits[0]), fire=bool(logits[1]))
```

Run it: `modac-server --policy my_model:MyPolicy`

## The Action contract

| field | type | meaning |
|---|---|---|
| `forward/back/left/right` | bool | WASD, held this frame |
| `jump/crouch/sprint` | bool | space / ctrl / shift |
| `yaw/pitch` | float | relative mouse motion (yaw + = right, pitch + = down) |
| `fire/aim` | bool | left / right mouse button |
| `reload/use` | bool | R / E |
| `weapon` | int 0–9 | 0 = no change, 1–9 = slot |

Frames go over the wire as JPEG bytes (a binary WebSocket frame on `/stream`,
or the `POST /act` body); actions come back as JSON. See `modac/protocol.py`.

## API

WebSocket `/stream` is the **primary** interface — one persistent connection,
one `Action` per frame, no per-frame HTTP handshake. REST `/act` is kept for
debugging / single-shot calls (`modac-agent --transport http`).

| method | path | in → out |
|---|---|---|
| **WS** | **`/stream`** | **binary frame → `Action` JSON (per frame); text `{"cmd":"reset"}` → reset** |
| GET | `/health` | → `{status, frames}` |
| POST | `/reset` | → resets episode state |
| POST | `/act` | JPEG/PNG bytes → `Action` JSON (debug) |

## Recording format & interop (`tdm_run_*.json`)

The capture/injection worker records sessions as an **event stream** (schema
`0.1`): edge-triggered `key_down`/`key_up` (with set-1 `scan` codes),
`mouse_move {dx,dy}`, `mouse_button_{down,up}`. The model instead works in
**per-frame `Action`s**. `modac.events` is the codec between them:

```bash
# Bot plays and saves its output in the SAME format as human recordings:
modac-agent --adapter mock --fps 30 --record bot_run.json

# Decode any recording into fixed-rate frame actions (training pairs):
modac-convert tdm_run_001.json --fps 20
```

- `action_to_events(prev, cur, t)` — diff two frames into edge events (this is
  also exactly what the live Windows adapter does to drive injection).
- `events_to_actions(events, fps)` — resample a recording onto a frame grid.
- `SessionRecorder` — writes JSON byte-compatible with `tdm_run_*.json`.

The scan codes in `modac.events` and `modac.adapters.win_input` match the
recorder's, so both sides share one vocabulary.

### ⚠️ Mouse capture must use Raw Input

The committed sample's `mouse_move` deltas are unusable: they were polled from
`GetCursorPos`, but FPS games lock the cursor to screen center, so polling only
sees the cursor snapping back (deltas cancel out — note the file's own
`environment.note`). Keyboard events are fine; the look axis is lost.

To capture real yaw/pitch, the worker must read **relative** mouse motion:

| method | notes |
|---|---|
| **Raw Input API** (`WM_INPUT` + `RIDEV_INPUTSINK`) | standard fix; same data the game reads; works while unfocused |
| Interception driver | kernel-level, most robust; needs a driver install |
| read view angles from game memory | most accurate, but game-specific / fragile |
| ~~`WH_MOUSE_LL` hook~~ | coordinate-based — fails the same way under cursor lock |

Raw Input `dx/dy` are in the same "mouse count" unit as `Action.yaw/pitch` and
as `SendInput`, so capture → action → injection round-trips with no unit
conversion (degree conversion is only needed for sensitivity-independent angles).

## Layout

```
src/modac/
  protocol.py            # Action schema + frame encode/decode  (the contract)
  policy/                # Policy interface + RandomPolicy placeholder
  server/                # FastAPI policy server (/act, /stream)
  events.py              # Action <-> event-stream codec (tdm_run format) + recorder
  adapters/              # EnvAdapter: mock, crossfire_windows, win_input
  client/                # agent loop (capture → act → apply, --record)
examples/                # MineRL reference adapter (shows retargeting)
tests/
```

## Note

This is research tooling for **authorized** use. Automating input into online
multiplayer titles is typically restricted by the game's Terms of Service and
anti-cheat; check those before pointing the live adapter at an online match.
Develop and validate against the `mock` adapter or offline targets.
```
