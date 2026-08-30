import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


# What `-hwaccel` is passed to FFmpeg. An installation setting, not a business
# one (CLAUDE.md §4): it describes the GPU in this machine, and no project wants
# a different one. "none" sends no flag at all.
#
# It is safe to be wrong about: FFmpeg treats `-hwaccel` as a preference, not a
# requirement — measured on a 4080x4080 h264 source, NVDEC refused the surface
# (`CUDA_ERROR_INVALID_VALUE`), FFmpeg fell back to software and the run still
# exited 0 with the right frames. step_extract watches for that line and says so,
# because a silent fallback is otherwise indistinguishable from a fast one.
HWACCELS = ("none", "auto", "cuda", "d3d11va", "dxva2", "qsv", "vulkan")


class ToolPaths(BaseModel):
    # One binary drives steps 3 to 5: sfm, train, mesh, sam and geometry are all
    # tools inside it (CLAUDE.md §5.1). `rc_exe_path`, `lfs_exe_path`,
    # `supersplat_url` and `blender_exe_path` are gone with the tools and the
    # step they named.
    spirula_exe_path: Optional[str] = None
    # Where `spirula sam` and `spirula geometry` cache the checkpoints they fetch
    # on first use. The tool's own default is
    # %LOCALAPPDATA%\spirula-studio\models\; empty means "leave it to the
    # tool". Held here so the setup panel can show it, pre-seed it and report its
    # size - the MoGe normal model alone is 419.4 MB (§7.5).
    spirula_model_cache: Optional[str] = None
    ffmpeg_path: str = ""
    ffmpeg_hwaccel: str = "none"
    # `@playcanvas/splat-transform`, the optional Node CLI behind the three
    # compressed export formats (CLAUDE.md §7.6c). Empty means "look where
    # `npm install --prefix tools/splat-transform` puts it, then on PATH" —
    # `core/steps/splat_transform.py` owns that search. Nothing else in the app
    # needs it, so an install that never exports a .sog never sets it.
    splat_transform_path: Optional[str] = None


class AppConfig(BaseModel):
    tools: ToolPaths = ToolPaths()


def load_config() -> AppConfig:
    # A fresh clone has no config.json: the setup panel writes one, so its
    # absence must not put the whole app behind a file the app itself creates.
    #
    # A *corrupt* one is a different question and is not swallowed. Reading it as
    # `{}` would start the app on empty defaults with every tool path silently
    # gone - which is how this function was first written here, and it hid a
    # config.json whose Windows paths had lost their backslash escaping for a
    # full test cycle. An unreadable file is a failure with the reason attached.
    if not CONFIG_PATH.exists():
        raw = {}
    else:
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{CONFIG_PATH} is not valid JSON ({exc}). Fix it or delete it - "
                "the setup panel rewrites it from scratch."
            ) from exc
    return AppConfig(
        tools=ToolPaths(
            spirula_exe_path=raw.get("spirula_exe_path"),
            spirula_model_cache=raw.get("spirula_model_cache"),
            ffmpeg_path=raw.get("ffmpeg_path", ""),
            ffmpeg_hwaccel=raw.get("ffmpeg_hwaccel", "none") or "none",
            splat_transform_path=raw.get("splat_transform_path"),
        ),
    )


def save_config(cfg) -> None:
    """Save config to disk. Accepts AppConfig, a flat dict, or a nested one.

    config.json is flat on disk, but the API exposes the AppConfig shape
    (`{tools: {...}}`). A nested payload is flattened here rather than written
    verbatim, which would create a dead `tools` key that load_config never reads
    back.
    """
    if isinstance(cfg, dict):
        # Merge incoming dict over the existing file so no field is lost.
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                flat: dict = json.load(f)
        except Exception:
            flat = {}
        for key, value in cfg.items():
            if key == "tools" and isinstance(value, dict):
                flat.update(value)
            else:
                flat[key] = value
    else:
        flat = {}
        flat.update(cfg.tools.model_dump())
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(flat, f, indent=4)


def reload_config() -> AppConfig:
    """Reload config from disk into the existing singleton, in place.

    Mutated rather than rebound: every step does `from ...config import
    app_config`, which binds the object at import time. Rebinding the module
    global would leave all of them holding the *previous* config, so a tool path
    corrected in the Setup panel would not reach the steps until a restart —
    which is indistinguishable from the fix not working.
    """
    app_config.tools = load_config().tools
    return app_config


app_config: AppConfig = load_config()
