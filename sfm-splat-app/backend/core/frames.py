"""What counts as a frame in `projects/<slug>/frames/`, in one place.

Three modules kept their own copy of this set — `api/routes/files.py`,
`core/steps/step_analyze.py` and `core/steps/step_rc.py` — and they had drifted
into the same shape by luck rather than by rule. They also disagreed about what
happens to anything that is *not* a frame: the RS coverage check compares the
number of files here against the number of exported cameras, so one stray image
in this directory reports a perfect alignment as a partial one.

Pure module: no FastAPI, no cv2 — it is a naming rule and nothing else.
"""

from __future__ import annotations

from pathlib import Path

# What the app is willing to treat as a frame. TIFF is accepted because an
# imported image set may hold one (§6.7); the extraction and the conform only
# ever write JPEG or PNG.
FRAME_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}

# RealityScan's mask-layer convention: `DSC_0001.jpg.mask.png` beside
# `DSC_0001.jpg` (`Help/en-US/tools/imglayers.htm`).
#
# Nothing in this app writes one — RS has no alpha concept for source images,
# so an imported set's alpha is kept *inside* the frames instead (§6.7). The
# rule stays because a file with that name can still arrive here in a set
# imported from a project that used masks, and it is not a frame: counting it
# as one would score a mask for sharpness, double the gallery, and halve the
# reported alignment coverage.
MASK_SUFFIX = ".mask.png"


# Where step 2 puts the alpha channel it extracted from a PNG image set
# (§6.7): `projects/<slug>/masks/`, one greyscale PNG per frame, same basename.
#
# A directory of its own rather than sidecars in `frames/`, because `frames/` is
# what `-addFolder` hands to RealityScan: a `<frame>.mask.png` beside its frame
# is RS's mask-layer convention and RS would ingest it, which is not this
# workflow. It is also the layout LichtFeld Studio reads — `masks/` mirroring
# the image names.
MASKS_DIRNAME = "masks"


def masks_dir(project_path: Path) -> Path:
    return project_path / MASKS_DIRNAME


def list_mask_images(masks: Path) -> list[Path]:
    """The extracted alpha images, sorted by name — the frame order."""
    if not masks.is_dir():
        return []
    return sorted(
        (f for f in masks.iterdir() if f.is_file() and f.suffix.lower() == ".png"),
        key=lambda f: f.name,
    )


def is_mask(path: Path) -> bool:
    return path.name.lower().endswith(MASK_SUFFIX)


def is_frame(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() in FRAME_SUFFIXES
        and not is_mask(path)
    )


def list_frames(frames_dir: Path) -> list[Path]:
    """Every frame in the directory, sorted by name.

    Sorted by name because the name carries the index: `frame_0007.jpg` from
    the extraction, `<set>_0007.png` from an imported set. Both are zero-padded
    for exactly this reason.
    """
    if not frames_dir.is_dir():
        return []
    return sorted((f for f in frames_dir.iterdir() if is_frame(f)), key=lambda f: f.name)


def count_frames(frames_dir: Path) -> int:
    return len(list_frames(frames_dir))
