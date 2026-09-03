"""job.py — one row per pipeline run, so a run is a record and not an object.

Why this table exists (CLAUDE.md TODO P7.1): everything that described a
running step used to live in memory — the `asyncio.Task` in
`api/routes/pipeline.py`'s `_running_tasks`, the bar and the 500-line log in
the browser's store. A page that reloaded therefore lost the run entirely: no
progress, no log, and no Abort button, which renders on client-side
`pipelineRunning`. The job itself was still working the whole time, which is
what made the symptom read as "closing the page killed it".

The row is written at every transition and throttled in between, so
`/api/pipeline/status` answers from the database rather than from a dict that
only the process that started the run can see.

Scope, so nobody reads more into it than it does: this is not a queue
(CLAUDE.md §1 — one user, one job at a time, and `_claim_slot` still enforces
it). It is the same single job, made durable enough to be found again.
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    id: Optional[str] = Field(
        default_factory=lambda: uuid4().hex[:12], primary_key=True
    )
    project_id: str = Field(index=True)
    # The run's own name on the WS bus — `extract`, `curate`, `sfm`, `masks`,
    # `geometry`, `train`, `crop`, `splat_export`, `mesh`. Not the wizard step:
    # four of those attach to a step without being it (§7.4, §7.5, §7.6b/c).
    kind: str
    # The wizard step this run reports into, which is what the UI restores.
    step: int
    # The step vocabulary, deliberately: running | done | error | aborted. The
    # frontend already types those four and needs no fifth for a job.
    state: str = "running"
    progress: float = 0.0
    message: str = ""
    error_message: Optional[str] = None
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None
    # `runs/<id>.ndjson` — one line per bus message that carried text, which is
    # exactly what the LiveLog keeps. Outside `projects/`: a log is about a run,
    # not about the reconstruction, so §14.1 gains no row and no reset, copy or
    # archive has to reason about it.
    log_path: Optional[str] = None
    log_lines: int = 0
    # ── what makes a run survive this process (TODO P7.2) ────────────────────
    # The tool's own stdout, verbatim, in `runs/<id>.tool.log`: a pipe cannot
    # outlive its reader, and a file can be replayed through the step's parser
    # from byte 0 by whoever comes next.
    tool_log_path: Optional[str] = None
    # The child, identified well enough to be adopted an hour later: the pid
    # alone is not enough, because the OS recycles it. `pid_created` is the
    # process creation FILETIME, and it is what makes a wrong adoption
    # impossible rather than unlikely (core/proc.py `process_is`).
    pid: Optional[int] = None
    pid_image: Optional[str] = None
    pid_created: Optional[int] = None
    #: how many commands this run started — a step that started more than one
    #: cannot be replayed safely, so it is not adopted
    spawns: int = 0
    #: how many times this run has been re-attached, for the log line
    adopted: int = 0
    # The settings the run actually resolved, so a re-attached run reports what
    # *this* run did and not what the project's stored layer says now. The panel
    # PATCHes a debounced diff, so the two are usually the same and occasionally
    # are not.
    settings_json: Optional[str] = None
