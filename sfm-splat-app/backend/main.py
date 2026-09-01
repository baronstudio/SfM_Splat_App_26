import asyncio
import mimetypes
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# On Windows, asyncio defaults to SelectorEventLoop which does NOT support
# subprocesses. Force ProactorEventLoop so asyncio.create_subprocess_exec works.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.api import file_handles, websocket
from backend.api.routes import (
    defaults,
    files,
    hardware,
    models,
    pipeline,
    projects,
    settings,
    version,
)
from backend.core.pipeline_runner import reconcile_orphaned_steps
from backend.db.database import create_db_and_tables

PROJECTS_DIR = Path(__file__).parent.parent / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    # Nothing survives a restart: a step still persisted as "running" belongs to
    # a run this process never started, and it would freeze that step's button.
    swept = reconcile_orphaned_steps()
    if swept:
        print(f"[startup] reconciled {swept} project(s) with a stale 'running' step", flush=True)
    yield


app = FastAPI(lifespan=lifespan)

# The frontend talks to its own origin through the dev server's proxy, so the
# normal path raises no CORS question at all. The regex is for the other one:
# on the LAN staging box (start.bat) somebody will sooner or later point a
# browser straight at :8000, or serve the built bundle from another port. It
# admits the loopback and the three private IPv4 ranges only - a public origin
# still gets nothing, because CLAUDE.md §1 keeps "no VPS / remote deployment".
_LAN_ORIGIN = (
    r"^https?://("
    r"localhost"
    r"|127\.\d+\.\d+\.\d+"
    r"|10\.\d+\.\d+\.\d+"
    r"|192\.168\.\d+\.\d+"
    r"|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+"
    r"|[A-Za-z0-9-]+(\.local)?"
    r")(:\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_LAN_ORIGIN,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["pipeline"])
app.include_router(settings.router, prefix="/api/settings", tags=["settings"])
app.include_router(defaults.router, prefix="/api/defaults", tags=["defaults"])
app.include_router(files.router, prefix="/api/files", tags=["files"])
# Installation-level, like /api/settings: the neural checkpoints of §7.4 and
# §7.5 are a property of this machine, not of a project (backend/core/model_store.py).
app.include_router(models.router, prefix="/api/models", tags=["models"])
# Also installation-level: the CPU and the GPUs in this machine, and what they
# are doing right now (backend/core/hardware.py). Same panel, same argument.
app.include_router(hardware.router, prefix="/api/hardware", tags=["hardware"])
app.include_router(version.router, prefix="/api/version", tags=["version"])
app.include_router(websocket.router)

# The viewer previews are binary; unknown extensions are served as text/plain,
# which invites any proxy in the way to treat them as text and re-encode them.
mimetypes.add_type("application/octet-stream", ".splat")
mimetypes.add_type("application/octet-stream", ".pc3d")
mimetypes.add_type("application/octet-stream", ".ply")
# Step 5's mesh is served as it stands - there is no decimated copy of a glb
# (core/preview.py) - so its own types are registered rather than left to the
# platform's registry, which on Windows answers whatever a 3D app last wrote
# there.
mimetypes.add_type("model/gltf-binary", ".glb")
mimetypes.add_type("model/gltf+json", ".gltf")
# The compressed export formats (CLAUDE.md 7.6c). Same argument as `.splat`
# above, and it bites harder here: these are downloads rather than previews, and
# a `.sog` re-encoded as UTF-8 on the way out is a corrupt deliverable that only
# fails when somebody else opens it. `.compressed.ply` ends in `.ply` and is
# already covered.
mimetypes.add_type("application/octet-stream", ".sog")
mimetypes.add_type("application/octet-stream", ".spz")

# A cancelled download must not leave the file open - see the module.
file_handles.apply_sync_close()

app.mount("/static", StaticFiles(directory=str(PROJECTS_DIR)), name="static")


@app.get("/")
def read_root():
    return {"Hello": "World"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
