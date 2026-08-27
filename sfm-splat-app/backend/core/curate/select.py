"""
select.py — merge the verdicts into a selection, manual overrides always win.

`scores.json` is the measurement record: what the analysis *saw*. It is
regenerated on every analysis run.

`selection.json` is the decision record: what gets shipped to RealityScan. It is
derived from the scores plus `overrides.json`, and can be rebuilt on its own
whenever the user flips a single frame — no image is re-read for that.

`overrides.json` is never regenerated and always wins (CLAUDE.md §5).
"""

from datetime import datetime, timezone
from typing import Optional, Sequence

REASON_BLUR = "blur"
REASON_REDUNDANT = "redundant"
REASON_MANUAL = "manual"
WARNING_GAP = "gap"

KEEP = "keep"
DROP = "drop"


def build_scores(
    filenames: Sequence[str],
    sequence_ids: Sequence[int],
    sharpness: Sequence[float],
    sharpness_median: Sequence[float],
    displacements: Sequence[Optional[float]],
    blur: Sequence[bool],
    redundant: Sequence[bool],
    gap: Sequence[bool],
) -> list[dict]:
    """One record per frame, in extraction order."""
    records: list[dict] = []
    for i, filename in enumerate(filenames):
        if blur[i]:
            verdict, reason = "rejected", REASON_BLUR
        elif redundant[i]:
            verdict, reason = "rejected", REASON_REDUNDANT
        else:
            verdict, reason = "kept", None
        records.append({
            "index": i,
            "filename": filename,
            "sequence_id": int(sequence_ids[i]),
            "sharpness": round(float(sharpness[i]), 4),
            "sharpness_median": round(float(sharpness_median[i]), 4),
            "displacement_pct": None if displacements[i] is None else round(float(displacements[i]), 3),
            "auto_verdict": verdict,
            "auto_reason": reason,
            "warning": WARNING_GAP if gap[i] else None,
        })
    return records


def build_selection(scores: Sequence[dict], overrides: Optional[dict] = None) -> dict:
    """Apply the overrides on top of the auto verdicts and summarise.

    An override that merely agrees with the automatic verdict is still recorded
    as `overridden`, so the UI can show the user which frames they have touched.
    """
    overrides = overrides or {}

    kept: list[str] = []
    rejected: list[dict] = []
    warnings: list[dict] = []
    counts = {
        "kept": 0,
        "rejected_blur": 0,
        "rejected_redundant": 0,
        "rejected_manual": 0,
        "kept_manual": 0,
        "warning_gap": 0,
    }
    sequences: dict[int, dict] = {}

    for record in scores:
        filename = record["filename"]
        verdict = record["auto_verdict"]
        reason = record["auto_reason"]
        override = overrides.get(filename)

        if override == KEEP:
            verdict, reason = "kept", REASON_MANUAL
        elif override == DROP:
            verdict, reason = "rejected", REASON_MANUAL

        sid = int(record.get("sequence_id", 0))
        seq = sequences.setdefault(sid, {"id": sid, "frame_count": 0, "kept": 0})
        seq["frame_count"] += 1

        if verdict == "kept":
            kept.append(filename)
            counts["kept"] += 1
            seq["kept"] += 1
            if reason == REASON_MANUAL:
                counts["kept_manual"] += 1
            if record.get("warning") == WARNING_GAP:
                warnings.append({"frame": filename, "reason": WARNING_GAP, "index": record["index"]})
                counts["warning_gap"] += 1
        else:
            rejected.append({"frame": filename, "reason": reason, "index": record["index"]})
            if reason == REASON_BLUR:
                counts["rejected_blur"] += 1
            elif reason == REASON_REDUNDANT:
                counts["rejected_redundant"] += 1
            elif reason == REASON_MANUAL:
                counts["rejected_manual"] += 1

    total = len(scores)
    removed = total - counts["kept"]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kept": kept,
        "rejected": rejected,
        "warnings": warnings,
        "sequences": [sequences[k] for k in sorted(sequences)],
        "summary": {
            "total": total,
            "removed": removed,
            "removed_pct": round(removed / total * 100.0, 2) if total else 0.0,
            **counts,
        },
    }


def pass_through_selection(filenames: Sequence[str]) -> dict:
    """Selection for a project with curation disabled: everything is kept.

    Downstream steps read selection.json unconditionally, so it must exist and
    be well-formed even when no analysis ever ran.
    """
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "curation": "disabled",
        "kept": list(filenames),
        "rejected": [],
        "warnings": [],
        "sequences": [{"id": 0, "frame_count": len(filenames), "kept": len(filenames)}],
        "summary": {
            "total": len(filenames),
            "removed": 0,
            "removed_pct": 0.0,
            "kept": len(filenames),
            "rejected_blur": 0,
            "rejected_redundant": 0,
            "rejected_manual": 0,
            "kept_manual": 0,
            "warning_gap": 0,
        },
    }
