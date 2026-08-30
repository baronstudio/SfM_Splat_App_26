import asyncio
import json
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from backend.core import (
    cameras, colmap, crop, frames as frame_files, ply, preview, sources,
    splat_export, viewpoint,
)
from backend.core.config import app_config
from backend.core.steps import splat_transform, step_crop, step_splat_export
from backend.core.steps.step_analyze import read_json
from backend.core.steps.step_export import find_export_splat
from backend.core.steps.step_mesh import find_outputs
from backend.core.steps.step_train import find_splat, splat_count
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


def _canonical_volumes(raw: object) -> Optional[list[dict]]:
    """The volume stack as the crop would read it, rounded for comparison.

    Both sides go through `crop.parse_volumes`, so a stack stored by an older
    build, or edited by hand into `settings_json`, is compared as the cut would
    actually apply it rather than as it happens to be spelled. Returns None when
    it would not parse at all — the panel says so instead of claiming staleness.
    """
    try:
        return [
            {
                "kind": v.kind, "mode": v.mode,
                "center": [round(c, 6) for c in v.center],
                "half": [round(h, 6) for h in v.half],
                "rotation": [round(r, 6) for r in v.rotation],
            }
            for v in crop.parse_volumes(raw)
        ]
    except crop.CropError:
        return None


@router.get("/{project_id}/crop")
async def read_crop(project_id: str, session: Session = Depends(get_session)):
    """What the crop pass last wrote, and whether it still matches the volumes.

    The staleness answer is the point of this route. The volumes live in
    `settings_json` and are edited by dragging a gizmo, which saves on a 300 ms
    debounce like every other panel (§4) — so the moment a box moves, the file
    under `train/crop/` describes a crop nobody asked for any more, and steps 5
    and 6 would read it without knowing. Nothing here corrects that silently:
    the panel is told, and re-running the pass is one click.

    200 with nulls before the first run — the caller is a UI panel.
    """
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project_path = PROJECTS_DIR / project.slug
    train_dir = project_path / "train"

    try:
        stored = json.loads(project.settings_json or "{}")
    except json.JSONDecodeError:
        stored = {}
    section = stored.get("crop") if isinstance(stored, dict) else None
    wanted = _canonical_volumes((section or {}).get("volumes"))

    result = step_crop.read_result(train_dir)
    applied = _canonical_volumes((result or {}).get("volumes"))
    cropped = step_crop.find_crop(train_dir)
    source = find_splat(train_dir)

    return {
        "volumes": (section or {}).get("volumes") or [],
        "valid": wanted is not None,
        "max_volumes": crop.MAX_VOLUMES,
        "source": {
            "available": source is not None,
            "file": source.name if source else None,
            "count": splat_count(source),
        },
        "applied": result,
        "cropped": cropped is not None,
        # A crop the volumes have moved on from. Nothing downstream refuses it —
        # it is a real file describing a real earlier cut — but every reader of
        # it should be able to say so.
        "stale": bool(cropped and wanted is not None and applied != wanted),
    }


