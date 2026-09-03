from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)

    async def broadcast_json(self, payload: dict) -> None:
        dead: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_json(payload)
            except Exception:
                dead.append(connection)
        for c in dead:
            if c in self.active_connections:
                self.active_connections.remove(c)


manager = ConnectionManager()


@router.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


async def broadcast(
    step: str,
    level: str,
    message: str,
    progress: Optional[float] = None,
    data: Optional[dict] = None,
    file: Optional[str] = None,
    status: Optional[str] = None,
    project_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> None:
    """
    Broadcast a WebSocket message to all connected clients.

    msg_type priority:
      status     → explicit step-state transition (step start / done / error / aborted)
      metric     → LFS training metric (data payload)
      file_ready → export file available
      progress   → numeric progress update
      log        → plain log line (default)

    `project_id` rides along on everything a run broadcasts, because the bus is
    one channel for the whole app and every consumer used to map a step name
    onto whatever project happened to be open: step 4 of one project displayed
    another's bar at 56 % with a live ETA (CLAUDE.md §13.7). The store drops a
    message whose project is not the one on screen. `job_id` is what lets a
    client that just reconnected tell the run it restored from the row from a
    newer one that started meanwhile (`core/jobs.py`).
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    if status is not None:
        msg_type = "status"
    elif data is not None:
        msg_type = "metric"
    elif file is not None:
        msg_type = "file_ready"
    elif progress is not None:
        msg_type = "progress"
    else:
        msg_type = "log"

    payload: dict = {"type": msg_type, "step": step, "timestamp": timestamp}
    if level:
        payload["level"] = level
    if message:
        payload["message"] = message
    if progress is not None:
        payload["progress"] = progress
    if data is not None:
        payload["data"] = data
    if file is not None:
        payload["file"] = file
    if status is not None:
        payload["status"] = status
    if project_id is not None:
        payload["project_id"] = project_id
    if job_id is not None:
        payload["job_id"] = job_id

    await manager.broadcast_json(payload)
