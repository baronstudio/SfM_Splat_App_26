"""
scenes.py — cut detection. Each cut splits the footage into a *sequence*.

Sequences matter downstream: the sharpness median must not straddle a cut, the
overlap gate resets at every cut, and RealityScan should import each sequence as
its own image group (CLAUDE.md §7).

Three paths, in order of preference:

  1. FFmpeg's `scdet` scores, captured during the extraction on frames it was
     decoding anyway (`analysis/scene_scores.json`). Free, in the sense that
     matters: no second decode of the source. This is what §15.4's flat curation
     bar was waiting for.
  2. PySceneDetect `AdaptiveDetector` on the **source video**, which decodes the
     whole file again. Kept as the fallback and as the forced option, because it
     is the reference this detector was measured against.
  3. An HSV-histogram fallback over the **extracted frames**, used whenever the
     source video is gone, unreadable, or mpdecimate has broken the
     frame-index <-> timecode mapping the first two paths depend on.

All three return the same thing: the frame indices that *start* a new sequence.
"""

import math
import re
from pathlib import Path
from typing import Callable, Optional, Sequence

import cv2
import numpy as np

# ── Scene scores produced during extraction ──────────────────────────────────
#
# FFmpeg's `scdet` filter scores every *source* frame while the extraction is
# already decoding it, so the cuts cost no second decode of the video (§15.4:
# PySceneDetect re-decoding the source is where the curation bar sat flat —
# measured at 318 s on a 52 s 4K/100fps rush, against 5 s for this branch).
#
# One line pair per frame, as `metadata=mode=print` writes it:
#     frame:743  pts:743     pts_time:24.7667
#     lavfi.scd.score=1.203
_SCD_HEADER = re.compile(r"^frame:(\d+)\s+pts:(\S+)\s+pts_time:(\S+)")
_SCD_SCORE = re.compile(r"^lavfi\.scd\.score=([0-9.]+)")

# Both bars again, for the same asymmetry as the histogram fallback below: a
# missed cut costs a wide sharpness window, an invented one resets the overlap
# gate mid-shot.
#
# Measured on this workstation. Two real hard cuts in a spliced source scored
# 14.59 and 13.14; across four genuinely continuous rushes (a 4080x4080 h264
# turntable, a 4K stabilised walk, a portrait HEVC pan and a 4K/100fps orbit)
# the *highest* score seen anywhere was 2.51, with medians of 0.009-0.066. A
# floor of 6 sits more than 2x clear of both, and FFmpeg's own scdet default is
# 10. The frame straight after a cut echoes it (4.51 and 2.49 in the same
# measurement), which is what min_scene_len suppresses.
SCORE_RELATIVE_K = 8.0
SCORE_MIN_ABSOLUTE = 6.0

# Frames the fallback compares at; cut detection needs colour layout, not detail.
FALLBACK_MAX_DIM = 320

# A cut must clear both bars: be a local outlier *and* a large absolute change.
#
# The relative bar alone over-fires badly. Measured on two real continuous
# shots (a 212-frame drone orbit and a 148-frame walkthrough), median + 6*MAD
# reported 13 and 4 cuts where PySceneDetect on the source found none — at one
# frame every 0.5 s the camera has simply moved a lot between samples.
#
# The absolute bar is safe here in a way an absolute *sharpness* threshold never
# is: histogram correlation is already normalised to [0, 1] and independent of
# content scale. Those same continuous shots peak at 0.46, while a hard cut
# between two different scenes lands at 0.8+.
#
# The error is asymmetric, so both bars lean conservative: a missed cut costs a
# slightly wide sharpness window and one un-reset overlap reference, whereas an
# invented cut resets the gate mid-shot and forces a redundant frame to be kept.
FALLBACK_RELATIVE_K = 8.0
FALLBACK_MIN_DELTA = 0.6


def sequence_ids(frame_count: int, boundaries: Sequence[int]) -> list[int]:
    """Expand sequence-start indices into one sequence id per frame."""
    starts = sorted({b for b in boundaries if 0 <= b < frame_count} | {0})
    ids = [0] * frame_count
    current = -1
    next_start = 0
    for i in range(frame_count):
        if next_start < len(starts) and i == starts[next_start]:
            current += 1
            next_start += 1
        ids[i] = max(current, 0)
    return ids


