"""routes/hardware.py -- what this workstation is, and what it is doing.

An installation concern like `/api/settings` and `/api/models`, and drawn in
the same **global setup panel** for the same reason (CLAUDE.md section 4): the
CPU in this machine is not a per-project setting, and asking about it from
inside a wizard step would ask the same question once per project.

**Two routes because there are two costs.** `GET /` is the machine's
description -- cached for the life of the process, and the only thing here that
spawns a subprocess (`spirula sam devices`). `GET /live` is the gauge payload,
measured at 1.4 ms a poll, which is what makes it safe to call every second
while a 956-second training run goes.

**Start-and-poll rather than the WS bus**, mirroring `/preview` and
`/api/models`: the bus carries no project id (section 13.7) and every consumer
of it maps a step name onto the open project's bar. A CPU reading belongs to no
project and must not move one.
"""

from fastapi import APIRouter

from backend.core import hardware
from backend.core.steps.spirula import resolve_spirula_path

router = APIRouter()


def _spirula_path() -> str | None:
    """The binary the Vulkan device list is read from, or None.

    `resolve_spirula_path` is the one place that knows the config key, the
    repo-relative default a fresh clone ships and the "not configured" message
    (section 2.2). Borrowing it here rather than reading
    `config.tools.spirula_exe_path` directly is what stops this panel drawing a
    device list from a path the pipeline would refuse.

    It raises when there is no usable binary, which is a perfectly ordinary
    state for a fresh install -- a machine still has a CPU and two GPUs to
    report -- so it is caught and `spirula_devices` says why the list is empty.
    """
    try:
        return str(resolve_spirula_path())
    except (FileNotFoundError, OSError):
        return None


@router.get("/")
def get_hardware(refresh: bool = False):
    """CPU, memory, graphics adapters and spirula's Vulkan verdict on them.

    Cached per process. `?refresh=1` re-reads it, which is what the panel sends
    after the user points the Tools section at a different spirula binary --
    the only part of this payload a running server can see change.
    """
    return hardware.static_info(_spirula_path(), refresh=refresh)


@router.get("/live")
def get_live():
    """CPU %, RAM and per-adapter GPU % / VRAM -- one gauge tick.

    Every field is nullable and the caller draws what it got: the very first
    poll has no interval to average the CPU over and reports `null` rather than
    a zero it cannot stand behind.
    """
    return hardware.live_sample()
