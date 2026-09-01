import asyncio
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import Session, select

from backend.api.routes.pipeline import is_running
from backend.api.websocket import broadcast
from backend.core import imageset
from backend.core.curate.select import DROP, KEEP
from backend.core.defaults import deep_merge
from backend.core.project_ops import (
    RESETTABLE_STEPS,
    archive_to_zip,
    copy_project_files,
    ensure_subdirs,
    reset_steps,
    restore_from_zip,
)
from backend.core.steps.step_analyze import (
    load_overrides,
    rebuild_selection,
    save_overrides,
)
from backend.db.database import get_session
from backend.models.project import Project

router = APIRouter()

PROJECTS_DIR = Path(__file__).parents[3] / "projects"
# Underscored so it can never collide with a slug: slugs are lowercased and
# stripped to [a-z0-9_-], and a leading underscore is not something a project
# name produces on its own.
ARCHIVES_DIR = PROJECTS_DIR / "_archives"


def get_project_path(slug: str) -> Path:
    return PROJECTS_DIR / slug


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "_", name.lower()).strip("_") or "project"


def _unique_slug(session: Session, name: str) -> str:
    """A slug no other project owns.

    Two projects sharing a name used to share a directory — the second one
    extracted its frames on top of the first one's. The suffix is the cheapest
    fix that keeps the slug readable.
    """
    base = _slugify(name)
    taken = {row for row in session.exec(select(Project.slug)).all()}
    slug = base
    index = 2
    while slug in taken or get_project_path(slug).exists():
        slug = f"{base}-{index}"
        index += 1
    return slug


def _require_project(session: Session, id: str) -> Project:
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _require_idle(project: Project) -> None:
    """Refuse to move files under a step that is still writing them."""
    if is_running(project.id):
        raise HTTPException(
            status_code=409,
            detail="A job is running for this project — abort it first.",
        )


def _thread_progress(loop: "asyncio.AbstractEventLoop", label: str, name: str):
    """A progress callback for a worker thread, landing on the WS bus.

    The file operations run in `asyncio.to_thread` (they are blocking I/O over
    gigabytes), so they cannot await the broadcast themselves — the callback
    hops back onto the loop instead. The UI holds a modal open for the whole
    operation and reads these messages; without them it is a spinner with no
    end in sight.
    """
    def report(done: int, total: int, done_bytes: int) -> None:
        asyncio.run_coroutine_threadsafe(
            broadcast(
                "project", "INFO",
                f"[{label}] {name} — {done}/{total} files · {done_bytes / 1_048_576:.0f} MB",
                progress=done / total,
            ),
            loop,
        )

    return report


def _require_live(project: Project) -> None:
    if project.archived_at is not None:
        raise HTTPException(
            status_code=409,
            detail="This project is archived — restore it first.",
        )


def _get_thumbnail_url(slug: str) -> Optional[str]:
    frames_dir = PROJECTS_DIR / slug / "frames"
    if not frames_dir.exists():
        return None
    for ext in (".jpg", ".jpeg", ".png"):
        files = sorted(frames_dir.glob(f"*{ext}"))
        if files:
            return f"/static/{slug}/frames/{files[0].name}"
    return None


def project_to_dict(project: Project) -> dict:
    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "current_step": project.current_step,
        "step_status": project.get_step_status(),
        "input_video_path": project.input_video_path,
        "frame_count": project.frame_count,
        # Capture metadata (§4): free text, shown on the tile and in the
        # wizard's Project info panel, read by no step.
        "footage_author": project.footage_author,
        "description": project.description,
        "settings_json": project.settings_json,
        "error_message": project.error_message,
        "thumbnail_url": _get_thumbnail_url(project.slug),
        # Shown on the tile: where the data actually is, so the user can open it
        # in the explorer without hunting for the slug.
        "path": str(get_project_path(project.slug)),
        "archived": project.archived_at is not None,
        "archived_at": project.archived_at.isoformat() if project.archived_at else None,
        "archive_path": project.archive_path,
    }


