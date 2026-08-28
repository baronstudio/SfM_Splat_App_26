import asyncio
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.core import cameras, colmap, frames as frame_files, preview, sources
from backend.core.config import app_config
from backend.core.steps.step_analyze import read_json
from backend.db.database import get_session
from backend.models.project import Project

router = APIRouter()

PROJECTS_DIR = Path(__file__).parents[3] / "projects"

FRAME_SUFFIXES = frame_files.FRAME_SUFFIXES


def get_slug_from_id(project_id: str, session: Session) -> str:
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.slug


@router.get("/{project_id}/probe")
async def read_probe(project_id: str, session: Session = Depends(get_session)):
    """ffprobe metadata of the source video, written by the extraction step.

    Returns `{probe: null}` before the first extraction rather than 404 — the
    caller is a UI hint, not a hard dependency.
    """
    slug = get_slug_from_id(project_id, session)
    probe = read_json(PROJECTS_DIR / slug / "analysis" / "probe.json")
    return {"probe": probe}


@router.get("/{project_id}/sources")
async def read_sources(
    project_id: str,
    thumbnails: bool = True,
    session: Session = Depends(get_session),
):
    """What `input/` holds: every source file, probed, with a poster frame.

    Distinct from `/probe`, which reads `analysis/probe.json` — the source of
    the *last* extraction, and absent until there has been one. This one probes
    what is on disk now, which is what step 2 is about to read, and says which
    of several videos that is.

    ffprobe and the thumbnail are subprocesses, so the work goes to a worker
    thread; both results are cached on the file's fingerprint, and a second call
    costs a directory listing.
    """
    slug = get_slug_from_id(project_id, session)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        sources.list_sources,
        PROJECTS_DIR / slug,
        slug,
        app_config.tools.ffmpeg_path,
        thumbnails,
    )


@router.get("/{project_id}/analysis")
async def read_analysis(project_id: str, session: Session = Depends(get_session)):
    """Everything the curation phase produced, for the timeline and the metrics.

    Absent before the first analysis — the UI shows the extraction-only view in
    that case, so this answers 200 with nulls rather than 404.
    """
    slug = get_slug_from_id(project_id, session)
    adir = PROJECTS_DIR / slug / "analysis"
    scores = read_json(adir / "scores.json")
    selection = read_json(adir / "selection.json")
    overrides = read_json(adir / "overrides.json") or {}
    return {
        "scores": scores,
        "selection": selection,
        "overrides": overrides.get("frames", {}),
        "extract": read_json(adir / "extract.json"),
        "analysed": scores is not None,
    }


@router.get("/{project_id}/sfm")
async def read_sfm_result(project_id: str, session: Session = Depends(get_session)):
    """How the last reconstruction went: exit code, coverage, reprojection error.

    Written by step 3 to `sfm/sfm_result.json`. `sfm auto` grades its own result
    in the exit code - 0 sound, 2 nothing reconstructed, 3 partial (under half
    the images registered, or over 2 px mean reprojection) - and exit 3 warns
    without failing the pipeline (CLAUDE.md §7.1). That verdict has to outlive
    the scrollback that announced it, which is what this file and this route are
    for.

    Answers 200 with null before the first run - the caller is a UI panel, not a
    dependency.
    """
    slug = get_slug_from_id(project_id, session)
    report = read_json(PROJECTS_DIR / slug / "sfm" / "sfm_result.json")
    return {"sfm": report}


@router.get("/{project_id}/train")
async def read_train_result(project_id: str, session: Session = Depends(get_session)):
    """How the last training run went: steps, splat count, the final metrics.

    Written by step 4 to `train/train_result.json`, for the same reason step 3
    writes `sfm_result.json`: the trainer prints its numbers on a bar line every
    100 steps and the LiveLog keeps 500 lines, so a 30 000-iteration run's own
    final psnr is gone from the scrollback long before anybody asks what it was.

    `dataset` comes with it, because the panel has to say what the run will read
    before it reads it - the same contract step 2's source panel and step 3's
    input strip keep. `depths` and `normals` are counted inside `sfm/`, which is
    what `--data` points at, so their presence is what decides whether step 4
    sends `--load-depths` / `--load-normals` at all (CLAUDE.md §7.5).

    Answers 200 with null before the first run - the caller is a UI panel, not a
    dependency.
    """
    slug = get_slug_from_id(project_id, session)
    project_path = PROJECTS_DIR / slug
    report = read_json(project_path / "train" / "train_result.json")

    sfm_dir = project_path / "sfm"

    def _count(name: str) -> int:
        target = sfm_dir / name
        return sum(1 for p in target.iterdir() if p.is_file()) if target.is_dir() else 0

    return {
        "train": report,
        "dataset": {
            "has_model": colmap.find_model(sfm_dir) is not None,
            "images": frame_files.count_frames(project_path / "frames"),
            "depths": _count("depths"),
            "normals": _count("normals"),
        },
    }