def detect_from_video(
    video_path: Path,
    frame_count: int,
    working_fps: float,
    detector: str = "adaptive",
    min_scene_len_frames: int = 15,
) -> list[int]:
    """Cut indices from the source video, expressed in extracted-frame numbers.

    `working_fps` is the fps FFmpeg actually sampled at, so extracted frame i
    sits at t = i / working_fps in the source. Raises on any failure — the
    caller falls back to `detect_from_frames`.
    """
    from scenedetect import AdaptiveDetector, ContentDetector, detect

    if working_fps <= 0:
        raise ValueError("working_fps must be > 0 to map cuts onto frame indices")

    # min_scene_len is expressed in *extracted* frames by the settings; convert
    # to seconds, which PySceneDetect accepts directly as a float.
    min_len_s = max(0.1, min_scene_len_frames / working_fps)

    det = (
        ContentDetector(min_scene_len=min_len_s)
        if detector == "content"
        else AdaptiveDetector(min_scene_len=min_len_s)
    )
    scene_list = detect(str(video_path), det)

    boundaries: list[int] = [0]
    for start_tc, _end_tc in scene_list:
        idx = math.floor(start_tc.get_seconds() * working_fps)
        if 0 < idx < frame_count:
            boundaries.append(idx)
    return sorted(set(boundaries))


def parse_scdet_metadata(path: Path) -> tuple[list[float], list[float]]:
    """Read FFmpeg's `metadata=mode=print` dump into (pts_times, scores).

    Tolerant by construction: a header with no score line after it, or a score
    with no header before it, is skipped rather than raising. The file is
    written by a filter running next to a 4K decode, and a partial last line is
    the normal shape of an aborted run.
    """
    times: list[float] = []
    scores: list[float] = []
    pending: Optional[float] = None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    for line in text.splitlines():
        header = _SCD_HEADER.match(line)
        if header:
            try:
                pending = float(header.group(3))
            except ValueError:
                pending = None
            continue
        score = _SCD_SCORE.match(line)
        if score is not None and pending is not None:
            times.append(pending)
            scores.append(float(score.group(1)))
            pending = None
    return times, scores


def scores_cover_source(times: Sequence[float], duration_s: Optional[float]) -> bool:
    """True when the score series spans the whole source, not a tail of it.

    This is not paranoia about a missing file — it is the one way `metadata=print`
    fails silently. FFmpeg rebuilds the filter graph when the input's resolution,
    pixel format or SAR changes mid-stream, and the rebuilt `metadata` filter
    reopens its output in write mode: everything scored before that point is
    truncated away, and the frame counter restarts at 0 while pts_time carries on.
    Measured on a spliced 3-segment source, a 720-frame video left 240 scores
    whose first entry sat at t=16 s.

    A normal single-camera rush never reinitialises, so this only ever fires on
    the sources where the cuts would have been wrong. The caller falls back to
    PySceneDetect, which decodes the video itself and cannot be fooled this way.
    """
    if not times:
        return False
    if not duration_s or duration_s <= 0:
        # No probe to compare against; trust the file if it starts at the top.
        return times[0] <= 1.0
    return times[0] <= 1.0 and times[-1] >= 0.90 * duration_s


def detect_from_scene_scores(
    times: Sequence[float],
    scores: Sequence[float],
    frame_count: int,
    working_fps: float,
    min_scene_len_frames: int = 15,
    relative_k: float = SCORE_RELATIVE_K,
    min_absolute: float = SCORE_MIN_ABSOLUTE,
) -> list[int]:
    """Cut indices from the scdet scores captured during extraction.

    Same contract as `detect_from_video`: source timecodes mapped onto extracted
    frame numbers through the working fps, returned as sequence-start indices.

    A cut must clear both bars — a local outlier *and* a large absolute score.
    `scdet` normalises its score to 0-100 (it is min(MAFD, |MAFD - previous
    MAFD|)), so an absolute floor is meaningful here in the way it never is for
    a Tenengrad sharpness score.
    """
    if working_fps <= 0:
        raise ValueError("working_fps must be > 0 to map cuts onto frame indices")
    if len(scores) < 2:
        raise ValueError("not enough scene scores to detect anything")

    arr = np.asarray(scores, dtype=float)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median))) or 1e-6
    threshold = max(median + relative_k * mad, min_absolute)

    # The gap is enforced in extracted frames, which is the unit min_scene_len is
    # expressed in and the unit the overlap gate resets on.
    boundaries: list[int] = [0]
    for i in range(1, len(arr)):
        if arr[i] <= threshold:
            continue
        idx = math.floor(times[i] * working_fps)
        if 0 < idx < frame_count and (idx - boundaries[-1]) >= min_scene_len_frames:
            boundaries.append(idx)
    return boundaries


