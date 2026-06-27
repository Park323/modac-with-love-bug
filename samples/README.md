# Sample I/O

Representative input/output for the MODAC system, regenerate with
`python samples/make_samples.py` (seeded — stable).

## INPUT — one game frame

| file | what it is |
|---|---|
| `input_frame.png` | the frame, viewable (640×360 RGB) |
| `input_frame.jpg` | the **exact bytes sent on the wire** (`POST /act` body), ~9.6 KB |

The image here is from the `MockAdapter` (a stand-in for a live CrossFire
capture — gradient + a red "target" box). Shape/contract are identical to live
play: an RGB `uint8` H×W×3 frame, JPEG-encoded for transport.

## OUTPUT — the action for that frame

`output_action.json` — the per-frame `Action` the policy/server returns
(this is what `POST /act` responds with):

```json
{ "forward": true, "yaw": -8.06, "pitch": -4.42, "fire": false, "weapon": 0, ... }
```

Read it as: hold W, turn left ~8 mouse-counts and up ~4 this frame, don't fire.

`output_sequence_events.json` — the same actions over 10 frames, serialized in
the worker's **`tdm_run` event-stream format** (schema 0.1): edge-triggered
`key_down`/`key_up` with scan codes, `mouse_move {dx,dy}`, `mouse_button_*`.
This is the format the capture/injection side consumes.

## The loop in one line

```
input_frame.jpg  ──POST /act──▶  output_action.json  ──action_to_events──▶  events (inject)
```
