"""splat_export.py — the deliverable copy of a trained splat (CLAUDE.md §7.6c).

The trained `splat.ply` is a **working file**: 62 float properties per vertex,
248 bytes each, 177 542 251 B for the 715 890 gaussians of the reference run.
That is the right thing for step 5 to mesh and the wrong thing to hand anybody —
a web viewer, a client, a phone. So this module writes a *third* file, beside
the trained splat and beside the crop, and nothing in the pipeline ever reads
it back.

That last sentence is the whole design. A crop is **pipeline data**: step 5 asks
`resolve_splat` for it and meshes what it returns. An export is **terminal**: it
is smaller than its source on purpose, by throwing away things the mesher's
colour pass wants, and `resolve_splat` must never find it. Hence a
directory of its own, `train/export/`, and filenames that carry what was done to
them rather than the name `splat.ply` that both readers look for.

── What can actually be reduced, measured ──────────────────────────────────

On `soupirail_alfredriom`, 715 890 gaussians, 62 properties, 248 B/vertex,
177 542 251 B (2026-08-30):

| Reduction                    | Result                        | Cost   |
|------------------------------|-------------------------------|--------|
| SH 3 → 0 (drop 45 `f_rest_*`)| 68 B/vertex, 48 680 936 B     | 0.29 s |
| SH 3 → 1 (keep 9 of 45)      | 104 B/vertex, 74 453 198 B    | 0.48 s |
| `.splat` 32 B record         | ~22.9 MB, 7.75x               | —      |
| opacity floor alpha >= 0.005 | drops **1.2 %**               | —      |
| opacity floor alpha >= 0.05  | drops **43.2 %**              | —      |

Two findings behind the defaults below:

* **The `f_rest` layout is channel-major** (`f_rest_{c*15+k}`, the INRIA
  convention), verified rather than assumed: the per-index RMS profile of the 45
  coefficients repeats with period **15**, not 3 — blocks
  `0.0632 0.0810 0.0641…` / `0.0630 0.0787 0.0635…` / `0.0641 0.0792 0.0651…`.
  So a degree cut is a clean subset of columns and never a re-ordering.

* **Spirula's gaussians are low-opacity by construction** — median linear alpha
  **0.059** under `--opacity-reg 0.01` against a 1 M cap. The 1/255 prune
  threshold every other 3DGS toolchain ships as free housekeeping drops 1.2 % of
  this file, and anything high enough to matter is a real edit of the picture.
  So the opacity floor ships **off**, and the panel shows the count it would
  drop before it runs rather than after.

── Two families of format, one pipeline ────────────────────────────────────

`ply` and `splat` are ours: numpy over a memory map, no dependency, the same
two-pass chunked shape `crop.py` uses and for the same reason (a PLY states its
vertex count *before* its vertices, so nothing can be written until every
gaussian has been tested).

`sog`, `spz` and `compressed-ply` are `@playcanvas/splat-transform`'s, and they
are reached by writing the reduced PLY first and converting it in a second pass
(`core/steps/splat_transform.py`). That ordering is not laziness: it means the
reduction knobs below behave identically in every format, and it means the one
file the external tool ever sees is one we just wrote.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

import numpy as np

from backend.core import crop, ply

# ── Formats ─────────────────────────────────────────────────────────────────

FORMAT_PLY = "ply"
FORMAT_SPLAT = "splat"
FORMAT_SOG = "sog"
FORMAT_SPZ = "spz"
FORMAT_COMPRESSED_PLY = "compressed-ply"

#: Written by this module alone, with nothing installed.
NATIVE_FORMATS = (FORMAT_PLY, FORMAT_SPLAT)
#: Written by `@playcanvas/splat-transform` from a PLY this module writes first.
EXTERNAL_FORMATS = (FORMAT_SOG, FORMAT_SPZ, FORMAT_COMPRESSED_PLY)
FORMATS = NATIVE_FORMATS + EXTERNAL_FORMATS

#: File extension per format. `compressed-ply` keeps PlayCanvas's own double
#: extension, which is what tells a reader it is compressed — a bare `.ply`
#: holding chunked quantised columns is the one genuinely confusing artefact
#: this feature could produce.
SUFFIXES = {
    FORMAT_PLY: ".ply",
    FORMAT_SPLAT: ".splat",
    FORMAT_SOG: ".sog",
    FORMAT_SPZ: ".spz",
    FORMAT_COMPRESSED_PLY: ".compressed.ply",
}

SELECT_IMPORTANCE = "importance"
SELECT_UNIFORM = "uniform"
SELECTIONS = (SELECT_IMPORTANCE, SELECT_UNIFORM)

#: Vertices per pass. `crop.CHUNK`'s figure, for `crop.CHUNK`'s reason.
CHUNK = 262_144

#: Coefficients per colour channel in a degree-3 SH block. Read off the file
#: rather than assumed — `_sh_layout` divides the `f_rest_*` count by three.
_SH_DEGREES = (0, 1, 2, 3)

ProgressFn = Callable[[float], None]
AbortFn = Callable[[], bool]


class SplatExportError(ValueError):
    """A request, or a source file, this module refuses."""


class SplatExportAborted(RuntimeError):
    """The cooperative abort, observed between chunks."""


# ── The request ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExportPlan:
    """What to write, and what to leave out of it.

    Every field is a *reduction*: at its default the export is the trained splat
    byte for byte, in the trainer's own format. That matters more than it looks
    — "give me the file" has to be one obvious setting, not a combination.
    """

    format: str = FORMAT_PLY
    #: Highest SH band to keep, or None for "whatever the trainer wrote".
    #: 0 drops all 45 `f_rest_*` and with them 72.6 % of every vertex.
    sh_degree: Optional[int] = None
    #: Linear alpha floor, after the sigmoid. 0 keeps every gaussian.
    opacity_min: float = 0.0
    #: Target gaussian count. 0 keeps every one that survives the floor.
    max_count: int = 0
    #: How `max_count` chooses. `importance` ranks by alpha x ellipsoid volume;
    #: `uniform` is `ply.selection`'s even spread over the file.
    selection: str = SELECT_IMPORTANCE

    def validate(self) -> "ExportPlan":
        if self.format not in FORMATS:
            raise SplatExportError(
                f"unknown export format {self.format!r} — expected one of "
                f"{', '.join(FORMATS)}"
            )
        if self.sh_degree is not None and self.sh_degree not in _SH_DEGREES:
            raise SplatExportError(
                f"sh_degree {self.sh_degree!r} — expected 0, 1, 2, 3 or none"
            )
        if not (0.0 <= float(self.opacity_min) < 1.0):
            raise SplatExportError(
                f"opacity_min {self.opacity_min!r} is not a linear alpha in "
                f"[0, 1) — 0 keeps every gaussian"
            )
        if int(self.max_count) < 0:
            raise SplatExportError(
                f"max_count {self.max_count!r} is negative — 0 means no limit"
            )
        if self.selection not in SELECTIONS:
            raise SplatExportError(
                f"unknown selection {self.selection!r} — expected "
                f"{' or '.join(SELECTIONS)}"
            )
        return self

    @property
    def reduces_rows(self) -> bool:
        """Whether any gaussian can be dropped. Decides the cheap path."""
        return self.opacity_min > 0.0 or self.max_count > 0

    def as_dict(self) -> dict:
        return {
            "format": self.format,
            "sh_degree": self.sh_degree,
            "opacity_min": self.opacity_min,
            "max_count": self.max_count,
            "selection": self.selection,
        }


def plan_from_settings(settings: dict) -> ExportPlan:
    """The validated plan out of the `export` section of the settings."""
    section = settings.get("export") if isinstance(settings, dict) else None
    if not isinstance(section, dict):
        section = {}
    degree = section.get("sh_degree")
    return ExportPlan(
        format=str(section.get("format") or FORMAT_PLY),
        sh_degree=None if degree in (None, "", "keep") else int(degree),
        opacity_min=float(section.get("opacity_min") or 0.0),
        max_count=int(section.get("max_count") or 0),
        selection=str(section.get("selection") or SELECT_IMPORTANCE),
    ).validate()


# ── Spherical harmonics ─────────────────────────────────────────────────────

def _sh_layout(header: ply.PlyHeader) -> tuple[list[str], int]:
    """The `f_rest_*` property names in file order, and how many per channel.

    Returns `([], 0)` for a splat with no SH beyond the DC term, which is a
    legitimate file — `--sh-degree 0` is what the `meshing` preset trains at.
    """
    rest = [n for n in header.names if n.startswith("f_rest_")]
    if not rest:
        return [], 0
    if len(rest) % 3:
        raise SplatExportError(
            f"{len(rest)} f_rest_* properties, which is not three channels of "
            f"the same length — this file's SH block is not the INRIA layout "
            f"and dropping columns from it would silently re-colour the splat"
        )
    return rest, len(rest) // 3


def source_sh_degree(header: ply.PlyHeader) -> Optional[int]:
    """The SH degree the file carries, or None when it is not a whole one.

    `per_channel = (d + 1)^2 - 1`, so 0/3/8/15 coefficients are degrees 0/1/2/3.
    """
    _, per_channel = _sh_layout(header)
    for degree in _SH_DEGREES:
        if per_channel == (degree + 1) ** 2 - 1:
            return degree
    return None


def kept_properties(
    header: ply.PlyHeader, sh_degree: Optional[int],
) -> list[tuple[str, str, str]]:
    """`(output name, source name, type code)` of what an export keeps.

    **Channel-major**, `f_rest_{c * per_channel + k}`: measured on this build's
    own output (see the module docstring), and the reason the subset is a
    `k < (degree + 1)^2 - 1` test per channel rather than a head slice of the 45.
    A head slice would keep the first 15 columns — the whole of the red channel
    and nothing of green or blue — and produce a file that is not wrong so much
    as luridly red.

    **And the survivors are renumbered contiguously**, which is why this returns
    a source name beside the output one. Measured 2026-08-30: a degree-1 PLY
    written with the source's own indices — `f_rest_0,1,2,15,16,17,30,31,32` —
    was refused by `splat-transform` with `readPly: unrecognized f_rest_* count
    33`, because a reader sizes the SH block from the highest index it sees, not
    from how many properties there are. So a degree-1 export carries
    `f_rest_0..8` and the mapping back to columns 0, 1, 2, 15, 16, 17, 30, 31, 32
    lives here. The file that failed had the right 9 columns of data in it and
    was still unreadable by everything but us.
    """
    plain = [(name, name, code) for name, code in header.props]
    if sh_degree is None:
        return plain
    rest, per_channel = _sh_layout(header)
    if not rest:
        return plain

    wanted = (sh_degree + 1) ** 2 - 1
    if wanted >= per_channel:
        # Asking for more bands than the file holds is not an error: it is what
        # "keep degree 3" means against a splat trained at degree 0.
        return plain

    renamed = {
        f"f_rest_{channel * per_channel + k}": f"f_rest_{channel * wanted + k}"
        for channel in range(3)
        for k in range(wanted)
    }
    return [
        (renamed.get(name, name), name, code)
        for name, code in header.props
        if not name.startswith("f_rest_") or name in renamed
    ]


def header_text(
    count: int, props: list[tuple[str, str, str]],
    comments: Sequence[str] = (),
) -> bytes:
    """A minimal binary-little-endian PLY header for `props`.

    Unlike `crop.py` this cannot re-use the source's own header bytes: dropping
    columns means the property list itself changes, so the header is rebuilt
    from the subset. What is lost with it is whatever comments spirula wrote,
    and that is said out loud here rather than discovered later — an export at
    the source's own SH degree takes the verbatim path instead (`_copy_header`).

    `comments` is what the *export* adds back: a saved viewpoint (§7.6d), on
    both header routes, so the camera survives a degree cut as readily as a
    verbatim copy.
    """
    names = {"i1": "char", "u1": "uchar", "i2": "short", "u2": "ushort",
             "i4": "int", "u4": "uint", "f4": "float", "f8": "double"}
    lines = ["ply", "format binary_little_endian 1.0",
             f"element vertex {count}"]
    lines += [f"property {names[code]} {name}" for name, _src, code in props]
    lines += [f"comment {c}" for c in comments]
    lines.append("end_header")
    return ("\n".join(lines) + "\n").encode("ascii")


# ── Scoring ─────────────────────────────────────────────────────────────────

def linear_alpha(opacity: np.ndarray) -> np.ndarray:
    """The stored logit as the 0-1 opacity a viewer actually composites."""
    return 1.0 / (1.0 + np.exp(-np.clip(np.nan_to_num(opacity), -30.0, 30.0)))


def importance(alpha: np.ndarray, scales: np.ndarray) -> np.ndarray:
    """alpha x ellipsoid volume — the cheap stand-in for "how much is this seen".

    LightGaussian's significance score weights this by how many training rays
    actually hit each gaussian, which needs a rasteriser and therefore a whole
    trainer. Volume x opacity is what is left when you refuse that dependency,
    and it is honestly weaker: it favours large faint splats over small bright
    ones. Measured on the reference splat it does move in the right direction —
    keeping the top 50 % raises the mean alpha of what survives from 0.098 to
    0.128 — but the panel calls this option approximate for a reason.
    """
    return alpha * np.exp(np.clip(np.nan_to_num(scales, nan=-30.0), -30.0, 20.0)).prod(axis=1)


# ── The source ──────────────────────────────────────────────────────────────

class Source:
    """An open, memory-mapped splat PLY and the chunk operations on it.

    Same shape as `crop.Source`, and exposed the same way: the caller is an
    async pass that runs one chunk per executor hop so the event loop — and with
    it the WebSocket and the abort route — stays answerable through a long pure
    numpy phase (§15).
    """

    def __init__(self, path: Path):
        header = ply.read_header(path)
        if header.is_ascii:
            raise SplatExportError(
                f"{path.name}: ASCII PLY. spirula writes binary_little_endian "
                f"and the record copy this module relies on needs it"
            )
        if header.kind != ply.KIND_SPLAT:
            raise SplatExportError(
                f"{path.name}: not a gaussian PLY — no f_dc_0 / opacity / "
                f"scale_0 / rot_0"
            )
        self.path = path
        self.header = header
        self.data = ply.memmap(path, header)
        self.total = int(len(self.data))

    def close(self) -> None:
        """Drop the mapping. On Windows an open one blocks the next reset."""
        self.data = None  # type: ignore[assignment]

    # -- pass 1 --------------------------------------------------------------

    def alpha_chunk(self, lo: int, hi: int) -> np.ndarray:
        return linear_alpha(np.asarray(self.data[lo:hi]["opacity"], dtype=np.float64))

    def score_chunk(self, lo: int, hi: int) -> np.ndarray:
        rows = self.data[lo:hi]
        alpha = linear_alpha(np.asarray(rows["opacity"], dtype=np.float64))
        scales = np.stack(
            [np.asarray(rows[f"scale_{i}"], dtype=np.float64) for i in range(3)],
            axis=1,
        )
        return importance(alpha, scales)

    # -- pass 2 --------------------------------------------------------------

    def pack_chunk(
        self, mask: Optional[np.ndarray], lo: int, hi: int,
        out_dtype: Optional[np.dtype], props: list[tuple[str, str, str]],
    ) -> bytes:
        """The kept records of [lo, hi), as raw bytes of `out_dtype`.

        With `out_dtype is None` the records go out byte for byte in the
        source's own dtype, which is `crop.py`'s verbatim copy and what an
        export at the file's own SH degree does.
        """
        rows = self.data[lo:hi]
        if mask is not None:
            rows = rows[mask[lo:hi]]
        if out_dtype is None:
            return rows.tobytes()
        out = np.empty(len(rows), dtype=out_dtype)
        for name, source_name, _ in props:
            out[name] = rows[source_name]
        return out.tobytes()

    def splat_chunk(self, mask: Optional[np.ndarray], lo: int, hi: int) -> bytes:
        """The kept records of [lo, hi) as the 32-byte `.splat` record."""
        rows = self.data[lo:hi]
        if mask is not None:
            rows = rows[mask[lo:hi]]
        return ply.encode_splat({
            field: np.asarray(rows[field], dtype=np.float32)
            for field in ply.SPLAT_FIELDS
        })

    def copy_header(self, count: int, comments: Sequence[str] = ()) -> bytes:
        """The source's own header bytes with the vertex count changed."""
        return crop.header_with_count(self.path, self.header, count, comments)


