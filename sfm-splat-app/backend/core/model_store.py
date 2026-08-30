"""model_store.py — install, verify and remove the checkpoints of §7.4 / §7.5.

The post-installation problem this closes: `spirula.exe` ships without a single
neural checkpoint (CLAUDE.md §5.1), and the two tools that want one behave
differently and both badly for an app driving them.

* **`sam track --model` takes a file and never fetches.** Until this module the
  mask panel's own hint was *"the .pt / .onnx file you downloaded"* — the user
  had to find a checkpoint on the web, know which of six they wanted, and paste
  an absolute path.
* **`geometry --model` fetches a known id and does it mid-run**, 419.4 MB
  through a `curl` child whose CR-redrawn bar is the one §15.1 defect in this
  tool family, on the machine's first geometry pass and with a WARNING about an
  unaudited licence going past in the log.

**Where the files go is spirula's own model directory, and that is the whole
interop argument.** Measured on a real `geometry` run: it saved
`C:\\Users\\jbbar\\AppData\\Local\\spirula-studio\\models\\moge2-vitb-normal.onnx`,
and the sha256 of that file is byte-identical to the one compiled into
`spirula.exe`. So a checkpoint installed here under the manifest's own name is
one the tool finds by itself with no flag at all — and every route in this app
additionally passes the **absolute path**, which works whether or not the cache
sits where the tool would look.

Four things this does that a browser download into Downloads/ does not:

* **It resumes.** Measured 2026-08-30, HuggingFace's CDN answers
  `206 Partial Content` with `Accept-Ranges: bytes`, so a 1.8 GB fetch that died
  at 90 % continues instead of starting again. The partial is `<name>.part`,
  which is **spirula's own convention** — its aborted `moge2-vitl` fetch left
  exactly that file in the cache — so a part left by either side is resumed by
  either side.
* **It verifies before it installs.** The geometry rows carry the sha256 the
  binary carries; the SAM rows carry none, so the byte count is the check. A
  file that fails is left as `.part` and named, never renamed into place: a
  truncated checkpoint that loads is worse than one that is missing.
* **It never renames over a live file.** The verified `.part` is moved onto the
  final name with `os.replace`, which is atomic on one volume.
* **One download at a time**, refused rather than queued (§2.5). A queue here
  would buy nothing but a state machine.

Pure module: no FastAPI import (§2.4). The one caller-facing async surface is
`downloads`, which mirrors `preview.builds` — start and poll, no bus traffic.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import httpx

from backend.core.config import app_config
from backend.core.models_catalog import (
    CATALOGUE,
    LICENCES,
    ExtraFile,
    ModelSpec,
)
from backend.core.models_catalog import get as get_spec

# The suffix spirula itself writes while fetching — seen in its own cache after
# an aborted `moge2-vitl` download. Sharing it is what lets a part written by
# either side be finished by the other.
PART_SUFFIX = ".part"

_CHUNK = 1 << 20  # 1 MiB: big enough that the cancel check is free, small
                  # enough that cancelling is felt immediately.

# Generous, and deliberately not a total: a 2.8 GB checkpoint on a slow link is
# not a hung one. `read` is the gap between chunks, which is what actually
# distinguishes a stall from a slow transfer.
_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=60.0, pool=30.0)


# ── Where the cache lives ────────────────────────────────────────────────────

def spirula_default_cache() -> Path:
    """The directory spirula fetches into when nothing says otherwise.

    Measured from a real run's `[moge] saved …` line on Windows. There is **no
    environment variable that moves it** — the binary's own list is
    `SS_LANG, SS_VK_DEVICE, SS_NO_AUTO_FETCH, SS_NN_LOG…` and none of them names
    a model directory — so this is where the tool's automatic fetch will always
    land, whatever `spirula_model_cache` is set to. Which is exactly why every
    step in this app passes the checkpoint as an absolute path instead of
    trusting the lookup.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        return Path(base) / "spirula-studio" / "models"
    return Path.home() / ".local" / "share" / "spirula-studio" / "models"


