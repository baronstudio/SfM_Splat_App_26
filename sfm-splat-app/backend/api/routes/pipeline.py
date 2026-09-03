import asyncio
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.core import jobs as job_store
from backend.core.pipeline_runner import (
    adoption_plan,
    request_abort,
    resume_run,
    run_analysis_only,
    run_crop_only,
    run_geometry_only,
    run_mask_generation,
    request_pause,
    request_resume,
    run_pipeline,
    run_splat_export_only,
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


def adopt_orphaned_runs() -> dict:
    """Re-attach the runs whose tools outlived the last backend (TODO P7.2).

    Called once from the app lifespan. It lives here rather than in the runner
    because the one-job-at-a-time slot is this module's `_running_tasks`: an
    adopted run missing from it would leave `is_running` false, and a project
    copy, reset or archive could then start on top of a step that is still
    writing to the directory.
    """
    rows, closed = adoption_plan()
    adopted: list[str] = []
    for row in rows:
        task = asyncio.create_task(resume_run(row))
        _running_tasks[row.project_id] = task
        adopted.append(row.project_id)
    return {"adopted": adopted, "closed": closed}


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
    """What is running, from the database rather than from this process's memory.

    `_running_tasks` is the authority on whether *this* process still holds the
    task, and the job row is the authority on whether a run was ever started
    and how far it got — which is what a page that has just loaded needs. It
    knew none of this before: `pipelineRunning` was client-side state, so a
    reload lost the bar, the log and the Abort button while the tool kept
    working (TODO P7.1).
    """
    if project_id:
        project = session.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        running = project_id in _running_tasks and not _running_tasks[project_id].done()
        active = job_store.active_job(project_id)
        return {
            "project_id": project_id,
            "running": running,
            "current_step": project.current_step,
            "step_status": project.get_step_status(),
            # The run to restore, or null. It survives the page; it does not yet
            # survive the backend (P7.2), and the startup sweep closes any row
            # that outlived its process — so a job reported here is live.
            "job": active,
        }

    return [
        {"project_id": pid, "running": not task.done()}
        for pid, task in _running_tasks.items()
    ]


@router.get("/jobs")
async def list_jobs(project_id: Optional[str] = None, limit: int = 20):
    """The recent runs, newest first — this project's or the whole machine's."""
    return job_store.recent_jobs(project_id, min(max(limit, 1), 200))


@router.get("/jobs/{job_id}/log")
async def get_job_log(job_id: str, limit: int = 500, after: Optional[int] = None):
    """One run's log lines: the tail by default, or everything after line `after`.

    The tail is what a page load wants — the LiveLog keeps 500 lines and a run
    in its fifteenth minute has more than that. Only the lines that carried
    text are stored, which is what the panel keeps too: `step_mesh` rides the
    bar on 354 of its 419 lines with an empty message precisely so neither sees
    them (CLAUDE.md §7.8).
    """
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    entries, total = job_store.read_log(job, min(max(limit, 1), 5000), after)
    return {"job_id": job_id, "total": total, "entries": entries}


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


@router.post("/crop")
async def crop_splat(body: AnalyzeBody, session: Session = Depends(get_session)):
    """Cut step 4's splat to the stored crop volumes (CLAUDE.md §7.6b).

    Separate from /start for the same reason /geometry is: it must not re-train.
    A 30 000-iteration run measured 956 s on the reference project and the crop
    that follows it measured under a second — tying the two together would make
    every adjustment of a box cost sixteen minutes, which is exactly what §6.3's
    "the expensive phase must never be redone to change a threshold" forbids.

    It writes `train/crop/splat.ply` beside the trained one and never over it,
    so this route can be called as often as the user drags a gizmo, and calling
    it with no volumes clears the crop rather than failing.
    """
    _claim_slot(body.project_id, session, "cropping the splat")

    task = asyncio.create_task(run_crop_only(body.project_id, body.settings))
    _running_tasks[body.project_id] = task

    return {"status": "cropping", "project_id": body.project_id}


@router.post("/export-splat")
async def export_splat(body: AnalyzeBody, session: Session = Depends(get_session)):
    """Write a deliverable copy of step 4's splat (CLAUDE.md §7.6c).

    Separate from /start and from /crop, and for the third time the same
    reason: it must not re-train. It is also the one pass on this router whose
    output the pipeline never reads back — `train/export/` is a drawer of files
    to download, not an input to step 5 — so it can be re-run per format as
    often as the user wants a different one without moving anything downstream.

    The source is `resolve_splat`, so an export made after a crop carries the
    crop, and the run's own log line names which file it read.
    """
    _claim_slot(body.project_id, session, "exporting the splat")

    task = asyncio.create_task(run_splat_export_only(body.project_id, body.settings))
    _running_tasks[body.project_id] = task

    return {"status": "exporting", "project_id": body.project_id}
