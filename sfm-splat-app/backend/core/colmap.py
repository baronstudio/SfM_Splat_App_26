"""colmap.py — reading the sparse model `spirula sfm auto` writes.

**The model is binary.** `sfm auto` writes `cameras.bin`, `images.bin` and
`points3D.bin`, not the text form RealityScan wrote (CLAUDE.md §7.1), so there is
no `.txt` path here and nothing of the predecessor's `transforms.json` parsing
survives.

Pure module, stdlib plus NumPy: the viewer route, the step-3 result and the
preview builder all read the same model, and none of them should each grow their
own struct format.

The formats are COLMAP's own, and the parsers below were written against a real
`sfm auto` output (251 images, 84 359 points) rather than against the spec.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import BinaryIO, Iterator, NamedTuple, Optional

import numpy as np

# The order `train` probes for a reconstruction under `--data`, read off the
# tool's own behaviour. `sfm/` puts its model in the first of them, so
# `--data <project>/sfm` needs no `--colmap-recon-dir` (CLAUDE.md §5.2).
RECON_DIR_CANDIDATES = ("sparse/0", "colmap/sparse/0", "sparse", "colmap", ".")

_MODEL_FILES = ("cameras.bin", "images.bin", "points3D.bin")


# COLMAP's own camera-model table: id -> (name, number of `double` parameters).
# `sfm auto` offers models COLMAP never had - `equirectangular`,
# `thin-prism-fisheye` and friends (CLAUDE.md §7.1) - and an id that is not in
# this table has an unknown parameter count, so `read_cameras` stops there
# rather than guessing a stride and returning nonsense for every camera after
# it. Width and height are read before the parameters, so even a truncated read
# gives the aspect ratio the overlay actually needs.
CAMERA_MODELS: dict[int, tuple[str, int]] = {
    0: ("SIMPLE_PINHOLE", 3),
    1: ("PINHOLE", 4),
    2: ("SIMPLE_RADIAL", 4),
    3: ("RADIAL", 5),
    4: ("OPENCV", 8),
    5: ("OPENCV_FISHEYE", 8),
    6: ("FULL_OPENCV", 12),
    7: ("FOV", 5),
    8: ("SIMPLE_RADIAL_FISHEYE", 4),
    9: ("RADIAL_FISHEYE", 5),
    10: ("THIN_PRISM_FISHEYE", 12),
}

# Models whose leading parameter is a pinhole focal length in pixels, so that
# `2*atan(width / 2f)` is the horizontal field of view. The fisheye models carry
# a focal too, but it is not a pinhole one and that formula does not describe
# them - they get no fov and the overlay falls back to its own frustum shape,
# which is honest rather than confidently wrong.
_PINHOLE_LIKE = frozenset({0, 1, 2, 3, 4, 6, 7})


class Camera(NamedTuple):
    """One intrinsic group. `sfm auto` writes one per image resolution."""
    camera_id: int
    model_id: int
    model: str
    width: int
    height: int
    params: tuple[float, ...]


class Image(NamedTuple):
    """One registered view. `rotation` is world-from-camera, row-major 3x3."""
    image_id: int
    name: str
    position: tuple[float, float, float]
    rotation: tuple[float, ...]
    camera_id: int
    n_points: int


def find_model(workspace: Path) -> Optional[Path]:
    """The first `sparse/N` under a workspace that holds a full model.

    `sparse/0` is the largest component and the one everything reads; a
    `sparse/1` and up mean a fragmented capture, which `count_models` reports and
    step 3 warns about.
    """
    for candidate in sorted(_model_dirs(workspace)):
        return candidate
    return None


def count_models(workspace: Path) -> int:
    """How many components the reconstruction came back as.

    More than one is the honest signal that the capture did not connect - the
    thing to raise `--overlap` or switch `--data-type video` for.
    """
    return len(_model_dirs(workspace))


def _model_dirs(workspace: Path) -> list[Path]:
    sparse = workspace / "sparse"
    if not sparse.is_dir():
        return []
    return [
        d for d in sparse.iterdir()
        if d.is_dir() and all((d / f).exists() for f in _MODEL_FILES)
    ]


def _read(fh: BinaryIO, fmt: str) -> tuple:
    return struct.unpack(fmt, fh.read(struct.calcsize(fmt)))


def _quat_to_rotation(w: float, x: float, y: float, z: float) -> tuple[float, ...]:
    return (
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w),
        2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
        2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y),
    )


def read_images(path: Path) -> list[Image]:
    """Every registered view of an `images.bin`, in the order it stores them.

    COLMAP stores world-to-camera (`R`, `t`); the camera's position in the world
    is `-R^T t`, which is what a viewer overlay wants. The rotation returned is
    `R^T`, world-from-camera, so `rotation @ (0,0,1)` is the viewing direction.
    """
    images: list[Image] = []
    with path.open("rb") as fh:
        (count,) = _read(fh, "<Q")
        for _ in range(count):
            (image_id,) = _read(fh, "<i")
            qw, qx, qy, qz = _read(fh, "<4d")
            tx, ty, tz = _read(fh, "<3d")
            (camera_id,) = _read(fh, "<i")

            name_bytes = bytearray()
            while (char := fh.read(1)) not in (b"\x00", b""):
                name_bytes += char

            (n_points,) = _read(fh, "<Q")
            fh.seek(24 * n_points, 1)          # (x, y, point3D_id) per observation

            r = _quat_to_rotation(qw, qx, qy, qz)
            # -R^T t, written out rather than via NumPy: this runs once per image
            # and a 3x3 by hand is cheaper than an array round-trip.
            position = (
                -(r[0] * tx + r[3] * ty + r[6] * tz),
                -(r[1] * tx + r[4] * ty + r[7] * tz),
                -(r[2] * tx + r[5] * ty + r[8] * tz),
            )
            transposed = (r[0], r[3], r[6], r[1], r[4], r[7], r[2], r[5], r[8])
            images.append(Image(
                image_id=image_id,
                name=name_bytes.decode("utf-8", "replace"),
                position=position,
                rotation=transposed,
                camera_id=camera_id,
                n_points=n_points,
            ))
    return images


def iter_points(path: Path) -> Iterator[tuple[float, float, float, int, int, int]]:
    """(x, y, z, r, g, b) per point of a `points3D.bin`, streamed.

    Streamed rather than returned as a list because the preview builder decimates
    it and never wants the whole cloud in memory at once - the same argument
    `core/ply.py` makes about a 247 MB splat (CLAUDE.md §7.9).
    """
    with path.open("rb") as fh:
        (count,) = _read(fh, "<Q")
        for _ in range(count):
            fh.seek(8, 1)                      # point3D_id
            x, y, z = _read(fh, "<3d")
            r, g, b = _read(fh, "<3B")
            fh.seek(8, 1)                      # reprojection error
            (track_len,) = _read(fh, "<Q")
            fh.seek(8 * track_len, 1)          # (image_id, point2D_idx) pairs
            yield x, y, z, r, g, b


def read_points(path: Path) -> np.ndarray:
    """Every point of a `points3D.bin` as an (N, 3) float64 array."""
    return np.array([p[:3] for p in iter_points(path)], dtype=np.float64)


def point_count(path: Path) -> int:
    """The point count from the header alone, without reading the block."""
    with path.open("rb") as fh:
        return _read(fh, "<Q")[0]


def read_cameras(path: Path) -> list[Camera]:
    """Every intrinsic group of a `cameras.bin`, in the order it stores them.

    Stops at the first model id outside `CAMERA_MODELS` rather than raising:
    the record has no length field, so an unknown model makes everything after
    it unreadable but leaves everything before it perfectly good. A caller that
    wants one frustum shape only ever looks at the first entry.
    """
    cameras: list[Camera] = []
    with path.open("rb") as fh:
        (count,) = _read(fh, "<Q")
        for _ in range(count):
            camera_id, model_id = _read(fh, "<2i")
            width, height = _read(fh, "<2Q")
            known = CAMERA_MODELS.get(model_id)
            if known is None:
                break
            name, n_params = known
            params = _read(fh, f"<{n_params}d")
            cameras.append(Camera(
                camera_id=camera_id,
                model_id=model_id,
                model=name,
                width=int(width),
                height=int(height),
                params=params,
            ))
    return cameras


def frustum_shape(model: Path) -> tuple[Optional[float], Optional[float]]:
    """(horizontal fov in radians, aspect ratio) for the camera overlay.

    Read off `cameras.bin` rather than assumed, because a 360 rig is a
    first-class input here (CLAUDE.md §1) and drawing its cameras as 16:9
    pinholes would be a picture of a different capture.

    Returns `(None, aspect)` when the model is not pinhole-like - a fisheye or
    an equirectangular camera has no single horizontal fov a wire frustum could
    stand for. The first group is the answer: `--camera-mode folder` splits on
    image resolution, so several groups mean several resolutions, and the
    overlay draws one frustum size for all of them either way.
    """
    try:
        cameras = read_cameras(model / "cameras.bin")
    except (OSError, ValueError, struct.error):
        return None, None
    if not cameras:
        return None, None

    camera = cameras[0]
    aspect = camera.width / camera.height if camera.height else None
    if camera.model_id not in _PINHOLE_LIKE or not camera.params:
        return None, aspect

    focal_x = camera.params[0]
    if focal_x <= 0:
        return None, aspect
    return 2.0 * math.atan(camera.width / (2.0 * focal_x)), aspect