@router.get("/{project_id}/masks")
async def read_masks(project_id: str, session: Session = Depends(get_session)):
    """What `masks/` holds, read off the folder rather than off a log line.

    The mask run says how it went on the log bus, but a log line is gone on the
    next page load and the durable answer is the folder the later tools read.
    `masks/` is a sibling of `frames/`, which is exactly what `sfm auto` adopts
    by itself and what `train --mask-dir` defaults to (CLAUDE.md §5.2), so there
    is one folder to ask about rather than a dataset to locate first.

    No dimension check here, unlike the predecessor's: spirula *resizes* a mask
    that does not match its image rather than refusing it (§6.7), so a mismatch
    is not the failure it was in `3DGS_App_26` and counting it would be
    reporting a problem the tool does not have.

    200 with `state: "none"` when there are no masks - the caller is a UI panel.
    """
    slug = get_slug_from_id(project_id, session)
    project_path = PROJECTS_DIR / slug

    masks = frame_files.list_mask_images(frame_files.masks_dir(project_path))
    frames = frame_files.list_frames(project_path / "frames")
    if not masks:
        return {"masks": {"masks": 0, "frames": len(frames), "matched": 0,
                          "state": "none", "note": "No masks yet."}}

    frame_stems = {p.stem for p in frames}
    matched = sum(1 for m in masks if m.stem in frame_stems)
    return {"masks": {
        "masks": len(masks),
        "frames": len(frames),
        "matched": matched,
        "state": "ready" if matched else "unmatched",
        "note": (
            f"{matched}/{len(masks)} masks pair with a frame by basename."
            if matched else
            "No mask basename matches a frame - the tools pair them by name."
        ),
    }}


def _verdict_index(slug: str) -> tuple[dict, dict, dict]:
    """(per-frame measurements, effective verdicts, overrides) — all keyed by filename."""
    adir = PROJECTS_DIR / slug / "analysis"

    scores = read_json(adir / "scores.json") or {}
    measurements = {f["filename"]: f for f in scores.get("frames", [])}

    selection = read_json(adir / "selection.json") or {}
    verdicts: dict[str, dict] = {}
    for name in selection.get("kept", []):
        verdicts[name] = {"verdict": "kept", "reason": None}
    for entry in selection.get("rejected", []):
        verdicts[entry["frame"]] = {"verdict": "rejected", "reason": entry.get("reason")}
    for entry in selection.get("warnings", []):
        if entry["frame"] in verdicts:
            verdicts[entry["frame"]]["warning"] = entry.get("reason")

    overrides = (read_json(adir / "overrides.json") or {}).get("frames", {})
    return measurements, verdicts, overrides


@router.get("/{project_id}/frames")
async def list_frames(project_id: str, session: Session = Depends(get_session)):
    """Frame list carrying the curation verdicts.

    The verdicts come from `analysis/selection.json` and the measurements from
    `analysis/scores.json`. Before the first analysis every frame reports
    `verdict: null` — an unanalysed set has no verdict to give, and guessing one
    from the JPEG file size (as this route used to) is worse than saying so.
    """
    slug = get_slug_from_id(project_id, session)
    frames_dir = PROJECTS_DIR / slug / "frames"

    empty = {"frames": [], "total": 0, "kept_count": 0, "rejected_count": 0,
             "warning_count": 0, "analysed": False, "summary": None}
    if not frames_dir.exists():
        return empty

    files = frame_files.list_frames(frames_dir)
    if not files:
        return empty

    measurements, verdicts, overrides = _verdict_index(slug)
    selection = read_json(PROJECTS_DIR / slug / "analysis" / "selection.json") or {}
    analysed = bool(verdicts)

    frames = []
    kept_count = rejected_count = warning_count = 0
    for i, f in enumerate(files):
        m = measurements.get(f.name, {})
        v = verdicts.get(f.name, {})
        verdict = v.get("verdict")
        if verdict == "kept":
            kept_count += 1
        elif verdict == "rejected":
            rejected_count += 1
        if v.get("warning"):
            warning_count += 1

        frames.append({
            "filename": f.name,
            "path": (Path(slug) / "frames" / f.name).as_posix(),
            "size_bytes": f.stat().st_size,
            "url": f"/static/{slug}/frames/{f.name}",
            "index": m.get("index", i),
            "sequence_id": m.get("sequence_id"),
            "sharpness": m.get("sharpness"),
            "sharpness_median": m.get("sharpness_median"),
            "displacement_pct": m.get("displacement_pct"),
            "verdict": verdict,
            "reason": v.get("reason"),
            "warning": v.get("warning"),
            "override": overrides.get(f.name),
        })

    return {
        "frames": frames,
        "total": len(frames),
        "kept_count": kept_count,
        "rejected_count": rejected_count,
        "warning_count": warning_count,
        "analysed": analysed,
        "summary": selection.get("summary"),
    }


