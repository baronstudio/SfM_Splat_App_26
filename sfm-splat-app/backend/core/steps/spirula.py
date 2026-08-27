"""spirula.py — the one place that builds a `spirula.exe` command line.

Four wizard steps drive the same binary (CLAUDE.md §5.1: `sfm`, `train`, `mesh`,
`sam` and `geometry` are tools inside one 119 MB executable), and §7.0 lists four
rules that hold for every one of them. Implementing those four rules in four
step modules is implementing them wrong three times, so they live here.

Pure module: no FastAPI import, like everything else under `core/steps/`.
"""

import subprocess
from pathlib import Path
from typing import Any, Iterable

from backend.core.config import app_config

# The language every invocation is pinned to. Not a nicety: the tool localizes
# every line it prints - `--lang`, else `SS_LANG`, else the OS - and this is a
# French Windows, where `spirula --help` answers `Commandes :` and `par défaut`.
# Every progress regex in step_sfm / step_train / step_mesh would match nothing,
# silently, and the bar would sit at zero for the length of a training run
# (CLAUDE.md §12, 2026-08-27). Emitted here so it cannot be forgotten in one of
# six call sites.
LANG = "en"


def resolve_spirula_path() -> Path:
    """The binary to run, or a failure naming the path it looked for.

    CLAUDE.md §2.2: a missing or misconfigured binary fails the step with the
    path it looked for. There is no stub and no fallback.
    """
    configured = (app_config.tools.spirula_exe_path or "").strip()
    if not configured:
        raise FileNotFoundError(
            "spirula_exe_path is not configured.\n"
            "Download spirula.exe from "
            "https://github.com/harry7557558/spirula-studio/releases\n"
            "and set spirula_exe_path in Settings -> Tools."
        )

    path = Path(configured)
    if not path.is_absolute():
        # config.json ships a repo-relative default so a fresh clone works after
        # `setup.py` without anyone typing an absolute path.
        path = (Path(__file__).parent.parent.parent.parent / configured).resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"spirula.exe not found at: {path}\n"
            "Download it from "
            "https://github.com/harry7557558/spirula-studio/releases\n"
            "and set spirula_exe_path in Settings -> Tools."
        )
    return path


def base_command(tool: str) -> list[str]:
    """`<spirula.exe> --lang en <tool>` - the head of every invocation."""
    return [str(resolve_spirula_path()), "--lang", LANG, tool]


def read_version() -> str:
    """What `spirula --version` says, or a note that it would not say.

    CLAUDE.md §2.7: read the installed build, never remember it. This is logged
    at the top of every run and recorded in the step result, so a flag that
    stops working can be tied to the build it stopped working on.
    """
    try:
        done = subprocess.run(
            [str(resolve_spirula_path()), "--version"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unknown ({exc})"
    return (done.stdout or done.stderr or "").strip() or "unknown"


def flag(name: str, value: Any) -> list[str]:
    """One `train` / `mesh` / `geometry` flag and its value.

    Their shared convention, from `train --help`: flags are flattened
    (`--sh-degree`, never `--model.sh-degree`), `-` and `_` are interchangeable,
    **bools take 0/1**, `none` clears an optional value and tuples take N values.
    A bool rendered as a bare switch would be read as the next flag's value.
    """
    key = "--" + name.replace("_", "-").lstrip("-")
    if isinstance(value, bool):
        return [key, "1" if value else "0"]
    if value is None:
        return [key, "none"]
    if isinstance(value, (list, tuple)):
        return [key, *(str(v) for v in value)]
    return [key, str(value)]


def flags(pairs: Iterable[tuple[str, Any]]) -> list[str]:
    """`flag()` over a sequence, skipping any pair whose value is `...`."""
    out: list[str] = []
    for name, value in pairs:
        if value is not Ellipsis:
            out += flag(name, value)
    return out


def switch(name: str, enabled: bool) -> list[str]:
    """One `sfm auto` switch - the exception to `flag()`.

    `sfm auto` does not follow the other tools' convention: its bools are bare
    `--no-x` switches that take no value at all (`--no-masks`, `--no-merge`,
    `--no-loop-closure`), and its help prints their *current* state in brackets
    rather than a default to pass back. Handing one a `0` would make the tool
    read that `0` as the next positional argument.
    """
    return [f"--{name.replace('_', '-').lstrip('-')}"] if enabled else []