class CreateProjectBody(BaseModel):
    name: str
    settings: Optional[dict] = None
    footage_author: Optional[str] = None
    description: Optional[str] = None


class UpdateProjectBody(BaseModel):
    name: Optional[str] = None
    footage_author: Optional[str] = None
    description: Optional[str] = None
    current_step: Optional[int] = None
    step_status: Optional[dict] = None
    settings_json: Optional[dict] = None
    error_message: Optional[str] = None


@router.post("/create")
async def create_project(
    body: CreateProjectBody, session: Session = Depends(get_session)
):
    slug = _unique_slug(session, body.name)

    project_path = get_project_path(slug)
    ensure_subdirs(project_path)

    project = Project(
        name=body.name,
        slug=slug,
        footage_author=body.footage_author,
        description=body.description,
        settings_json=json.dumps(body.settings) if body.settings else "{}",
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    return project_to_dict(project)


@router.get("/")
async def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(
        select(Project).order_by(Project.created_at.desc())
    ).all()
    return [project_to_dict(p) for p in projects]


@router.get("/{id}")
async def get_project(id: str, session: Session = Depends(get_session)):
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    base = get_project_path(project.slug)
    result = project_to_dict(project)
    result["paths"] = {
        "input": (base / "input").as_posix(),
        "frames": (base / "frames").as_posix(),
        "rc_output": (base / "rc_output").as_posix(),
        "lfs_output": (base / "lfs_output").as_posix(),
        "export": (base / "export").as_posix(),
    }
    return result


@router.delete("/{id}")
async def delete_project(id: str, session: Session = Depends(get_session)):
    """Remove the row, the directory and — if archived — the .zip.

    The only place in the app allowed to delete inside `projects/` wholesale
    (CLAUDE.md §3): it acts on one slug, on the user's explicit request.
    """
    project = _require_project(session, id)
    _require_idle(project)

    project_path = get_project_path(project.slug)
    if project_path.exists():
        shutil.rmtree(project_path, ignore_errors=True)

    if project.archive_path:
        archive = Path(project.archive_path)
        if archive.exists():
            archive.unlink()

    session.delete(project)
    session.commit()

    return {"deleted": id}


# ── Project operations: copy / reset / archive ───────────────────────────────


class CopyProjectBody(BaseModel):
    name: str


class ResetProjectBody(BaseModel):
    """Which steps to wipe. `null` means every step — never the source video."""

    steps: Optional[list[int]] = None


@router.post("/{id}/copy")
async def copy_project(
    id: str,
    body: CopyProjectBody,
    session: Session = Depends(get_session),
):
    """Duplicate a project — files, wizard position and per-project settings.

    A copy is how a threshold gets tried without losing the run it came from, so
    it carries the outputs too; what it does not carry is `preview/`, which the
    viewer rebuilds from those outputs on demand (§7.3).
    """
    source = _require_project(session, id)
    _require_idle(source)
    _require_live(source)

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A name is required")

    slug = _unique_slug(session, name)
    src_path = get_project_path(source.slug)
    dst_path = get_project_path(slug)

    await broadcast("project", "INFO", f"[copy] '{source.name}' → '{name}'…", progress=0.0)
    try:
        count, size = await asyncio.to_thread(
            copy_project_files, src_path, dst_path,
            _thread_progress(asyncio.get_running_loop(), "copy", name),
        )
    except Exception as exc:
        # Half a project is worse than none: the copy is its own directory, so
        # removing it leaves the source untouched and the list unchanged.
        shutil.rmtree(dst_path, ignore_errors=True)
        await broadcast("project", "ERROR", f"[copy] Failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Copy failed: {exc}")

    clone = Project(
        name=name,
        slug=slug,
        footage_author=source.footage_author,
        description=source.description,
        current_step=source.current_step,
        step_status=source.step_status,
        frame_count=source.frame_count,
        settings_json=source.settings_json,
        # The video lives in the copy now; a path into the source project would
        # make the two share a file that either of them may delete.
        input_video_path=(
            str(dst_path / Path(source.input_video_path).relative_to(src_path))
            if source.input_video_path
            and Path(source.input_video_path).is_relative_to(src_path)
            else source.input_video_path
        ),
    )
    session.add(clone)
    session.commit()
    session.refresh(clone)

    await broadcast(
        "project", "SUCCESS",
        f"[copy] '{source.name}' → '{clone.name}' — {count} files, {size / 1_048_576:.0f} MB",
        progress=1.0,
    )
    return project_to_dict(clone)


@router.post("/{id}/reset")
async def reset_project(
    id: str,
    body: ResetProjectBody,
    session: Session = Depends(get_session),
):
    """Wipe the artefacts of the given steps and rewind the wizard to them.

    `input/` is never touched: re-uploading the source video is the one cost a
    reset must not have. Resetting step N implies every later step, since their
    outputs were derived from the ones being deleted.
    """
    project = _require_project(session, id)
    _require_idle(project)
    _require_live(project)

    requested = body.steps if body.steps else list(RESETTABLE_STEPS)
    unknown = [s for s in requested if s not in RESETTABLE_STEPS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Steps {unknown} have nothing to reset. Resettable: {list(RESETTABLE_STEPS)}",
        )

    first = min(requested)
    steps = [s for s in RESETTABLE_STEPS if s >= first]

    await broadcast("project", "INFO", f"[reset] '{project.name}' from step {first}…", progress=0.0)
    removed = await asyncio.to_thread(reset_steps, get_project_path(project.slug), steps)

    status = project.get_step_status()
    for step in steps:
        status.pop(str(step), None)
    project.set_step_status(status)
    project.current_step = first - 1
    project.error_message = None
    if first <= 2:
        project.frame_count = 0
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)

    await broadcast(
        "project", "SUCCESS",
        f"[reset] '{project.name}' from step {first} — removed: "
        + (", ".join(removed) if removed else "nothing (already clean)")
        + ". Source video kept.",
        progress=1.0,
    )
    return {**project_to_dict(project), "reset_steps": steps, "removed": removed}


