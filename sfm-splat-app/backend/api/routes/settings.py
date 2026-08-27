from fastapi import APIRouter, HTTPException
from backend.core.config import load_config, save_config, reload_config

router = APIRouter()


@router.get("/")
def read_settings():
    try:
        return load_config()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="config.json not found")


@router.put("/")
def update_settings(new_settings: dict):
    try:
        save_config(new_settings)
        updated = reload_config()
        return {"config": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
