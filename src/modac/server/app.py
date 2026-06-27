"""The policy server: receives a frame, returns an Action.

Primary interface: the WebSocket /stream (one persistent connection, one Action
per frame — no per-frame HTTP handshake). The REST /act is kept for debugging
and single-shot calls.

Endpoints
---------
WS   /stream   -> send binary frame -> receive Action JSON (one per frame).
                  Send a text message {"cmd": "reset"} to reset episode state.
GET  /health   -> {"status": "ok", "frames": N}
POST /reset    -> resets the policy's episode state
POST /act      -> body = raw JPEG/PNG bytes, response = Action JSON

The separate capture/injection client only needs /stream (or /act) plus the
Action schema in modac.protocol to integrate.
"""

from __future__ import annotations

import json
import time

from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from modac.policy.base import Policy
from modac.protocol import decode_frame


def create_app(policy: Policy) -> FastAPI:
    app = FastAPI(title="MODAC policy server", version="0.1.0")
    state = {"frames": 0}

    @app.get("/health")
    def health():
        return {"status": "ok", "frames": state["frames"]}

    @app.post("/reset")
    def reset():
        policy.reset()
        state["frames"] = 0
        return {"status": "reset"}

    @app.post("/act")
    async def act(request: Request, x_frame_id: str | None = Header(default=None)):
        data = await request.body()
        frame = decode_frame(data)
        action = policy.act(frame, {"frame_id": x_frame_id, "recv_ts": time.time()})
        state["frames"] += 1
        return JSONResponse(action.model_dump())

    @app.websocket("/stream")
    async def stream(ws: WebSocket):
        """Primary path: a persistent connection, one Action per frame.

        Binary message  -> a frame (JPEG/PNG) -> replies with an Action JSON.
        Text message     -> a control command, e.g. {"cmd": "reset"}.
        """
        await ws.accept()
        policy.reset()  # fresh episode per connection
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    frame = decode_frame(msg["bytes"])
                    action = policy.act(frame, {"recv_ts": time.time()})
                    state["frames"] += 1
                    await ws.send_json(action.model_dump())
                elif msg.get("text") is not None:
                    cmd = json.loads(msg["text"]).get("cmd")
                    if cmd == "reset":
                        policy.reset()
                        state["frames"] = 0
                        await ws.send_json({"status": "reset"})
        except WebSocketDisconnect:
            pass

    return app
