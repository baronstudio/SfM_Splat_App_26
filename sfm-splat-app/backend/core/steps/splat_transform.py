"""splat_transform.py — the one place that builds a `splat-transform` command.

`spirula.py` is this module's twin, and the pattern is deliberately identical:
one resolver that fails with the path it looked for, one command builder, one
version reader. What differs is that this tool is **optional**. The three
compressed formats it writes — SOG, SPZ and PlayCanvas's compressed PLY — are
the only thing in this app that needs it, and a workstation that never exports
one never has to install it.

── Why this tool and not a Python package ──────────────────────────────────

Both obvious Python routes were checked on 2026-08-30 and both are refused:

* **`spz` (PyPI, MIT/Apache-2.0)** publishes exactly one wheel,
  `cp313-manylinux_2_34_x86_64`. There is no Windows wheel, so installing it
  here means building a Rust crate from the sdist — a *build* toolchain on the
  target machine, which is the thing CLAUDE.md §5.1 refuses for spirula itself.

* **`sogs` (PyPI, Apache-2.0)** declares `numpy, pillow, plyfile, tyro` and then
  imports `torch`, `torchpq` and `plas` at module scope. `torchpq` is CUDA, and
  a CUDA dependency is §1's hard non-goal — this app runs on the Intel UHD 770
  in this laptop as readily as on the RTX 4060. Its declared `plyfile` is
  **GPLv3** on top of that, and importing it would pull GPL into our own
  process, which is precisely the line §10 draws around spirula.

`@playcanvas/splat-transform` is **MIT**, is a Node CLI rather than a library we
link, needs no GPU compute for these three formats, and reads and writes all of
them from one invocation. Its two dependencies are `@adobe/spz` (ISC) and
`webgpu` (MIT). Being a subprocess, it has exactly the standing FFmpeg and
spirula have.

── What it is pointed at ───────────────────────────────────────────────────

`tools/splat-transform/`, gitignored beside `tools/spirula/`, installed with

    npm install --prefix tools/splat-transform @playcanvas/splat-transform

which puts the launcher at `node_modules/.bin/splat-transform(.cmd)`. That path
is the resolver's own fallback rather than a value in `config.json`, so a fresh
clone works after the one npm line with nobody typing a path. Setting
`splat_transform_path` (Settings -> Tools) points it somewhere else instead —
including at a global `npm install -g`, which the PATH lookup below also finds
on its own.

Pure module: no FastAPI import, like everything else under `core/steps/`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Optional

from backend.core.config import app_config

APP_ROOT = Path(__file__).parent.parent.parent.parent

#: Where `npm install --prefix tools/splat-transform` leaves the launcher.
#: The `.cmd` first, its extensionless sh sibling second — npm writes both, and
#: only the first is the Windows one. `subprocess` runs the `.cmd` directly with
#: no `shell=True` (measured on this build: exit 0, `splat-transform v3.3.3`),
#: so nothing here has to quote a command line by hand.
_BUNDLED = (
    "tools/splat-transform/node_modules/.bin/splat-transform.cmd",
    "tools/splat-transform/node_modules/.bin/splat-transform",
)

INSTALL_HINT = (
    "Install it with:\n"
    "    npm install --prefix tools/splat-transform @playcanvas/splat-transform\n"
    "or set splat_transform_path in Settings -> Tools to an existing install.\n"
    "It is MIT-licensed and needs Node.js; the PLY and .splat exports need "
    "nothing at all."
)


def _candidates() -> list[Path]:
    configured = (app_config.tools.splat_transform_path or "").strip()
    found: list[Path] = []
    if configured:
        path = Path(configured)
        found.append(path if path.is_absolute() else (APP_ROOT / configured).resolve())
    else:
        found += [(APP_ROOT / rel).resolve() for rel in _BUNDLED]
        # A global `npm install -g` puts it on PATH and nowhere predictable.
        on_path = shutil.which("splat-transform")
        if on_path:
            found.append(Path(on_path))
    return found


def find_splat_transform() -> Optional[Path]:
    """The launcher, or None. Never raises — the panel asks with this."""
    for path in _candidates():
        if path.is_file():
            return path
    return None


def is_available() -> bool:
    return find_splat_transform() is not None


def resolve_path() -> Path:
    """The launcher, or a failure naming every path it looked for.

    CLAUDE.md §2.2: a missing binary fails the step with the path it looked for.
    There is no stub and no silent fallback to an uncompressed format — an
    export that quietly wrote a 178 MB PLY when it was asked for a 12 MB SOG
    would be discovered by the person waiting for the download.
    """
    found = find_splat_transform()
    if found is not None:
        return found
    looked = "\n".join(f"  {p}" for p in _candidates())
    raise FileNotFoundError(
        "splat-transform was not found. The compressed formats (SOG, SPZ, "
        "compressed PLY) are written by it.\nLooked in:\n"
        f"{looked}\n{INSTALL_HINT}"
    )


def base_command() -> list[str]:
    """The head of every invocation."""
    return [str(resolve_path())]


def convert_command(src: Path, dst: Path) -> list[str]:
    """`splat-transform <in.ply> <out.<fmt>>` — the whole conversion.

    The output format is chosen by the **extension**, which is why
    `splat_export.SUFFIXES` keeps PlayCanvas's own `.compressed.ply` rather than
    a name of ours. No reduction flags are sent: the PLY handed over here has
    already been through `splat_export.write_reduced`, so every knob behaves the
    same whichever format is asked for, and this tool only ever re-encodes.
    """
    return base_command() + [str(src), str(dst)]


def read_version() -> str:
    """What `splat-transform --version` says, or a note that it would not.

    §2.7 at one remove: the version that wrote a `.sog` belongs in the run's
    result file, because a format that changes under a tool update is exactly
    the thing nobody will remember six months later.
    """
    path = find_splat_transform()
    if path is None:
        return "not installed"
    try:
        done = subprocess.run(
            [str(path), "--version"], capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unknown ({exc})"
    text = (done.stdout or done.stderr or "").strip()
    # `splat-transform v3.3.3 (d092ae9)` — one line, and it is also the banner
    # every run prints, so the first line is the version and the rest is noise.
    return text.splitlines()[0] if text else "unknown"