class DeleteFramesBody(BaseModel):
    filenames: List[str]


@router.delete("/{project_id}/frames")
async def delete_frames(
    project_id: str,
    body: DeleteFramesBody,
    session: Session = Depends(get_session),
):
    slug = get_slug_from_id(project_id, session)
    frames_dir = PROJECTS_DIR / slug / "frames"

    deleted = 0
    for filename in body.filenames:
        target = frames_dir / Path(filename).name  # prevent path traversal
        if target.exists() and target.is_file():
            target.unlink()
            deleted += 1

    remaining = len(list(frames_dir.glob("*"))) if frames_dir.exists() else 0
    return {"deleted": deleted, "remaining": remaining}


@router.get("/{project_id}/export")
async def list_export_files(project_id: str, session: Session = Depends(get_session)):
    slug = get_slug_from_id(project_id, session)
    export_dir = PROJECTS_DIR / slug / "export"

    if not export_dir.exists():
        return {"files": []}

    files = [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "url": f"/static/{slug}/export/{f.name}",
        }
        for f in sorted(export_dir.iterdir(), key=lambda f: f.name)
        if f.is_file()
    ]
    return {"files": files}


# ── 3D viewer ────────────────────────────────────────────────────────────────

def _preview_query(source: str, max_count: int) -> tuple[str, Optional[int]]:
    if source not in preview.SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown source '{source}'. Expected one of "
                   f"{', '.join(preview.SOURCES)}.",
        )
    # 0 means "the whole file" — the viewer's "full quality" level.
    return source, (None if max_count <= 0 else max_count)


@router.get("/{project_id}/preview")
async def preview_status(
    project_id: str,
    source: str = "sfm",
    max_count: int = 1_000_000,
    session: Session = Depends(get_session),
):
    """State of one viewer preview: what the step produced and what is cached.

    Cheap enough to poll — it reads the PLY header, never the body, so it costs
    the same in front of a 1.24 GB splat as in front of a small sparse cloud.
    """
    slug = get_slug_from_id(project_id, session)
    source, max_count = _preview_query(source, max_count)
    project_path = PROJECTS_DIR / slug

    state = preview.status(project_path, slug, source, max_count)
    job = preview.builds.get(slug, source, max_count)
    if job and not job["task"].done():
        state["building"] = True
        state["progress"] = job["progress"]
    elif job and job["error"] and not state.get("ready"):
        state["error"] = job["error"]
    return state


@router.post("/{project_id}/preview")
async def preview_build(
    project_id: str,
    source: str = "sfm",
    max_count: int = 1_000_000,
    session: Session = Depends(get_session),
):
    """Start building a preview and return immediately.

    Converting five million gaussians is seconds, not milliseconds, and the
    caller is a viewer that would rather draw a progress bar than hold a
    request open. Calling this twice for the same level joins the first build.
    """
    slug = get_slug_from_id(project_id, session)
    source, max_count = _preview_query(source, max_count)
    project_path = PROJECTS_DIR / slug

    state = preview.status(project_path, slug, source, max_count)
    if not state.get("available"):
        raise HTTPException(status_code=404, detail=f"No {source} output to preview yet.")
    if state.get("ready"):
        return state

    job = preview.builds.start(project_path, slug, source, max_count)
    return {**state, "building": True, "progress": job["progress"]}


@router.get("/{project_id}/cameras")
async def read_cameras(project_id: str, session: Session = Depends(get_session)):
    """Camera poses of the last alignment, for the step 3 overlay.

    200 with `available: false` before the first alignment — the overlay is a
    layer of the viewer, not a reason to fail the request.
    """
    slug = get_slug_from_id(project_id, session)
    return cameras.read_cameras(PROJECTS_DIR / slug)
