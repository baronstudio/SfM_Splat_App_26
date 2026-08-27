"""
overlap.py — the ORB displacement gate (CLAUDE.md §6.3 step 3).

Per sequence, every candidate frame is matched against the **last kept frame**,
not against its immediate neighbour: that is what makes the gate cumulative, so
a slow orbit drops a run of near-identical frames instead of keeping all of them
because each one barely moved relative to the one before it.

The measure is the median ORB keypoint displacement as a percentage of frame
width, which makes it resolution-independent and directly comparable to the
band carried by the capture presets.

  displacement < min_step   -> rejected:redundant   (reference unchanged)
  min_step .. band_max      -> kept
  displacement > band_max   -> kept, flagged warning:gap
"""

from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

MAX_DIM = 1080
N_FEATURES = 2000
# Below this many cross-checked matches the median is noise, not a measurement.
MIN_MATCHES = 8
# Keep the best fraction by descriptor distance before taking the median, so a
# repeated texture (foliage, gravel) cannot drag the estimate.
MATCH_KEEP_RATIO = 0.7


class FrameFeatures:
    """ORB keypoints + descriptors for one frame, plus the width they refer to."""

    __slots__ = ("points", "descriptors", "width")

    def __init__(self, points: np.ndarray, descriptors: Optional[np.ndarray], width: int):
        self.points = points
        self.descriptors = descriptors
        self.width = width


def extract_features(path: Path, orb: cv2.ORB, max_dim: int = MAX_DIM) -> Optional[FrameFeatures]:
    """ORB features for one frame, or None when the frame yields nothing usable."""
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > max_dim:
        scale = max_dim / longest
        img = cv2.resize(
            img, (max(1, round(w * scale)), max(1, round(h * scale))),
            interpolation=cv2.INTER_AREA,
        )
    keypoints, descriptors = orb.detectAndCompute(img, None)
    if descriptors is None or len(keypoints) == 0:
        return None
    points = np.array([kp.pt for kp in keypoints], dtype=np.float32)
    return FrameFeatures(points, descriptors, img.shape[1])


def extract_all(
    paths: Sequence[Path],
    max_dim: int = MAX_DIM,
    n_features: int = N_FEATURES,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[Optional[FrameFeatures]]:
    """One pass over the frames. Descriptors are reused as references move on."""
    orb = cv2.ORB_create(nfeatures=n_features)
    out: list[Optional[FrameFeatures]] = []
    total = len(paths)
    for i, path in enumerate(paths):
        out.append(extract_features(path, orb, max_dim))
        if progress_cb is not None:
            progress_cb(i + 1, total)
    return out


def displacement_pct(
    ref: Optional[FrameFeatures],
    cur: Optional[FrameFeatures],
) -> Optional[float]:
    """Median keypoint displacement between two frames, in % of frame width.

    None means "not measurable" (no features, too few matches). The caller must
    treat that as unknown and keep the frame — silently dropping frames the gate
    could not measure would be the worst possible failure mode.
    """
    if ref is None or cur is None:
        return None
    if ref.descriptors is None or cur.descriptors is None:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = matcher.match(ref.descriptors, cur.descriptors)
    if len(matches) < MIN_MATCHES:
        return None

    matches = sorted(matches, key=lambda m: m.distance)
    keep = max(MIN_MATCHES, int(len(matches) * MATCH_KEEP_RATIO))
    matches = matches[:keep]

    src = np.array([ref.points[m.queryIdx] for m in matches], dtype=np.float32)
    dst = np.array([cur.points[m.trainIdx] for m in matches], dtype=np.float32)
    dists = np.linalg.norm(dst - src, axis=1)

    width = cur.width or ref.width
    if not width:
        return None
    return float(np.median(dists) / width * 100.0)


def gate(
    features: Sequence[Optional[FrameFeatures]],
    sequence_ids: Sequence[int],
    blocked: Sequence[bool],
    min_step_pct: float,
    band_max_pct: float,
) -> tuple[list[Optional[float]], list[bool], list[bool]]:
    """Run the gate over a whole frame set.

    `blocked[i]` marks frames already rejected upstream (blur): they are skipped
    entirely and never become the reference — a blurred frame must not define
    where the next kept frame sits.

    Returns (displacement_pct, redundant, gap_warning), all frame-aligned.
    """
    n = len(features)
    displacements: list[Optional[float]] = [None] * n
    redundant: list[bool] = [False] * n
    gap: list[bool] = [False] * n

    reference: Optional[FrameFeatures] = None
    current_sequence: Optional[int] = None

    for i in range(n):
        if blocked[i]:
            continue

        # A cut resets the gate: the first surviving frame of a sequence is
        # always kept and becomes the new reference.
        if sequence_ids[i] != current_sequence:
            current_sequence = sequence_ids[i]
            reference = features[i]
            continue

        if reference is None:
            reference = features[i]
            continue

        d = displacement_pct(reference, features[i])
        displacements[i] = d

        if d is None:
            # Unmeasurable — keep it and move the reference on, otherwise a run
            # of featureless frames would all be compared to a stale reference.
            reference = features[i]
            continue

        if d < min_step_pct:
            redundant[i] = True
            continue  # reference deliberately unchanged

        if d > band_max_pct:
            gap[i] = True
        reference = features[i]

    return displacements, redundant, gap


def band_quality(
    displacements: Sequence[Optional[float]],
    kept: Sequence[bool],
    min_step_pct: float,
    band_max_pct: float,
) -> dict:
    """Share of consecutive kept pairs whose step lands inside the band."""
    measured = [
        d for i, d in enumerate(displacements)
        if d is not None and kept[i]
    ]
    if not measured:
        return {"pairs": 0, "in_band": 0, "in_band_ratio": 0.0, "median_pct": 0.0}
    in_band = [d for d in measured if min_step_pct <= d <= band_max_pct]
    return {
        "pairs": len(measured),
        "in_band": len(in_band),
        "in_band_ratio": len(in_band) / len(measured),
        "median_pct": float(np.median(measured)),
    }
