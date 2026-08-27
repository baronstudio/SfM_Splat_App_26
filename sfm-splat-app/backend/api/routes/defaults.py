from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.core.defaults import (
    CAPTURE_PRESETS,
    ExtractDefaults,
    load_defaults,
    reset_defaults,
    resolve_extract_fps,
    save_defaults,
)

router = APIRouter()


@router.get("/")
def read_defaults():
    """Business defaults per wizard step (layer 2 of the settings model)."""
    return load_defaults()


@router.put("/")
def update_defaults(patch: dict):
    """Deep-merge a partial payload over the stored defaults."""
    try:
        return save_defaults(patch)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/reset")
def reset(section: Optional[str] = Query(None)):
    """Factory-reset every section, or just one via ?section=extract."""
    try:
        return reset_defaults(section)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/presets")
def read_presets():
    """Capture presets — read-only, defined in code so updates reach installs."""
    return CAPTURE_PRESETS


class FpsPreviewRequest(BaseModel):
    extract: ExtractDefaults
    source_fps: Optional[float] = None
    duration_s: Optional[float] = None


@router.post("/fps-preview")
def fps_preview(req: FpsPreviewRequest):
    """Resolve the working fps for a hypothetical source, without extracting.

    Lets the setup panel show what the policy actually produces instead of
    leaving the user to guess.
    """
    fps, explanation = resolve_extract_fps(req.extract, req.source_fps, req.duration_s)
    return {"fps": fps, "explanation": explanation}
