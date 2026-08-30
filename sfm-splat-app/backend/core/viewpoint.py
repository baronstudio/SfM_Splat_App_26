"""viewpoint.py — the saved camera of the splat preview (CLAUDE.md §7.6d).

One thing, stored once, used in two places: the camera the user parked the
viewer at on step 4, and what the export writes beside — or inside — the file it
hands somebody else.

── Which frame it is stored in, which is the only subtle part ───────────────

The viewer draws everything under one `Rx-90` scene root plus the "Flip up"
toggle (`frontend/.../frame.ts`, §7.3), so a camera read straight off
`viewer.camera` is in **viewer** space and would mean a different thing the next
time the scene was opened with the flip the other way — exactly the trap the
crop volumes are stored in the dataset frame to avoid (§7.6b).

So a viewpoint is stored in the **dataset** frame: the +Z-up frame spirula's
mapper wrote, the frame `x, y, z` in the PLY are in, and the frame the crop
volumes already use. `viewpoint.ts` converts on both sides; nothing here has to
know about three.js.

── What "used on export" means, per format ─────────────────────────────────

* A native `.ply` carries it in its **header**, as `comment viewpoint …` lines.
  PLY comments are part of the format and every reader skips the ones it does
  not know, so this costs a few dozen bytes and breaks nothing.
* Every other format — `.splat`, and the three `splat-transform` writes — has
  nowhere to put it, so the export writes a `<name>.viewpoint.json` **beside**
  the file. A sidecar is honest about what it is; inventing a private container
  would not be.

Pure module: no FastAPI import (§2.4), and no numpy either — this is six floats
and a bool.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

#: The one place the sidecar's name is built. `.viewpoint.json` rather than
#: `.json`, so a drawer holding five exports never leaves anybody guessing which
#: file a bare `slug_sh0.json` belongs to.
SIDECAR_SUFFIX = ".viewpoint.json"

#: What the header comments and the sidecar both say about the numbers below.
FRAME_NOTE = "dataset frame (+Z up), the frame spirula wrote"


class ViewpointError(ValueError):
    """A stored viewpoint this module refuses to use."""


def _triple(raw: Any, name: str) -> tuple[float, float, float]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3:
        raise ViewpointError(f"viewpoint.{name}: expected three numbers")
    out = []
    for value in raw:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ViewpointError(f"viewpoint.{name}: expected three numbers")
        value = float(value)
        if value != value or value in (float("inf"), float("-inf")):
            raise ViewpointError(f"viewpoint.{name}: not finite")
        out.append(value)
    return (out[0], out[1], out[2])


@dataclass(frozen=True)
class Viewpoint:
    """Where the user parked the camera, in the dataset frame."""

    position: tuple[float, float, float]
    target: tuple[float, float, float]
    #: The camera's up vector. Stored rather than assumed: the viewer's up is
    #: three.js's +Y, which is dataset +Z or -Z depending on "Flip up".
    up: tuple[float, float, float]
    #: Vertical field of view, degrees — three.js's `PerspectiveCamera.fov`.
    fov_y: float
    #: Which vertical was on when it was saved. Informative: the numbers above
    #: are already frame-independent, and this is what lets the viewer restore
    #: the toggle as well as the camera.
    flip_up: bool = False
    saved_at: Optional[str] = None

    def as_dict(self) -> dict:
        return {
            "position": list(self.position),
            "target": list(self.target),
            "up": list(self.up),
            "fov_y": self.fov_y,
            "flip_up": self.flip_up,
            "saved_at": self.saved_at,
        }

    # ── The two ways it reaches a file ──────────────────────────────────────

    def ply_comments(self) -> list[str]:
        """The `comment` lines a native PLY export carries in its header.

        Deliberately one key per line and space-separated: a PLY comment is
        free text, so the only thing that makes it readable by anything else is
        being trivially parseable — `comment viewpoint position X Y Z`.
        """
        def fmt(v: Sequence[float]) -> str:
            return " ".join(f"{c:.6g}" for c in v)

        lines = [
            f"viewpoint frame {FRAME_NOTE}",
            f"viewpoint position {fmt(self.position)}",
            f"viewpoint target {fmt(self.target)}",
            f"viewpoint up {fmt(self.up)}",
            f"viewpoint fov_y {self.fov_y:.6g}",
        ]
        if self.saved_at:
            lines.append(f"viewpoint saved_at {self.saved_at}")
        return lines

    def sidecar(self, target_file: Path) -> dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "file": target_file.name,
            "frame": FRAME_NOTE,
            **self.as_dict(),
        }


def parse(raw: Any) -> Optional[Viewpoint]:
    """A stored viewpoint, or None when there is none.

    Raises `ViewpointError` for a value that is there and unusable, because an
    export that silently drops the camera the user saved is the failure this
    whole feature exists to avoid.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ViewpointError("viewpoint: expected an object")

    position = _triple(raw.get("position"), "position")
    target = _triple(raw.get("target"), "target")
    up = _triple(raw.get("up"), "up") if raw.get("up") is not None else (0.0, 0.0, 1.0)

    if position == target:
        raise ViewpointError("viewpoint: the camera is standing on its target")

    fov = raw.get("fov_y", 50.0)
    if not isinstance(fov, (int, float)) or isinstance(fov, bool):
        raise ViewpointError("viewpoint.fov_y: expected a number")
    fov = float(fov)
    if not 1.0 <= fov <= 179.0:
        raise ViewpointError(f"viewpoint.fov_y: {fov:g} is not a usable field of view")

    saved_at = raw.get("saved_at")
    return Viewpoint(
        position=position, target=target, up=up, fov_y=fov,
        flip_up=bool(raw.get("flip_up")),
        saved_at=saved_at if isinstance(saved_at, str) else None,
    )


def from_settings(settings: dict) -> Optional[Viewpoint]:
    """The viewpoint out of `settings_json`, or None.

    `viewpoint` is layer 3 only, with no `defaults.json` counterpart — for the
    crop volumes' reason (§7.6b): a camera parked in front of *this* scene is
    not a default anything could inherit.
    """
    raw = settings.get("viewpoint") if isinstance(settings, dict) else None
    return parse(raw)


def write_sidecar(view: Viewpoint, target_file: Path) -> Path:
    """Write `<name>.viewpoint.json` beside an exported file. Returns its path."""
    path = target_file.with_name(target_file.name + SIDECAR_SUFFIX)
    path.write_text(
        json.dumps(view.sidecar(target_file), indent=2), encoding="utf-8",
    )
    return path


def describe(view: Viewpoint) -> str:
    """One line for the log, in the dataset frame's own numbers."""
    pos = ", ".join(f"{c:.3g}" for c in view.position)
    tgt = ", ".join(f"{c:.3g}" for c in view.target)
    return f"({pos}) looking at ({tgt}), {view.fov_y:.0f}° vertical"