@router.post("/{id}/archive")
async def archive_project(id: str, session: Session = Depends(get_session)):
    """Zip the project directory and keep the row, disabled, in the list."""
    project = _require_project(session, id)
    _require_idle(project)
    if project.archived_at is not None:
        raise HTTPException(status_code=409, detail="Project is already archived")

    project_path = get_project_path(project.slug)
    zip_path = ARCHIVES_DIR / f"{project.slug}.zip"

    progress = _thread_progress(asyncio.get_running_loop(), "archive", project.name)

    await broadcast("project", "INFO", f"[archive] Compressing '{project.name}'…", progress=0.0)
    try:
        size = await asyncio.to_thread(archive_to_zip, project_path, zip_path, progress)
    except Exception as exc:  # the directory is still intact — say so and stop
        await broadcast("project", "ERROR", f"[archive] Failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Archiving failed: {exc}")

    shutil.rmtree(project_path, ignore_errors=True)

    project.archived_at = datetime.utcnow()
    project.archive_path = str(zip_path)
    project.updated_at = project.archived_at
    session.add(project)
    session.commit()
    session.refresh(project)

    await broadcast(
        "project", "SUCCESS",
        f"[archive] '{project.name}' archived → {zip_path.name} ({size / 1_048_576:.1f} MB)",
        progress=1.0,
    )
    return project_to_dict(project)


@router.post("/{id}/unarchive")
async def unarchive_project(id: str, session: Session = Depends(get_session)):
    """Unpack the archive back into `projects/` and re-enable the project."""
    project = _require_project(session, id)
    if project.archived_at is None:
        raise HTTPException(status_code=409, detail="Project is not archived")

    zip_path = Path(project.archive_path) if project.archive_path else None
    if not zip_path or not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"Archive file is missing: {zip_path}")

    project_path = get_project_path(project.slug)
    await broadcast("project", "INFO", f"[archive] Restoring '{project.name}'…", progress=0.0)
    try:
        await asyncio.to_thread(
            restore_from_zip, zip_path, project_path,
            _thread_progress(asyncio.get_running_loop(), "restore", project.name),
        )
    except Exception as exc:
        await broadcast("project", "ERROR", f"[archive] Restore failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Restore failed: {exc}")

    # The zip only goes once the files are back — a failed extraction must not
    # be the moment the only copy disappears.
    zip_path.unlink(missing_ok=True)

    project.archived_at = None
    project.archive_path = None
    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)

    await broadcast("project", "SUCCESS", f"[archive] '{project.name}' restored", progress=1.0)
    return project_to_dict(project)