def cache_dir() -> Path:
    """The directory this app installs into.

    `config.json`'s `spirula_model_cache` when it is set, else spirula's own.
    The default is the tool's directory rather than one under `sfm-splat-app/`
    on purpose: it is the one place where a file installed by this panel and a
    file fetched by the tool are the same file.
    """
    configured = (app_config.tools.spirula_model_cache or "").strip()
    if configured:
        return Path(configured).expanduser()
    return spirula_default_cache()


# ── Reading what is on disk ──────────────────────────────────────────────────

def sha256_of(path: Path, progress_cb: Optional[Callable[[int], None]] = None) -> str:
    """Stream a file's sha256. 419 MB measured at 0.45 s, so this is free."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
            if progress_cb:
                progress_cb(len(block))
    return digest.hexdigest()


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _file_state(directory: Path, filename: str, expected: int) -> dict[str, Any]:
    """What one file of a checkpoint is: installed, half-there or absent.

    The state is decided by **length alone**, not by hashing: a GET on this
    panel must not re-read 2.8 GB, and a download already verified its hash
    before the file got this name. `POST /verify` is the explicit re-check.
    """
    final = directory / filename
    part = directory / (filename + PART_SUFFIX)
    on_disk, part_size = _size(final), _size(part)

    if final.is_file():
        state = "ready" if on_disk == expected else "damaged"
    elif part.is_file():
        state = "partial"
    else:
        state = "missing"

    return {
        "filename": filename,
        "path": str(final),
        "state": state,
        "bytes": on_disk,
        "part_bytes": part_size,
        "expected_bytes": expected,
    }


def model_status(spec: ModelSpec, directory: Optional[Path] = None) -> dict[str, Any]:
    """One catalogue row plus what is on disk for it.

    A checkpoint is `ready` only when **every** file it needs is — which is the
    whole reason `metric3d-vit-giant2` has an `extras` list rather than a
    footnote. A 1.4 GB graph without its 1.36 GB of external data is a file that
    exists and cannot be loaded.
    """
    directory = directory or cache_dir()
    files = [_file_state(directory, spec.filename, spec.size_bytes)]
    files += [
        _file_state(directory, extra.filename, extra.size_bytes)
        for extra in spec.extras
    ]

    states = {f["state"] for f in files}
    if states == {"ready"}:
        state = "ready"
    elif "damaged" in states:
        state = "damaged"
    elif states == {"missing"}:
        state = "missing"
    else:
        # Some there, some not: the giant2 graph downloaded and its data not, or
        # a resumable part. Both are "unfinished", and both are fixed by the
        # same button.
        state = "partial"

    job = downloads.get(spec.id)
    if job and job["state"] in ("downloading", "verifying"):
        state = job["state"]

    return {
        **spec.model_dump(),
        "total_bytes": spec.total_bytes,
        "state": state,
        "files": files,
        "installed_bytes": sum(f["bytes"] for f in files),
        "path": str(directory / spec.filename),
        "job": _public_job(job) if job else None,
    }


def _disk_free(directory: Path) -> Optional[int]:
    probe = directory
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def overview() -> dict[str, Any]:
    """Everything the checkpoints panel draws, in one GET."""
    directory = cache_dir()
    models = [model_status(spec, directory) for spec in CATALOGUE]

    # Every file in the cache, so a checkpoint installed by spirula's GUI or
    # dropped in by hand is still counted against the disk this reports.
    cache_bytes = 0
    stray: list[dict[str, Any]] = []
    # A catalogue row owns both its final name and its part, or spirula's own
    # aborted `moge2-vitl-normal.onnx.part` would be reported as a stranger by
    # the very panel offering to resume it.
    known = {f["filename"] for m in models for f in m["files"]}
    known |= {name + PART_SUFFIX for name in set(known)}
    if directory.is_dir():
        for entry in sorted(directory.iterdir()):
            if not entry.is_file():
                continue
            size = _size(entry)
            cache_bytes += size
            if entry.name not in known:
                stray.append({"filename": entry.name, "bytes": size})

    return {
        "cache_dir": str(directory),
        "cache_dir_exists": directory.is_dir(),
        "spirula_default_cache": str(spirula_default_cache()),
        "is_spirula_default": directory == spirula_default_cache(),
        "cache_bytes": cache_bytes,
        "disk_free_bytes": _disk_free(directory),
        "licences": {k: v.model_dump() for k, v in LICENCES.items()},
        "models": models,
        # Files sitting in the cache that no catalogue row claims — a checkpoint
        # from the tool's own GUI, or an id this build knows and this app does
        # not. Listed rather than hidden: the panel reports the size of the
        # directory it is showing, so it has to say what is making it up.
        "unmanaged": stray,
        "download": _public_job(downloads.active()),
    }


# ── Installing ───────────────────────────────────────────────────────────────

class DownloadCancelled(RuntimeError):
    """Stopped by the user. The part stays on disk and is resumable."""


def _fetch_one(
    target: ExtraFile | ModelSpec,
    directory: Path,
    cancel: threading.Event,
    on_bytes: Callable[[int, int], None],
) -> Path:
    """Fetch one file into `directory`, resuming a part if there is one.

    Returns the installed path. Raises `DownloadCancelled`, or `RuntimeError`
    naming what did not match if the finished file fails its check.
    """
    final = directory / target.filename
    part = directory / (target.filename + PART_SUFFIX)
    expected = target.size_bytes

    if final.is_file() and _size(final) == expected:
        on_bytes(expected, expected)
        return final

    digest = hashlib.sha256()
    have = 0
    if part.is_file():
        have = _size(part)
        if have > expected:
            # Longer than the file it claims to be: not a prefix of anything.
            part.unlink()
            have = 0
        elif have:
            # Fold the bytes already there into the hash before asking for the
            # rest, so the digest still covers the whole file.
            with part.open("rb") as handle:
                for block in iter(lambda: handle.read(_CHUNK), b""):
                    digest.update(block)
                    if cancel.is_set():
                        raise DownloadCancelled()

    headers = {"Range": f"bytes={have}-"} if have else {}
    directory.mkdir(parents=True, exist_ok=True)

    with httpx.Client(follow_redirects=True, timeout=_TIMEOUT, trust_env=True) as client:
        with client.stream("GET", target.url, headers=headers) as response:
            if have and response.status_code == 200:
                # The server ignored the range and is sending the whole file.
                # Start over rather than append a second copy onto the first.
                digest, have = hashlib.sha256(), 0
            elif have and response.status_code != 206:
                response.raise_for_status()
            else:
                response.raise_for_status()

            mode = "ab" if have else "wb"
            on_bytes(have, expected)
            with part.open(mode) as handle:
                for block in response.iter_bytes(_CHUNK):
                    if cancel.is_set():
                        handle.flush()
                        raise DownloadCancelled()
                    handle.write(block)
                    digest.update(block)
                    have += len(block)
                    on_bytes(have, expected)

    got = _size(part)
    if got != expected:
        raise RuntimeError(
            f"{target.filename}: downloaded {got:,} bytes, expected "
            f"{expected:,}. The part is kept — run the download again to resume "
            "it, or delete it to start over."
        )

    wanted = getattr(target, "sha256", None)
    if wanted:
        actual = digest.hexdigest()
        if actual != wanted:
            raise RuntimeError(
                f"{target.filename}: sha256 {actual} does not match the "
                f"{wanted} compiled into spirula.exe. The file is NOT installed "
                "and is kept as a .part; delete it and download again."
            )

    # Verified, so it may take its real name. `os.replace` is atomic on one
    # volume, which is what stops a reader ever seeing a half file under the
    # name the tool looks up.
    os.replace(part, final)
    return final


def _targets(spec: ModelSpec) -> Iterable[ExtraFile | ModelSpec]:
    return [spec, *spec.extras]


def install(
    model_id: str,
    cancel: threading.Event,
    on_progress: Callable[[int, int], None],
) -> dict[str, Any]:
    """Fetch every file one checkpoint needs. Blocking — call it in a thread."""
    spec = get_spec(model_id)
    directory = cache_dir()
    total = spec.total_bytes
    done_before = 0
    paths: list[str] = []

    for target in _targets(spec):
        def on_bytes(current: int, _expected: int, _base: int = done_before) -> None:
            on_progress(_base + current, total)

        paths.append(str(_fetch_one(target, directory, cancel, on_bytes)))
        done_before += target.size_bytes
        on_progress(done_before, total)

    return {"model_id": model_id, "paths": paths, "bytes": total}


class _Downloads:
    """The one in-flight install, so a polling panel cannot start it twice.

    Deliberately outside `pipeline_runner` and outside the abort machinery, on
    `preview._Builds`' argument: nothing here is a pipeline step, no project
    owns it, and cancelling leaves a resumable part rather than a half-written
    project directory. One at a time and refused rather than queued (§2.5) —
    two 2 GB fetches over one link finish no sooner for overlapping.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}

    def get(self, model_id: str) -> Optional[dict]:
        return self._jobs.get(model_id)

    def active(self) -> Optional[dict]:
        for job in self._jobs.values():
            if job["state"] in ("downloading", "verifying"):
                return job
        return None

    def start(self, model_id: str) -> dict:
        spec = get_spec(model_id)
        running = self.active()
        if running:
            if running["model_id"] == model_id:
                return running
            raise RuntimeError(
                f"{running['model_id']} is downloading. One checkpoint at a "
                "time — cancel it first, or wait for it to finish."
            )

        cancel = threading.Event()
        job: dict[str, Any] = {
            "model_id": model_id,
            "label": spec.label,
            "state": "downloading",
            "downloaded": 0,
            "total": spec.total_bytes,
            "started_at": time.time(),
            "finished_at": None,
            "error": None,
            "cancel": cancel,
            "task": None,
        }

        def on_progress(downloaded: int, total: int) -> None:
            job["downloaded"], job["total"] = downloaded, total

        async def run() -> dict:
            try:
                result = await asyncio.to_thread(install, model_id, cancel, on_progress)
                job["state"] = "ready"
                return result
            except DownloadCancelled:
                job["state"] = "cancelled"
                job["error"] = (
                    "Cancelled. What was fetched is kept as a .part and the next "
                    "download resumes from it."
                )
                raise
            except Exception as exc:
                job["state"] = "error"
                job["error"] = str(exc)
                raise
            finally:
                job["finished_at"] = time.time()

        task = asyncio.get_running_loop().create_task(run())
        # Nobody awaits this task; without a done-callback a failure would be
        # reported by asyncio at garbage-collection time and nowhere else.
        task.add_done_callback(
            lambda t: t.exception() if not t.cancelled() else None
        )
        job["task"] = task
        self._jobs[model_id] = job
        return job

    def cancel(self, model_id: str) -> bool:
        job = self._jobs.get(model_id)
        if not job or job["state"] not in ("downloading", "verifying"):
            return False
        job["cancel"].set()
        return True


