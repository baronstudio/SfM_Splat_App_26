"""
pipeline_runner.py — Full orchestrator for the 3DGS pipeline.

Step numbering:
  1  Import       (handled at project creation — skipped here)
  2  Extract      FFmpeg frame extraction, then curation (CLAUDE.md §6)
  3  SfM          `spirula sfm auto` (CLAUDE.md §7.1)
  4  Train        `spirula train` (CLAUDE.md §7.6)
  5  Mesh         `spirula mesh`, then fill export/ (CLAUDE.md §7.8, §7.10)
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from backend.api.websocket import broadcast
from backend.core.defaults import deep_merge
from backend.core import jobs as job_store
from backend.core.jobs import JobRecord, start_job
from backend.core.proc import (
    ProcessAborted,
    RunContext,
    kill_project_children,
    reset_run_context,
    set_run_context,
)
from backend.core.steps.step_analyze import (
    AnalysisAborted,
    resolve_curate_settings,
    run_analysis,
)
from backend.core.steps.step_crop import run_crop as _run_crop
from backend.core.steps.step_export import run_export
from backend.core.steps.step_extract import run_extract
from backend.core.steps.step_geometry import run_geometry as _run_geometry
from backend.core.steps.step_mesh import run_mesh
from backend.core.steps.step_sam import run_masking as _run_masking
from backend.core.steps.step_sfm import run_sfm
from backend.core.steps.step_splat_export import (
    run_splat_export as _run_splat_export,
)
from backend.core.steps.step_train import run_train
from backend.db.database import engine
from backend.models.project import Project

PROJECTS_DIR = Path(__file__).parents[2] / "projects"

# ── Pause / Abort control ────────────────────────────────────────────────────

_abort_flags: dict[str, bool] = {}
_pause_events: dict[str, asyncio.Event] = {}


def _install_run_context(job: JobRecord, adopt=None):
    """Tell `proc.spawn` where this run's output goes, and what to re-attach to.

    The steps never learn that a job record exists — they take the bus by
    injection and call `spawn` as they always did (§2.4). This is the one place
    that knows both, and it is what makes the child's stdout land in a file the
    next backend can read (TODO P7.2).
    """
    return set_run_context(RunContext(
        tool_log=job.tool_log_path,
        adopt_pid=adopt.pid if adopt else None,
        adopt_image=adopt.pid_image if adopt else None,
        adopt_created=adopt.pid_created if adopt else None,
        on_spawn=job.record_pid,
    ))


async def _announce_adoption(bus, job: JobRecord, adopt) -> None:
    """Say, in the log, that this run is being rejoined rather than started.

    It is the first line of the rebuilt transcript, and it has to be: everything
    after it is a *replay* of output the tool produced before this process
    existed, so a reader who did not know that would misread every timestamp.
    """
    lines = 0
    try:
        if job.tool_log_path and job.tool_log_path.exists():
            lines = sum(1 for _ in open(job.tool_log_path, "rb"))
    except OSError:
        pass
    await bus(
        job.kind, "WARNING",
        f"[run] Re-attached to a run that outlived the backend: "
        f"{adopt.pid_image or 'the tool'} pid {adopt.pid}, started "
        f"{adopt.started_at}Z, {lines} line(s) already in its transcript. "
        f"Replaying them through this step, then following it live — nothing is "
        f"re-run and nothing is deleted.",
    )


def _abort_checker(project_id: str):
    """Injectable predicate so a long step can honour abort (CLAUDE.md §2.6)."""
    return lambda: bool(_abort_flags.get(project_id))


# Four passes attach to a wizard step without being it: `spirula sam` reports
# as `masks` into step 3, `spirula geometry` as `geometry` into step 4, the
# volume crop as `crop` and the deliverable export as `splat_export`, both also
# into step 4. The frontend store maps all four, the same shape as `curate`
# reporting into step 2 (CLAUDE.md §12, 2026-08-20). None of them ever marks its
# step done — see `_run_attached_pass`.
MASK_STEP_NAME = "masks"
GEOMETRY_STEP_NAME = "geometry"
CROP_STEP_NAME = "crop"
SPLAT_EXPORT_STEP_NAME = "splat_export"

_STEP_NAMES: dict[int, str] = {
    1: "import",
    2: "extract",
    3: "sfm",
    4: "train",
    5: "mesh",
}


async def _run_extract_and_curate(
    project_path: Path, broadcast_fn, settings: dict, should_abort=None
) -> dict:
    """Step 2 is one job with two phases (CLAUDE.md §6).

    The analysis is skipped only when the project turns curation off; it is
    always re-runnable on its own afterwards through `run_analysis_only`, so a
    threshold change never costs a re-extraction.
    """
    result = await run_extract(project_path, broadcast_fn, settings)

    curate, _band = resolve_curate_settings(settings)
    if not (curate.enabled and curate.auto_after_extract):
        await broadcast_fn(
            "curate", "INFO",
            "[curate] Auto-analysis is off — use 'Re-analyse' to curate this set.",
        )
        return result

    analysis = await run_analysis(project_path, broadcast_fn, settings, should_abort)
    result["analysis"] = analysis.get("selection", {}).get("summary")
    return result


async def _run_mesh_and_export(
    project_path: Path, broadcast_fn, settings: dict
) -> dict:
    """Step 5 meshes, then fills `export/`, and it is the last step (§7.10)."""
    result = await run_mesh(project_path, broadcast_fn, settings)
    result["export"] = await run_export(project_path, broadcast_fn, settings)
    return result


_STEP_RUNNERS = {
    2: _run_extract_and_curate,
    3: run_sfm,
    4: run_train,
    5: _run_mesh_and_export,
}

# Steps that accept the abort predicate. The others predate it and check the
# flag only between steps.
_ABORT_AWARE_STEPS = {2}


def _debug(msg: str) -> None:
    """Print a timestamped [WIZARD-DEBUG] line to the CLI.

    Never lets the console encoding break the pipeline. On a French Windows
    console stdout is cp1252, which has no mapping for the arrows these messages
    use, and the resulting UnicodeEncodeError propagates out of the caller — it
    killed the abort handler mid-way, so the step stayed "running" in the UI. A
    debug line is never worth an exception.
    """
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
    line = f"[WIZARD-DEBUG {ts}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "ascii"
        print(line.encode(encoding, errors="replace").decode(encoding), flush=True)


async def _broadcast_best_effort(bus, *args, **kwargs) -> None:
    """Broadcast from inside a cancellation handler.

    Awaiting in the `except CancelledError` branch of a task that has just been
    cancelled can be cancelled again before it completes. The abort notice is
    the last thing the UI hears, so losing it is what leaves a step stuck on
    "running" — swallow the second cancellation rather than the message.
    """
    try:
        await asyncio.shield(bus(*args, **kwargs))
    except asyncio.CancelledError:
        pass


def request_abort(project_id: str) -> None:
    """Signal the running pipeline for this project to abort — and kill its tools.

    Cancelling the task is not enough. A step driving an .exe spends the whole
    run parked in a thread-pool `readline()`, so the cancellation unwinds the
    coroutine while the child keeps going: an aborted training used to leave
    LichtFeld-Studio.exe on the GPU with no reference left to stop it. Kill the
    process tree first, then let the cancellation do the bookkeeping — killing it
    also closes the pipe, which is what unblocks the reader thread.
    """
    _abort_flags[project_id] = True
    # Unblock any paused coroutine so it can observe the abort flag.
    event = _pause_events.get(project_id)
    if event:
        event.set()

    project = _get_project(project_id)
    if project:
        killed = kill_project_children(PROJECTS_DIR / project.slug)
        if killed:
            _debug(f"  → abort: killed {killed} running tool process tree(s)")


def request_pause(project_id: str) -> None:
    """Hold the pipeline at the next inter-step gate.

    Scope, so nobody wires a button to this expecting more: the event is awaited
    between steps only, and `/start` runs exactly one step per call — so no
    running step observes it today. None of the tools we drive (FFmpeg,
    RealityScan, LichtFeld Studio) has a pause verb, and the LFS step's Pause
    button was removed rather than left lying about what it did. Reviving pause
    means suspending the child process (`NtSuspendProcess` / `psutil.suspend`)
    or threading the event into the curation loops — a feature, not a wiring fix.
    """
    event = _pause_events.get(project_id)
    if event:
        event.clear()


def request_resume(project_id: str) -> None:
    """Resume a paused pipeline."""
    event = _pause_events.get(project_id)
    if event:
        event.set()


# ── DB helpers ───────────────────────────────────────────────────────────────

def _get_project(project_id: str) -> Project | None:
    with Session(engine) as session:
        return session.get(Project, project_id)


def _stored_settings(project: Project) -> dict:
    """The project's own settings layer, out of `settings_json`.

    Sectioned exactly as defaults.json — `extract`, `curate`, `rc`, `lfs` —
    because that is what the resolver of each step already reads.
    """
    try:
        stored = json.loads(project.settings_json or "{}")
    except json.JSONDecodeError:
        return {}
    return stored if isinstance(stored, dict) else {}


def _with_project_settings(project: Project, settings: dict) -> dict:
    """Overlay the request's settings onto the ones stored on the project.

    A run started from a wizard step sends the panel it has on screen, which is
    the same thing — but a run started with `{}` (step 5, or a call from
    anywhere but that step's own panel) would otherwise silently drop the whole
    per-project layer back onto the app defaults, which is precisely what
    CLAUDE.md §4's precedence forbids.
    """
    return deep_merge(_stored_settings(project), settings or {})


def _update_project(project_id: str, **kwargs) -> None:
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project:
            return
        for key, val in kwargs.items():
            if key == "_step_status_dict":
                project.set_step_status(val)
            else:
                setattr(project, key, val)
        project.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        session.add(project)
        session.commit()


def _demote_if_still_running(project_id: str, reason: str) -> None:
    """Last-resort cleanup: no step may outlive the task that runs it.

    The named handlers above cover the failures we know about, but a step left
    on "running" is not a cosmetic glitch — it disables the step's start button
    for good (see reconcile_orphaned_steps). Run from `finally`, so whatever way
    the task leaves — including a BaseException no `except` clause names — the
    UI gets a state it can act on.
    """
    project = _get_project(project_id)
    if not project:
        return
    status = project.get_step_status()
    stale = [k for k, v in status.items() if v == "running"]
    if not stale:
        return
    for key in stale:
        status[key] = "error"
    _debug(f"  → cleanup: steps {sorted(stale)} left on 'running' — demoted to 'error' ({reason})")
    _update_project(
        project_id,
        error_message=f"[Interrupted] Step {', '.join(sorted(stale))} stopped unexpectedly ({reason}).",
        _step_status_dict=status,
    )


def reconcile_orphaned_steps(skip: frozenset[str] = frozenset()) -> int:
    """Demote every step still persisted as "running" at process start.

    `step_status` is the only thing the wizard hydrates from, and nothing but a
    live run ever moves a step off "running". A backend killed or reloaded mid
    step therefore leaves that state in the DB forever: `Step*` components
    disable their start button on `status === 'running'`, and the Abort button
    only renders while `pipelineRunning` is true — which a fresh page load never
    is. The step becomes a dead end reachable only by editing the DB by hand.

    Since P7.2 a restart re-attaches the runs whose child outlived it, and
    `skip` is those projects: their steps are not stale. Everything else still
    is — a step is moved off "running" only by a live run — so it is demoted to
    "error" with a message that says what happened, rather than to "pending",
    which would claim the step was never attempted.
    """
    swept = 0
    with Session(engine) as session:
        for project in session.exec(select(Project)).all():
            # Not stale at all: this project's run was just re-attached and is
            # still going, so its step really is running (TODO P7.2).
            if project.id in skip:
                continue
            status = project.get_step_status()
            stale = [k for k, v in status.items() if v == "running"]
            if not stale:
                continue
            for key in stale:
                status[key] = "error"
            project.set_step_status(status)
            project.error_message = (
                f"[Interrupted] Step {', '.join(sorted(stale))} was still running when the"
                " backend stopped. Restart the step."
            )
            session.add(project)
            swept += 1
            _debug(
                f"  → startup sweep: project {project.id} ({project.name}) —"
                f" steps {sorted(stale)} were stale 'running', demoted to 'error'"
            )
        if swept:
            session.commit()
    return swept


# ── Re-attaching a run that outlived the backend (TODO P7.2) ─────────────────

def adoption_plan() -> tuple[list, int]:
    """What to re-attach to, and how many rows were closed instead.

    Returns `([job rows], closed)`. The caller creates the tasks, because the
    one-job-at-a-time slot (`_running_tasks`) belongs to the route module: an
    adopted run that is not in that dict would leave `is_running` false, and a
    project copy or reset could then start on top of a live step.
    """
    adoptable, orphans = job_store.adoption_candidates()
    closed = job_store.close_orphaned_jobs(orphans)
    for row in adoptable:
        _debug(
            f"  → adoption: {row.kind} of project {row.project_id} is still"
            f" alive as {row.pid_image} pid {row.pid} — re-attaching"
        )
    return adoptable, closed


def resume_run(job):
    """The coroutine that re-enters the right runner for an adopted row.

    Nothing new runs: the step is re-entered from the top, which re-reads its
    inputs, skips its reset (`proc.adopting()`), attaches to the live child
    instead of starting one, and replays the transcript through the parser that
    wrote it. Which is why only the five single-command tool runs are adoptable
    at all (`jobs.ADOPTABLE_KINDS`).
    """
    if job.kind in ("sfm", "train", "mesh"):
        return run_pipeline(job.project_id, job.step, {}, adopt=job)
    if job.kind == MASK_STEP_NAME:
        return run_mask_generation(job.project_id, {}, adopt=job)
    if job.kind == GEOMETRY_STEP_NAME:
        return run_geometry_only(job.project_id, {}, adopt=job)
    raise ValueError(f"{job.kind} is not an adoptable run")


# ── Main orchestrator ────────────────────────────────────────────────────────

async def run_pipeline(
    project_id: str,
    start_from_step: int = 1,
    settings: dict = {},
    adopt=None,
) -> None:
    project = _get_project(project_id)
    if not project:
        _debug(f"run_pipeline called — project {project_id} NOT FOUND")
        await broadcast("pipeline", "ERROR", f"Project {project_id} not found")
        return

    project_path = PROJECTS_DIR / project.slug
    # Per-project > defaults > code fallback (CLAUDE.md §4). The request wins
    # over the stored layer, never replaces it.
    settings = _with_project_settings(project, settings)

    # Step 1 is always already done (handled at project creation), and each
    # call runs exactly ONE step — the user validates and triggers the next one.
    actual_start = max(start_from_step, 2)

    # The run becomes a row on disk before it says a word, so every line it
    # says is teed into that row's log and carries its project id (TODO P7.1).
    # Without it a run was visible only to the browser that started it: a page
    # reload lost the bar, the log and the Abort button, which is what made
    # "the job died when I left the page" the obvious reading.
    #
    # `adopt` is that row coming back: a run whose child outlived the backend
    # (P7.2). The step is re-entered from the top with its reset suppressed and
    # `spawn` attaching to the live pid, so its own parser replays the
    # transcript and then follows the tool live. The settings come from the row
    # rather than from the project, because what this run *did* is what it has
    # to report.
    if adopt is not None:
        job = job_store.from_row(adopt)
        settings = job_store.stored_settings(adopt) or settings
        job.reset_log()
        job.mark_adopted((adopt.adopted or 0) + 1)
    else:
        job = start_job(
            project_id, _STEP_NAMES[actual_start], actual_start, settings
        )
    bus = job.wrap(broadcast)
    context_token = _install_run_context(job, adopt)
    outcome, outcome_error = "error", "the run exited without reporting"

    # ── Debug: log invocation context ────────────────────────────────────────
    existing_statuses = project.get_step_status()
    _debug(
        f"run_pipeline CALLED — project='{project.name}' ({project_id})"
        f"  start_from_step={start_from_step}"
        f"  DB current_step={project.current_step}"
        f"  DB step_status={existing_statuses}"
    )
    await bus(
        "pipeline", "DEBUG",
        f"[WIZARD-DEBUG] run_pipeline called: project='{project.name}'"
        f" start_from_step={start_from_step}"
        f" DB_current_step={project.current_step}"
        f" DB_step_status={existing_statuses}",
    )

    # Initialise abort / pause state for this run
    _abort_flags[project_id] = False
    pause_event = asyncio.Event()
    pause_event.set()  # Not paused initially
    _pause_events[project_id] = pause_event

    step_status = project.get_step_status()

    _debug(f"  → actual_start resolved to step {actual_start} ({_STEP_NAMES.get(actual_start, '?')})")

    if adopt is not None:
        await _announce_adoption(bus, job, adopt)

    try:
        for step in range(actual_start, actual_start + 1):
            step_name = _STEP_NAMES[step]

            # ── Abort check ────────────────────────────────────────────────
            if _abort_flags.get(project_id):
                _debug(f"  → ABORT flag set before step {step} — aborting")
                outcome, outcome_error = "aborted", None
                step_status[str(step)] = "aborted"
                _update_project(
                    project_id,
                    error_message="Pipeline aborted by user",
                    _step_status_dict=step_status,
                )
                await bus(
                    "pipeline", "WARNING",
                    f"Pipeline aborted for project {project_id}",
                    data={"status": "aborted", "project_id": project_id},
                )
                return

            # ── Pause check (blocks until resumed) ────────────────────────
            await pause_event.wait()

            # Second abort check in case abort was set while paused
            if _abort_flags.get(project_id):
                _debug(f"  → ABORT flag set after pause for step {step} — aborting")
                outcome, outcome_error = "aborted", None
                await bus(
                    "pipeline", "WARNING",
                    f"Pipeline aborted for project {project_id}",
                    data={"status": "aborted", "project_id": project_id},
                )
                return

            # ── Mark step as running ───────────────────────────────────────
            _debug(f"  → Step {step} ({step_name.upper()}) — marking as RUNNING")
            step_status[str(step)] = "running"
            _update_project(
                project_id,
                current_step=step,
                _step_status_dict=step_status,
            )
            # Broadcast a "status" message so the frontend can update stepStatuses[step] = 'running'
            await bus(
                step_name, "INFO",
                f"▶ Step {step} ({step_name.upper()}) starting...",
                status="running",
            )

            # ── Execute step ───────────────────────────────────────────────
            try:
                runner = _STEP_RUNNERS[step]
                if step in _ABORT_AWARE_STEPS:
                    await runner(
                        project_path, bus, settings,
                        should_abort=_abort_checker(project_id),
                    )
                else:
                    await runner(project_path, bus, settings)

                _debug(f"  → Step {step} ({step_name.upper()}) — DONE")
                outcome, outcome_error = "done", None
                step_status[str(step)] = "done"
                _update_project(project_id, _step_status_dict=step_status)
                # Broadcast "status" done + progress=1.0 so frontend marks step complete
                await bus(
                    step_name, "SUCCESS",
                    f"✔ Step {step} ({step_name.upper()}) complete."
                    f" ⏳ Waiting for user to click 'Validate & Continue'.",
                    progress=1.0,
                    status="done",
                )
                _debug(
                    f"  → Step {step} done — broadcast 'status=done' sent."
                    f" Frontend must wait for user click to advance wizard."
                )

            except (AnalysisAborted, ProcessAborted, asyncio.CancelledError) as exc:
                # A user abort is not a failure — do not poison the project with
                # an error_message the user then has to clear by hand. Naming
                # CancelledError matters: /control cancels the task outright, and
                # that derives from BaseException, so without this the step
                # stayed "running" in the UI until a page reload. ProcessAborted
                # covers the other order: the tool was killed and its non-zero
                # exit came back before the cancellation did.
                _debug(f"  → Step {step} ({step_name.upper()}) — ABORTED by user")
                outcome, outcome_error = "aborted", None
                step_status[str(step)] = "aborted"
                _update_project(project_id, _step_status_dict=step_status)
                await _broadcast_best_effort(
                    bus,
                    step_name, "WARNING",
                    f"■ Step {step} ({step_name.upper()}) aborted by user.",
                    status="aborted",
                )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return

            except Exception as exc:
                step_status[str(step)] = "error"
                outcome = "error"
                exc_detail = f"[{type(exc).__name__}] {exc}" if str(exc) else type(exc).__name__
                _debug(f"  → Step {step} ({step_name.upper()}) — ERROR: {exc_detail}")
                outcome_error = exc_detail
                _update_project(
                    project_id,
                    error_message=exc_detail,
                    _step_status_dict=step_status,
                )
                await bus(
                    step_name, "ERROR",
                    f"✖ Step {step} ({step_name.upper()}) failed: {exc_detail}",
                    status="error",
                )
                return

        # ── Pipeline complete (only when the last step, the mesh, finishes) ────
        if actual_start == 5:
            _debug(f"  → Full pipeline complete for project {project_id}")
            _update_project(project_id, current_step=5)
            await bus(
                "pipeline", "SUCCESS",
                f"🎉 Pipeline complete for project {project_id}",
                progress=1.0,
                data={"status": "complete", "project_id": project_id},
            )

    finally:
        _debug(f"  → run_pipeline cleanup for project {project_id}")
        reset_run_context(context_token)
        aborted = bool(_abort_flags.get(project_id))
        _abort_flags.pop(project_id, None)
        _pause_events.pop(project_id, None)
        _demote_if_still_running(project_id, "runner exited")
        # A job row left on "running" is the same dead end one layer down from a
        # step left on "running" (see reconcile_orphaned_steps), so the row
        # closes however the task left — including on a BaseException no
        # `except` clause above names.
        if job.state == "running":
            if outcome == "error" and aborted:
                outcome, outcome_error = "aborted", None
            job.finish(outcome, outcome_error)


# ── Standalone re-analysis ───────────────────────────────────────────────────

async def run_analysis_only(project_id: str, settings: dict = {}) -> None:
    """Re-run the curation phase of step 2 without touching the extracted frames.

    Threshold tuning is iterative by nature (CLAUDE.md §6.3): the user changes a
    sensitivity, looks at the gallery, changes it again. Re-extracting for that
    would be unacceptable, so this reuses frames/ as-is and rewrites only
    analysis/scores.json and analysis/selection.json. overrides.json is read,
    never regenerated.
    """
    project = _get_project(project_id)
    if not project:
        await broadcast("curate", "ERROR", f"Project {project_id} not found")
        return

    project_path = PROJECTS_DIR / project.slug
    settings = _with_project_settings(project, settings)
    step_status = project.get_step_status()

    # Its own job row, under its own name: curation is step 2's second phase and
    # is separately re-runnable, so a re-analysis is a run in its own right and
    # a page that reloads during one has to be able to find it (TODO P7.1).
    job = start_job(project_id, "curate", 2, settings)
    bus = job.wrap(broadcast)
    # No subprocess in this pass — the curation is numpy in this process — but
    # the context costs nothing and keeps one rule: anything a run spawns writes
    # into that run's transcript.
    context_token = _install_run_context(job)
    outcome, outcome_error = "error", "the analysis exited without reporting"

    _abort_flags[project_id] = False
    pause_event = asyncio.Event()
    pause_event.set()
    _pause_events[project_id] = pause_event

    _debug(f"run_analysis_only CALLED — project='{project.name}' ({project_id})")

    try:
        step_status["2"] = "running"
        _update_project(project_id, _step_status_dict=step_status)

        await run_analysis(
            project_path, bus, settings,
            should_abort=_abort_checker(project_id),
        )

        outcome, outcome_error = "done", None
        step_status["2"] = "done"
        _update_project(project_id, _step_status_dict=step_status)
        await bus("curate", "SUCCESS", "✔ Re-analysis complete.", status="done")

    except (AnalysisAborted, asyncio.CancelledError) as exc:
        # Two ways in: the cooperative flag, observed between chunks, or the hard
        # task.cancel() the /control route fires as a fallback. CancelledError
        # derives from BaseException, so it has to be named explicitly — left to
        # a bare `except Exception` it slips through and the UI stays stuck on
        # "running" forever.
        _debug(f"  → run_analysis_only ABORTED for project {project_id}")
        outcome, outcome_error = "aborted", None
        step_status["2"] = "aborted"
        _update_project(project_id, _step_status_dict=step_status)
        await _broadcast_best_effort(
            bus,
            "curate", "WARNING", "■ Re-analysis aborted by user.", status="aborted"
        )
        if isinstance(exc, asyncio.CancelledError):
            raise  # a cancelled task must end cancelled

    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        exc_detail = f"[{type(exc).__name__}] {exc}" if str(exc) else type(exc).__name__
        _debug(f"  → run_analysis_only ERROR: {exc_detail}")
        outcome, outcome_error = "error", exc_detail
        step_status["2"] = "error"
        _update_project(project_id, error_message=exc_detail, _step_status_dict=step_status)
        await bus("curate", "ERROR", f"✖ Re-analysis failed: {exc_detail}", status="error")

    finally:
        reset_run_context(context_token)
        aborted = bool(_abort_flags.get(project_id))
        _abort_flags.pop(project_id, None)
        _pause_events.pop(project_id, None)
        _demote_if_still_running(project_id, "analysis exited")
        if job.state == "running":
            if outcome == "error" and aborted:
                outcome, outcome_error = "aborted", None
            job.finish(outcome, outcome_error)


# ── Standalone mask generation ───────────────────────────────────────────────

async def _run_attached_pass(
    project_id: str,
    settings: dict,
    *,
    step: int,
    name: str,
    runner,
    opening: str,
    label: str,
    adopt=None,
) -> None:
    """One re-runnable pass that *attaches* to a wizard step without being it.

    `spirula sam` (step 3) and `spirula geometry` (step 4) are both this shape:
    separately re-runnable so that changing a threshold never costs the
    expensive phase, which is the argument `run_analysis_only` makes for
    curation (CLAUDE.md §6.3, §7.4, §7.5).

    **The wizard step's status is restored, never set to `done`.** This is the
    one place these differ from `/analyze`, and it is not a detail: curation
    really is the second phase of step 2 and finishing it finishes the step,
    whereas a mask run produces no reconstruction and a geometry run produces no
    splat. Marking step 3 `done` because `sam mask` wrote 238 PNGs would put a
    green tick on a step that has never been run — so the prior status is
    captured here and handed back, and only the run's own name carries the
    live state to the LiveLog and the bar.
    """
    project = _get_project(project_id)
    if not project:
        await broadcast(name, "ERROR", f"Project {project_id} not found")
        return

    project_path = PROJECTS_DIR / project.slug
    settings = _with_project_settings(project, settings)
    step_status = project.get_step_status()
    key = str(step)
    # What the wizard step was before this pass, to be handed back afterwards.
    # `pending` is the honest answer for a step that has never run, and it is a
    # value the store accepts explicitly.
    previous = step_status.get(key, "pending")

    _abort_flags[project_id] = False
    pause_event = asyncio.Event()
    pause_event.set()
    _pause_events[project_id] = pause_event

    # The pass gets a job row under its own name (`masks`, `geometry`, `crop`,
    # `splat_export`) and carries the wizard step it attaches to, so a reload
    # restores the bar on that step without claiming the step itself is running
    # any differently than it was (TODO P7.1).
    if adopt is not None:
        # A `sam` or `geometry` child that outlived the backend (P7.2). Same
        # re-entry as a step: reset suppressed, `spawn` attaching to the live
        # pid, the pass's own parser replaying its transcript.
        job = job_store.from_row(adopt)
        settings = job_store.stored_settings(adopt) or settings
        job.reset_log()
        job.mark_adopted((adopt.adopted or 0) + 1)
    else:
        job = start_job(project_id, name, step, settings)
    bus = job.wrap(broadcast)
    context_token = _install_run_context(job, adopt)
    outcome, outcome_error = "error", "the pass exited without reporting"

    _debug(f"{label} CALLED — project='{project.name}' ({project_id})")

    if adopt is not None:
        await _announce_adoption(bus, job, adopt)

    try:
        step_status[key] = "running"
        _update_project(project_id, _step_status_dict=step_status)
        await bus(name, "INFO", opening, status="running")

        await runner(project_path, bus, settings)

        outcome, outcome_error = "done", None
        step_status[key] = previous
        _update_project(project_id, _step_status_dict=step_status)
        await bus(
            name, "SUCCESS", f"✔ {label} complete.", status=previous
        )

    except (ProcessAborted, asyncio.CancelledError) as exc:
        # Same two ways in as every exe-driven step: the tree kill that raises
        # ProcessAborted, and the hard task.cancel() the /control route fires.
        # CancelledError derives from BaseException, so naming it is what stops
        # the step from being stuck on "running" until a page reload.
        _debug(f"  → {label} ABORTED for project {project_id}")
        outcome, outcome_error = "aborted", None
        step_status[key] = previous
        _update_project(project_id, _step_status_dict=step_status)
        await _broadcast_best_effort(
            bus,
            name, "WARNING", f"■ {label} aborted by user.", status="aborted",
        )
        if isinstance(exc, asyncio.CancelledError):
            raise

    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        exc_detail = f"[{type(exc).__name__}] {exc}" if str(exc) else type(exc).__name__
        _debug(f"  → {label} ERROR: {exc_detail}")
        outcome, outcome_error = "error", exc_detail
        # The wizard step is not in error — this pass is. Restoring it keeps a
        # failed mask run from painting a finished reconstruction red.
        step_status[key] = previous
        _update_project(
            project_id, error_message=exc_detail, _step_status_dict=step_status
        )
        await bus(
            name, "ERROR", f"✖ {label} failed: {exc_detail}", status="error",
        )

    finally:
        reset_run_context(context_token)
        aborted = bool(_abort_flags.get(project_id))
        _abort_flags.pop(project_id, None)
        _pause_events.pop(project_id, None)
        _demote_if_still_running(project_id, f"{label} exited")
        if job.state == "running":
            if outcome == "error" and aborted:
                outcome, outcome_error = "aborted", None
            job.finish(outcome, outcome_error)


async def run_mask_generation(project_id: str, settings: dict = {}, adopt=None) -> None:
    """Write `masks/` with `spirula sam` — never a re-alignment (§7.4).

    Attaches to wizard step 3 and broadcasts under the name `masks`, which the
    frontend store maps to step 3 exactly as it maps `curate` to step 2. The
    masks themselves belong to step 2's directory in §14.1's table: they are an
    *input* to the reconstruction, so a step 3 reset must not take them and this
    run must not claim step 3 is done.
    """
    await _run_attached_pass(
        project_id, settings,
        step=3, name=MASK_STEP_NAME, runner=_run_masking,
        opening="▶ Writing masks with spirula sam…",
        label="Mask generation",
        adopt=adopt,
    )


async def run_geometry_only(project_id: str, settings: dict = {}, adopt=None) -> None:
    """Write `sfm/normals/` and `sfm/depths/` with `spirula geometry` (§7.5).

    Attaches to wizard step 4, whose training run reads them through `--data`
    with no flag. Never a re-training, and never a reset of `sfm/`: the maps sit
    *inside* the dataset the run is reading, so clearing it would delete the
    sparse model. A step 3 re-run is what takes them, and `step_sfm` says so
    before it does (§14.1).
    """
    await _run_attached_pass(
        project_id, settings,
        step=4, name=GEOMETRY_STEP_NAME, runner=_run_geometry,
        opening="▶ Estimating depth and normal maps with spirula geometry…",
        label="Geometry supervision",
        adopt=adopt,
    )


async def run_crop_only(project_id: str, settings: dict = {}) -> None:
    """Cut the trained splat to the stored volumes, into `train/crop/` (§7.6b).

    Attaches to wizard step 4, and is the third pass of that shape — but the
    first whose abort is *only* the cooperative flag. `sam` and `geometry` are
    subprocesses, so §2.6's tree kill is what unblocks their reader; this one is
    numpy over a memory map in our own process, and nothing external can be
    killed to stop it. `step_crop` checks the flag between chunks and raises the
    same `ProcessAborted` the other two do, which is what `_run_attached_pass`
    already reports as `aborted` rather than as an error.

    Never a re-training, and never a rewrite of what the trainer produced: the
    cut writes a second file and `resolve_splat` is what steps 5 and 6 ask.
    """
    should_abort = _abort_checker(project_id)

    async def runner(project_path, broadcast_fn, resolved: dict):
        return await _run_crop(
            project_path, broadcast_fn, resolved, should_abort=should_abort,
        )

    await _run_attached_pass(
        project_id, settings,
        step=4, name=CROP_STEP_NAME, runner=runner,
        opening="▶ Cutting the splat to the crop volumes…",
        label="Splat crop",
    )


async def run_splat_export_only(project_id: str, settings: dict = {}) -> None:
    """Write a deliverable copy of step 4's splat into `train/export/` (§7.6c).

    The fourth pass of this shape and the first whose output **nothing in the
    pipeline reads**. A crop is pipeline data — step 5 meshes it through
    `resolve_splat` — whereas an export is terminal: it drops spherical
    harmonics and quantises into formats no mesher reads, so it lives in
    its own directory under a name neither `find_splat` nor `find_export_splat`
    will ever match.

    Its abort has both halves of the pattern in one pass: the native PLY and
    `.splat` writers are numpy over a memory map and stop on the cooperative
    flag, while the three compressed formats shell out to `splat-transform` and
    stop on §2.6's tree kill. Both arrive here as `ProcessAborted`.
    """
    should_abort = _abort_checker(project_id)

    async def runner(project_path, broadcast_fn, resolved: dict):
        return await _run_splat_export(
            project_path, broadcast_fn, resolved, should_abort=should_abort,
        )

    await _run_attached_pass(
        project_id, settings,
        step=4, name=SPLAT_EXPORT_STEP_NAME, runner=runner,
        opening="▶ Exporting the splat…",
        label="Splat export",
    )