# ── Row selection ───────────────────────────────────────────────────────────

def apply_target_count(
    scores: Optional[np.ndarray], mask: np.ndarray, plan: ExportPlan,
) -> np.ndarray:
    """Narrow `mask` down to `plan.max_count` rows. Returns the new mask.

    `importance` keeps the highest-scoring survivors; `uniform` keeps an even
    spread of them, which is `ply.selection`'s guarantee — the first N rows of a
    PLY are one corner of the scene, never a smaller picture of it.
    """
    kept = int(mask.sum())
    target = int(plan.max_count)
    if target <= 0 or target >= kept:
        return mask

    surviving = np.flatnonzero(mask)
    if plan.selection == SELECT_UNIFORM:
        chosen = surviving[ply.selection(kept, target)]
    else:
        assert scores is not None  # the caller computes them for this branch
        order = np.argpartition(scores[surviving], kept - target)[kept - target:]
        chosen = surviving[order]

    narrowed = np.zeros_like(mask)
    narrowed[chosen] = True
    return narrowed


def check_kept(kept: int) -> int:
    """Refuse an export of nothing, before a byte is written.

    `crop.check_kept`'s argument at one remove: an empty result is always a
    mistake — an opacity floor above every gaussian in the file — and writing it
    would hand somebody a valid PLY of zero splats and no explanation.
    """
    if kept == 0:
        raise SplatExportError(
            "the export keeps no gaussians at all — every one of them is below "
            "the opacity floor. Nothing was written, so the trained result is "
            "untouched"
        )
    return kept


