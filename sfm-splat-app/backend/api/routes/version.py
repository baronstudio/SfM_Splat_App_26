"""Application identity: name, version, commit.

The version number is derived from the repository itself — the date of the
commit the app is running followed by that commit's number in the history,
`YYYY.MM.DD.N`. The date is what the GitHub history shows for that commit; the
count is `git rev-list --count HEAD`, which makes the version **unique and
monotone per commit** where the date alone repeats on every day that carries
more than one. Deriving it from the local clone rather than querying github.com
keeps it honest: it describes the code actually running, not the tip of the
remote, and it works with no network.

A shallow clone counts only the commits it has, so the number is smaller than
the history's — the sha beside it is what identifies the build either way.

**Except on a machine the code was copied to.** `scripts/sync_staging.sh`
delivers with `cp` over `git ls-files`; it never touches `.git`, so the staging
clone's HEAD is wherever somebody last ran a git command over there while the
files on disk are days newer. Asking git on that machine answers the wrong
question. So the sync leaves a `.version_stamp.json` naming what it pushed, and
this module prefers it — that file exists only where git cannot describe the
code, which is exactly when it should win.

Read once per process: the metadata cannot change under a running server
without a restart, and a subprocess per page load would be pure waste.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

APP_NAME = "3DGS Pipeline App"

# backend/api/routes/version.py -> the app root, which is inside the repo.
_APP_ROOT = Path(__file__).resolve().parents[3]

# Written by scripts/sync_staging.sh --apply, on the machine it pushed to.
# Untracked and gitignored: it describes one machine, never the repository.
_STAMP_PATH = _APP_ROOT / ".version_stamp.json"

_cache: dict | None = None


def _run(*args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a git command in the app directory, or None if git/the repo is absent."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=_APP_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _git(*args: str) -> str | None:
    """Stdout of a git command, or None when it fails or says nothing."""
    out = _run(*args)
    if out is None or out.returncode != 0:
        return None
    return out.stdout.strip() or None


def _git_ok(*args: str) -> bool:
    """True when a git command succeeds — for the verbs that answer by exit code."""
    out = _run(*args)
    return out is not None and out.returncode == 0


def _commit_count() -> int | None:
    """Number of commits reachable from HEAD — the version's ordinal half."""
    out = _git("rev-list", "--count", "HEAD")
    try:
        return int(out) if out else None
    except ValueError:
        return None


def _commit_url(remote: str | None, sha: str | None) -> str | None:
    """https URL of the commit on GitHub, from whatever form `origin` takes."""
    if not remote or not sha:
        return None
    m = re.match(r"^(?:https://github\.com/|git@github\.com:)(.+?)(?:\.git)?/?$", remote)
    if not m:
        return None
    return f"https://github.com/{m.group(1)}/commit/{sha}"


def _read_git() -> dict:
    sha = _git("rev-parse", "HEAD")
    short = _git("rev-parse", "--short=8", "HEAD")
    date = _git("log", "-1", "--date=format:%Y.%m.%d", "--format=%cd")
    iso = _git("log", "-1", "--format=%cI")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    remote = _git("config", "--get", "remote.origin.url")
    count = _commit_count()

    return {
        "name": APP_NAME,
        # No git, no version: saying "0.0.0" would be inventing one. A date
        # without a count is still a version, so the suffix is appended rather
        # than required — a repository git can date but not count is not one
        # this app should refuse to name.
        "version": f"{date}.{count}" if date and count is not None else date,
        "commit_count": count,
        "commit": sha,
        "commit_short": short,
        "commit_date": iso,
        "branch": branch,
        "commit_url": _commit_url(remote, sha),
        "source": "git",
        "dirty": False,
        "synced_at": None,
        "synced_from": None,
    }


def _read_stamp() -> dict | None:
    """The stamp the sync left behind, or None on a machine nobody pushed to."""
    try:
        raw = json.loads(_STAMP_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or not raw.get("commit"):
        return None
    return raw


def _stamp_is_stale(sha: str) -> bool:
    """True when this clone has moved *past* the commit the stamp names.

    Somebody pulled here after the last sync, so git is the better witness and
    the stamp is history. A commit the clone does not have is **not** stale:
    git cannot be ahead of what it has never seen, and that is the normal case
    on a machine fed by file copy.
    """
    head = _git("rev-parse", "HEAD")
    if not head or head == sha:
        return False
    if not _git_ok("cat-file", "-e", f"{sha}^{{commit}}"):
        return False
    return _git_ok("merge-base", "--is-ancestor", sha, "HEAD")


def _from_stamp(stamp: dict) -> dict:
    sha = stamp.get("commit")
    return {
        "name": APP_NAME,
        "version": stamp.get("version"),
        "commit_count": stamp.get("commit_count"),
        "commit": sha,
        "commit_short": stamp.get("commit_short"),
        "commit_date": stamp.get("commit_date"),
        "branch": stamp.get("branch"),
        "commit_url": stamp.get("commit_url") or _commit_url(
            _git("config", "--get", "remote.origin.url"), sha
        ),
        "source": "sync",
        # The sync pushes the working tree, which is not always the commit it
        # names. Reporting the date of a commit whose content is not what is
        # running is the very drift this stamp exists to end, so say so.
        "dirty": bool(stamp.get("dirty")),
        "synced_at": stamp.get("synced_at"),
        "synced_from": stamp.get("synced_from"),
    }


def _read_version() -> dict:
    stamp = _read_stamp()
    if stamp and not _stamp_is_stale(stamp["commit"]):
        return _from_stamp(stamp)
    return _read_git()


@router.get("/")
def read_version() -> dict:
    global _cache
    if _cache is None:
        _cache = _read_version()
    return _cache