@router.get("/{project_id}/export-splat")
async def read_splat_export(project_id: str, session: Session = Depends(get_session)):
    """The export drawer: what it holds, what it would write, and with what.

    Three questions the panel cannot answer on its own, and the third is the
    one this route exists for:

    * **What is in `train/export/`** — a list of deliverables that accumulates,
      one per format and per settings change, with a `/static` URL each. Nothing
      in the pipeline reads them and nothing prunes them, so the panel is the
      only place they are ever seen.
    * **What the next export would read** — `resolve_splat`, so an export made
      after a crop carries the crop, and its SH degree read off the file rather
      than assumed, because that is what decides whether "SH 0" is a reduction
      or a no-op.
    * **Whether the compressed formats can be written at all.** `sog`, `spz` and
      `compressed-ply` need `@playcanvas/splat-transform`, which is optional
      (§7.6c). The panel greys them out and names the install command rather
      than offering a button that fails after the reduction has already run.

    …and a fourth that is really part of the second. **The crop the export will
    honour is the one on disk, not the one in the viewer.** Volumes placed with
    the gizmo and never applied, or applied and then dragged, leave
    `resolve_splat` answering the *trained* splat while the user is looking at a
    cut scene — so `crop` below carries the same `applied` / `stale` pair
    `/crop` does, and the panel says which of the two files is about to be read.

    200 with nulls before the first run — the caller is a UI panel.
    """
    project = session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    slug = project.slug
    project_path = PROJECTS_DIR / slug
    train_dir = project_path / "train"

    source, cropped = step_crop.resolve_splat(train_dir)
    source_info: dict = {"available": source is not None}
    if source is not None:
        try:
            header = ply.read_header(source)
            source_info.update(
                file=source.name,
                cropped=cropped,
                count=header.count,
                bytes=source.stat().st_size,
                sh_degree=splat_export.source_sh_degree(header),
                properties=len(header.props),
            )
        except (ply.PlyError, OSError) as exc:
            source_info["error"] = str(exc)

    # The crop as the *pipeline* sees it, so the panel can tell "cut" from
    # "a box is drawn on the preview". Same two comparisons `/crop` makes,
    # through the same canonicaliser, so the two panels cannot disagree.
    try:
        stored = json.loads(project.settings_json or "{}")
    except json.JSONDecodeError:
        stored = {}
    section = stored.get("crop") if isinstance(stored, dict) else None
    wanted = _canonical_volumes((section or {}).get("volumes"))
    crop_result = step_crop.read_result(train_dir)
    crop_applied = _canonical_volumes((crop_result or {}).get("volumes"))

    # The saved viewpoint (§7.6d). Reported through the same parser the export
    # itself uses, so a stored value the run would refuse is refused here too
    # rather than shown as saved and then quietly dropped from the file.
    view_info: dict = {"saved": False, "valid": True, "error": None,
                       "viewpoint": None}
    try:
        view = viewpoint.from_settings(stored)
        if view is not None:
            view_info.update(saved=True, viewpoint=view.as_dict())
    except viewpoint.ViewpointError as exc:
        view_info.update(saved=True, valid=False, error=str(exc))

    tool = splat_transform.find_splat_transform()
    return {
        "source": source_info,
        # Whether it lands in the header or in a sidecar is decided by the
        # format, so the panel is told which formats can carry it.
        "viewpoint": {
            **view_info,
            "header_formats": [splat_export.FORMAT_PLY],
            "sidecar_suffix": viewpoint.SIDECAR_SUFFIX,
        },
        "crop": {
            # Volumes the user has placed, whether or not they have been cut.
            "volumes": len(wanted) if wanted is not None else 0,
            # A cut file exists, so `resolve_splat` returns it and this export
            # will read it.
            "applied": cropped,
            # It exists and the volumes have moved on since: the export is
            # about to read a real file describing an earlier cut.
            "stale": bool(cropped and wanted is not None and crop_applied != wanted),
        },
        "applied": step_splat_export.read_result(train_dir),
        "files": [
            {
                "filename": f.name,
                "bytes": f.stat().st_size,
                "url": f"/static/{slug}/train/export/{f.name}",
            }
            for f in step_splat_export.list_exports(train_dir)
        ],
        "formats": {
            "native": list(splat_export.NATIVE_FORMATS),
            "external": list(splat_export.EXTERNAL_FORMATS),
        },
        "splat_transform": {
            "available": tool is not None,
            "path": str(tool) if tool else None,
            "install_hint": splat_transform.INSTALL_HINT,
        },
    }


@router.delete("/{project_id}/export-splat")
async def clear_splat_exports(project_id: str, session: Session = Depends(get_session)):
    """Empty `train/export/`.

    The one destructive button on that panel, and it is deliberately all-or-
    nothing: every file in there is a deliverable somebody asked for, so a
    per-file delete would be a file manager and this is a "clear the drawer".
    Nothing downstream can notice — no step reads this directory.
    """
    slug = get_slug_from_id(project_id, session)
    removed = step_splat_export.clear_exports(PROJECTS_DIR / slug / "train")
    return {"removed": removed}


