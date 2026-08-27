"""
sharpness.py — Tenengrad focus measure + *relative* blur rejection.

The rejection is deliberately relative to a rolling median (CLAUDE.md §6.3):
an absolute Tenengrad threshold does not generalise across content — a textured
facade scores an order of magnitude above a clear sky, and any fixed number
would either keep every blurred sky frame or reject every sharp one.
"""

from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import cv2
import numpy as np

# Long-edge cap before scoring. Tenengrad is a gradient energy measure, so it
# scales with resolution; downscaling every frame to the same bound keeps the
# scores comparable and makes a 4K set roughly as fast as a 1080p one.
MAX_DIM = 1080


def load_grey(path: Path, max_dim: int = MAX_DIM) -> np.ndarray:
    """Read a frame as greyscale, downscaled so its long edge is <= max_dim."""
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise OSError(f"Cannot read image: {path}")
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > max_dim:
        scale = max_dim / longest
        img = cv2.resize(
            img, (max(1, round(w * scale)), max(1, round(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return img


def tenengrad(grey: np.ndarray) -> float:
    """Mean squared Sobel gradient magnitude. Higher = sharper."""
    if grey.size == 0 or min(grey.shape[:2]) < 3:
        # Degenerate frame (a truncated or 1x1 JPEG) — score it 0 rather than
        # raising, so one bad file still leaves a complete scores.json.
        return 0.0
    gx = cv2.Sobel(grey, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(grey, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))


def score_frames(
    paths: Sequence[Path],
    max_dim: int = MAX_DIM,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[float]:
    """Tenengrad score for every frame, in the order given.

    An unreadable frame scores 0.0 instead of aborting the run — one corrupt
    JPEG out of two thousand must not cost the user the whole analysis.
    """
    total = len(paths)
    scores: list[float] = []
    for i, path in enumerate(paths):
        try:
            scores.append(tenengrad(load_grey(path, max_dim)))
        except OSError:
            scores.append(0.0)
        if progress_cb is not None:
            progress_cb(i + 1, total)
    return scores


def rolling_median(
    values: Sequence[float],
    sequence_ids: Sequence[int],
    window: int,
) -> list[float]:
    """Median of a centred window, never straddling a cut.

    The window is clipped at sequence boundaries: the first frames after a cut
    must not be compared against the shot that preceded them.
    """
    n = len(values)
    if n == 0:
        return []
    half = max(1, window // 2)
    out: list[float] = []
    for i in range(n):
        lo = i
        while lo > 0 and (i - lo) < half and sequence_ids[lo - 1] == sequence_ids[i]:
            lo -= 1
        hi = i
        while hi < n - 1 and (hi - i) < half and sequence_ids[hi + 1] == sequence_ids[i]:
            hi += 1
        out.append(float(np.median(values[lo:hi + 1])))
    return out


def blur_flags(
    scores: Sequence[float],
    medians: Sequence[float],
    sensitivity: int,
) -> list[bool]:
    """True where the frame is to be rejected as blurred.

    `sensitivity` is 0-100 and maps linearly to the fraction of the local median
    a frame must reach: 0 rejects nothing, 50 rejects anything under half the
    local median, 100 rejects everything below the median itself.
    """
    factor = max(0.0, min(100.0, float(sensitivity))) / 100.0
    if factor <= 0.0:
        return [False] * len(scores)
    return [
        bool(median > 0.0 and score < factor * median)
        for score, median in zip(scores, medians)
    ]


def summarise(scores: Iterable[float]) -> dict:
    """Mean / median / min / max of a score series (empty-safe)."""
    arr = np.asarray(list(scores), dtype=float)
    if arr.size == 0:
        return {"mean": 0.0, "median": 0.0, "min": 0.0, "max": 0.0}
    return {
        "mean": float(arr.mean()),
        "median": float(np.median(arr)),
        "min": float(arr.min()),
        "max": float(arr.max()),
    }
