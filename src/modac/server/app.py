"""The policy server: receives a frame, returns an Action.

Endpoints
---------
GET  /health   -> {"status": "ok", "frames": N}
POST /reset    -> resets the policy's episode state
POST /act      -> body = raw JPEG/PNG bytes, response = Action JSON
WS   /stream   -> send binary frame, receive Action JSON (one per frame)

The separate capture/injection client only needs /act (or /stream) plus the
Action schema in modac.protocol to integrate.
"""

from __future__ import annotations

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
        """Lower-overhead path for sustained high-FPS loops."""
        await ws.accept()
        try:
            while True:
                data = await ws.receive_bytes()
                frame = decode_frame(data)
                action = policy.act(frame, {"recv_ts": time.time()})
                state["frames"] += 1
                await ws.send_json(action.model_dump())
        except WebSocketDisconnect:
            pass

    return app