@router.get("/{project_id}/mesh")
async def read_mesh_result(project_id: str, session: Session = Depends(get_session)):
    """How the last meshing run went, and what step 5 is about to read.

    Written by step 5 to `mesh/mesh_result.json`, for the same reason steps 3
    and 4 write theirs: the run prints 419 lines of which 360 are one counter,
    and the numbers that matter - vertices, faces, components, boundary edges,
    texture size and texel coverage - are on four of them.

    `input` is the panel's half of the contract every step here keeps: say what
    the run will read before it reads it. The checkpoint is step 4's splat and
    the cameras are step 3's model, and a mesh needs both unless the user turns
    the cameras off.

    Answers 200 with null before the first run - the caller is a UI panel, not
    a dependency.
    """
    slug = get_slug_from_id(project_id, session)
    project_path = PROJECTS_DIR / slug
    report = read_json(project_path / "mesh" / "mesh_result.json")

    splat = find_splat(project_path / "train")
    outputs = find_outputs(project_path / "mesh")
    return {
        "mesh": report,
        "input": {
            "has_splat": splat is not None,
            "splat_file": splat.name if splat else None,
            "splat_bytes": splat.stat().st_size if splat else None,
            "checkpoint": splat.parent.name if splat else None,
            "has_model": colmap.find_model(project_path / "sfm") is not None,
        },
        "files": [
            {
                "filename": p.name,
                "bytes": p.stat().st_size,
                "url": f"/static/{slug}/mesh/{p.name}",
            }
            for p in outputs
        ],
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
    # What the last `spirula sam` run did, for the same reason step 3 and step 4
    # write a result file: the run says how it went on the log bus and a log line
    # is gone on the next page load. Null before the first run.
    report = read_json(project_path / "analysis" / "mask_result.json")

    if not masks:
        return {"masks": {"masks": 0, "frames": len(frames), "matched": 0,
                          "state": "none", "note": "No masks yet."},
                "run": report}

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
    }, "run": report}


@router.get("/{project_id}/geometry")
async def read_geometry(project_id: str, session: Session = Depends(get_session)):
    """What `sfm/normals/` and `sfm/depths/` hold, and how the last run went.

    The counts are read off the folders rather than off the report, because the
    folders are what `train` reads: `--depth-dir` and `--normal-dir` default to
    `depths` and `normals` relative to `--data`, and with `--data <project>/sfm`
    that is these two directories exactly (CLAUDE.md §7.5). Their being non-empty
    is what decides whether step 4 sends `--load-depths` / `--load-normals` at
    all, so the panel showing a count is showing the same thing the step will
    decide on.

    200 with zeroes before the first run - the caller is a UI panel.
    """
    slug = get_slug_from_id(project_id, session)
    project_path = PROJECTS_DIR / slug
    sfm_dir = project_path / "sfm"

    def _count(name: str) -> int:
        target = sfm_dir / name
        return sum(1 for p in target.iterdir() if p.is_file()) if target.is_dir() else 0

    return {
        "geometry": {
            "normals": _count("normals"),
            "depths": _count("depths"),
            "images": frame_files.count_frames(project_path / "frames"),
            # `geometry` reads the reconstruction's cameras, so a project with
            # no model has nothing for this pass to run against.
            "has_model": colmap.find_model(sfm_dir) is not None,
        },
        "run": read_json(sfm_dir / "geometry_result.json"),
    }


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
    """What `export/` holds — step 5's delivery drawer (§7.10, §14.1).

    `role` tells the splat from the mesh, because with `mesh --format ply` both
    are a `.ply` and only the name says which is which
    (`step_export.find_export_splat`).
    """
    slug = get_slug_from_id(project_id, session)
    export_dir = PROJECTS_DIR / slug / "export"

    if not export_dir.exists():
        return {"files": []}

    splat = find_export_splat(export_dir)
    mesh_suffixes = {".ply", ".obj", ".gltf", ".glb"}

    def role_of(path: Path) -> str:
        if splat is not None and path == splat:
            return "splat"
        if path.suffix.lower() in mesh_suffixes:
            return "mesh"
        # Anything else a previous build left behind — the directory is step 5's
        # and it only ever writes a splat and a mesh into it.
        return "other"

    files = [
        {
            "filename": f.name,
            "bytes": f.stat().st_size,
            "role": role_of(f),
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
