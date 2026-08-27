"""Camera poses from the reconstruction, for the step 3 viewer overlay.

The sparse cloud alone does not say whether the reconstruction is any good - the
camera path does. A drone orbit that came back as an orbit is fine; one that
folds back on itself, or breaks into two arcs sitting at different scales, is a
fragmented capture you can see instead of read about (CLAUDE.md §7.1).

What cannot be drawn: the frames the mapper did not register. They are absent
from the model precisely because they have no pose. What is drawn instead is the
*edge* of each hole - the registered cameras whose neighbour in the input order
never came back.

**Everything here is simpler than the predecessor's version, for one measured
reason.** `3DGS_App_26` had to reconcile three frames and a rename: RealityScan
wrote a NeRF `transforms.json` in a Y-up frame, a point cloud in its own Z-up
one, and renamed every undistorted copy `00000.png`, so a fully aligned project
matched zero names and reported `0/300 - 0%`. Here the sparse cloud and the
splat share one frame (identity, 90.1 % occupancy overlap - §7.3), the model is
COLMAP binary rather than two disagreeing exports, and `images.bin` keeps the
input filename. There is no name/position/count fallback because there is no
rename.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from backend.core import colmap, frames as frame_files


def _sequence_index(project_path: Path) -> dict[str, int]:
    """frame filename -> sequence id, from the curation scores (CLAUDE.md §6.3)."""
    path = project_path / "analysis" / "scores.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("frames", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return {}
    return {
        str(e["filename"]): int(e["sequence_id"])
        for e in entries
        if isinstance(e, dict) and e.get("filename") and e.get("sequence_id") is not None
    }


def read_cameras(project_path: Path) -> dict:
    """Registered poses in filename order, tagged with sequence and hole edges.

    The rotation handed out is world-from-camera, row major, and the position is
    the camera's own place in the world - `colmap.read_images` does that
    conversion, since COLMAP stores the inverse.

    The frame needs no correction on the way out. Everything the viewer draws -
    this overlay, the sparse cloud and the trained splat - is in one +Z-up frame,
    and the single `Rx-90` that makes it three.js's Y-up is applied on the scene
    root at display time (§7.3). Nothing is rotated here and nothing on disk
    moves.
    """
    model = colmap.find_model(project_path / "sfm")
    if model is None:
        return {"available": False, "count": 0, "cameras": []}

    try:
        images = colmap.read_images(model / "images.bin")
    except (OSError, ValueError, IndexError):
        return {"available": False, "count": 0, "cameras": []}
    if not images:
        return {"available": False, "count": 0, "cameras": []}

    # The mapper does not register in filename order - a real run started 00227,
    # 00165, 00005 (§7.2) - so the path is drawn in the order the frames were
    # *shot*, which is the name order, not the order the model stores them.
    images = sorted(images, key=lambda i: i.name)

    by_sequence = _sequence_index(project_path)
    registered = {image.name for image in images}
    input_names = [p.name for p in frame_files.list_frames(project_path / "frames")]
    missing = [name for name in input_names if name not in registered]
    position_of = {name: i for i, name in enumerate(input_names)}

    cameras: list[dict] = []
    for image in images:
        index = position_of.get(image.name)
        cameras.append({
            "name": image.name,
            "source_name": image.name,
            "position": list(image.position),
            "basis": list(image.rotation),
            "sequence_id": by_sequence.get(image.name),
            # The bridge frames: a registered camera whose neighbour in the
            # shooting order never came back is where the path visibly stops.
            "gap_edge": index is not None and any(
                0 <= n < len(input_names) and input_names[n] in set(missing)
                for n in (index - 1, index + 1)
            ),
        })

    sequence_ids = sorted(
        {c["sequence_id"] for c in cameras if c["sequence_id"] is not None}
    )
    return {
        "available": True,
        "count": len(cameras),
        "cameras": cameras,
        # Kept for the panel's wording, but it is always "name" now: the model
        # stores the input filename and nothing renames anything (see the module
        # docstring).
        "matched_by": "name",
        "gaps_known": bool(input_names),
        "missing_count": len(missing),
        "input_count": len(input_names),
        "sequence_ids": sequence_ids,
        "models": colmap.count_models(project_path / "sfm"),
    }
