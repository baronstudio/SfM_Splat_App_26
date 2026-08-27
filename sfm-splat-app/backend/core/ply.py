"""PLY reading and preview conversion - pure, no FastAPI (CLAUDE.md 2.4).

Two kinds of PLY reach this module and they share nothing but the header:

* the RC sparse cloud - **ASCII**, `x y z` plus `uchar red green blue`,
  a few hundred thousand points, ~18 MB;
* the LichtFeld splat - **binary little-endian**, 62 float properties per
  vertex (SH degree 3), five million of them, **1.24 GB** on disk.

Neither can be handed to a browser as it is, so the viewer never reads the
source file: it reads a decimated preview written next to it. Gaussians go out
as the 32-byte `.splat` record every web splat viewer understands; point clouds
go out as a 16-byte record of our own (`PC3D`), which is exactly what an
interleaved `THREE.Points` geometry wants.

Decimation is a uniform spread over the whole file, never a head slice: a PLY
is not shuffled, and the first N points of an RC cloud show one corner of the
scene while claiming to show the scene.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

# Zeroth-order spherical harmonic. The DC term of a gaussian is not a colour
# until it goes through it - skipping it is the classic washed-out preview.
SH_C0 = 0.28209479177387814

KIND_SPLAT = "splat"
KIND_CLOUD = "cloud"

CLOUD_MAGIC = b"PC3D"
CLOUD_VERSION = 1
CLOUD_HEADER_BYTES = 16
CLOUD_RECORD_BYTES = 16   # 3 float32 position + 4 uint8 rgba
SPLAT_RECORD_BYTES = 32   # 3 float32 pos + 3 float32 scale + 4 uint8 rgba + 4 uint8 rot

# Present together on a gaussian and on nothing else.
_SPLAT_MARKERS = ("f_dc_0", "opacity", "scale_0", "rot_0")

_SPLAT_FIELDS = (
    "x", "y", "z",
    "scale_0", "scale_1", "scale_2",
    "f_dc_0", "f_dc_1", "f_dc_2",
    "opacity",
    "rot_0", "rot_1", "rot_2", "rot_3",
)

_TYPES = {
    "char": "i1", "int8": "i1",
    "uchar": "u1", "uint8": "u1",
    "short": "i2", "int16": "i2",
    "ushort": "u2", "uint16": "u2",
    "int": "i4", "int32": "i4",
    "uint": "u4", "uint32": "u4",
    "float": "f4", "float32": "f4",
    "double": "f8", "float64": "f8",
}

# RC writes `red/green/blue`; other exporters disagree. First hit wins.
_RGB_ALIASES = (
    ("red", "green", "blue"),
    ("r", "g", "b"),
    ("diffuse_red", "diffuse_green", "diffuse_blue"),
)

_CHUNK = 262_144  # vertices per read/write pass - bounds memory, paces progress

ProgressFn = Callable[[float], None]


class PlyError(ValueError):
    """Malformed or unsupported PLY."""


@dataclass(frozen=True)
class PlyHeader:
    fmt: str                              # ascii | binary_little_endian | binary_big_endian
    count: int
    props: tuple[tuple[str, str], ...]    # (name, numpy type code), declaration order
    data_offset: int                      # byte offset of the first vertex

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.props)

    @property
    def kind(self) -> str:
        names = set(self.names)
        return KIND_SPLAT if all(m in names for m in _SPLAT_MARKERS) else KIND_CLOUD

    @property
    def is_ascii(self) -> bool:
        return self.fmt == "ascii"

    @property
    def dtype(self) -> np.dtype:
        order = ">" if self.fmt == "binary_big_endian" else "<"
        return np.dtype([(name, order + code) for name, code in self.props])

    def rgb_props(self) -> Optional[tuple[str, str, str]]:
        names = set(self.names)
        for alias in _RGB_ALIASES:
            if all(a in names for a in alias):
                return alias
        return None


def read_header(path: Path) -> PlyHeader:
    """Parse the header of `path`, vertex element only.

    A `face` element after the vertices is legal and simply ignored: the reader
    never walks past the vertex block.
    """
    blob = b""
    with open(path, "rb") as fh:
        while b"end_header" not in blob:
            chunk = fh.read(8192)
            if not chunk:
                raise PlyError(f"{path.name}: no end_header - not a PLY file")
            blob += chunk
            if len(blob) > 1 << 20:
                raise PlyError(f"{path.name}: header longer than 1 MB")

    if not blob.startswith(b"ply"):
        raise PlyError(f"{path.name}: missing the 'ply' magic")

    end = blob.index(b"end_header")
    newline = blob.index(b"\n", end)
    lines = blob[:end].decode("ascii", "replace").splitlines()

    fmt = ""
    count = 0
    props: list[tuple[str, str]] = []
    element: Optional[str] = None

    for line in lines:
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "format" and len(tok) >= 2:
            fmt = tok[1]
        elif tok[0] == "element" and len(tok) >= 3:
            element = tok[1]
            if element == "vertex":
                count = int(tok[2])
        elif tok[0] == "property" and element == "vertex":
            if tok[1] == "list":
                raise PlyError(f"{path.name}: list property on the vertex element")
            code = _TYPES.get(tok[1])
            if code is None:
                raise PlyError(f"{path.name}: unknown property type {tok[1]!r}")
            props.append((tok[-1], code))

    if fmt not in ("ascii", "binary_little_endian", "binary_big_endian"):
        raise PlyError(f"{path.name}: unsupported format {fmt!r}")
    if not props:
        raise PlyError(f"{path.name}: no vertex element")

    return PlyHeader(fmt=fmt, count=count, props=tuple(props), data_offset=newline + 1)


# -- Selection ---------------------------------------------------------------

def selection(total: int, max_count: Optional[int]) -> Optional[np.ndarray]:
    """Uniformly spread indices over [0, total), or None when nothing is dropped."""
    if not max_count or max_count <= 0 or max_count >= total:
        return None
    return np.unique(np.linspace(0, total - 1, max_count).round().astype(np.int64))


# -- Encoders ----------------------------------------------------------------

_SPLAT_DTYPE = np.dtype([
    ("pos", "<f4", 3),
    ("scale", "<f4", 3),
    ("rgba", "u1", 4),
    ("rot", "u1", 4),
])

_CLOUD_DTYPE = np.dtype([
    ("pos", "<f4", 3),
    ("rgba", "u1", 4),
])


def _encode_splat(cols: dict[str, np.ndarray]) -> bytes:
    """Gaussian columns -> the 32-byte `.splat` record.

    Scales are stored logged and opacity logit-ed by every 3DGS trainer; the
    `.splat` record wants them linear, so the exp and the sigmoid happen here
    and not in the viewer.
    """
    n = cols["x"].size
    out = np.zeros(n, dtype=_SPLAT_DTYPE)

    for i, axis in enumerate(("x", "y", "z")):
        out["pos"][:, i] = np.nan_to_num(cols[axis], nan=0.0, posinf=0.0, neginf=0.0)

    scale = np.stack([cols[f"scale_{i}"] for i in range(3)], axis=1)
    out["scale"] = np.exp(np.clip(np.nan_to_num(scale, nan=-10.0), -30.0, 20.0))

    dc = np.stack([cols[f"f_dc_{i}"] for i in range(3)], axis=1)
    out["rgba"][:, :3] = np.clip((0.5 + SH_C0 * np.nan_to_num(dc)) * 255.0, 0, 255)
    opacity = np.clip(np.nan_to_num(cols["opacity"]), -30.0, 30.0)
    out["rgba"][:, 3] = np.clip(255.0 / (1.0 + np.exp(-opacity)), 0, 255)

    # Quaternion stored w,x,y,z and quantised as q*128+128 - the convention the
    # antimatter15 .splat format fixed and every reader of it expects.
    quat = np.stack([cols[f"rot_{i}"] for i in range(4)], axis=1)
    quat = np.nan_to_num(quat)
    norm = np.linalg.norm(quat, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    out["rot"] = np.clip(quat / norm * 128.0 + 128.0, 0, 255)

    return out.tobytes()


def _encode_cloud(xyz: np.ndarray, rgb: Optional[np.ndarray]) -> bytes:
    """Positions (n,3) and uint8 colours (n,3) -> the 16-byte `PC3D` record."""
    n = xyz.shape[0]
    out = np.zeros(n, dtype=_CLOUD_DTYPE)
    out["pos"] = np.nan_to_num(xyz, nan=0.0, posinf=0.0, neginf=0.0)
    out["rgba"][:, :3] = 190 if rgb is None else rgb
    out["rgba"][:, 3] = 255
    return out.tobytes()


def cloud_header(count: int) -> bytes:
    return CLOUD_MAGIC + np.array(
        [CLOUD_VERSION, count, CLOUD_RECORD_BYTES], dtype="<u4"
    ).tobytes()


# -- Readers -----------------------------------------------------------------

def _memmap(path: Path, header: PlyHeader) -> np.ndarray:
    """The vertex block as a structured array, without loading it."""
    itemsize = header.dtype.itemsize
    available = path.stat().st_size - header.data_offset
    usable = min(header.count, available // itemsize)
    if usable <= 0:
        raise PlyError(f"{path.name}: header claims {header.count} vertices, file holds none")
    return np.memmap(path, dtype=header.dtype, mode="r", offset=header.data_offset,
                     shape=(int(usable),))


def _ascii_columns(
    path: Path, header: PlyHeader, wanted: Sequence[str], keep: Optional[np.ndarray],
    on_chunk: Callable[[np.ndarray], None], progress: Optional[ProgressFn],
) -> int:
    """Stream the ASCII body, parsing only `wanted` columns of the kept rows.

    Line by line rather than `np.loadtxt`: on a five-million-line body the
    latter builds the whole float table in memory before anything is written,
    and we are here precisely because the file does not fit.
    """
    index = {name: i for i, (name, _) in enumerate(header.props)}
    missing = [w for w in wanted if w not in index]
    if missing:
        raise PlyError(f"{path.name}: missing properties " + ", ".join(missing))
    cols = [index[w] for w in wanted]
    last_col = max(cols)
    keep_set = None if keep is None else set(keep.tolist())

    buffer: list[list[float]] = []
    written = 0

    def flush() -> None:
        nonlocal buffer, written
        if not buffer:
            return
        on_chunk(np.asarray(buffer, dtype=np.float64))
        written += len(buffer)
        buffer = []

    with open(path, "rb") as fh:
        fh.seek(header.data_offset)
        for i, raw in enumerate(fh):
            if i >= header.count:
                break
            if keep_set is not None and i not in keep_set:
                continue
            tok = raw.split()
            if len(tok) <= last_col:
                continue
            buffer.append([float(tok[c]) for c in cols])
            if len(buffer) >= _CHUNK:
                flush()
                if progress:
                    progress(min(0.99, (i + 1) / max(header.count, 1)))
    flush()
    return written


def _iter_rows(data: np.ndarray, keep: Optional[np.ndarray], progress: Optional[ProgressFn]):
    """Yield (offset, rows) in bounded chunks, honouring the selection."""
    n = int(keep.size) if keep is not None else len(data)
    for lo in range(0, n, _CHUNK):
        hi = min(lo + _CHUNK, n)
        rows = data[lo:hi] if keep is None else data[keep[lo:hi]]
        yield lo, rows
        if progress:
            progress(min(0.99, hi / max(n, 1)))


# -- Conversion --------------------------------------------------------------

def _finalise(tmp: Path, dst: Path) -> None:
    """Move the finished `.part` onto its final name.

    Windows refuses to rename onto a handle another process still holds - an
    antivirus mid-scan, an indexer, a download the browser abandoned - and the
    answer is `[WinError 5] Access denied`, not a sharing violation. Preview
    names now carry the source fingerprint (`core/preview.py`) so the target is
    normally absent, but a couple of retries cover the transient holders, and
    the `.part` goes rather than sitting in the cache directory forever.
    """
    last: Optional[OSError] = None
    for delay in (0.0, 0.2, 0.5):
        if delay:
            time.sleep(delay)
        try:
            tmp.replace(dst)
            return
        except PermissionError as exc:
            last = exc
    try:
        tmp.unlink()
    except OSError:
        pass
    raise PlyError(
        f"{dst.name}: another process is holding the previous preview open "
        f"({last})"
    )


def convert_ply(
    src: Path, dst: Path, max_count: Optional[int] = None,
    progress: Optional[ProgressFn] = None,
) -> dict:
    """Write a decimated preview of `src` to `dst`. Returns its metadata."""
    header = read_header(src)
    kind = header.kind
    total = header.count
    keep = selection(total, max_count)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")

    if kind == KIND_SPLAT:
        written = _write_splat(src, tmp, header, keep, progress)
    else:
        written = _write_cloud(src, tmp, header, keep, progress)

    _finalise(tmp, dst)
    if progress:
        progress(1.0)
    return {
        "kind": kind,
        "total": total,
        "count": written,
        "record_bytes": SPLAT_RECORD_BYTES if kind == KIND_SPLAT else CLOUD_RECORD_BYTES,
        "bytes": dst.stat().st_size,
        "decimated": written < total,
    }


def _write_splat(src: Path, tmp: Path, header: PlyHeader,
                 keep: Optional[np.ndarray], progress: Optional[ProgressFn]) -> int:
    written = 0
    with open(tmp, "wb") as out:
        if header.is_ascii:
            def on_chunk(table: np.ndarray) -> None:
                nonlocal written
                cols = {name: table[:, i] for i, name in enumerate(_SPLAT_FIELDS)}
                out.write(_encode_splat(cols))
                written += table.shape[0]
            _ascii_columns(src, header, _SPLAT_FIELDS, keep, on_chunk, progress)
        else:
            missing = [f for f in _SPLAT_FIELDS if f not in header.names]
            if missing:
                raise PlyError(f"{src.name}: missing {', '.join(missing)}")
            data = _memmap(src, header)
            try:
                for _, rows in _iter_rows(data, keep, progress):
                    cols = {f: np.asarray(rows[f], dtype=np.float32) for f in _SPLAT_FIELDS}
                    out.write(_encode_splat(cols))
                    written += len(rows)
            finally:
                del data
    return written


def _write_cloud(src: Path, tmp: Path, header: PlyHeader,
                 keep: Optional[np.ndarray], progress: Optional[ProgressFn]) -> int:
    rgb_props = header.rgb_props()
    fields = ("x", "y", "z") + (rgb_props or ())
    written = 0
    planned = header.count if keep is None else int(keep.size)

    with open(tmp, "wb") as out:
        out.write(cloud_header(planned))
        if header.is_ascii:
            def on_chunk(table: np.ndarray) -> None:
                nonlocal written
                rgb = None
                if rgb_props:
                    rgb = np.clip(table[:, 3:6], 0, 255).astype(np.uint8)
                out.write(_encode_cloud(table[:, :3].astype(np.float32), rgb))
                written += table.shape[0]
            _ascii_columns(src, header, fields, keep, on_chunk, progress)
        else:
            data = _memmap(src, header)
            try:
                for _, rows in _iter_rows(data, keep, progress):
                    xyz = np.stack([np.asarray(rows[a], dtype=np.float32)
                                    for a in ("x", "y", "z")], axis=1)
                    rgb = _as_uint8_rgb(rows, rgb_props) if rgb_props else None
                    out.write(_encode_cloud(xyz, rgb))
                    written += len(rows)
            finally:
                del data

        # The count is the first thing the viewer reads; a short body (a row
        # skipped, a truncated source) must not leave it lying.
        if written != planned:
            out.seek(len(CLOUD_MAGIC) + 4)
            out.write(np.array([written], dtype="<u4").tobytes())
    return written


def _as_uint8_rgb(rows: np.ndarray, rgb_props: tuple[str, str, str]) -> np.ndarray:
    """Colours as 0-255 bytes, whether the file stored bytes or floats."""
    channels = []
    for name in rgb_props:
        col = np.asarray(rows[name])
        if col.dtype.kind == "f":
            col = col * 255.0 if float(col.max(initial=0.0)) <= 1.0 else col
        channels.append(np.clip(col, 0, 255))
    return np.stack(channels, axis=1).astype(np.uint8)


# -- Already-a-splat passthrough ---------------------------------------------

def convert_splat_file(
    src: Path, dst: Path, max_count: Optional[int] = None,
    progress: Optional[ProgressFn] = None,
) -> dict:
    """Decimate a `.splat` file - same record in, same record out."""
    total = src.stat().st_size // SPLAT_RECORD_BYTES
    if total == 0:
        raise PlyError(f"{src.name}: empty splat file")
    data = np.memmap(src, dtype=np.dtype((np.uint8, SPLAT_RECORD_BYTES)), mode="r",
                     shape=(int(total),))
    keep = selection(int(total), max_count)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".part")
    written = 0
    try:
        with open(tmp, "wb") as out:
            for _, rows in _iter_rows(data, keep, progress):
                out.write(np.ascontiguousarray(rows).tobytes())
                written += len(rows)
    finally:
        del data
    _finalise(tmp, dst)
    if progress:
        progress(1.0)
    return {
        "kind": KIND_SPLAT, "total": int(total), "count": written,
        "record_bytes": SPLAT_RECORD_BYTES, "bytes": dst.stat().st_size,
        "decimated": written < total,
    }


def describe(src: Path) -> dict:
    """Kind and vertex count of `src`, from the header alone."""
    if src.suffix.lower() == ".splat":
        return {"kind": KIND_SPLAT, "total": src.stat().st_size // SPLAT_RECORD_BYTES}
    header = read_header(src)
    return {"kind": header.kind, "total": header.count}


def convert(src: Path, dst: Path, max_count: Optional[int] = None,
            progress: Optional[ProgressFn] = None) -> dict:
    """Build the preview for whichever of the two source formats `src` is."""
    if src.suffix.lower() == ".splat":
        return convert_splat_file(src, dst, max_count, progress)
    return convert_ply(src, dst, max_count, progress)


def read_xyz(src: Path, max_count: Optional[int] = None) -> np.ndarray:
    """The `x y z` columns of a PLY as an (n, 3) float64 array.

    Both bodies the pipeline produces are covered — RS's ASCII sparse cloud and
    a binary gaussian PLY — because the caller (`rc_region.coverage`) is handed
    whichever file the project happens to hold.

    `max_count` decimates by the same uniform spread the preview uses: a
    coverage ratio is a statistic, and a million points answer it exactly as
    well as five million while costing a fifth of the read.
    """
    header = read_header(src)
    keep = selection(header.count, max_count)

    if header.is_ascii:
        chunks: list[np.ndarray] = []
        _ascii_columns(src, header, ("x", "y", "z"), keep, chunks.append, None)
        if not chunks:
            return np.empty((0, 3), dtype=np.float64)
        return np.concatenate(chunks, axis=0)

    data = _memmap(src, header)
    rows = data if keep is None else data[keep[keep < len(data)]]
    return np.stack(
        [np.asarray(rows["x"], dtype=np.float64),
         np.asarray(rows["y"], dtype=np.float64),
         np.asarray(rows["z"], dtype=np.float64)],
        axis=1,
    )
