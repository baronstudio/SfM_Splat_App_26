"""
probe.py — ffprobe wrapper.

Pure module: takes an explicit ffprobe/ffmpeg path, imports no FastAPI and no
app config, so it stays callable from the pipeline and from tests.
"""

import json
import subprocess
from pathlib import Path
from typing import Any, Optional


def ffprobe_path_from_ffmpeg(ffmpeg_path: str) -> str:
    """Derive the ffprobe binary sitting next to a given ffmpeg binary."""
    if not ffmpeg_path:
        return "ffprobe"
    p = Path(ffmpeg_path)
    candidate = p.with_name(p.name.replace("ffmpeg", "ffprobe"))
    return str(candidate) if candidate.exists() else "ffprobe"


def _parse_fraction(value: Optional[str]) -> Optional[float]:
    """Parse ffprobe rationals like '30000/1001'. Returns None when unusable."""
    if not value:
        return None
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else None
        return float(value)
    except (ValueError, ZeroDivisionError):
        return None


def probe_video(video_path: Path, ffmpeg_path: str = "") -> dict[str, Any]:
    """Return normalised metadata for a video file.

    Raises RuntimeError when ffprobe fails or the file holds no video stream —
    the caller decides whether to fall back or abort.
    """
    exe = ffprobe_path_from_ffmpeg(ffmpeg_path)
    cmd = [
        exe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=60)
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffprobe not found at '{exe}'") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ffprobe timed out on {video_path}") from exc

    if completed.returncode != 0:
        err = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffprobe failed ({completed.returncode}): {err}")

    raw = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    streams = raw.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"No video stream in {video_path}")

    fmt = raw.get("format", {})

    # avg_frame_rate is the honest average; r_frame_rate is the container's
    # nominal rate and lies on VFR footage. Prefer the average, fall back.
    fps = _parse_fraction(video.get("avg_frame_rate")) or _parse_fraction(video.get("r_frame_rate"))
    duration = None
    for candidate in (video.get("duration"), fmt.get("duration")):
        try:
            duration = float(candidate)
            break
        except (TypeError, ValueError):
            continue

    transfer = (video.get("color_transfer") or "").lower()
    primaries = (video.get("color_primaries") or "").lower()
    hdr = transfer in {"smpte2084", "arib-std-b67"} or primaries == "bt2020"

    try:
        bitrate = int(fmt.get("bit_rate"))
    except (TypeError, ValueError):
        bitrate = None

    return {
        "path": str(video_path),
        "container": fmt.get("format_name"),
        "codec": video.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": round(fps, 3) if fps else None,
        "duration_s": round(duration, 3) if duration else None,
        "bitrate": bitrate,
        "hdr": hdr,
        "pix_fmt": video.get("pix_fmt"),
        "nb_frames": video.get("nb_frames"),
    }
