import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.core.pipeline_runner import (
    request_abort,
    run_analysis_only,
    run_geometry_only,
    run_mask_generation,
    request_pause,
    request_resume,
    run_pipeline,
)
from backend.db.database import get_session
from backend.models.project import Project

router = APIRouter()

_running_tasks: dict[str, asyncio.Task] = {}


def is_running(project_id: str) -> bool:
    """True while this project owns a live job.

    Exported because the destructive project operations (copy, reset, archive,
    delete) must not run under a step that is still writing to the directory —
    the one-job-at-a-time rule is enforced here, so the answer lives here too.
    """
    task = _running_tasks.get(project_id)
    return task is not None and not task.done()


class StartBody(BaseModel):
    project_id: str
    start_from_step: int = 1
    settings: dict = {}


class AnalyzeBody(BaseModel):
    project_id: str
    settings: dict = {}


class ControlBody(BaseModel):
    project_id: str
    action: Literal["pause", "resume", "abort"]


@router.post("/start")
async def start_pipeline(body: StartBody, session: Session = Depends(get_session)):
    project = session.get(Project, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.archived_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This project is archived — restore it before running a step.",
        )

    if body.project_id in _running_tasks and not _running_tasks[body.project_id].done():
        raise HTTPException(status_code=409, detail="Pipeline already running for this project")

    task = asyncio.create_task(
        run_pipeline(body.project_id, body.start_from_step, body.settings)
    )
    _running_tasks[body.project_id] = task

    return {"status": "started", "project_id": body.project_id, "step": body.start_from_step}


@router.post("/control")
async def control_pipeline(body: ControlBody, session: Session = Depends(get_session)):
    project = session.get(Project, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.action == "abort":
        request_abort(body.project_id)
        # Also cancel the asyncio task as a hard fallback
        task = _running_tasks.get(body.project_id)
        if task and not task.done():
            task.cancel()
        _running_tasks.pop(body.project_id, None)

    elif body.action == "pause":
        request_pause(body.project_id)

    elif body.action == "resume":
        request_resume(body.project_id)

    return {"status": body.action, "project_id": body.project_id}


@router.get("/status")
async def get_status(
    project_id: Optional[str] = None,
    session: Session = Depends(get_session),
):
    if project_id:
        project = session.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        running = project_id in _running_tasks and not _running_tasks[project_id].done()
        return {
            "project_id": project_id,
            "running": running,
            "current_step": project.current_step,
            "step_status": project.get_step_status(),
        }

    return [
        {"project_id": pid, "running": not task.done()}
        for pid, task in _running_tasks.items()
    ]


@router.post("/analyze")
async def analyze_project(body: AnalyzeBody, session: Session = Depends(get_session)):
    """Re-run the curation phase of step 2 on the frames already on disk.

    Separate from /start because it must not re-extract: thresholds are tuned
    iteratively and re-extracting to change one number is unacceptable
    (CLAUDE.md §6.3). Shares _running_tasks with /start so the one-job-at-a-time
    rule and the abort button keep working across both.
    """
    project = session.get(Project, body.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.archived_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This project is archived — restore it before analysing.",
        )

    if body.project_id in _running_tasks and not _running_tasks[body.project_id].done():
        raise HTTPException(status_code=409, detail="A job is already running for this project")

    task = asyncio.create_task(run_analysis_only(body.project_id, body.settings))
    _running_tasks[body.project_id] = task

    return {"status": "analyzing", "project_id": body.project_id}


def _claim_slot(project_id: str, session: Session, doing: str) -> Project:
    """The guards every standalone pass shares, in one place.

    `/analyze`, `/masks` and `/geometry` are the same shape: a re-runnable pass
    that must not redo the expensive phase before it (CLAUDE.md §6.3, §7.4,
    §7.5). They also share `_running_tasks` with `/start`, which is what keeps
    the one-job-at-a-time rule and the abort button working across all four.
    """
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.archived_at is not None:
        raise HTTPException(
            status_code=409,
            detail=f"This project is archived — restore it before {doing}.",
        )

    if project_id in _running_tasks and not _running_tasks[project_id].done():
        raise HTTPException(
            status_code=409, detail="A job is already running for this project"
        )
    return project


@router.post("/masks")
async def generate_masks(body: AnalyzeBody, session: Session = Depends(get_session)):
    """Write `masks/` with `spirula sam` (CLAUDE.md §7.4).

    Separate from /start for the same reason /analyze is: it must not re-align.
    The masks are an *input* to step 3 — `sfm auto` adopts a `masks/` sibling of
    the image directory with no flag at all — so generating them is not running
    the reconstruction, and this route never marks step 3 done.
    """
    _claim_slot(body.project_id, session, "generating masks")

    task = asyncio.create_task(run_mask_generation(body.project_id, body.settings))
    _running_tasks[body.project_id] = task

    return {"status": "masking", "project_id": body.project_id}


@router.post("/geometry")
async def generate_geometry(body: AnalyzeBody, session: Session = Depends(get_session)):
    """Write `sfm/normals/` and `sfm/depths/` with `spirula geometry` (§7.5).

    Separate from /start because it must not re-train, and because the maps it
    writes land *inside* the dataset step 4 reads: `--depth-dir` and
    `--normal-dir` default to `depths` and `normals` relative to `--data`, so
    with `--data <project>/sfm` the pairing costs no flag. Never a reset of
    `sfm/` — that would delete the sparse model the pass is reading.
    """
    _claim_slot(body.project_id, session, "estimating geometry")

    task = asyncio.create_task(run_geometry_only(body.project_id, body.settings))
    _running_tasks[body.project_id] = task

    return {"status": "geometry", "project_id": body.project_id}
