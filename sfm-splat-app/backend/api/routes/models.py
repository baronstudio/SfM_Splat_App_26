"""routes/models.py — the checkpoint manager (CLAUDE.md §7.4, §7.5, §10).

An installation concern, not a per-project one, so it lives beside
`/api/settings` and `/api/defaults` and is drawn in the **global setup panel**
rather than on a wizard step: a checkpoint is a property of this machine, like
the FFmpeg path, and asking for it from inside step 3 would be asking the same
question once per project.

Start-and-poll rather than the WS bus, mirroring `/preview`: the bus carries no
project id (§13.7) and every consumer of it maps a step name onto the open
project's bar. A 2 GB download belongs to no project and must not move one.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core import model_store, models_catalog
from backend.core.defaults import load_defaults, save_defaults

router = APIRouter()


@router.get("/")
def list_models():
    """The catalogue, the licences, the cache and what is installed in it."""
    return model_store.overview()


class DownloadRequest(BaseModel):
    # The licence id the user read and accepted, which must be **this model's**.
    # Four licences and four separate acceptances (§10): accepting SAM 2.1's
    # Apache-2.0 says nothing about SAM 3's, and a single flag spanning both
    # would answer the harder question by accident.
    accept_licence: str


@router.post("/{model_id}/download")
async def download_model(model_id: str, req: DownloadRequest):
    """Start fetching one checkpoint and return at once — poll `GET /`."""
    try:
        spec = models_catalog.get(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    if req.accept_licence != spec.licence:
        licence = models_catalog.LICENCES[spec.licence]
        raise HTTPException(
            status_code=400,
            detail=(
                f"{spec.label} is published under {licence.name} and that "
                "licence has not been accepted. Read it at "
                f"{licence.url} and accept it for this checkpoint."
            ),
        )

    try:
        model_store.downloads.start(model_id)
    except RuntimeError as exc:
        # One at a time, refused rather than queued.
        raise HTTPException(status_code=409, detail=str(exc))
    return model_store.model_status(spec)


@router.post("/{model_id}/cancel")
def cancel_model(model_id: str):
    """Stop a running download. What was fetched stays as a resumable `.part`."""
    stopped = model_store.downloads.cancel(model_id)
    if not stopped:
        raise HTTPException(status_code=404, detail=f"{model_id} is not downloading.")
    return {"cancelled": model_id}


@router.post("/{model_id}/verify")
async def verify_model(model_id: str):
    """Re-read the installed files and re-check them against the manifest."""
    import asyncio

    try:
        return await asyncio.to_thread(model_store.verify, model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


class AdoptRequest(BaseModel):
    # A path on this machine. Read server-side for §6.7's reason: the app runs
    # on the workstation that holds the file, so a 2 GB checkpoint already on
    # this disk is a local copy and never an upload.
    path: str


@router.post("/{model_id}/adopt")
async def adopt_model(model_id: str, req: AdoptRequest):
    """Install a checkpoint the user downloaded by hand, verifying it first."""
    import asyncio

    try:
        return await asyncio.to_thread(model_store.adopt, model_id, req.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{model_id}")
def delete_model(model_id: str):
    """Delete a checkpoint's files. Nothing in the pipeline reads the cache."""
    try:
        return model_store.remove(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# `sam.model_licence` is a two-value field — the two rows of §10 — while the
# catalogue names licences by what they are. SAM 2.1's Apache-2.0 is the
# `sam2.1` acceptance; SAM 3's own licence is the `sam3` one.
_SAM_LICENCE = {"apache-2.0": "sam2.1", "sam3": "sam3"}


@router.post("/{model_id}/use")
def use_model(model_id: str):
    """Point the app's defaults at this checkpoint.

    Layer 2 of §4, not layer 3: "which SAM checkpoint is installed on this
    machine" is exactly the kind of thing that should follow the user between
    projects, and a per-project override still wins over it.

    For a SAM checkpoint this also records **which licence was accepted**, which
    `step_sam.check_settings` refuses a run without. That is not a shortcut past
    the question: the file cannot be here without the same licence having been
    accepted to fetch or adopt it, and leaving the field empty afterwards would
    make the mask panel ask a second time for an answer it already has.
    """
    try:
        spec = models_catalog.get(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    status = model_store.model_status(spec)
    if status["state"] != "ready":
        raise HTTPException(
            status_code=400,
            detail=(
                f"{spec.label} is not installed ({status['state']}). Download it "
                "before pointing the pipeline at it — a --model that does not "
                "exist is a run that fails at its first image."
            ),
        )

    # The **absolute path**, never the id: `sam track --model` takes nothing
    # else, and for `geometry` a path is what makes the run independent of where
    # the tool would have looked (`model_store.spirula_default_cache`).
    path = status["path"]
    if spec.family == "sam":
        patch = {"sam": {"model": path,
                         "model_licence": _SAM_LICENCE[spec.licence]}}
    else:
        patch = {"geometry": {"model": path}}

    try:
        save_defaults(patch)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"model_id": model_id, "family": spec.family, "path": path,
            "defaults": load_defaults()}


@router.get("/in-use")
def models_in_use():
    """Which checkpoint each family's default currently names, and whether it exists.

    The panel draws a "used by masking / geometry" badge off this rather than
    guessing from equality of paths in the browser: `geometry.model` may hold an
    id, a path outside the cache, or nothing at all, and each of the three means
    something different.
    """
    defaults = load_defaults()
    out: dict[str, Optional[dict]] = {}
    for family, value in (("sam", defaults.sam.model),
                          ("geometry", defaults.geometry.model)):
        value = (value or "").strip()
        if not value:
            out[family] = None
            continue
        match = next(
            (m for m in models_catalog.CATALOGUE
             if m.family == family and value.endswith(m.filename)),
            None,
        )
        out[family] = {
            "value": value,
            "model_id": match.id if match else None,
            "label": match.label if match else None,
        }
    return out