# ── The one-shot composition ────────────────────────────────────────────────

def write_reduced(
    src: Path, dst: Path, plan: ExportPlan,
    progress: Optional[ProgressFn] = None,
    should_abort: Optional[AbortFn] = None,
    comments: Sequence[str] = (),
) -> dict:
    """Write the reduction of `src` described by `plan` to `dst`, in one call.

    The synchronous composition of the two passes. `step_splat_export` drives
    them itself so it can report between chunks; this is what a test or a
    one-shot script wants, and it is the definition the two of them share.

    `plan.format` is only consulted for the *record* — a `splat` plan writes the
    32-byte record here, and an external format is a `ply` written here and
    converted afterwards by the caller.
    """
    started = time.perf_counter()
    source = Source(src)

    def _abort() -> None:
        if should_abort and should_abort():
            raise SplatExportAborted("export aborted by user")

    try:
        total = source.total
        mask = np.ones(total, dtype=bool)
        scores: Optional[np.ndarray] = None
        wants_scores = plan.max_count > 0 and plan.selection == SELECT_IMPORTANCE

        if plan.reduces_rows:
            if wants_scores:
                scores = np.empty(total, dtype=np.float64)
            for lo in range(0, total, CHUNK):
                _abort()
                hi = min(lo + CHUNK, total)
                if wants_scores:
                    chunk = source.score_chunk(lo, hi)
                    scores[lo:hi] = chunk  # type: ignore[index]
                    alpha = source.alpha_chunk(lo, hi)
                else:
                    alpha = source.alpha_chunk(lo, hi)
                if plan.opacity_min > 0.0:
                    mask[lo:hi] = alpha >= plan.opacity_min
                if progress:
                    progress(0.02 + 0.48 * hi / max(total, 1))
            check_kept(int(mask.sum()))
            mask = apply_target_count(scores, mask, plan)

        kept = check_kept(int(mask.sum()))
        out_dtype, props = output_dtype(source.header, plan)
        selective = None if kept == total else mask

        tmp = begin_write(dst)
        try:
            with open(tmp, "wb") as out:
                if plan.format != FORMAT_SPLAT:
                    # `.splat` is a headerless stream of 32-byte records; a PLY
                    # carries either the source's own header (verbatim rows) or
                    # one rebuilt from the surviving property list.
                    out.write(
                        source.copy_header(kept, comments) if out_dtype is None
                        else header_text(kept, props, comments)
                    )
                for lo in range(0, total, CHUNK):
                    _abort()
                    hi = min(lo + CHUNK, total)
                    out.write(
                        source.splat_chunk(selective, lo, hi)
                        if plan.format == FORMAT_SPLAT
                        else source.pack_chunk(selective, lo, hi, out_dtype, props)
                    )
                    if progress:
                        progress(0.50 + 0.49 * hi / max(total, 1))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    finally:
        source.close()

    ply.finalise(tmp, dst)
    if progress:
        progress(1.0)
    return result_of(total, kept, started, dst, plan)


def output_dtype(
    header: ply.PlyHeader, plan: ExportPlan,
) -> tuple[Optional[np.dtype], list[tuple[str, str, str]]]:
    """The dtype an export writes its rows in, and the properties it keeps.

    A `None` dtype means **verbatim**, and that is not an optimisation but the
    guarantee: an export that drops no SH band is the trainer's own 62
    properties, bit for bit, including the ones nothing in this app understands.
    """
    props = kept_properties(header, plan.sh_degree)
    if plan.format == FORMAT_SPLAT or len(props) == len(header.props):
        # Nothing dropped means nothing renamed either, so the verbatim path is
        # safe: `kept_properties` only renumbers columns it is also thinning.
        return None, props
    return np.dtype([(name, "<" + code) for name, _src, code in props]), props


def begin_write(dst: Path) -> Path:
    """The `.part` to write into — the real name is only ever a rename."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    return dst.with_suffix(dst.suffix + ".part")


def result_of(total: int, kept: int, started: float, dst: Path,
              plan: ExportPlan) -> dict:
    return {
        "source_count": total,
        "count": kept,
        "removed": total - kept,
        "seconds": round(time.perf_counter() - started, 2),
        "bytes": dst.stat().st_size,
        "plan": plan.as_dict(),
    }