downloads = _Downloads()


def _public_job(job: Optional[dict]) -> Optional[dict[str, Any]]:
    """A job without its Event and its Task, and with the numbers a bar needs."""
    if not job:
        return None
    elapsed = max((job["finished_at"] or time.time()) - job["started_at"], 1e-6)
    downloaded, total = job["downloaded"], job["total"]
    # Bytes a second over the whole job. A resumed download counts only what it
    # actually moved, because `downloaded` starts at the size of the part.
    rate = downloaded / elapsed if job["state"] == "downloading" else None
    return {
        "model_id": job["model_id"],
        "label": job["label"],
        "state": job["state"],
        "downloaded": downloaded,
        "total": total,
        "progress": min(downloaded / total, 1.0) if total else 0.0,
        "elapsed_s": round(elapsed, 1),
        "rate_bps": round(rate) if rate else None,
        "eta_s": round((total - downloaded) / rate) if rate and rate > 0 else None,
        "error": job["error"],
    }


# ── The other three verbs ────────────────────────────────────────────────────

def verify(model_id: str) -> dict[str, Any]:
    """Re-read a checkpoint and say whether it is what it claims to be.

    The panel's state is decided by length, which is free; this is the explicit
    re-check, and it is the only thing here that reads 2.8 GB. It is offered
    because the interesting case is a file this app did not write — one adopted
    from a manual download, or fetched by spirula's own GUI.
    """
    spec = get_spec(model_id)
    directory = cache_dir()
    results = []
    for target in _targets(spec):
        path = directory / target.filename
        if not path.is_file():
            results.append({"filename": target.filename, "ok": False,
                            "reason": "not installed"})
            continue
        size = _size(path)
        if size != target.size_bytes:
            results.append({
                "filename": target.filename, "ok": False,
                "reason": f"{size:,} bytes, expected {target.size_bytes:,}",
            })
            continue
        wanted = getattr(target, "sha256", None)
        if not wanted:
            results.append({
                "filename": target.filename, "ok": True,
                "reason": (f"{size:,} bytes — the length matches. spirula.exe "
                           "carries no sha256 for the SAM checkpoints, so there "
                           "is nothing stronger to check against."),
            })
            continue
        actual = sha256_of(path)
        results.append({
            "filename": target.filename, "ok": actual == wanted,
            "reason": ("sha256 matches spirula.exe's own"
                       if actual == wanted else
                       f"sha256 {actual} != {wanted}"),
        })
    return {"model_id": model_id, "ok": all(r["ok"] for r in results),
            "files": results}


