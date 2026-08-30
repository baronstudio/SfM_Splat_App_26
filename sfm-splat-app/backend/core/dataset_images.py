"""dataset_images.py — `<dataset>/images` → `frames/`, for one run only.

Two spirula tools resolve their images inside the dataset folder and offer no
flag that moves them, while §5.2's layout deliberately keeps the one copy of
the frames *outside* it — `frames/` beside `sfm/`, never a second copy:

* **`spirula geometry` has no `--image-dir` at all.** Measured 2026-08-28 (§7.5,
  `docs/spirula/geometry-run.txt`): pointed at `<project>/sfm` it resolved
  `<project>/sfm\\images\\frame_0001.jpg`, answered `can't fopen` and `skipping`
  for all 238 images and finished `done: 0 written` at **exit 0**.

* **`spirula mesh` has none either**, and it is the same parser: measured
  2026-08-30 on `poubelle_garnier_v2`, a run whose checkpoint was the crop's
  `train/crop/splat.ply` died on
  `ColmapParser: <project>\\sfm\\images\\frame_0001.jpg does not exist (set
  --image-dir if needed)` at **exit 1**, having written nothing. The flag that
  message names does not exist on `mesh` — `mesh --help` has no `--image-dir`.
  A checkpoint *inside* `train/run/` survives without a junction because the
  tool reads the `image_dir` recorded in the run's own `config.json`, which is
  why step 5 worked before the crop existed and not after it.

So one junction, created before the command and removed in `__exit__`. Nothing
else in the app ever meets it: not `reset_steps`' `rmtree`, not the project
copy's `copytree`, not the archive's zip, not `colmap.find_model`. §5's layout
on disk stays exactly what §5 says it is, and this stays an implementation
detail of two commands rather than a new rule.

Pure module: no FastAPI import (§2.4).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# Not configurable, because it is not ours: both tools resolve
# `<dataset>\images\<name>` and there is no flag that moves it.
DATASET_IMAGE_DIRNAME = "images"


class ImageJunction:
    """`<dataset>/images` → `frames/`, for the length of one run and no longer.

    A junction reconciles the tools' fixed lookup with §5.2's single copy of
    the frames without duplicating the images — 226 MB on the reference
    project, tens of gigabytes on a 4K one.

    A junction needs neither administrator rights nor Developer Mode, unlike
    `os.symlink` on Windows; POSIX gets a plain symlink, which needs neither
    there. `os.rmdir` removes the link and leaves the target untouched —
    verified before it was relied on, because getting it wrong deletes
    `frames/`.
    """

    def __init__(self, dataset_dir: Path, frames_dir: Path) -> None:
        self.link = dataset_dir / DATASET_IMAGE_DIRNAME
        self.frames_dir = frames_dir
        self.created = False

    @staticmethod
    def _is_link(path: Path) -> bool:
        # `os.path.isjunction` is 3.12+; 3.11 is supported (§3), so fall back to
        # the symlink test rather than assuming the newer name is there.
        is_junction = getattr(os.path, "isjunction", None)
        return bool(is_junction and is_junction(path)) or os.path.islink(path)

    def __enter__(self) -> "ImageJunction":
        if self.link.exists() or self._is_link(self.link):
            if self._is_link(self.link):
                # Left by a run that died before its `finally` — ours to reuse.
                os.rmdir(self.link)
            else:
                # A real directory of that name is somebody's data, and this
                # class only ever removes links. Refuse rather than delete.
                raise FileExistsError(
                    f"{self.link} already exists as a real directory. "
                    "spirula needs that name for a link to "
                    f"{self.frames_dir}; move it aside and run this again."
                )
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(self.frames_dir), str(self.link))
        else:
            os.symlink(str(self.frames_dir), str(self.link),
                       target_is_directory=True)
        self.created = True
        return self

    def __exit__(self, *_exc: Any) -> None:
        if not self.created:
            return
        try:
            # `os.rmdir` on a junction removes the link and leaves the target
            # untouched — verified, because getting this wrong deletes `frames/`.
            os.rmdir(self.link)
        except OSError:
            pass
