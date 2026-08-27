import shutil
import glob
from pathlib import Path
from typing import Optional

from backend.core.config import load_config

TOOLS_META = [
    {"id": "ffmpeg",  "name": "FFmpeg"},
    {"id": "rc",      "name": "RealityScan"},
    {"id": "lfs",     "name": "LichtFeld Studio"},
    {"id": "blender", "name": "Blender"},
]


def _ffmpeg_found(cfg) -> tuple[bool, Optional[str]]:
    """Return (found, path) for FFmpeg."""
    # 1. Explicit path in config
    p = cfg.tools.ffmpeg_path
    if p and Path(p).is_file():
        return True, p
    # 2. PATH lookup
    which = shutil.which("ffmpeg")
    if which:
        return True, which
    return False, None


def _exe_found(path: Optional[str]) -> tuple[bool, Optional[str]]:
    if path and Path(path).is_file():
        return True, path
    return False, None


def check_all_tools() -> dict[str, bool]:
    """Run all checks, return {tool_id: found}."""
    cfg = load_config()
    ffmpeg_found, _ = _ffmpeg_found(cfg)
    rc_found, _    = _exe_found(cfg.tools.rc_exe_path)
    lfs_found, _   = _exe_found(cfg.tools.lfs_exe_path)
    blender_found, _ = _exe_found(cfg.tools.blender_exe_path)
    return {
        "ffmpeg":  ffmpeg_found,
        "rc":      rc_found,
        "lfs":     lfs_found,
        "blender": blender_found,
    }


def auto_detect_rc() -> Optional[str]:
    """Search common install locations for RealityCapture / RealityScan."""
    patterns = [
        "C:/Program Files/Epic Games/**/RealityScan.exe",
        "C:/Program Files/Capturing Reality/**/RealityCapture.exe",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
    return None


def auto_detect_blender() -> Optional[str]:
    """Search common install locations for Blender."""
    patterns = [
        "C:/Program Files/Blender Foundation/**/blender.exe",
        "C:/Program Files (x86)/Blender Foundation/**/blender.exe",
    ]
    for pattern in patterns:
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]
    return None


def auto_detect_ffmpeg() -> Optional[str]:
    """Return the ffmpeg executable from PATH, if available."""
    return shutil.which("ffmpeg")


def get_tool_status() -> list[dict]:
    """Return a list of tool status dicts with id, name, found, path."""
    cfg = load_config()

    ffmpeg_found, ffmpeg_path = _ffmpeg_found(cfg)
    rc_found, rc_path         = _exe_found(cfg.tools.rc_exe_path)
    lfs_found, lfs_path       = _exe_found(cfg.tools.lfs_exe_path)
    blender_found, blender_path = _exe_found(cfg.tools.blender_exe_path)

    return [
        {
            "id":          "ffmpeg",
            "name":        "FFmpeg",
            "found":       ffmpeg_found,
            "path":        ffmpeg_path,
        },
        {
            "id":          "rc",
            "name":        "RealityScan",
            "found":       rc_found,
            "path":        rc_path,
        },
        {
            "id":          "lfs",
            "name":        "LichtFeld Studio",
            "found":       lfs_found,
            "path":        lfs_path,
        },
        {
            "id":          "blender",
            "name":        "Blender",
            "found":       blender_found,
            "path":        blender_path,
        },
    ]
