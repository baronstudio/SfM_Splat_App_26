"""
pipeline_runner.py — Full orchestrator for the 3DGS pipeline.

Step numbering:
  1  Import       (handled at project creation — skipped here)
  2  Extract      FFmpeg frame extraction, then curation (CLAUDE.md §6)
  3  RC           RealityCapture alignment
  4  LFS          LichtFeld Studio 3DGS training
  5  Export       Copy PLY/splat to export/
  6  Blender      SplatForge scene export
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from backend.api.websocket import broadcast
from backend.core.defaults import deep_merge
from backend.core.proc import ProcessAborted, kill_project_children
from backend.core.steps.step_analyze import (
    AnalysisAborted,
    resolve_curate_settings,
    run_analysis,
)
from backend.core.steps.step_export import run_export
from backend.core.steps.step_extract import run_extract
from backend.core.steps.step_geometry import run_geometry as _run_geometry
from backend.core.steps.step_mesh import run_mesh
from backend.core.steps.step_sam import run_masking as _run_masking
from backend.core.steps.step_scene import run_blender
from backend.core.steps.step_sfm import run_sfm
from backend.core.steps.step_train import run_train
from backend.db.database import engine
from backend.models.project import Project

PROJECTS_DIR = Path(__file__).parents[2] / "projects"

# ── Pause / Abort control ────────────────────────────────────────────────────

_abort_flags: dict[str, bool] = {}
_pause_events: dict[str, asyncio.Event] = {}


def _abort_checker(project_id: str):
    """Injectable predicate so a long step can honour abort (CLAUDE.md §2.6)."""
    return lambda: bool(_abort_flags.get(project_id))


# Two passes attach to a wizard step without being it: `spirula sam` reports as
# `masks` into step 3 and `spirula geometry` as `geometry` into step 4. The
# frontend store maps both, the same shape as `curate` reporting into step 2
# (CLAUDE.md §12, 2026-08-20). Neither ever marks its step done — see
# `_run_attached_pass`.
MASK_STEP_NAME = "masks"
GEOMETRY_STEP_NAME = "geometry"

_STEP_NAMES: dict[int, str] = {
    1: "import",
    2: "extract",
    3: "sfm",
    4: "train",
    5: "mesh",
    6: "scene",
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
    """Step 5 meshes, then fills `export/` - the two share a directory (§14.1)."""
    result = await run_mesh(project_path, broadcast_fn, settings)
    result["export"] = await run_export(project_path, broadcast_fn, settings)
    return result


_STEP_RUNNERS = {
    2: _run_extract_and_curate,
    3: run_sfm,
    4: run_train,
    5: _run_mesh_and_export,
    6: run_blender,
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


async def _broadcast_best_effort(*args, **kwargs) -> None:
    """Broadcast from inside a cancellation handler.

    Awaiting in the `except CancelledError` branch of a task that has just been
    cancelled can be cancelled again before it completes. The abort notice is
    the last thing the UI hears, so losing it is what leaves a step stuck on
    "running" — swallow the second cancellation rather than the message.
    """
    try:
        await asyncio.shield(broadcast(*args, **kwargs))
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
    the same thing — but a run started with `{}` (steps 5 and 6, or a call from
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


def reconcile_orphaned_steps() -> int:
    """Demote every step still persisted as "running" at process start.

    `step_status` is the only thing the wizard hydrates from, and nothing but a
    live run ever moves a step off "running". A backend killed or reloaded mid
    step therefore leaves that state in the DB forever: `Step*` components
    disable their start button on `status === 'running'`, and the Abort button
    only renders while `pipelineRunning` is true — which a fresh page load never
    is. The step becomes a dead end reachable only by editing the DB by hand.

    Nothing survives a restart, so any "running" found here is by definition
    stale. Demote it to "error" with a message that says what happened, rather
    than to "pending", which would claim the step was never attempted.
    """
    swept = 0
    with Session(engine) as session:
        for project in session.exec(select(Project)).all():
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


# ── Main orchestrator ────────────────────────────────────────────────────────

async def run_pipeline(
    project_id: str,
    start_from_step: int = 1,
    settings: dict = {},
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

    # ── Debug: log invocation context ────────────────────────────────────────
    existing_statuses = project.get_step_status()
    _debug(
        f"run_pipeline CALLED — project='{project.name}' ({project_id})"
        f"  start_from_step={start_from_step}"
        f"  DB current_step={project.current_step}"
        f"  DB step_status={existing_statuses}"
    )
    await broadcast(
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

    # Step 1 is always already done (handled at project creation).
    # Each call runs exactly ONE step — the user validates and triggers the next one.
    actual_start = max(start_from_step, 2)

    _debug(f"  → actual_start resolved to step {actual_start} ({_STEP_NAMES.get(actual_start, '?')})")

    try:
        for step in range(actual_start, actual_start + 1):
            step_name = _STEP_NAMES[step]

            # ── Abort check ────────────────────────────────────────────────
            if _abort_flags.get(project_id):
                _debug(f"  → ABORT flag set before step {step} — aborting")
                step_status[str(step)] = "aborted"
                _update_project(
                    project_id,
                    error_message="Pipeline aborted by user",
                    _step_status_dict=step_status,
                )
                await broadcast(
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
                await broadcast(
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
            await broadcast(
                step_name, "INFO",
                f"▶ Step {step} ({step_name.upper()}) starting...",
                status="running",
            )

            # ── Execute step ───────────────────────────────────────────────
            try:
                runner = _STEP_RUNNERS[step]
                if step in _ABORT_AWARE_STEPS:
                    await runner(
                        project_path, broadcast, settings,
                        should_abort=_abort_checker(project_id),
                    )
                else:
                    await runner(project_path, broadcast, settings)

                _debug(f"  → Step {step} ({step_name.upper()}) — DONE")
                step_status[str(step)] = "done"
                _update_project(project_id, _step_status_dict=step_status)
                # Broadcast "status" done + progress=1.0 so frontend marks step complete
                await broadcast(
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
                step_status[str(step)] = "aborted"
                _update_project(project_id, _step_status_dict=step_status)
                await _broadcast_best_effort(
                    step_name, "WARNING",
                    f"■ Step {step} ({step_name.upper()}) aborted by user.",
                    status="aborted",
                )
                if isinstance(exc, asyncio.CancelledError):
                    raise
                return

            except Exception as exc:
                step_status[str(step)] = "error"
                exc_detail = f"[{type(exc).__name__}] {exc}" if str(exc) else type(exc).__name__
                _debug(f"  → Step {step} ({step_name.upper()}) — ERROR: {exc_detail}")
                _update_project(
                    project_id,
                    error_message=exc_detail,
                    _step_status_dict=step_status,
                )
                await broadcast(
                    step_name, "ERROR",
                    f"✖ Step {step} ({step_name.upper()}) failed: {exc_detail}",
                    status="error",
                )
                return

        # ── Pipeline complete (only when the last step, Blender, finishes) ──────
        if actual_start == 6:
            _debug(f"  → Full pipeline complete for project {project_id}")
            _update_project(project_id, current_step=6)
            await broadcast(
                "pipeline", "SUCCESS",
                f"🎉 Pipeline complete for project {project_id}",
                progress=1.0,
                data={"status": "complete", "project_id": project_id},
            )

    finally:
        _debug(f"  → run_pipeline cleanup for project {project_id}")
        _abort_flags.pop(project_id, None)
        _pause_events.pop(project_id, None)
        _demote_if_still_running(project_id, "runner exited")


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

    _abort_flags[project_id] = False
    pause_event = asyncio.Event()
    pause_event.set()
    _pause_events[project_id] = pause_event

    _debug(f"run_analysis_only CALLED — project='{project.name}' ({project_id})")

    try:
        step_status["2"] = "running"
        _update_project(project_id, _step_status_dict=step_status)

        await run_analysis(
            project_path, broadcast, settings,
            should_abort=_abort_checker(project_id),
        )

        step_status["2"] = "done"
        _update_project(project_id, _step_status_dict=step_status)
        await broadcast("curate", "SUCCESS", "✔ Re-analysis complete.", status="done")

    except (AnalysisAborted, asyncio.CancelledError) as exc:
        # Two ways in: the cooperative flag, observed between chunks, or the hard
        # task.cancel() the /control route fires as a fallback. CancelledError
        # derives from BaseException, so it has to be named explicitly — left to
        # a bare `except Exception` it slips through and the UI stays stuck on
        # "running" forever.
        _debug(f"  → run_analysis_only ABORTED for project {project_id}")
        step_status["2"] = "aborted"
        _update_project(project_id, _step_status_dict=step_status)
        await _broadcast_best_effort(
            "curate", "WARNING", "■ Re-analysis aborted by user.", status="aborted"
        )
        if isinstance(exc, asyncio.CancelledError):
            raise  # a cancelled task must end cancelled

    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        exc_detail = f"[{type(exc).__name__}] {exc}" if str(exc) else type(exc).__name__
        _debug(f"  → run_analysis_only ERROR: {exc_detail}")
        step_status["2"] = "error"
        _update_project(project_id, error_message=exc_detail, _step_status_dict=step_status)
        await broadcast("curate", "ERROR", f"✖ Re-analysis failed: {exc_detail}", status="error")

    finally:
        _abort_flags.pop(project_id, None)
        _pause_events.pop(project_id, None)
        _demote_if_still_running(project_id, "analysis exited")


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

    _debug(f"{label} CALLED — project='{project.name}' ({project_id})")

    try:
        step_status[key] = "running"
        _update_project(project_id, _step_status_dict=step_status)
        await broadcast(name, "INFO", opening, status="running")

        await runner(project_path, broadcast, settings)

        step_status[key] = previous
        _update_project(project_id, _step_status_dict=step_status)
        await broadcast(
            name, "SUCCESS", f"✔ {label} complete.", status=previous
        )

    except (ProcessAborted, asyncio.CancelledError) as exc:
        # Same two ways in as every exe-driven step: the tree kill that raises
        # ProcessAborted, and the hard task.cancel() the /control route fires.
        # CancelledError derives from BaseException, so naming it is what stops
        # the step from being stuck on "running" until a page reload.
        _debug(f"  → {label} ABORTED for project {project_id}")
        step_status[key] = previous
        _update_project(project_id, _step_status_dict=step_status)
        await _broadcast_best_effort(
            name, "WARNING", f"■ {label} aborted by user.", status="aborted",
        )
        if isinstance(exc, asyncio.CancelledError):
            raise

    except Exception as exc:  # noqa: BLE001 — surfaced to the user verbatim
        exc_detail = f"[{type(exc).__name__}] {exc}" if str(exc) else type(exc).__name__
        _debug(f"  → {label} ERROR: {exc_detail}")
        # The wizard step is not in error — this pass is. Restoring it keeps a
        # failed mask run from painting a finished reconstruction red.
        step_status[key] = previous
        _update_project(
            project_id, error_message=exc_detail, _step_status_dict=step_status
        )
        await broadcast(
            name, "ERROR", f"✖ {label} failed: {exc_detail}", status="error",
        )

    finally:
        _abort_flags.pop(project_id, None)
        _pause_events.pop(project_id, None)
        _demote_if_still_running(project_id, f"{label} exited")


async def run_mask_generation(project_id: str, settings: dict = {}) -> None:
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
    )


async def run_geometry_only(project_id: str, settings: dict = {}) -> None:
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
    )
