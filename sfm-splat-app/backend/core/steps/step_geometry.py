"""step_geometry.py -- depth and normal maps with `spirula geometry` (a panel on step 4).

**Shell.** The command line, the traps and the progress channel are specified in
CLAUDE.md §7.5, and the numbers there were measured on the installed build --
but nothing here runs yet. TODO.md P3 is the implementation.

Refusing to write a plausible body on purpose: CLAUDE.md §2.2 says every step
calls its real tool and there is no simulation layer, and a shell that pretends
to succeed is exactly the stub that project deleted on 2026-08-22.
"""

from pathlib import Path

from backend.core.steps import spirula


async def run_geometry(project_path: Path, broadcast_fn, settings: dict) -> dict:
    """Not implemented yet -- see TODO.md P3."""
    version = spirula.read_version()          # fails first if the exe is missing
    await broadcast_fn(
        "geometry", "ERROR",
        f"[geometry] Step 4 is not implemented yet (spirula {version}). "
        "See TODO.md P3.",
    )
    raise NotImplementedError(
        "Step 4 (geometry) is not implemented yet -- see TODO.md P3."
    )
