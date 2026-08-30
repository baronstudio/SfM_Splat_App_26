"""models_catalog.py — the checkpoints this app can install, and where from.

`spirula.exe` is one 119 MB binary and none of its neural checkpoints are in it
(CLAUDE.md §5.1, §10). Two of the six tools want one:

* **`sam track` / `sam segment` take `--model <file>` and nothing else.** There
  is no id and no fetch on that route at all — read off
  `docs/spirula/sam-help.txt` — so before this module the only way to mask an
  object was to find a checkpoint on the web by hand and paste its path into the
  mask panel.
* **`geometry --model <id|file>` fetches a known id** into
  `%LOCALAPPDATA%\\spirula-studio\\models\\` with a `curl` child, which is the
  one CR-redrawn progress bar in this tool family (§7.5) and 419.4 MB arriving
  in the middle of a pipeline run.

**The manifest below was read out of the installed binary, not off a web page.**
`spirula.exe` carries, for every checkpoint it knows, the local file name, the
URL and — for the geometry models — a sha256, laid out as three
null-terminated strings in a row. That is the tool's own integrity registry, and
using it is what makes a file this app installs indistinguishable from one
spirula fetched itself. Verified rather than assumed: the
`moge2-vitb-normal.onnx` a real `geometry` run had already downloaded hashes to
`bbf14e07…f35a21` over 419 411 850 bytes, exactly the value in the binary.

Every `size_bytes` below is `X-Linked-Size` off a live HEAD against the URL on
the same row, measured 2026-08-30 — so the panel states a real download size
before anything is fetched, and a truncated file is caught by its length alone
even where there is no hash.

**The licences are four, not one, and they are accepted separately** (§10). An
Apache-2.0 checkpoint and a bespoke corporate one are not the same question, and
a single "I agree" spanning both would be answering the harder one by accident.

Pure module: no FastAPI import, and no IO at all.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# `sam3.cpp` publishes both SAM families under one repo; the geometry models
# each carry their own full URL because they come from three different ones.
_SAM_BASE = "https://huggingface.co/PABannier/sam3.cpp/resolve/main/"


class Licence(BaseModel):
    """One licence to read and accept, with the URL that carries its text."""

    id: str
    name: str
    url: str
    # Shown above the accept control. Says what accepting actually means.
    summary: str
    # Whether this app has audited it. `False` puts the row's amber note on
    # screen rather than leaving §13.5's open question invisible.
    audited: bool = True


LICENCES: dict[str, Licence] = {
    "apache-2.0": Licence(
        id="apache-2.0",
        name="SAM 2.1 — Apache-2.0 (Meta)",
        url="https://github.com/facebookresearch/sam2/blob/main/LICENSE",
        summary=(
            "Meta's model under the Apache 2.0 licence. Nothing unusual to "
            "agree to; it is downloaded rather than bundled only to keep this "
            "app small."
        ),
    ),
    "sam3": Licence(
        id="sam3",
        name="SAM 3 — Meta's own licence, NOT Apache-2.0",
        url="https://github.com/facebookresearch/sam3/blob/main/LICENSE",
        summary=(
            "SAM 3 is published under a licence Meta wrote for it, with its own "
            "terms on use and redistribution. It is not Apache-2.0 and it is "
            "not covered by having accepted SAM 2.1's. Read it in full before "
            "downloading this checkpoint."
        ),
    ),
    "mit": Licence(
        id="mit",
        name="MoGe-2 — MIT (Microsoft), via the Ruicheng ONNX mirrors",
        url="https://huggingface.co/Ruicheng/moge-2-vitl-normal-onnx",
        summary=(
            "MoGe-2 upstream is MIT. Measured 2026-08-30 through the "
            "HuggingFace API, the `moge-2-vitl-normal-onnx` mirror declares "
            "`mit` on its card and the `vits` / `vitb` mirrors declare no "
            "licence at all — so the terms are taken from upstream rather than "
            "from the repository these rows actually download."
        ),
        audited=False,
    ),
    "cc0-1.0": Licence(
        id="cc0-1.0",
        name="Metric3D (ONNX) — CC0-1.0",
        url="https://huggingface.co/onnx-community/metric3d-vit-large",
        summary=(
            "The `onnx-community` conversions declare CC0-1.0 on their cards, "
            "measured 2026-08-30 through the HuggingFace API. A public-domain "
            "dedication: nothing to comply with. The upstream Metric3D research "
            "code is a separate question this app never touches."
        ),
    ),
}


class ExtraFile(BaseModel):
    """A second file one checkpoint cannot be used without.

    Only `metric3d-vit-giant2` has one: ONNX external data, 1.36 GB of weights
    beside a 1.4 GB graph. **Its local name is `model_fp16.onnx_data`, not the
    checkpoint's own stem** — that is the name recorded inside the .onnx and the
    name spirula saves it under, so renaming it to match its partner would
    break the very file it belongs to.
    """

    filename: str
    url: str
    sha256: Optional[str] = None
    size_bytes: int


class ModelSpec(BaseModel):
    """One installable checkpoint."""

    id: str
    # Which tool wants it. `sam` is `--model <file>`; `geometry` is
    # `--model <id|file>`, and this id is what the tool itself would fetch.
    family: Literal["sam", "geometry"]
    label: str
    # What it costs and what it buys, in the tool's own terms where it has them.
    blurb: str
    filename: str
    url: str
    # Present for the geometry models, absent for the SAM ones: the binary
    # carries a hash for the first family and not for the second. Where there is
    # none, the byte count is the check.
    sha256: Optional[str] = None
    size_bytes: int
    licence: str
    extras: list[ExtraFile] = []
    # Drawn first in its family and marked. Not a default — nothing here
    # downloads on its own.
    recommended: bool = False

    @property
    def total_bytes(self) -> int:
        return self.size_bytes + sum(e.size_bytes for e in self.extras)


CATALOGUE: list[ModelSpec] = [
    # ── SAM: `sam track --model <file>` (CLAUDE.md §7.4) ─────────────────────
    ModelSpec(
        id="sam3-q4_0",
        family="sam",
        label="SAM 3 (recommended)",
        blurb=(
            "Understands text prompts — type what to mask out. ~2 GB VRAM and "
            "about 1 s a frame on a laptop GPU, 3x slower than any SAM 2.1 "
            "below. Quantised to q4_0, and the only checkpoint here that takes "
            "`--text`."
        ),
        filename="sam3-q4_0.ggml",
        url=_SAM_BASE + "sam3-q4_0.ggml",
        size_bytes=706_606_590,
        licence="sam3",
        recommended=True,
    ),
    ModelSpec(
        id="sam3-f16",
        family="sam",
        label="SAM 3, full precision",
        blurb=(
            "The same network as above at f16 — 2.6x the download and 2.6x the "
            "card for a difference you will have to look for. Take it only if a "
            "quantised mask comes out visibly wrong."
        ),
        filename="sam3-f16.ggml",
        url=_SAM_BASE + "sam3-f16.ggml",
        size_bytes=1_837_873_470,
        licence="sam3",
    ),
    ModelSpec(
        id="sam2.1-large",
        family="sam",
        label="SAM 2.1 Large",
        blurb=(
            "Clicks and boxes only — no text prompt. The most accurate of the "
            "four and the one for thin structure: railings, wires and cables, "
            "foliage. ~470 ms a frame."
        ),
        filename="sam2.1_hiera_large_f16.ggml",
        url=_SAM_BASE + "sam2.1_hiera_large_f16.ggml",
        size_bytes=450_930_592,
        licence="apache-2.0",
    ),
    ModelSpec(
        id="sam2.1-base-plus",
        family="sam",
        label="SAM 2.1 Base+",
        blurb=(
            "Clicks and boxes only. Close to Large on most subjects at two "
            "thirds the time, ~320 ms a frame."
        ),
        filename="sam2.1_hiera_base_plus_f16.ggml",
        url=_SAM_BASE + "sam2.1_hiera_base_plus_f16.ggml",
        size_bytes=163_303_648,
        licence="apache-2.0",
    ),
    ModelSpec(
        id="sam2.1-small",
        family="sam",
        label="SAM 2.1 Small (best value)",
        blurb=(
            "Clicks and boxes only, ~255 ms a frame. The best speed-for-quality "
            "of the four: below Large the frame is mostly tracking, which does "
            "not care how big the backbone is."
        ),
        filename="sam2.1_hiera_small_f16.ggml",
        url=_SAM_BASE + "sam2.1_hiera_small_f16.ggml",
        size_bytes=93_559_264,
        licence="apache-2.0",
        recommended=True,
    ),
    ModelSpec(
        id="sam2.1-tiny",
        family="sam",
        label="SAM 2.1 Tiny",
        blurb=(
            "Clicks and boxes only. Just 4% quicker than Small at ~245 ms a "
            "frame and it loses thin structure first — take it for the download "
            "size, not for the speed."
        ),
        filename="sam2.1_hiera_tiny_f16.ggml",
        url=_SAM_BASE + "sam2.1_hiera_tiny_f16.ggml",
        size_bytes=79_320_544,
        licence="apache-2.0",
    ),

    # ── Geometry: `geometry --model <id|file>` (CLAUDE.md §7.5) ──────────────
    ModelSpec(
        id="moge2-vitb",
        family="geometry",
        label="MoGe-2 ViT-B (spirula's own default)",
        blurb=(
            "About 0.4 s an image. This is what `geometry` fetches for itself "
            "when `--model` is left empty — installing it here is what stops "
            "that 419 MB download landing in the middle of a run."
        ),
        filename="moge2-vitb-normal.onnx",
        url="https://huggingface.co/Ruicheng/moge-2-vitb-normal-onnx/resolve/main/model.onnx",
        sha256="bbf14e07a30f11e69d36ab861590123f5598ababcbc8946a063eb4a966f35a21",
        size_bytes=419_411_850,
        licence="mit",
        recommended=True,
    ),
    ModelSpec(
        id="moge2-vits",
        family="geometry",
        label="MoGe-2 ViT-S",
        blurb=(
            "About 0.12 s an image. Enough to try the idea out; the normals are "
            "visibly coarser."
        ),
        filename="moge2-vits-normal.onnx",
        url="https://huggingface.co/Ruicheng/moge-2-vits-normal-onnx/resolve/main/model.onnx",
        sha256="24eacb5dc7a2c54c7bc98f7de085ffbed79ad006ea5b664c2c2cdc02ff3a52f0",
        size_bytes=140_852_051,
        licence="mit",
    ),
    ModelSpec(
        id="moge2-vitl",
        family="geometry",
        label="MoGe-2 ViT-L",
        blurb=(
            "The largest MoGe-2 here. Larger is slower and only somewhat "
            "better, in the tool's own words — worth it on a capture whose "
            "normals came out mushy, not by default."
        ),
        filename="moge2-vitl-normal.onnx",
        url="https://huggingface.co/Ruicheng/moge-2-vitl-normal-onnx/resolve/main/model.onnx",
        sha256="afbc4ccc3450298f3afb35b90f015f4c4f552dea21dc6470d5f7b78b77e2d751",
        size_bytes=1_324_265_014,
        licence="mit",
    ),
    ModelSpec(
        id="metric3d-vit-small",
        family="geometry",
        label="Metric3D ViT-S",
        blurb=(
            "The other family: metric depth, and the sky written as no ground "
            "truth rather than as a wall. Metric3D ignores `--num-tokens`."
        ),
        filename="metric3d-vit-small-fp16.onnx",
        url="https://huggingface.co/onnx-community/metric3d-vit-small/resolve/main/onnx/model_fp16.onnx",
        sha256="4afcc0893dbb3c0c63e270f8bb24bfa63ccf2dc68ab9c0c3601fdae4f0aafd9b",
        size_bytes=75_778_144,
        licence="cc0-1.0",
    ),
    ModelSpec(
        id="metric3d-vit-large",
        family="geometry",
        label="Metric3D ViT-L",
        blurb="Metric depth at eleven times the small model's download.",
        filename="metric3d-vit-large-fp16.onnx",
        url="https://huggingface.co/onnx-community/metric3d-vit-large/resolve/main/onnx/model_fp16.onnx",
        sha256="bede661725a7e1808f2cd69d07f3d2a6e5b011a1bde4128e5df9d21232224d8e",
        size_bytes=825_204_259,
        licence="cc0-1.0",
    ),
    ModelSpec(
        id="metric3d-vit-giant2",
        family="geometry",
        label="Metric3D ViT-g2",
        blurb=(
            "The largest checkpoint in this list and the only one that is two "
            "files: 1.4 GB of graph plus 1.36 GB of ONNX external data, 2.8 GB "
            "installed. The sidecar keeps the name `model_fp16.onnx_data` "
            "because that is the name recorded inside the .onnx itself."
        ),
        filename="metric3d-vit-giant2-fp16.onnx",
        url="https://huggingface.co/onnx-community/metric3d-vit-giant2/resolve/main/onnx/model_fp16.onnx",
        sha256="8ae9cb119397b42dedede351ea648d3cfcb26b6fa7d2eb537d43508c7fe4f72e",
        size_bytes=1_401_125_724,
        licence="cc0-1.0",
        extras=[
            ExtraFile(
                filename="model_fp16.onnx_data",
                url="https://huggingface.co/onnx-community/metric3d-vit-giant2/resolve/main/onnx/model_fp16.onnx_data",
                sha256="7ebc3f5e95bd919d14b2aa5c1837cbb2c31f9276a5600adf4418b2b78353f4fd",
                size_bytes=1_355_808_768,
            ),
        ],
    ),
]

BY_ID: dict[str, ModelSpec] = {m.id: m for m in CATALOGUE}


def get(model_id: str) -> ModelSpec:
    """The spec for an id, or a ValueError naming every id there is."""
    spec = BY_ID.get(model_id)
    if spec is None:
        raise ValueError(
            f"Unknown checkpoint id {model_id!r}. Known ids: "
            + ", ".join(sorted(BY_ID))
        )
    return spec
