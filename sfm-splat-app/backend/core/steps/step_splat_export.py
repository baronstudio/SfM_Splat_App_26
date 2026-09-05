"""step_splat_export.py — the deliverable export of step 4's splat (§7.6c).

Not a wizard step: the fourth re-runnable pass attached to one, after
`spirula sam` on step 3, `spirula geometry` on step 4 and the volume crop on
step 4. It never re-trains, it never touches what the trainer wrote, and — the
part that separates it from the crop — **nothing in the pipeline ever reads what
it writes**.

That is the whole of its design and it is worth being explicit about, because
the crop sitting right next to it works the other way round:

* `train/crop/splat.ply` is **pipeline data**. `resolve_splat` hands it to step 5
  in place of the trained splat, and the run names which one it got.
* `train/export/<name>` is **terminal**. It is smaller than its source on
  purpose, by dropping spherical harmonics the mesher's colour pass would want
  and by quantising into formats no mesher reads at all. So it lives in a
  directory of its own, it is never called `splat.ply`, and neither `find_splat`
  (which globs `step-*.ckpt/splat.ply`) nor `find_crop` (which looks only in
  `train/crop/`) can see it.

**It exports the crop when there is one.** The source is `resolve_splat`, so an
export made after a crop carries the crop, and the log line says which file it
read — the same rule step 5 follows for the same reason.

`train/export/` is inside `train/`, so a step 4 reset takes it (§14.1). That is
right: the file is a copy of a splat that reset has just deleted. What survives
is the *plan* in `settings_json`, so a re-train re-exports with one click.

Two families of format, and the second one is a second process:

* `ply` and `splat` are `core/splat_export.py`, numpy over a memory map, with
  no dependency at all.
* `sog`, `spz` and `compressed-ply` are `@playcanvas/splat-transform`, and they
  are reached by writing the reduced PLY into a `.part` first and converting it.
  So the reduction knobs behave identically in all five formats, and the
  external tool only ever re-encodes a file we just wrote.

The native half is pure Python, so — like the crop — its abort is only the
cooperative flag. The external half is a subprocess, so §2.6's tree kill applies
to it and both are translated into the same `ProcessAborted` every other step
reports abort with.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from backend.core import ply, splat_export, viewpoint as viewpoint_mod
from backend.core.proc import ProcessAborted, iter_lines, release, spawn, was_killed
from backend.core.steps import splat_transform
from backend.core.steps.step_crop import resolve_splat, volumes_from_settings

EXPORT_DIR_NAME = "export"
EXPORT_RESULT_NAME = "export_result.json"

#: `[####################] 2.627s` — the k-means bar `splat-transform` redraws
#: with a bare CR while it clusters SH coefficients for a `.sog`. It is the only
#: line of the tool worth dropping, and `proc.iter_lines` hands us the fragments
#: one at a time because it splits on CR as well as LF (§15.1).
_BAR_LINE = re.compile(r"\[[#\s.]*\]")


def export_dir(train_dir: Path) -> Path:
    return train_dir / EXPORT_DIR_NAME


def read_result(train_dir: Path) -> Optional[dict]:
    """`export_result.json` of the last run, or None."""
    path = export_dir(train_dir) / EXPORT_RESULT_NAME
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_exports(train_dir: Path) -> list[Path]:
    """Every file in `train/export/` bar the report, newest first."""
    target = export_dir(train_dir)
    if not target.is_dir():
        return []
    files = [
        f for f in target.iterdir()
        if f.is_file() and f.name != EXPORT_RESULT_NAME
        and not f.name.endswith(".part")
        # The two staging names a run writes under before it knows the number
        # its output gets. They live and die inside one run, but a hard kill
        # can leave one behind and the drawer is a list of deliverables.
        and not f.name.startswith(".")
    ]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def clear_exports(train_dir: Path) -> int:
    """Remove every exported file. Returns how many went.

    The panel's one destructive button, and now the *only* thing in the app
    that removes an export: `train/export/` is a drawer of deliverables that
    accumulates one numbered file per run, and nothing else ever prunes it,
    because every file in it is something somebody asked for on purpose.
    """
    removed = 0
    for path in list_exports(train_dir):
        try:
            path.unlink()
            removed += 1
        except OSError:
            pass
    return removed


#: Anything a filesystem, a URL or somebody else's downloads folder would
#: rather not carry. Runs of them collapse into one underscore.
_UNSAFE = re.compile(r"[^A-Za-z0-9]+")

#: `<slug>_<count>_<number>.<ext>` — the name this module writes, read back.
#: `.+` is greedy on purpose: a slug ending in digits (`..._006`) splits at the
#: *last* two numeric groups, which is where the count and the number are.
_NUMBERED = re.compile(r"^(?P<slug>.+)_(?P<count>\d+)_(?P<number>\d+)\.")


def conform(name: str) -> str:
    """A project name reduced to what every filesystem carries the same way.

    Project slugs are already lowercase and underscored, so this is a no-op on
    all of them — it is here for the one that is not, because the export is the
    only file in this app that leaves the machine under a name a person reads.
    """
    ascii_only = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    return _UNSAFE.sub("_", ascii_only).strip("_").lower() or "project"


def next_number(target_dir: Path, slug: str) -> int:
    """One past the highest number this project's drawer already holds.

    Counted across the whole drawer rather than per format or per count, so the
    numbers read as what they are — this project's exports, in the order they
    were made. Names written before this rule (the old settings-suffixed ones)
    match nothing and are simply left alone.
    """
    highest = 0
    if target_dir.is_dir():
        for f in target_dir.iterdir():
            match = _NUMBERED.match(f.name)
            if match and match.group("slug") == slug:
                highest = max(highest, int(match.group("number")))
    return highest + 1


def output_name(slug: str, count: int, number: int, fmt: str) -> str:
    """`<project>_<gaussians>_<number>.<ext>`.

    Never `splat.ply`: that is the name `find_splat` and `find_export_splat`
    look for, and an export dropped under it would be picked up by step 5 as
    the splat to mesh.

    **The number is what makes an export non-destructive.** The name used to be
    built out of the settings, on the theory that two exports could only collide
    if they really were the same export — and that was wrong in the case that
    matters: the settings say nothing about *which* splat was read, so re-cropping
    or re-training and exporting again wrote the new deliverable over the old one
    at the same path, silently. The count now says what is in the file and the
    number says nothing at all except that it is a different file.
    """
    return f"{slug}_{count}_{number}{splat_export.SUFFIXES[fmt]}"


def next_free(target_dir: Path, slug: str, count: int, fmt: str) -> Path:
    """The path this export gets. Never one that exists."""
    number = next_number(target_dir, slug)
    while True:
        path = target_dir / output_name(slug, count, number, fmt)
        if not path.exists():
            return path
        number += 1


def _describe(plan: splat_export.ExportPlan, source_degree: Optional[int]) -> str:
    bits = [plan.format]
    if plan.sh_degree is None:
        bits.append(f"SH {source_degree if source_degree is not None else '?'} (kept)")
    else:
        bits.append(f"SH {source_degree} → {plan.sh_degree}")
    if plan.opacity_min > 0:
        bits.append(f"alpha ≥ {plan.opacity_min:g}")
    if plan.max_count > 0:
        bits.append(f"≤ {plan.max_count:,} splats by {plan.selection}")
    return ", ".join(bits)


def _check_abort(should_abort) -> None:
    if should_abort and should_abort():
        raise ProcessAborted("export aborted by user")


# ── The native passes ───────────────────────────────────────────────────────

async def _write(
    src: Path, dst: Path, plan: splat_export.ExportPlan,
    broadcast_fn, should_abort, floor: float, ceiling: float,
    comments: tuple[str, ...] = (),
) -> dict:
    """`splat_export.write_reduced`, one chunk per executor hop.

    The synchronous version exists and this is deliberately not it: two
    sequential passes over a 178 MB memory map hold the event loop for as long
    as they take, which stops the WebSocket, the bar and — the part that matters
    — the abort route. Chunking through the executor is what `step_analyze` and
    `step_crop` do for the same reason, and the empty message on each tick is
    the trick step 5 uses for its camera counter: `websocket.broadcast` omits it
    from the payload, so the bar moves and the LiveLog stays readable.
    """
    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    source = splat_export.Source(src)
    span = ceiling - floor

    try:
        total = source.total
        mask = np.ones(total, dtype=bool)
        scores: Optional[np.ndarray] = None
        wants_scores = (
            plan.max_count > 0 and plan.selection == splat_export.SELECT_IMPORTANCE
        )

        if plan.reduces_rows:
            if wants_scores:
                scores = np.empty(total, dtype=np.float64)
            for lo in range(0, total, splat_export.CHUNK):
                _check_abort(should_abort)
                hi = min(lo + splat_export.CHUNK, total)
                if wants_scores:
                    scores[lo:hi] = await loop.run_in_executor(
                        None, source.score_chunk, lo, hi,
                    )
                if plan.opacity_min > 0.0:
                    alpha = await loop.run_in_executor(
                        None, source.alpha_chunk, lo, hi,
                    )
                    mask[lo:hi] = alpha >= plan.opacity_min
                await broadcast_fn(
                    "splat_export", "INFO", "",
                    progress=floor + span * 0.5 * hi / total,
                )
            splat_export.check_kept(int(mask.sum()))
            mask = await loop.run_in_executor(
                None, splat_export.apply_target_count, scores, mask, plan,
            )

        kept = splat_export.check_kept(int(mask.sum()))
        out_dtype, props = splat_export.output_dtype(source.header, plan)
        selective = None if kept == total else mask
        tmp = splat_export.begin_write(dst)

        try:
            with open(tmp, "wb") as out:
                if plan.format != splat_export.FORMAT_SPLAT:
                    out.write(
                        source.copy_header(kept, comments) if out_dtype is None
                        else splat_export.header_text(kept, props, comments)
                    )
                for lo in range(0, total, splat_export.CHUNK):
                    _check_abort(should_abort)
                    hi = min(lo + splat_export.CHUNK, total)
                    if plan.format == splat_export.FORMAT_SPLAT:
                        chunk = await loop.run_in_executor(
                            None, source.splat_chunk, selective, lo, hi,
                        )
                    else:
                        chunk = await loop.run_in_executor(
                            None, source.pack_chunk, selective, lo, hi,
                            out_dtype, props,
                        )
                    out.write(chunk)
                    await broadcast_fn(
                        "splat_export", "INFO", "",
                        progress=floor + span * (0.5 + 0.5 * hi / total),
                    )
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        source.close()

    ply.finalise(tmp, dst)
    return splat_export.result_of(source.total, kept, started, dst, plan)


# ── The external pass ───────────────────────────────────────────────────────

async def _convert(
    src: Path, dst: Path, project_path: Path, broadcast_fn, should_abort,
    floor: float, ceiling: float,
) -> None:
    """Re-encode `src` into `dst` with `splat-transform`.

    Its channel is a handful of `▸ [n/2]` and `· out.sog (1.1MB)` lines plus a
    **CR-redrawn k-means bar** while it clusters the SH coefficients for a SOG —
    the same shape as `spirula geometry`'s `curl` download (§7.5), and handled
    the same way: the bar fragments are dropped and the bar rides the stretch
    between `floor` and `ceiling` on its own.

    Exit code *is* trusted here, unlike `spirula geometry`: measured, the tool
    exits 1 and names the reason on a missing input, and it writes nothing on
    the way to a failure.
    """
    command = splat_transform.convert_command(src, dst)
    await broadcast_fn(
        "splat_export", "INFO", f"[export] {' '.join(command)}", progress=floor,
    )

    loop = asyncio.get_running_loop()
    proc = spawn(command, project_path)
    try:
        async for line in iter_lines(proc, loop):
            _check_abort(should_abort)
            if _BAR_LINE.search(line):
                # The k-means bar, hundreds of fragments per SOG. It rides the
                # stretch it was given with an empty message, which
                # `websocket.broadcast` omits from the payload — so the bar
                # moves and the 500-line LiveLog stays readable.
                await broadcast_fn(
                    "splat_export", "INFO", "", progress=(floor + ceiling) / 2,
                )
                continue
            await broadcast_fn("splat_export", "INFO", f"[export] {line}")
        code = await loop.run_in_executor(None, proc.wait)
    finally:
        killed = release(project_path, proc) or was_killed(proc)

    if killed:
        raise ProcessAborted("splat-transform aborted by user")
    if code != 0:
        raise RuntimeError(
            f"splat-transform exited {code} without writing {dst.name}. "
            f"The reduced PLY it was given is still in {src.parent}."
        )
    if not dst.exists():
        raise RuntimeError(
            f"splat-transform exited 0 but wrote no {dst.name}. "
            f"The format is chosen by the output extension, so an unknown one "
            f"is how this happens."
        )


# ── The pass ────────────────────────────────────────────────────────────────

async def run_splat_export(
    project_path: Path, broadcast_fn, settings: dict, should_abort=None,
) -> dict:
    """Write one deliverable copy of step 4's splat into `train/export/`."""
    train_dir = project_path / "train"
    plan = splat_export.plan_from_settings(settings)

    source, cropped = resolve_splat(train_dir)
    if source is None:
        raise FileNotFoundError(
            f"No splat to export: nothing matching step-*.ckpt/splat.ply under "
            f"{train_dir}. Run step 4 first."
        )

    external = plan.format in splat_export.EXTERNAL_FORMATS
    if external:
        # Before a byte is written, and before the native pass spends its
        # seconds: §14.1's rule, one feature along. A missing tool must not cost
        # the work that would be thrown away with it.
        splat_transform.resolve_path()

    header = ply.read_header(source)
    source_degree = splat_export.source_sh_degree(header)

    # The name carries the gaussian count of the file it names, and that count
    # is only known once the reduction has run — an opacity floor drops however
    # many gaussians sit under it. So both routes write under a staging name
    # first and the finished file is renamed onto its number, which is also
    # what keeps the number the *last* thing decided: a run that fails or is
    # aborted takes no number with it and leaves no gap in the drawer.
    exports = export_dir(train_dir)
    exports.mkdir(parents=True, exist_ok=True)
    slug = conform(project_path.name)

    # The viewpoint the user parked the viewer at and saved (§7.6d). It rides
    # *into* the file when the file can carry it — a native PLY, as `comment`
    # lines every reader skips — and *beside* it when it cannot, which is every
    # other format here: `.splat` is a headerless stream of 32-byte records, and
    # the three compressed formats are written by a tool that would drop
    # whatever we put in the PLY we hand it. A stored viewpoint that will not
    # parse is said out loud rather than silently dropped: it is the one thing
    # in this export the user placed by hand.
    try:
        view = viewpoint_mod.from_settings(settings)
    except viewpoint_mod.ViewpointError as exc:
        view = None
        await broadcast_fn(
            "splat_export", "WARNING",
            f"[export] the saved viewpoint is unusable and is left out: {exc}",
        )
    embed = view is not None and plan.format == splat_export.FORMAT_PLY
    comments = tuple(view.ply_comments()) if (view is not None and embed) else ()

    # The crop is a pass of its own and this one does not run it. So an export
    # taken while volumes sit in the viewer un-applied is the *full* splat, and
    # that is the one confusion this feature can plausibly cause: the user is
    # looking at a cut scene — the live shader hides the excluded gaussians
    # (§7.6b) — and downloads something that was never cut. Nothing is guessed
    # on their behalf and nothing is refused; the run says it out loud, where it
    # outlives the panel that could also have said it.
    if not cropped:
        volumes = len(volumes_from_settings(settings))
        if volumes:
            await broadcast_fn(
                "splat_export", "WARNING",
                f"[export] {volumes} crop volume(s) are placed but no crop has "
                f"been applied, so this export is the whole trained splat. "
                f"Apply the crop first if the cut is meant to be in it.",
            )

    await broadcast_fn(
        "splat_export", "INFO",
        f"[export] {source.parent.name}/{source.name} "
        f"({'the crop' if cropped else 'the trained splat'}, "
        f"{header.count:,} gaussians, {source.stat().st_size / 1e6:.1f} MB) "
        f"→ {_describe(plan, source_degree)}",
        progress=0.02,
    )

    started = time.perf_counter()
    if external:
        # The intermediate is a real PLY under a `.ply` name, because the
        # external tool picks its *reader* by extension too. It goes in a
        # `finally`, so a failed conversion does not leave a 178 MB file in a
        # directory the user is about to download from.
        staged = exports / ".export.staged.ply"
        try:
            native = await _write(
                source, staged,
                splat_export.ExportPlan(**{**plan.as_dict(),
                                           "format": splat_export.FORMAT_PLY}),
                broadcast_fn, should_abort, floor=0.02, ceiling=0.70,
            )
            # The native pass has counted the rows, so the converted file can be
            # written straight onto its final name — there is nothing to rename.
            target = next_free(exports, slug, native["count"], plan.format)
            await _convert(
                staged, target, project_path, broadcast_fn, should_abort,
                floor=0.72, ceiling=0.99,
            )
        finally:
            staged.unlink(missing_ok=True)
        result = {**native, "bytes": target.stat().st_size,
                  "intermediate_bytes": native["bytes"],
                  "plan": plan.as_dict(),
                  "seconds": round(time.perf_counter() - started, 2)}
    else:
        pending = exports / f".export.pending{splat_export.SUFFIXES[plan.format]}"
        try:
            result = await _write(
                source, pending, plan, broadcast_fn, should_abort,
                floor=0.02, ceiling=0.99, comments=comments,
            )
            target = next_free(exports, slug, result["count"], plan.format)
            ply.finalise(pending, target)
        except BaseException:
            pending.unlink(missing_ok=True)
            raise

    sidecar: Optional[Path] = None
    if view is not None and not embed:
        sidecar = viewpoint_mod.write_sidecar(view, target)
    if view is not None:
        await broadcast_fn(
            "splat_export", "INFO",
            f"[export] viewpoint {viewpoint_mod.describe(view)} — "
            + (f"in {target.name}'s PLY header"
               if embed else f"beside it, in {sidecar.name}"),
        )

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source.relative_to(project_path)),
        "source_cropped": cropped,
        "source_sh_degree": source_degree,
        "output": str(target.relative_to(project_path)),
        "filename": target.name,
        "splat_transform_version": (
            splat_transform.read_version() if external else None
        ),
        "viewpoint": view.as_dict() if view is not None else None,
        "viewpoint_in_header": embed,
        "viewpoint_sidecar": sidecar.name if sidecar is not None else None,
        **result,
    }
    export_dir(train_dir).mkdir(parents=True, exist_ok=True)
    (export_dir(train_dir) / EXPORT_RESULT_NAME).write_text(
        json.dumps(report, indent=2), encoding="utf-8",
    )

    ratio = source.stat().st_size / max(result["bytes"], 1)
    dropped = (
        f", {result['removed']:,} gaussians dropped" if result["removed"] else ""
    )
    await broadcast_fn(
        "splat_export", "SUCCESS",
        f"[export] {target.name} — {result['count']:,} gaussians, "
        f"{result['bytes'] / 1e6:.1f} MB ({ratio:.1f}x smaller){dropped}, "
        f"in {result['seconds']}s. Nothing in the pipeline reads it; it is "
        f"there to be downloaded.",
        progress=1.0,
    )
    return report