def adopt(model_id: str, source: str) -> dict[str, Any]:
    """Take a file the user downloaded by hand into the cache, under its real name.

    The third door of §6.7's argument, one layer down: the app runs on the
    workstation that holds the file, so a 2 GB checkpoint already on this disk is
    a local copy and never an upload. It is **verified before it is installed**,
    exactly like a fetch — a hand-downloaded file is the one most likely to be
    the wrong one, and the manifest is what can tell.
    """
    spec = get_spec(model_id)
    path = Path(source).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No file at {path}")

    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    size = _size(path)
    if size != spec.size_bytes:
        raise ValueError(
            f"{path.name} is {size:,} bytes; {spec.label} is {spec.size_bytes:,}. "
            "That is a different file — check which checkpoint you downloaded."
        )
    if spec.sha256:
        actual = sha256_of(path)
        if actual != spec.sha256:
            raise ValueError(
                f"{path.name} hashes to {actual}, not the {spec.sha256} "
                "spirula.exe carries for this checkpoint. Not installed."
            )

    final = directory / spec.filename
    if path.resolve() == final.resolve():
        return {"model_id": model_id, "path": str(final), "action": "already there"}

    # Hard-link where the volume allows it, on `step_conform._link_or_copy`'s
    # argument: a 2.8 GB checkpoint does not need to exist twice on one disk.
    staging = directory / (spec.filename + PART_SUFFIX)
    if staging.exists():
        staging.unlink()
    try:
        os.link(path, staging)
        action = "linked"
    except OSError:
        shutil.copy2(path, staging)
        action = "copied"
    os.replace(staging, final)

    return {"model_id": model_id, "path": str(final), "action": action}


def remove(model_id: str, include_part: bool = True) -> dict[str, Any]:
    """Delete a checkpoint's files. Nothing else in the app reads them."""
    spec = get_spec(model_id)
    directory = cache_dir()
    removed, freed = [], 0
    for target in _targets(spec):
        for candidate in (
            directory / target.filename,
            *( [directory / (target.filename + PART_SUFFIX)] if include_part else [] ),
        ):
            if candidate.is_file():
                freed += _size(candidate)
                candidate.unlink()
                removed.append(candidate.name)
    return {"model_id": model_id, "removed": removed, "freed_bytes": freed}
