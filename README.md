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
> Windows adapter they can build on (or reimplement against `POST /act`).

## Quickstart (works on this Mac — no game needed)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# terminal 1 — policy server with the placeholder random policy
modac-server --policy random

# terminal 2 — agent loop driving the mock game
modac-agent --adapter mock --fps 20 --max-steps 100
```

You'll see the mock adapter log the actions the server returns each frame.

## On the game machine (Windows)

```powershell
pip install "modac[windows]"            # dxcam, pydirectinput, mss
modac-server --policy random            # or your model: pkg.module:ClassName
modac-agent --adapter crossfire --fps 30
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

Frames go over the wire as JPEG bytes (`POST /act` body, or binary WebSocket
frame on `/stream`); actions come back as JSON. See `modac/protocol.py`.

## API

| method | path | in → out |
|---|---|---|
| GET | `/health` | → `{status, frames}` |
| POST | `/reset` | → resets episode state |
| POST | `/act` | JPEG/PNG bytes → `Action` JSON |
| WS | `/stream` | binary frame → `Action` JSON (per frame) |

## Layout

```
src/modac/
  protocol.py            # Action schema + frame encode/decode  (the contract)
  policy/                # Policy interface + RandomPolicy placeholder
  server/                # FastAPI policy server (/act, /stream)
  adapters/              # EnvAdapter: mock, crossfire_windows, win_input
  client/                # agent loop (capture → act → apply)
examples/                # MineRL reference adapter (shows retargeting)
tests/
```

## Note

This is research tooling for **authorized** use. Automating input into online
multiplayer titles is typically restricted by the game's Terms of Service and
anti-cheat; check those before pointing the live adapter at an online match.
Develop and validate against the `mock` adapter or offline targets.
```
