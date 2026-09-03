"""jobs.py — a run recorded on disk, so it can be found again.

Pure-ish: SQLModel and the filesystem, no FastAPI (CLAUDE.md §2.4). The bus
function is handed in — `JobRecord.wrap(broadcast)` returns a broadcaster with
the same signature that tees every line into the job's log and keeps the row up
to date on the way past.

Three things it buys, and none of them is a queue (§1 is untouched: one user,
one job at a time, still enforced by `_claim_slot`):

* `/api/pipeline/status` answers from the database instead of from
  `_running_tasks`, a dict only the process that started the run can see.
* The LiveLog has a tail to restore. The store keeps 500 lines in memory and a
  reload lost every one of them, so a run in its fifteenth minute came back as
  a blank panel.
* Every message carries its `project_id`, which is what lets a reconnecting
  client tell its own run from another project's (CLAUDE.md §13.7).

The log file lives in `runs/`, **outside `projects/`**: it describes a run, not
a reconstruction, so §14.1 gains no row and no reset, copy or archive has to
reason about it.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from backend.core import proc
from backend.db.database import engine
from backend.models.job import Job

# The runs that can be re-attached after a backend restart (TODO P7.2): the five
# whose entire work *is* one child process, so replaying the step over the
# transcript reproduces exactly what the first attempt was doing.
#
# `extract` is deliberately not here even though FFmpeg is a child: the image-set
# branch of step 2 (`step_conform`) starts up to three commands, and a replay
# would re-run the first while the live one is still writing. It is also the
# cheapest step to simply run again — 238 frames in 80 s.
#
# `curate`, `crop` and `splat_export` cannot be here at all: their work is numpy
# in this process, and it died with it.
ADOPTABLE_KINDS = frozenset({"sfm", "train", "mesh", "masks", "geometry"})

RUNS_DIR = Path(__file__).parents[2] / "runs"

# How many runs keep their row and their log. A run is a few hundred lines of
# text; the point of the cap is only that nothing here grows without bound.
_KEEP_JOBS = 200

# The row is rewritten at most this often while a run is going. Every state
# transition is written immediately — this throttles progress and the last
# message only. The engine echoes its SQL, so a per-line commit would bury the
# run's own output in the console it shares.
_ROW_THROTTLE_S = 2.0


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class JobRecord:
    """The live handle on one row: tee, throttle, close."""

    def __init__(
        self,
        job_id: str,
        project_id: str,
        kind: str,
        step: int,
        log_path: Path,
        tool_log_path: Optional[Path] = None,
    ):
        self.id = job_id
        self.project_id = project_id
        self.kind = kind
        self.step = step
        self.log_path = log_path
        self.tool_log_path = tool_log_path
        # Read by the runner's `finally`, which closes the row out for whatever
        # left the task without naming itself — including a BaseException no
        # `except` clause catches. Same argument as `_demote_if_still_running`.
        self.state = "running"
        self._fh = None
        self._lines = 0
        self._progress = 0.0
        self._message = ""
        self._spawns = 0
        self._flushed_at = time.monotonic()

    # ── the bus ──────────────────────────────────────────────────────────────

    def wrap(self, broadcast_fn):
        """A broadcaster with the bus signature, plus the tee.

        Handed to the step runners in place of `broadcast` itself, so nothing
        under `core/steps/` changes or learns what a job is — they already
        receive the bus by injection (§2.4).
        """

        async def job_broadcast(
            step: str,
            level: str,
            message: str = "",
            progress: Optional[float] = None,
            data: Optional[dict] = None,
            file: Optional[str] = None,
            status: Optional[str] = None,
        ) -> None:
            self.note(step, level, message, progress, data)
            await broadcast_fn(
                step, level, message,
                progress=progress, data=data, file=file, status=status,
                project_id=self.project_id, job_id=self.id,
            )

        return job_broadcast

    def note(
        self,
        step: str,
        level: str,
        message: str,
        progress: Optional[float] = None,
        data: Optional[dict] = None,
    ) -> None:
        """Record one bus message. Never raises — a log line is never worth a run.

        Empty messages are dropped rather than stored: `step_mesh` rides the bar
        on 354 of its 419 lines with an empty message precisely so the LiveLog
        never sees them (§7.8), and the file must hold what the panel holds.
        """
        try:
            if progress is not None:
                self._progress = progress
            if message:
                self._message = message
                self._append({
                    "t": datetime.now(timezone.utc).isoformat(),
                    "step": step,
                    "level": level,
                    "message": message,
                    "progress": progress,
                    # The metric payload rides along so a restored run gets its
                    # chart back too, not just its text. `spirula train` puts
                    # both on every one of its bar lines (§7.7), and a 30 000
                    # iteration run is 300 of them — the longest thing in this
                    # app is also the one most likely to be reloaded through.
                    "data": data,
                })
            self._maybe_flush()
        except Exception:  # noqa: BLE001 — see the docstring
            pass

    def _append(self, entry: dict) -> None:
        if self._fh is None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            # Line buffered: a backend killed mid-run still leaves every line
            # written before it on disk, which is the whole point of the file.
            self._fh = open(self.log_path, "a", encoding="utf-8", buffering=1)
        self._fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._lines += 1

    def _maybe_flush(self) -> None:
        if time.monotonic() - self._flushed_at < _ROW_THROTTLE_S:
            return
        self._flushed_at = time.monotonic()
        _update(
            self.id,
            progress=self._progress,
            message=self._message[:500],
            log_lines=self._lines,
        )

    # ── the child, so a later process can find it ───────────────────────────

    def record_pid(self, pid: int, image: str, created: int) -> None:
        """Write down what was just started. Called by `proc.spawn`.

        This is the whole of P7.2's durability: a backend that comes back has
        the pid, the image name and the creation time, which is enough to prove
        the child is still the same child and to wait on it (`proc.process_is`).
        """
        self._spawns += 1
        _update(
            self.id,
            pid=int(pid),
            pid_image=(image or "").lower(),
            pid_created=int(created or 0),
            spawns=self._spawns,
        )

    def mark_adopted(self, times: int) -> None:
        _update(self.id, adopted=times)

    def reset_log(self) -> None:
        """Drop the derived log and start it again.

        Used when a run is re-attached: the step is replayed over the tool's own
        transcript, so it will re-broadcast every line it already broadcast.
        The ndjson is *derived* from that transcript, so rebuilding it is exact
        and duplicating it would not be.
        """
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._fh = None
        try:
            self.log_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._lines = 0
        _update(self.id, log_lines=0)

    # ── the end ──────────────────────────────────────────────────────────────

    def finish(self, state: str, error: Optional[str] = None) -> None:
        """Close the row out. `state` is the step vocabulary: done/error/aborted."""
        self.state = state
        try:
            _update(
                self.id,
                state=state,
                progress=1.0 if state == "done" else self._progress,
                message=self._message[:500],
                error_message=error,
                finished_at=_now(),
                log_lines=self._lines,
            )
        finally:
            if self._fh is not None:
                try:
                    self._fh.close()
                except Exception:  # noqa: BLE001
                    pass
                self._fh = None


# ── row helpers ──────────────────────────────────────────────────────────────

def _update(job_id: str, **fields) -> None:
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            return
        for key, value in fields.items():
            setattr(job, key, value)
        session.add(job)
        session.commit()


def start_job(
    project_id: str, kind: str, step: int, settings: Optional[dict] = None
) -> JobRecord:
    """Open a row for a run about to start, and prune the old ones.

    `settings` is the layer the run resolved, stored so that a re-attached run
    reports what *this* run did rather than what the project's stored layer says
    by the time it comes back (§4's panels PATCH a debounced diff).
    """
    prune()
    job = Job(project_id=project_id, kind=kind, step=step, state="running")
    if settings is not None:
        try:
            job.settings_json = json.dumps(settings)
        except (TypeError, ValueError):
            job.settings_json = None
    with Session(engine) as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    log_path = RUNS_DIR / f"{job_id}.ndjson"
    tool_log = RUNS_DIR / f"{job_id}.tool.log"
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    # The transcript starts empty even if something left a file under this name:
    # `proc.spawn` appends, because a step may run more than one command.
    try:
        tool_log.unlink(missing_ok=True)
    except OSError:
        pass
    _update(job_id, log_path=str(log_path), tool_log_path=str(tool_log))
    return JobRecord(job_id, project_id, kind, step, log_path, tool_log)


def from_row(job: Job) -> JobRecord:
    """Rebuild a live handle on a row this process did not open.

    The counters start where the row left them, so a re-attached run's line
    count keeps counting rather than restarting.
    """
    record = JobRecord(
        job.id,
        job.project_id,
        job.kind,
        job.step,
        Path(job.log_path) if job.log_path else RUNS_DIR / f"{job.id}.ndjson",
        Path(job.tool_log_path) if job.tool_log_path else None,
    )
    record.state = job.state
    record._spawns = job.spawns or 0
    return record


def stored_settings(job: Job) -> dict:
    if not job.settings_json:
        return {}
    try:
        loaded = json.loads(job.settings_json)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def adoption_candidates() -> tuple[list[Job], list[Job]]:
    """Split the rows left `running` into what can be re-attached and what cannot.

    A row is adoptable when its kind is one of `ADOPTABLE_KINDS`, it started
    exactly one command, its transcript is on disk, and its child is **still the
    same child** — pid, image name and creation time all agreeing
    (`proc.process_is`). Everything else is an orphan: its tool, if any, is
    working for a result nobody will collect.
    """
    adoptable: list[Job] = []
    orphans: list[Job] = []
    with Session(engine) as session:
        rows = list(session.exec(select(Job).where(Job.state == "running")).all())

    for job in rows:
        alive = proc.process_is(job.pid, job.pid_image, job.pid_created)
        transcript = Path(job.tool_log_path) if job.tool_log_path else None
        if (
            alive
            and job.kind in ADOPTABLE_KINDS
            and (job.spawns or 0) <= 1
            and transcript is not None
            and transcript.exists()
        ):
            adoptable.append(job)
        else:
            orphans.append(job)
    return adoptable, orphans


def get_job(job_id: str) -> Optional[Job]:
    with Session(engine) as session:
        return session.get(Job, job_id)


def active_job(project_id: Optional[str] = None) -> Optional[Job]:
    """The run still marked `running`, for this project or anywhere."""
    with Session(engine) as session:
        query = select(Job).where(Job.state == "running")
        if project_id:
            query = query.where(Job.project_id == project_id)
        return session.exec(query.order_by(Job.started_at.desc())).first()


def recent_jobs(project_id: Optional[str] = None, limit: int = 20) -> list[Job]:
    with Session(engine) as session:
        query = select(Job)
        if project_id:
            query = query.where(Job.project_id == project_id)
        return list(
            session.exec(query.order_by(Job.started_at.desc()).limit(limit)).all()
        )


def read_log(
    job: Job, limit: int = 500, after: Optional[int] = None
) -> tuple[list[dict], int]:
    """The job's log lines and how many there are in total.

    `after` continues a listing the client already has; without it the **tail**
    comes back, which is what a page load wants — the store keeps 500 lines and
    a run in its fifteenth minute has more than that.
    """
    path = Path(job.log_path) if job.log_path else None
    if not path or not path.exists():
        return [], 0

    entries: list[dict] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for index, raw in enumerate(fh):
            raw = raw.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            payload["i"] = index
            entries.append(payload)

    total = len(entries)
    if after is not None:
        return entries[after : after + limit], total
    return entries[-limit:], total


def close_orphaned_jobs(rows: Optional[list[Job]] = None) -> int:
    """Close the rows left `running` that nobody is going to re-attach to.

    Since P7.2 a restart re-attaches what it can (`adoption_candidates`), so
    this is the other half: a run whose child is gone, or whose work was numpy
    in the process that died, or whose step started more than one command and
    therefore cannot be replayed safely.

    **A live child of an unadoptable run is killed here rather than left.** It
    is working for a result nobody will collect, and on this machine that means
    a tool sitting on the GPU — the exact orphan §2.6's tree kill exists for,
    arriving by a different route.
    """
    swept = 0
    killed_any = 0
    with Session(engine) as session:
        if rows is None:
            rows = list(session.exec(select(Job).where(Job.state == "running")).all())
        for row in rows:
            job = session.get(Job, row.id)
            if not job or job.state != "running":
                continue
            killed = proc.kill_orphan(job.pid, job.pid_image, job.pid_created)
            killed_any += 1 if killed else 0
            job.state = "error"
            job.finished_at = _now()
            job.error_message = _orphan_message(job, killed)
            session.add(job)
            swept += 1
        if swept:
            session.commit()
    if killed_any:
        print(f"[startup] killed {killed_any} orphaned tool tree(s)", flush=True)
    return swept


def delete_jobs_for_project(project_id: str) -> int:
    """Drop a deleted project's rows and their logs."""
    removed = 0
    with Session(engine) as session:
        for job in session.exec(select(Job).where(Job.project_id == project_id)).all():
            _unlink_log(job)
            session.delete(job)
            removed += 1
        if removed:
            session.commit()
    return removed


def prune(keep: int = _KEEP_JOBS) -> int:
    """Keep the most recent `keep` rows; delete the rest with their logs.

    Also sweeps log files no row claims any more. Windows refuses to unlink a
    file another handle still has open, so a delete that lands while its writer
    is live leaves the file behind and its row gone — nothing else would ever
    come back for it.
    """
    removed = 0
    with Session(engine) as session:
        rows = session.exec(select(Job).order_by(Job.started_at.desc())).all()
        for job in rows[keep:]:
            if job.state == "running":
                continue  # never prune a live run out from under itself
            _unlink_log(job)
            session.delete(job)
            removed += 1
        if removed:
            session.commit()
        claimed = {
            Path(path).name
            for j in session.exec(select(Job)).all()
            for path in (j.log_path, j.tool_log_path)
            if path
        }
    if RUNS_DIR.exists():
        for stray in list(RUNS_DIR.glob("*.ndjson")) + list(RUNS_DIR.glob("*.tool.log")):
            if stray.name not in claimed:
                try:
                    stray.unlink()
                except OSError:
                    pass
    return removed


def _orphan_message(job: Job, killed: bool) -> str:
    """Say which of the two things happened, because they need different answers.

    **The tool was still running** and could not be re-attached (its kind is not
    replayable, or it started more than one command): it has been stopped, and
    re-running the step is the whole story.

    **The tool was already gone**, and this is the case the first P7.2 run
    turned up rather than one that was predicted: measured 2026-09-03, a
    `spirula sfm auto` finished by itself with **no backend at all** — 31.43 s,
    `RESULT: OK -- 100% of the images registered`, `sparse/0` complete on disk —
    and nothing was here to collect it, so `sfm_result.json` was never written
    and the step read as an error over a reconstruction that is fine. The exit
    code cannot be recovered once the last handle to a process closes, and this
    app does not invent one (§2.2), so the run is not finalised — but the
    transcript is complete and is named here, because what the tool said about
    its own result is in it.
    """
    if killed:
        return (
            "[Interrupted] The backend stopped while this run was going, and its"
            " tool could not be re-attached — it has been stopped. Re-run the step."
        )
    where = f" Its output is in {job.tool_log_path}." if job.tool_log_path else ""
    return (
        "[Interrupted] The backend stopped while this run was going and its tool"
        " is no longer running. Whatever it wrote is on disk, but nothing was"
        " here to collect the result, so this step's report was not written —"
        f" re-run the step to regenerate it.{where}"
    )


def _unlink_log(job: Job) -> None:
    for path in (job.log_path, job.tool_log_path):
        if not path:
            continue
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass
