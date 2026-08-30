"""crop.py - the box / sphere volume cut applied to a trained splat.

A 3DGS training run reconstructs everything the cameras saw, which on a real
capture is the subject *and* the room it stands in, the operator's feet and a
halo of low-opacity floaters where the frustums stop overlapping. None of that
is a defect of the run - it is what was in shot - and none of it belongs in the
mesh step 5 extracts or the scene step 6 assembles.

So this module is the one place in the app that **removes** reconstructed data,
and CLAUDE.md's "the viewer looks, it never writes" is amended rather than
broken: the viewer still never writes. It places the volumes and shows the cut;
the cut itself happens here, server-side, over the full PLY, and it writes a
**new file beside the original** (`train/crop/splat.ply`). The trained
`splat.ply` is never touched, so a crop is always reversible by deleting one
directory.

Three things decide the shape of this module:

* **The preview cannot do the cut.** `SceneViewer` opens a decimated `.splat`
  of at most a million records against a `splat.ply` that measured 177 775 619
  bytes on the reference project (CLAUDE.md §12, 2026-08-28). The browser sees a
  sample; only this module sees every gaussian.

* **The rows are copied verbatim, never re-encoded.** A splat PLY carries 62
  properties per vertex - 45 of them spherical harmonics that `ply.py`'s preview
  path deliberately drops - and a crop must lose none of them. So the kept rows
  are moved as raw records of the source dtype and the header is the source's
  own bytes with one number changed. Whatever spirula wrote, we write back.

* **The volumes are stored in the dataset frame**, not the viewer's. The viewer
  applies one `Rx-90` for three.js's Y-up (§7.3) and offers "Flip up" on top of
  it, so a volume stored as seen would land somewhere else the next time the
  scene is opened flipped. `cropVolumes.ts` converts on both sides; everything
  below is in the frame the tools wrote.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from backend.core import ply

KIND_BOX = "box"
KIND_SPHERE = "sphere"
KINDS = (KIND_BOX, KIND_SPHERE)

MODE_KEEP = "keep"
MODE_DELETE = "delete"
MODES = (MODE_KEEP, MODE_DELETE)

# The shader that previews this cut declares a fixed-length uniform array, so
# the two ends have to agree on one number. Eight is well past what a cleanup
# needs and cheap enough to evaluate per gaussian per frame.
MAX_VOLUMES = 8

# Vertices per pass. Same figure `ply.py` settled on, for the same reason: the
# file is here precisely because it does not fit in memory.
CHUNK = 262_144

# A half-extent below this is a volume the user cannot see and cannot hit;
# dividing by it is how a NaN gets into the mask.
_MIN_HALF = 1e-9

ProgressFn = Callable[[float], None]
AbortFn = Callable[[], bool]


class CropError(ValueError):
    """A volume, or a source file, this module refuses."""


class CropAborted(RuntimeError):
    """The cooperative abort, observed between chunks."""


# -- The volume --------------------------------------------------------------

@dataclass(frozen=True)
class Volume:
    """One box or sphere, in the dataset frame.

    `half` is a half-extent per local axis, which makes a "sphere" an ellipsoid
    the moment the scale gizmo is dragged on one axis. That is deliberate: an
    ellipsoid is what a scaled sphere *is*, and refusing it would mean silently
    ignoring two thirds of a drag the user just made.
    """

    kind: str
    mode: str
    center: tuple[float, float, float]
    half: tuple[float, float, float]
    rotation: tuple[float, float, float, float]   # (x, y, z, w)

    def basis(self) -> np.ndarray:
        """The volume's local axes as the columns of a 3x3 rotation matrix."""
        x, y, z, w = self.rotation
        n = float(np.sqrt(x * x + y * y + z * z + w * w))
        if n < 1e-12:
            return np.eye(3, dtype=np.float64)
        x, y, z, w = x / n, y / n, z / n, w / n
        return np.array([
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w),     2 * (x * z + y * w)],
            [2 * (x * y + z * w),     1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w),     2 * (y * z + x * w),     1 - 2 * (x * x + y * y)],
        ], dtype=np.float64)

    def contains(self, xyz: np.ndarray) -> np.ndarray:
        """Boolean mask of the points of `xyz` (N, 3) inside this volume."""
        # R^T * (p - c), written as (p - c) @ R because R's columns are the axes.
        local = (xyz - np.asarray(self.center, dtype=np.float64)) @ self.basis()
        local /= np.maximum(np.asarray(self.half, dtype=np.float64), _MIN_HALF)
        if self.kind == KIND_SPHERE:
            return np.einsum("ij,ij->i", local, local) <= 1.0
        return np.max(np.abs(local), axis=1) <= 1.0

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "mode": self.mode,
            "center": list(self.center),
            "half": list(self.half),
            "rotation": list(self.rotation),
        }


def _floats(raw: object, count: int, field: str, index: int) -> tuple[float, ...]:
    if not isinstance(raw, (list, tuple)) or len(raw) != count:
        raise CropError(
            f"crop volume {index + 1}: {field} must be {count} numbers, got {raw!r}"
        )
    out = []
    for value in raw:
        if not isinstance(value, (int, float)) or not np.isfinite(float(value)):
            raise CropError(f"crop volume {index + 1}: {field} holds {value!r}")
        out.append(float(value))
    return tuple(out)


def parse_volumes(raw: object) -> list[Volume]:
    """Validate the stored volume list, naming the offending one.

    Called before a byte is written and before the panel's numbers are trusted:
    the list travels through `settings_json`, which is hand-editable, and a
    half-extent of zero or a NaN centre would otherwise reach `contains` and
    quietly keep or drop everything.
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise CropError(f"crop volumes must be a list, got {type(raw).__name__}")
    if len(raw) > MAX_VOLUMES:
        raise CropError(
            f"{len(raw)} crop volumes, and the preview shader carries {MAX_VOLUMES}"
        )

    volumes: list[Volume] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise CropError(f"crop volume {i + 1}: expected an object, got {entry!r}")
        kind = entry.get("kind", KIND_BOX)
        mode = entry.get("mode", MODE_KEEP)
        if kind not in KINDS:
            raise CropError(f"crop volume {i + 1}: unknown kind {kind!r}")
        if mode not in MODES:
            raise CropError(f"crop volume {i + 1}: unknown mode {mode!r}")

        half = _floats(entry.get("half"), 3, "half", i)
        if min(half) <= 0.0:
            raise CropError(
                f"crop volume {i + 1}: half-extent {half} is not positive - "
                f"a volume with no thickness selects nothing"
            )
        volumes.append(Volume(
            kind=kind,
            mode=mode,
            center=_floats(entry.get("center"), 3, "center", i),
            half=half,
            rotation=_floats(entry.get("rotation", [0.0, 0.0, 0.0, 1.0]), 4, "rotation", i),
        ))
    return volumes


def keep_mask(xyz: np.ndarray, volumes: Sequence[Volume]) -> np.ndarray:
    """Which of `xyz` (N, 3) survive the whole stack.

    One rule, and it is the one a cleanup actually wants:

        kept = (inside at least one `keep` volume, or there are none)
               and (inside no `delete` volume)

    So a stack reads as "keep this room, minus that lamp, minus those floaters",
    and **delete always wins** - which is what makes a delete volume dropped
    inside a keep volume do the obvious thing rather than nothing. With no keep
    volume at all the scene starts whole and the deletes carve it, which is why
    a single `delete` sphere is a complete, useful crop on its own.
    """
    n = len(xyz)
    keeps = [v for v in volumes if v.mode == MODE_KEEP]
    deletes = [v for v in volumes if v.mode == MODE_DELETE]

    if keeps:
        inside = np.zeros(n, dtype=bool)
        for volume in keeps:
            inside |= volume.contains(xyz)
    else:
        inside = np.ones(n, dtype=bool)

    for volume in deletes:
        inside &= ~volume.contains(xyz)
    return inside


# -- The cut -----------------------------------------------------------------

def _header_bytes(
    src: Path, header: ply.PlyHeader, count: int,
    comments: Sequence[str] = (),
) -> bytes:
    """The source's own header, with the vertex count changed and nothing else.

    Re-emitting the header from the parsed property list would work and would
    also throw away every comment spirula wrote into it. One substitution keeps
    the file the tool's, and keeps this module honest about how little it knows
    of what those 62 properties mean.

    `comments` is the one addition allowed, and the export is the only caller
    that uses it: a saved viewpoint goes into a native PLY as `comment` lines
    (§7.6d). They are appended just before `end_header`, which is where a reader
    that skips them expects them and where a reader that wants them can find
    them without parsing the property list first.
    """
    with open(src, "rb") as fh:
        raw = fh.read(header.data_offset)
    text = raw.decode("ascii", "strict")

    if len(re.findall(r"^element\s", text, flags=re.MULTILINE)) != 1:
        raise CropError(
            f"{src.name}: the header declares more than the vertex element, and "
            f"dropping vertices under a face list would corrupt it"
        )

    patched, hits = re.subn(
        r"^element[ \t]+vertex[ \t]+\d+[ \t]*$", f"element vertex {count}",
        text, count=1, flags=re.MULTILINE,
    )
    if hits != 1:
        raise CropError(f"{src.name}: no 'element vertex N' line to update")

    if comments:
        block = "".join(f"comment {c}\n" for c in comments)
        patched, hits = re.subn(
            r"^end_header[ \t]*$", block + "end_header",
            patched, count=1, flags=re.MULTILINE,
        )
        if hits != 1:
            raise CropError(f"{src.name}: no 'end_header' line to write before")
    return patched.encode("ascii")


def header_with_count(
    src: Path, header: ply.PlyHeader, count: int,
    comments: Sequence[str] = (),
) -> bytes:
    """The source's own header bytes with the vertex count changed.

    Public because `core/splat_export.py` writes the same verbatim-row copy for
    an export that drops no property, and re-implementing the substitution there
    would be two places that have to agree about a file format.
    """
    return _header_bytes(src, header, count, comments)


class Source:
    """An open, memory-mapped splat PLY, and the two chunk operations on it.

    The chunking is exposed rather than hidden because the caller is an async
    pass: `step_crop` runs one chunk per executor hop and awaits a progress
    broadcast between them, which is exactly how `step_analyze._chunked` keeps
    the event loop - and therefore the abort route and the WebSocket - alive
    through a long pure-Python phase.

    **Two passes, not one.** A PLY header states its vertex count *before* the
    vertices, and a crop cannot know that number until it has tested every
    gaussian. So the first pass builds the mask from x, y and z alone and the
    second copies the kept records; a 178 MB splat costs two sequential reads of
    a memory map and no resident copy of itself.
    """

    def __init__(self, path: Path):
        header = ply.read_header(path)
        if header.is_ascii:
            raise CropError(
                f"{path.name}: ASCII PLY. spirula writes binary_little_endian and "
                f"the verbatim record copy this module relies on needs it"
            )
        if header.kind != ply.KIND_SPLAT:
            raise CropError(
                f"{path.name}: not a gaussian PLY - no f_dc_0 / opacity / "
                f"scale_0 / rot_0"
            )
        self.path = path
        self.header = header
        self.data = ply.memmap(path, header)
        self.total = int(len(self.data))

    def close(self) -> None:
        """Drop the mapping. On Windows an open one blocks the next `train/` reset."""
        self.data = None  # type: ignore[assignment]

    def mask_chunk(self, lo: int, hi: int, volumes: Sequence[Volume]) -> np.ndarray:
        rows = self.data[lo:hi]
        xyz = np.stack([rows["x"], rows["y"], rows["z"]], axis=1).astype(np.float64)
        return keep_mask(xyz, volumes)

    def pack_chunk(self, mask: np.ndarray, lo: int, hi: int) -> bytes:
        """The kept records of [lo, hi) as raw bytes of the source's own dtype."""
        return self.data[lo:hi][mask[lo:hi]].tobytes()

    def header_bytes(self, count: int) -> bytes:
        return _header_bytes(self.path, self.header, count)


def apply_crop(
    src: Path, dst: Path, volumes: Sequence[Volume],
    progress: Optional[ProgressFn] = None,
    should_abort: Optional[AbortFn] = None,
) -> dict:
    """Write the gaussians of `src` that survive `volumes` to `dst`, in one call.

    The synchronous composition of `Source`'s two passes. `step_crop` drives
    them itself so it can report between chunks; this is what a test or a
    one-shot script wants, and it is the definition the two of them share.
    """
    started = time.perf_counter()
    source = Source(src)

    def _abort() -> None:
        if should_abort and should_abort():
            raise CropAborted("crop aborted by user")

    try:
        mask = np.empty(source.total, dtype=bool)
        for lo in range(0, source.total, CHUNK):
            _abort()
            hi = min(lo + CHUNK, source.total)
            mask[lo:hi] = source.mask_chunk(lo, hi, volumes)
            if progress:
                progress(0.5 * hi / max(source.total, 1))

        kept = check_kept(int(mask.sum()))

        tmp = begin_write(dst)
        try:
            with open(tmp, "wb") as out:
                out.write(source.header_bytes(kept))
                for lo in range(0, source.total, CHUNK):
                    _abort()
                    hi = min(lo + CHUNK, source.total)
                    out.write(source.pack_chunk(mask, lo, hi))
                    if progress:
                        progress(0.5 + 0.5 * hi / max(source.total, 1))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        source.close()

    ply.finalise(tmp, dst)
    if progress:
        progress(1.0)
    return result_of(source.total, kept, started, dst)


def check_kept(kept: int) -> int:
    """Refuse a crop that keeps nothing, before anything is written.

    An empty result is always a mistake - a volume dropped behind the camera, a
    `keep` box left where a `delete` sphere was meant - and writing it would
    hand step 5 a valid PLY of zero gaussians to spend four minutes meshing.
    """
    if kept == 0:
        raise CropError(
            "the crop keeps no gaussians at all - the volumes select nothing of "
            "this splat. Nothing was written, so the trained result is untouched"
        )
    return kept


def finalise_to(tmp: Path, dst: Path) -> None:
    """Move the finished `.part` onto its real name, retrying a held handle."""
    ply.finalise(tmp, dst)


def begin_write(dst: Path) -> Path:
    """The `.part` to write into. A half-written file under a name step 5 reads
    is worse than no file at all, so the real name is only ever a rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst.with_suffix(dst.suffix + ".part")


def result_of(total: int, kept: int, started: float, dst: Path) -> dict:
    return {
        "source_count": total,
        "kept": kept,
        "removed": total - kept,
        "seconds": round(time.perf_counter() - started, 2),
        "bytes": dst.stat().st_size,
    }