_ALLOWED_INPUT_EXTS = {".mp4", ".mov", ".srt"}


@router.get("/{id}/input-files")
async def list_input_files(id: str, session: Session = Depends(get_session)):
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    input_dir = get_project_path(project.slug) / "input"
    if not input_dir.exists():
        return {"files": []}
    files = [
        {"filename": f.name, "size_bytes": f.stat().st_size}
        for f in sorted(input_dir.iterdir(), key=lambda f: f.name)
        if f.is_file() and f.suffix.lower() in _ALLOWED_INPUT_EXTS
    ]
    return {"files": files}


@router.post("/{id}/upload-input")
async def upload_input_file(
    id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    raw_name = Path(file.filename or "unnamed").name  # strip any directory component
    ext = Path(raw_name).suffix.lower()
    if ext not in _ALLOWED_INPUT_EXTS:
        raise HTTPException(
            status_code=400,
            detail="Only .mp4, .mov, or .srt files are accepted",
        )

    input_dir = get_project_path(project.slug) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    target = input_dir / raw_name
    contents = await file.read()
    target.write_bytes(contents)

    return {"filename": raw_name, "size_bytes": len(contents)}


# -- Image sets: a folder or a zip of already-extracted frames (6.7) ----------


class ImportFolderBody(BaseModel):
    """A folder *on this machine*, read server-side.

    Not an upload: the app runs on the workstation that holds the files (1),
    and pushing 20 GB of PNG through multipart to write it back onto the same
    disk is a copy with extra steps. The browser cannot read a path, so the
    user types or pastes one - which is also the only way this works for a
    folder that is not under the project.
    """

    path: str
    name: Optional[str] = None


def _import_target(session: Session, id: str) -> tuple[Project, Path]:
    """The project's `input/`, once it is safe to write into.

    Same two guards as every other file operation (14): never while a job is
    running for this project, never on an archived one - an import lands in the
    directory the extraction reads.
    """
    project = _require_project(session, id)
    _require_idle(project)
    _require_live(project)
    input_dir = get_project_path(project.slug) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    return project, input_dir


@router.get("/{id}/image-sets")
async def list_image_sets(id: str, session: Session = Depends(get_session)):
    project = _require_project(session, id)
    input_dir = get_project_path(project.slug) / "input"
    loop = asyncio.get_running_loop()
    sets = await loop.run_in_executor(
        None,
        lambda: [
            # `deep=False`: this listing is polled by step 1, and sampling three
            # images with cv2 to answer the alpha question belongs to the step 2
            # panel that actually asks it.
            imageset.describe_set(d, project.slug, deep=False)
            for d in imageset.find_image_sets(input_dir)
        ],
    )
    return {"sets": sets}


@router.post("/{id}/import-folder")
async def import_image_folder(
    id: str,
    body: ImportFolderBody,
    session: Session = Depends(get_session),
):
    project, input_dir = _import_target(session, id)
    folder = Path(body.path.strip().strip('"'))

    await broadcast("project", "INFO", f"[import] Reading {folder}...", progress=0.0)
    loop = asyncio.get_running_loop()
    try:
        manifest = await asyncio.to_thread(
            imageset.import_folder, folder, input_dir, body.name or "",
            _thread_progress(loop, "import", folder.name),
        )
    except (NotADirectoryError, FileNotFoundError) as exc:
        await broadcast("project", "ERROR", f"[import] {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - the message is the answer
        await broadcast("project", "ERROR", f"[import] Failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    await broadcast(
        "project", "SUCCESS",
        f"[import] {manifest['image_count']} images -> input/{manifest['name']}/",
        progress=1.0,
    )
    return manifest


@router.post("/{id}/import-zip")
async def import_image_zip(
    id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """A dropped zip of images, unpacked into `input/<conformed-name>/`.

    The upload is streamed to disk in chunks rather than read into memory: a zip
    of 900 PNGs is gigabytes, and `await file.read()` would hold all of it at
    once to write the same bytes out again.
    """
    project, input_dir = _import_target(session, id)

    raw_name = Path(file.filename or "images.zip").name
    if Path(raw_name).suffix.lower() != ".zip":
        raise HTTPException(status_code=400, detail="Only a .zip of images is accepted")

    staged = input_dir / f".incoming_{raw_name}"
    await broadcast("project", "INFO", f"[import] Receiving {raw_name}...", progress=0.0)
    try:
        with staged.open("wb") as dst:
            await asyncio.to_thread(shutil.copyfileobj, file.file, dst, 1024 * 1024)
    except Exception as exc:  # noqa: BLE001
        staged.unlink(missing_ok=True)
        await broadcast("project", "ERROR", f"[import] Upload failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")

    loop = asyncio.get_running_loop()
    try:
        manifest = await asyncio.to_thread(
            imageset.import_zip, staged, input_dir, Path(raw_name).stem,
            _thread_progress(loop, "import", raw_name), True, raw_name,
        )
    except FileNotFoundError as exc:
        staged.unlink(missing_ok=True)
        await broadcast("project", "ERROR", f"[import] {exc}")
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        staged.unlink(missing_ok=True)
        await broadcast("project", "ERROR", f"[import] Failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Import failed: {exc}")

    await broadcast(
        "project", "SUCCESS",
        f"[import] {manifest['image_count']} images from {raw_name} -> "
        f"input/{manifest['name']}/",
        progress=1.0,
    )
    return manifest


@router.post("/{id}/import-images")
async def import_image_files(
    id: str,
    files: list[UploadFile] = File(...),
    name: str = Form(""),
    session: Session = Depends(get_session),
):
    """A selection of image files, or a folder picked in the browser.

    The browser's folder picker sends every file individually, so this is the
    same route either way. It is the slow lane by construction - every byte
    travels through HTTP - which is why `import-folder` exists next to it.
    """
    project, input_dir = _import_target(session, id)

    staging = input_dir / ".incoming_files"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        kept: list[tuple[Path, str]] = []
        for position, upload in enumerate(files):
            original = Path(upload.filename or f"image_{position}").name
            if Path(original).suffix.lower() not in imageset.IMAGE_SUFFIXES:
                continue
            # Prefixed while staged so two folders' `IMG_0001.jpg` cannot
            # collide; the real name travels beside it into the manifest.
            target = staging / f"{position:06d}_{original}"
            with target.open("wb") as dst:
                await asyncio.to_thread(shutil.copyfileobj, upload.file, dst, 1024 * 1024)
            kept.append((target, original))

        if not kept:
            raise HTTPException(status_code=400, detail="No image among the uploaded files")

        kept.sort(key=lambda pair: imageset.natural_key(pair[1]))
        set_name = imageset.unique_name(
            input_dir, imageset.conform_name(name or Path(kept[0][1]).stem)
        )
        loop = asyncio.get_running_loop()
        manifest = await asyncio.to_thread(
            imageset.import_files,
            [path for path, _ in kept], input_dir, set_name,
            "upload", "", "", True,
            _thread_progress(loop, "import", set_name),
            [original for _, original in kept],
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    await broadcast(
        "project", "SUCCESS",
        f"[import] {manifest['image_count']} images -> input/{manifest['name']}/",
        progress=1.0,
    )
    return manifest


@router.delete("/{id}/image-sets/{name}")
async def delete_image_set(id: str, name: str, session: Session = Depends(get_session)):
    project, input_dir = _import_target(session, id)
    target = input_dir / Path(name).name  # no directory component, no traversal
    if not target.is_dir() or not imageset.is_image_set(target):
        raise HTTPException(status_code=404, detail="Image set not found")
    await asyncio.to_thread(shutil.rmtree, target, True)
    return {"deleted": target.name}


# -- The Reconstruction Region is deleted, not ported -------------------------
#
# `3DGS_App_26` had a whole feature here: a box the user placed by hand, stored
# in two frames, round-tripped through RealityScan's `.rsbox` and used to drive
# mesh-based mask generation. Every part of it existed to work around
# RealityScan - the `.rsbox` shape, the `yawPitchRoll` axis order, the Z-up/NeRF
# frame check - and none of it applies here.
#
# The *idea* survives in two places that share no implementation with it:
# `spirula sam` for masking (CLAUDE.md §7.4) and `train --mask-boundary-offset`
# for growing or shrinking the result (§7.6).


@router.delete("/{id}/input-files/{filename}")
async def delete_input_file(
    id: str,
    filename: str,
    session: Session = Depends(get_session),
):
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Prevent path traversal by stripping any directory components
    safe_name = Path(filename).name
    target = get_project_path(project.slug) / "input" / safe_name

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    target.unlink()
    return {"deleted": safe_name}


@router.put("/{id}")
async def update_project(
    id: str,
    body: UpdateProjectBody,
    session: Session = Depends(get_session),
):
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.name is not None:
        project.name = body.name
    if body.footage_author is not None:
        project.footage_author = body.footage_author or None
    if body.description is not None:
        project.description = body.description or None
    if body.current_step is not None:
        project.current_step = body.current_step
    if body.step_status is not None:
        project.set_step_status(body.step_status)
    if body.settings_json is not None:
        project.settings_json = json.dumps(body.settings_json)
    if body.error_message is not None:
        project.error_message = body.error_message

    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)

    return project_to_dict(project)


class PatchProjectBody(BaseModel):
    """Partial update. Anything omitted is left alone."""

    name: Optional[str] = None
    # Free text, both cleared by sending "" — omitted leaves them alone.
    footage_author: Optional[str] = None
    description: Optional[str] = None
    current_step: Optional[int] = None
    step_status: Optional[dict] = None
    error_message: Optional[str] = None
    # Deep-merged into settings_json: a project stores only the keys it really
    # overrides, so changing a default keeps propagating to it (CLAUDE.md §4).
    settings: Optional[dict] = None
    # Manual curation verdicts: {"frame_0007.jpg": "keep" | "drop" | null}.
    # null removes the override and hands the frame back to the automatic verdict.
    overrides: Optional[dict] = None


@router.patch("/{id}")
async def patch_project(
    id: str,
    body: PatchProjectBody,
    session: Session = Depends(get_session),
):
    """Partial update, including the manual keep/drop overrides.

    Flipping a frame only rewrites overrides.json and re-derives selection.json
    from the existing scores — no image is re-read, so the gallery updates
    instantly instead of paying for a full re-analysis.
    """
    project = session.get(Project, id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.name is not None:
        project.name = body.name
    if body.footage_author is not None:
        project.footage_author = body.footage_author or None
    if body.description is not None:
        project.description = body.description or None
    if body.current_step is not None:
        project.current_step = body.current_step
    if body.step_status is not None:
        project.set_step_status(body.step_status)
    if body.error_message is not None:
        project.error_message = body.error_message

    if body.settings is not None:
        try:
            current = json.loads(project.settings_json or "{}")
        except json.JSONDecodeError:
            current = {}
        project.settings_json = json.dumps(deep_merge(current, body.settings))

    selection = None
    if body.overrides is not None:
        project_path = get_project_path(project.slug)
        overrides = load_overrides(project_path)
        for frame, verdict in body.overrides.items():
            safe = Path(frame).name  # never let a path escape analysis/
            if verdict is None:
                overrides.pop(safe, None)
            elif verdict in (KEEP, DROP):
                overrides[safe] = verdict
            else:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid override '{verdict}' for {safe}. Expected 'keep', 'drop' or null.",
                )
        save_overrides(project_path, overrides)
        selection = rebuild_selection(project_path)

    project.updated_at = datetime.utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)

    return {**project_to_dict(project), "selection": selection}