def _hsv_histogram(path: Path, max_dim: int = FALLBACK_MAX_DIM) -> Optional[np.ndarray]:
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
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
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist.flatten()


def detect_from_frames(
    paths: Sequence[Path],
    min_scene_len_frames: int = 15,
    relative_k: float = FALLBACK_RELATIVE_K,
    min_delta: float = FALLBACK_MIN_DELTA,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> list[int]:
    """Fallback cut detection over the extracted frames themselves.

    Adaptive in the same spirit as PySceneDetect's detector: a frame is a cut
    when its histogram distance from the previous frame stands out against the
    local median distance, rather than against a fixed threshold. Extracted
    frames are seconds apart, so absolute distances are meaningless here.
    """
    n = len(paths)
    if n < 2:
        return [0]

    deltas: list[float] = [0.0]
    prev = _hsv_histogram(paths[0])
    for i in range(1, n):
        cur = _hsv_histogram(paths[i])
        if prev is None or cur is None:
            deltas.append(0.0)
        else:
            # Correlation is 1.0 for identical histograms; use its complement.
            corr = float(cv2.compareHist(prev, cur, cv2.HISTCMP_CORREL))
            deltas.append(max(0.0, 1.0 - corr))
        prev = cur if cur is not None else prev
        if progress_cb is not None:
            progress_cb(i + 1, n)

    arr = np.asarray(deltas[1:], dtype=float)
    if arr.size == 0:
        return [0]
    median = float(np.median(arr))
    # Median absolute deviation — robust to the handful of real cuts we hunt for.
    mad = float(np.median(np.abs(arr - median))) or 1e-6
    threshold = max(median + relative_k * mad, min_delta)

    boundaries = [0]
    for i in range(1, n):
        if deltas[i] > threshold and (i - boundaries[-1]) >= min_scene_len_frames:
            boundaries.append(i)
    return boundaries


def detect_sequences(
    paths: Sequence[Path],
    video_path: Optional[Path],
    working_fps: Optional[float],
    detector: str = "adaptive",
    min_scene_len_frames: int = 15,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    scene_scores: Optional[dict] = None,
    cut_source: str = "auto",
) -> tuple[list[int], str]:
    """Resolve one sequence id per frame. Returns (ids, method_used).

    `scene_scores` is what the extraction captured on its way past
    (`analysis/scene_scores.json`); when it is usable it costs no decode at all,
    which is the whole point. `cut_source` forces a path: "video" pins
    PySceneDetect, "frames" pins the histogram fallback, "auto" tries them in
    order of preference.
    """
    n = len(paths)
    if n == 0:
        return [], "empty"

    if detector == "off":
        return [0] * n, "off (single sequence)"

    if cut_source == "auto" and scene_scores and working_fps:
        times = scene_scores.get("times") or []
        values = scene_scores.get("scores") or []
        if scores_cover_source(times, scene_scores.get("source_duration_s")):
            try:
                boundaries = detect_from_scene_scores(
                    times, values, n, working_fps, min_scene_len_frames
                )
                return (
                    sequence_ids(n, boundaries),
                    f"FFmpeg scdet, {len(values)} source frames scored during extraction",
                )
            except Exception:  # noqa: BLE001 — the fallbacks are the whole point
                pass

    if cut_source != "frames" and video_path is not None and video_path.exists() and working_fps:
        try:
            boundaries = detect_from_video(
                video_path, n, working_fps, detector, min_scene_len_frames
            )
            return sequence_ids(n, boundaries), f"PySceneDetect {detector} on source video"
        except Exception:  # noqa: BLE001 — the fallback is the whole point
            pass

    boundaries = detect_from_frames(paths, min_scene_len_frames, progress_cb=progress_cb)
    return sequence_ids(n, boundaries), "histogram fallback on extracted frames"


def sequence_spans(ids: Sequence[int]) -> list[dict]:
    """Compact [{id, start_index, end_index, frame_count}] view of the ids."""
    spans: list[dict] = []
    for i, sid in enumerate(ids):
        if not spans or spans[-1]["id"] != sid:
            spans.append({"id": sid, "start_index": i, "end_index": i, "frame_count": 0})
        spans[-1]["end_index"] = i
        spans[-1]["frame_count"] += 1
    return spans
