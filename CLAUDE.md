# CLAUDE.md — SfM Splat Pipeline App

> Local-first web app that drives a full video/photos → SfM → 3D Gaussian Splatting →
> mesh → Blender scene pipeline on **one external binary plus FFmpeg**: import, extract
> and curate frames, align, train, mesh, assemble the Blender scene.
>
> Owner: JB (baronstudio). Single user, Windows workstation, local GPU.
>
> Successor to `3DGS_App_26`, which is still alive and still used. The doctrine here is
> inherited from that project's own CLAUDE.md; everything in it about RealityScan and
> LichtFeld Studio is history, everything else is law. Where a measurement was made
> there and still applies, this file carries the number and says where it came from.

---

## 1. What this app is

A 6-step wizard (React) driving a FastAPI backend that orchestrates local binaries as
subprocesses:

```
video/images ─> [2] extract+curate ─> [3] SfM ─> [4] train ─> [5] mesh ─> [6] export+scene
                    FFmpeg (ours)      spirula   spirula      spirula     Blender
```

The single reconstruction tool is **Spirula Studio** (`spirula.exe`,
https://github.com/harry7557558/spirula-studio, GPL-3.0). It is **Vulkan, not CUDA**,
and that is the whole reason this project exists. `3DGS_App_26` requires an NVIDIA GPU
in two independent places — RealityScan needs CUDA to mesh, LichtFeld Studio needs CUDA
to exist at all. This app runs on NVIDIA, AMD, Intel and Apple silicon.

That claim is measured on this workstation rather than repeated from a README. The
shipped binary imports `vulkan-1.dll` and no CUDA library, and `spirula sam devices`
enumerates and accepts both GPUs in this machine:

```
idx name                                       type        vram      status
0   Intel(R) UHD Graphics 770                  integrated    11.9 G  ok
1   NVIDIA GeForce RTX 4060 Laptop GPU         discrete       7.8 G  ok
```

The integrated Intel part is not merely listed, it is `ok` against the tool's own
baseline (Vulkan 1.2 core with `bufferDeviceAddress` and `timelineSemaphore`).

**360° / fisheye capture is a first-class input**, not an afterthought: spirula reads
equirectangular and >180° fisheye natively, with no undistortion pass. Step 2 must not
assume a rectilinear frame, and §7.4's shape masking exists for the lens border.

### Non-goals

- **No RealityScan, no LichtFeld Studio, no CUDA dependency, ever.** If a feature can
  only be had by adding one of them back, it is out of scope, not a compromise.
- No multi-user, no auth, no job queue. One user, one running job at a time.
- **No VPS / remote deployment.** Local GPU, local binaries. Only hygiene kept: no
  hardcoded `localhost` in the frontend API client — it talks to its own origin
  (`3DGS_App_26`, 2026-08-22).
- No 3D *editor*. The viewer looks, it never writes.
- **We do not use `spirula sam extract`**, although it exists and writes the sharpest
  frames of a video. Step 2 is ours: it carries a measured `-hwaccel` (5×), the `scdet`
  cut capture riding along with the extraction, and the whole curation model. Handing
  that to a second tool would trade three measurements for one flag.

---

## 2. Core principles (do not violate)

1. **No superfluous dependencies.** Every new dependency is justified and added to the
   licence audit table (§10) in the same commit.
2. **No simulation layer.** Every step calls the real tool. There is no stub flag and no
   fake output — a missing or misconfigured binary fails the step with the path it
   looked for. (`3DGS_App_26` shipped stubs and deleted them on 2026-08-22: three of the
   four had drifted from the tools they claimed to simulate, and a simulation nobody
   trusts is a second, wrong implementation of every step.)
3. **`projects/` is sacred.** `sfm-splat-app/projects/` holds all user data and must
   NEVER be touched by a clean or reset script.
4. **Pipeline steps are pure-ish.** Modules under `backend/core/steps/` and
   `backend/core/curate/` must not import FastAPI. They receive `broadcast_fn` by
   injection — it is what makes them callable from tests.
5. **Simplicity over throughput.** No queues, no worker pools, no caching "for later".
6. **Every job is cancellable**, and abort kills the tool's **process tree**
   (`taskkill /F /T`), because that is what closes the pipe and unblocks the reader. It
   is not optional here: `spirula geometry` shells out to `curl` for its checkpoint, so
   the process that holds the work is not always the one we spawned.
7. **Read the installed build, never remember it.** `spirula --version` is logged at the
   top of every run and recorded in the step result. No flag is invented; `docs/spirula/`
   holds the raw `--help` of every command and is the reference (§5.1).
8. **Measure, then write it down.** Every structural decision gets a row in the decisions
   log (§12) **in the same commit**, with the numbers that settled it. A claim without a
   measurement behind it does not go in this file.

---

## 3. Stack

| Layer | Choice | Note |
|---|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn | `backend/main.py`, `.venv` at app root |
| Persistence | SQLite + SQLModel (`pipeline.db`) for projects | **JSON files** for per-frame data (§5) |
| App config | `config.json` — tool paths | Route `/api/settings` |
| App defaults | `defaults.json` — per-step business defaults | Route `/api/defaults` (§4) |
| Realtime | **WebSocket** `/ws/logs` (`backend/api/websocket.py`) | The bus is wired through the store, LiveLog and ProgressBar |
| Video | FFmpeg + ffprobe (system exe, subprocess) | Path in `config.json` |
| Curation | OpenCV (Tenengrad, ORB) + NumPy + PySceneDetect | Inherited whole |
| SfM / training / meshing / masking / geometry | **spirula.exe**, one binary, six tools | `step_sfm.py`, `step_train.py`, `step_mesh.py`, `step_sam.py`, `step_geometry.py` |
| Scene | Blender + `blender_splatforge.py` | `step_scene.py` |
| Frontend | React 18 + TS, Vite, Tailwind v4, shadcn/ui, Zustand, recharts | `frontend/` |
| Viewer | `three` + `@mkkellogg/gaussian-splats-3d` | §7.8 |
| Run | `start.bat` (Windows) / `start.sh` | Not a Makefile — this is a Windows-first app |

**React 18, not 19.** Any shadcn component pasted from the v4 docs must be wrapped in
`forwardRef` — `ui/button.tsx` already is. React 18.3 strips a `ref` passed as a plain
prop, which leaves every `<DropdownMenuTrigger asChild><Button/></...>` with no anchor
element, and an unanchored Radix Popper parks its content at `translate(0, -200%)`, off
the top of the page. The menu opens perfectly, where nobody can see it
(`3DGS_App_26`, 2026-08-21).

---

## 4. Settings model — three layers, explicit precedence

Three distinct things, three homes. Do not merge them.

| Layer | File / store | Contents | UI |
|---|---|---|---|
| **Installation** | `config.json` | binary paths, `ffmpeg_hwaccel`, the spirula model cache | Setup panel → "Tools" |
| **Defaults** | `defaults.json` | Business defaults per wizard step + capture presets + the 3D viewer | Setup panel → one section per step |
| **Per project** | `Project.settings_json` (SQLite) | What the user changed for THIS project | Wizard step "Advanced" panels |

**Precedence: per-project > defaults > code fallback.** A project stores only the keys it
actually overrides — never a full copy of the defaults, or changing a default would stop
propagating to existing projects. The panels PATCH a **diff** (`deepDiff`), debounced
300 ms and flushed on unmount, on a project switch and on `beforeunload`; there is no
Save button, because a panel that must be saved is a panel that gets lost
(`3DGS_App_26`, 2026-08-24).

`SECTIONS` is `extract, curate, sfm, sam, geometry, train, mesh, export, blender, viewer`.

The setup panel is opened by the **gear icon in the WizardShell top bar**.

---

## 5. Data layout

```
sfm-splat-app/
├── config.json                 # installation (binary paths, model cache)
├── defaults.json               # business defaults + capture presets
├── pipeline.db                 # SQLite: project registry only
├── backend/
│   ├── main.py                 # FastAPI app, routers, /static mount
│   ├── api/routes/             # projects, pipeline, settings, defaults, files
│   ├── api/websocket.py        # broadcast bus
│   ├── api/file_handles.py     # the AsyncFile close fix (§7.8)
│   ├── core/config.py          # config.json  (AppConfig singleton)
│   ├── core/defaults.py        # defaults.json (AppDefaults) + fps resolver
│   ├── core/proc.py            # spawn / iter_lines / kill_tree  (§15.1)
│   ├── core/probe.py           # ffprobe wrapper (pure)
│   ├── core/pipeline_runner.py # orchestrator, abort
│   ├── core/steps/             # step_extract, step_conform, step_analyze,
│   │                           #   step_sfm, step_train, step_mesh,
│   │                           #   step_sam, step_geometry, step_export, step_scene
│   └── core/curate/            # sharpness, scenes, overlap, select  (pure, no FastAPI)
├── frontend/src/…
├── tools/spirula/spirula.exe   # ⚙ vendored, gitignored (119 MB, §5.1)
├── projects/_archives/         # ⚙ <slug>.zip of archived projects (§14)
└── projects/<slug>/            # ⚠ user data — never auto-deleted
    ├── input/                  # source video(s), or an imported image set:
    │   └── <set>/             #   images renamed <set>_0001.png + imageset.json
    ├── frames/                 # ⭑ extracted frames — THE image directory (§5.2)
    ├── masks/                  # ⭑ one greyscale PNG per frame, same basename.
    │                          #   Written by step 2 (an imported set's alpha) or by
    │                          #   `spirula sam` (§7.4). Never inside frames/.
    ├── analysis/               # curation JSON + mask_result.json — see below
    ├── report/                 # report.json + report.md
    ├── sfm/                    # step 3 — the `spirula sfm auto` workspace:
    │   ├── features/          #   one .bin per image
    │   ├── matches.bin
    │   ├── sparse/0..N/       #   COLMAP model, BINARY (cameras/images/points3D.bin)
    │   ├── depths/  normals/  #   step 4's geometry panel writes here (§7.5)
    │   ├── sfm_result.json    #   exit code, registered/total, reprojection error
    │   └── geometry_result.json # how the last `spirula geometry` run went (§7.5)
    ├── train/                  # step 4 — `--output-dir-prefix`
    │   └── run/               #   `--output-dir-name`: config.json + step-%09d.ckpt/
    ├── mesh/                   # step 5 — `--output` (§7.8: never left to default):
    │                          #   mesh.glb / .ply / .obj / .gltf + mesh_result.json
    ├── export/                 # steps 5 and 6 share it: splat.ply + mesh.* (hard links,
    │                          #   §7.10), then step 6's scene.blend + README
    └── preview/                # ⚙ generated: browser-sized copies for the viewer,
        └── sources/           #   plus poster frames and cached ffprobe. Cache.
```

```
projects/<slug>/analysis/
├── probe.json        # ffprobe output of the source video
├── extract.json      # what the extraction actually did: resolved working fps, source
│                     #   path, mpdecimate flag, jpeg quality, scale %, frame count
├── scene_scores.json # FFmpeg `scdet` score per *source* frame, captured by the
│                     #   extraction on frames it was decoding anyway (§6.6)
├── scores.json       # per frame: index, filename, sharpness, displacement_pct, sequence_id
├── selection.json    # kept[] / rejected[{frame, reason}] — regenerated on each analysis
├── overrides.json    # manual keep/drop from the UI — NEVER regenerated, always wins
└── mask_result.json  # how the last `spirula sam` run went (§7.4). Here rather than in
                      #   masks/, which holds one greyscale PNG per frame and nothing else
```

**Why per-frame data is JSON and not SQL:** a single project produces thousands of frame
records, written once per analysis run and read as a block. They do not belong in the
`settings_json` blob, and giving them SQL tables would buy nothing but migrations.

### 5.1 The binary

`spirula-2026.8.23-windows-vulkan-x86_64.zip` is 36 327 606 bytes and contains **exactly
one file**, `spirula.exe`, 119 462 912 bytes (sha256 of the zip
`682f2602622a155f1dc529c224e2da544274dbfa27cc141a2363370d85002125`). No build from
source, no Vulkan SDK on the target machine — the SDK is a *build* dependency, and
`vulkan-1.dll` ships with every modern GPU driver. Only the VC++ 2015-2022
redistributable.

All six tools are in that one file: `gui`, `sfm`, `train`, `sam`, `geometry`, `mesh`.

`tools/` is gitignored. The predecessor's initial import tracked 27 393 files of which
27 096 were build artefacts, and undoing that cost a commit; `spirula.exe` is over
GitHub's hard 100 MB per-file limit anyway. `setup.py` fetches it.

**`docs/spirula/` is this project's `docs/rs/`.** It holds the raw, dated output of every
`--help` on the installed build, and it is what stops the next session from guessing a
flag. Regenerate it whenever the binary is updated.

### 5.2 The layout follows from one measurement

`--image-dir` **accepts an absolute path**, and that decides everything above. Measured
2026-08-27 on `v2026.8.23`, three runs against a dataset whose `--data` folder holds a
`sparse/0` and no `images/` at all:

| Run | Result |
|---|---|
| `--data ds --image-dir <absolute path outside ds>` | `Cameras: 251`, trains, exit 0 |
| no `--image-dir` (default `images`) | `error: ColmapParser: ds\images\00000.png does not exist (set --image-dir if needed)`, exit 1 |
| `--image-dir C:/nope/does/not/exist` | `error: ColmapParser: C:/nope/does/not/exist\00000.png does not exist`, exit 1 |

So a relative value is joined onto `--data` and an absolute one is used as it is, and a
wrong one fails loudly with the path it resolved — no silent fallback.

**The consequence is that there is no second copy of the images anywhere.** RealityScan
forced one: it undistorted every frame into its own COLMAP dataset, and step 4 trained on
that duplicate (`3DGS_App_26` §7.2). Here step 3 writes a workspace of features and a
sparse model beside `frames/`, and step 4 trains on `frames/` directly. On the 251-image
project measured below that is 226 MB not written twice — and on a 4K project it is tens
of gigabytes.

Three more properties of the layout, each read off the tool rather than chosen:

- **`sfm auto` adopts `masks/` as a sibling of the image directory**, automatically and
  without being named. `frames/` and `masks/` are already siblings, so the mask route
  costs no flag. `--no-masks` is what refuses them.
- **`train` finds the COLMAP model** by probing `{sparse/0, colmap/sparse/0, sparse,
  colmap, .}` under `--data`, so `--data <project>/sfm` finds `sfm/sparse/0` with no
  flag. Proven by the run in the table above.
- **`--mask-dir` takes an absolute path outside `--data`, and that is measured too.**
  Three 200-iteration runs each way on one 238-image dataset (2026-08-28): with a real
  absolute mask directory psnr came out **15.12 / 17.48 / 15.18**, with an absolute path
  that does not exist **22.40 / 22.68 / 20.33** — the runs are not deterministic but the
  bands are disjoint by ~2.9 dB, which is masked corners trained as empty space against an
  image that is not empty there. `--depth-dir` and `--normal-dir` are still assumed by
  symmetry and are not on the live path: `sfm/depths` and `sfm/normals` sit inside `--data`
  and keep their relative defaults. **The trap in the second row is the one to remember:
  a wrong `--mask-dir` exits 0 and trains unmasked**, because `--load-masks` is documented
  "use the dataset's masks when they exist" — so step 4 logs the mask count.

- **`geometry` is the exception to all of this.** It has no `--image-dir` at all and
  resolves `<dataset>\images\<name>`, which §7.5 measures and works around with a
  junction that lives only for the length of the run.

---

## 6. Step 2 — Extraction + curation

Inherited from `3DGS_App_26` §6 essentially unchanged, because none of it was about
RealityScan. It runs as one job with two phases, and the analysis phase is independently
re-runnable. The measurements below were made on that project's workstation and are
carried here as history, not re-derived.

### 6.1 Extraction

- FFmpeg, working fps resolved by policy (§6.2), JPEG quality, output scale, optional
  max frames.
- **JPEG quality and output scale are two different knobs.** `quality` is `-qscale:v`,
  the mjpeg quantiser: file weight and compression artefacts, never pixel dimensions.
  `scale_percent` is the resolution written to disk, applied *after* the fps gate so only
  the frames that survive it are resized. 100 % adds no `scale` clause at all. Both sides
  are truncated to an even number (`trunc(iw*f/2)*2`) — the mjpeg encoder writes yuvj420p
  and refuses an odd side.
- **Hardware decoding is an installation setting** (`config.json` → `ffmpeg_hwaccel`),
  not a per-project one: it describes the GPU in the machine. `none` sends no flag;
  anything else becomes `-hwaccel <name>` on the input. Deliberately *not* paired with
  `-hwaccel_output_format`, so frames come back to system memory and the whole filter
  chain is untouched. Measured on 20 s of 4K/100fps 10-bit HEVC: decode alone
  **92.9 s → 20.5 s**, the real extraction shape **95.5 s → 17.9 s**. FFmpeg treats
  `-hwaccel` as a *preference* — measured on a 4080×4080 h264 source, NVDEC answered
  `CUDA_ERROR_INVALID_VALUE`, FFmpeg decoded in software and **exited 0 with correct
  frames** — so step 2 matches that line, warns **on the line itself** — a step that fails later
  for an unrelated reason never reaches an end-of-run note, and the red NVDEC line is
  then read as the cause — and records `hwaccel_fell_back` in `extract.json`. A silent
  5× regression is worse than a loud failure. Re-confirmed 2026-08-28 on a 3840×2880
  h264 source: `cuvidCreateDecoder … CUDA_ERROR_INVALID_VALUE`, software fallback,
  **exit 0 with correct frames**.
- **`mpdecimate` defaults to OFF.** It duplicates the overlap gate's job and drops frames
  non-deterministically, breaking the frame-index ↔ timecode mapping that scene detection
  and the timeline depend on.

### 6.2 Working fps policy

| Mode | Meaning |
|---|---|
| `auto` *(default)* | `fps = target_frame_count / duration_s`, clamped to the preset bounds, from ffprobe |
| `ratio` | `fps = fps_ratio × source_fps`. Default ratio **0.2** — JB's habitual value. On a 100 fps rush that is 20 img/s |
| `absolute` | A literal fps typed by the user |

**A working fps that cannot place one frame inside the source is refused before
anything is deleted** (`check_fps_yields_frames`): FFmpeg does not write zero frames,
it fails — `mjpeg: Task finished with error code: -22` and `Nothing was written into
output file`, exit non-zero. Measured 2026-08-28: `fps=0.02` over 20 s of a 4K source
wrote nothing and exited non-zero, the same filter over 60 s wrote one frame and
exited 0. The probe and the fps resolution therefore sit **above** the step-2 reset.

`ratio` is the fallback whenever ffprobe fails or returns no duration. **Capture presets**
carry the target frame count and the overlap band together, because they are two views of
the same thing: `orbit_drone`, `handheld_walk`, `turntable`, `interior_scan`.

### 6.3 Analysis (curation)

Runs automatically after extraction, and can be re-run alone from a "Re-analyse" button —
thresholds are tuned iteratively and re-extracting frames to change one number is
unacceptable.

1. **Scenes** — from `analysis/scene_scores.json` when the extraction captured it (§6.6),
   otherwise PySceneDetect `AdaptiveDetector` on the source video, then a histogram
   fallback over the frames. Each cut splits the footage into a *sequence*.
2. **Sharpness** — Tenengrad on greyscale, downscaled to ≤1080 px. Rejection is
   **relative**: below the rolling median of a 15-frame window by more than the
   sensitivity factor → `rejected:blur`. **Never ship an absolute threshold as a
   default** — it does not generalise across content.
3. **Overlap gate** — per sequence, median ORB feature displacement (% of frame width)
   against the last kept frame: `< min_step` (2 %) → `rejected:redundant`; inside the
   band (2–12 %) → keep; `> band_max` → keep, flagged `warning:gap`.
4. **Select** — merge verdicts into `selection.json`; `overrides.json` always wins.

### 6.4 Step 2 UI

One step, two panes: extraction settings + launch, then the frame gallery with per-frame
verdicts, the sharpness timeline (recharts) with cut markers, and per-frame manual
override.

### 6.5 The input sources panel

Step 2 says what it is about to read before it reads it. `/api/files/{id}/sources` lists
every file in `input/` with its ffprobe reading and a poster frame (pulled a tenth of the
way in — frame 0 is the operator still reaching for the camera), and badges the one video
the extraction will consume. `find_extraction_source` in `core/sources.py` is called by
both the panel and `step_extract`, so the badge cannot drift from the file FFmpeg opens.
Probe and poster are cached under `preview/sources/` on an mtime+size fingerprint.

### 6.6 Cut detection rides along with the extraction

Curation used to decode the source video a second time: PySceneDetect measured **318 s**
on a 52 s 4K/100fps rush. The extraction now `split`s its decoded stream — one branch is
the unchanged `fps`/`mpdecimate`/`scale` chain, the other scales to 180 px and runs
FFmpeg's `scdet`, whose per-frame score `metadata=print` writes out. That branch costs
**~5 s per 20 s of 4K source**. End to end on a 20 s clip, step 2 went from **220 s to
26 s**.

- **Scores are stored, not cuts.** `scdet=threshold=100` never fires; the thresholding
  happens at analysis time, so a threshold stays tunable from a re-analysis alone.
- **A cut must clear a relative bar *and* an absolute one.** Median + 8·MAD, and a floor
  of 6. Two real hard cuts scored **14.59** and **13.14**; the highest score anywhere
  across four genuinely continuous rushes was **2.51** (medians 0.009–0.066). Errors are
  asymmetric: a missed cut is cheap, an invented one resets the overlap gate mid-shot.
- **The scores are checked for truncation before they are trusted.** FFmpeg rebuilds the
  filter graph when the input's resolution, pixel format or SAR changes mid-stream, and
  the rebuilt filter **reopens its file in write mode** — measured on a spliced source, a
  720-frame video left 240 scores whose first entry sat at t=16 s. The series is refused
  unless it starts at the top and reaches 90 % of the probed duration.

The metadata file is named **relative**, with `analysis/` given to FFmpeg as its working
directory: a filter option value is parsed for `:` and for the escape character, so an
absolute Windows path would have to be escaped into the filtergraph.

### 6.7 Image sets — when the frames already exist

Not every project starts from a video. Step 2 **conforms** an image set instead of
extracting — same `frames/`, same curation after it, so nothing downstream knows which
branch ran.

**Three doors, because they are three different costs.** A **folder path** is read
server-side (the app runs on the workstation that holds the files, so a 20 GB set is a
local copy, never an upload). A **zip** is one upload and one unpack. A **file
selection**, including the browser's folder picker, is the slow lane by construction and
is kept because it is the only one that works from another machine on the LAN.

**The images are renamed on the way in**, to `input/<set>/<set>_0001.png` — zero-padded
and contiguous, which is what makes the set readable by FFmpeg's `image2` demuxer as a
*single* input: 900 images convert in one subprocess with a real `-progress` channel
instead of 900 subprocesses with none. `imageset.json` keeps the mapping back to the
original filenames. A set that is not a clean sequence falls back to file-by-file, with a
line in the log.

**Every image is a frame.** The fps policy does not apply and is not shown; `max_frames`
is the only gate. `extract.json` records `working_fps: null` and `input_video: null`,
which routes curation to the frames-only cut detector. `probe.json` is written
`synthetic: true` at a nominal 30 img/s — a unit for the panel, not a claim.

**The conform copies rather than re-encodes when nothing has to change.** At 100 % scale
with a matching format the frames are hard-linked (falling back to a copy): re-encoding a
JPEG at `-qscale:v 2` is generation loss for no gain, and 900 20-megapixel PNGs is 18 GB
that does not need to exist twice.

**Alpha becomes `masks/`, and here that is the whole story.** When an imported set is PNG
with a real alpha channel, step 2 asks — inline, next to the estimate it changes — and
extracts the channel into `projects/<slug>/masks/`, one greyscale PNG per frame with the
frame's basename, in one `alphaextract` pass. That is the layout both `sfm auto` and
`train` read (§5.2), and there is nothing else to do with it.

Two repairs the predecessor needed and this project does not, both because the tool is
different:

- **No dimension check, and no `fit_dataset_masks`.** LichtFeld Studio refused a mask
  whose size did not match its image; spirula **resizes** instead —
  `DataManager.cpp` upsamples a smaller mask (nearest for masks, bilinear for depth and
  normal). There is nothing to fit.
- **No alpha-as-mask trap.** There is no alpha-as-mask behaviour anywhere in spirula:
  masks come from `--mask-dir` files only. The constant-255-alpha bug that made four
  measured `3DGS_App_26` runs silently unmasked (2026-08-26) cannot happen here.

---

## 7. Steps 3 to 6 — the spirula pipeline

Everything in this section was read off `v2026.8.23` on this workstation, and the
commands were run. `docs/spirula/` holds the raw output; where a number appears below it
came from a run recorded there.

### 7.0 Four rules that apply to every spirula invocation

1. **`--lang en`, always.** Every line the tool prints is localized — by `--lang`, else
   `SS_LANG`, else the OS. On JB's French Windows the default output is French:
   `spirula --help` answers `Commandes :`, `ouvrir l'application`, `par défaut`. Any
   regex matching an English progress line would match nothing. Pin the flag on every
   invocation and say why in the comment.
2. **`--disable-viewer 1` on every `train`.** `keep_viewer_alive` defaults **1** and
   `disable_viewer` defaults **0**, so after a *successful* run the trainer prints
   `Training complete. Viewer still running -- press Ctrl-C to exit.` and never returns.
   Measured: a 1-iteration run was still alive when a 90 s timeout fired (exit 124), the
   splat written and the checkpoint saved. A subprocess-driven step hangs forever
   otherwise.
3. **The viewer binds `0.0.0.0`, not localhost.** Its own line is
   `Viewer at http://0.0.0.0:7007/ (forward the port for remote boxes: ...)`. Disabling
   it settles this; if it is ever wanted in-app it must be proxied, never exposed.
4. **Flags are flattened** (`--sh-degree`, never `--model.sh-degree`); `-` and `_` are
   interchangeable; **bools take 0/1**; `none` clears an optional; tuples take N values.
   `sfm auto` is the exception — its bools are bare `--no-x` switches, not value-takers.

### 7.1 Step 3 — `spirula sfm auto`

```
spirula --lang en sfm auto <frames_dir> -o <project>/sfm [--quality Q] [--data-type T] ...
```

Writes `sfm/{features/, matches.bin, sparse/0..N}`. A capture that is not one connected
view graph also writes `sparse/1`, `sparse/2`… — the remaining components, largest first.

Measured end to end on 251 images of ~1 Mpx at `--quality medium`:

```
[run]  Extraction: 5.83 s   Images: 251   Features: 1005425
[run]  Matching:   7.26 s   Pairs kept: 3518/3542   Inliers: 1615416/1750330
[run]  Mapping:   21.54 s   Registered: 251/251 images   Points: 84359   Cameras: 2
[run]  Total: 34.62 s
[run]  Reprojection error: mean 0.502 px, median 0.392 px, over 488928 observations
[run]  RESULT: OK -- 100% of the images registered, 0.50 px mean reprojection
```

**The sparse model is COLMAP BINARY**, `cameras.bin` / `images.bin` / `points3D.bin` —
not the text form RealityScan wrote. Anything of ours that reads a model reads `.bin`.

**`sfm auto` grades its own result in the exit code**, which is `3DGS_App_26` §7.1's
coverage check made native:

```
0  a reconstruction that looks sound
1  usage or runtime error
2  no reconstruction at all
3  partial: under half the images registered, or over 2 px mean reprojection
```

**Exit 3 warns, names the number, and never fails the pipeline.** Same reasoning as the
predecessor's `alignment_check.json`: blocking on a partial reconstruction stops the
pipeline over a handful of unalignable frames, and the decision to re-run is the user's.
`sfm/sfm_result.json` persists the exit code, the registered/total pair and the
reprojection error, because the answer to "did this actually work" must outlive the
scrollback that announced it.

**Two knobs are the interface, not the whole surface.** `--quality`
(`low|medium|high|extreme`, default **high**) and `--data-type`
(`individual|video|internet`) set the working resolution, feature budget and pair
selection, and the run **reports what they moved**:

```
[run]  The presets set --max-image-size to 1600 (was 0)
[run]  The presets set --max-features to 4096 (was 8192)
[run]  The presets set --prefilter-neighbors to 24 (was 32)
```

Everything they set can be overridden by naming the flag. `docs/spirula/sfm-auto-help.txt`
lists all of it — pipeline, colour, camera, features, matching, mapper, manage, merge,
runtime. We model the handful in `SfmDefaults` and leave the rest at the build's own
values until a measurement says otherwise.

**The lens model is a setting, because 360 is a first-class input.**
`--camera-model` takes `equirectangular`, `opencv-fisheye` and `thin-prism-fisheye`
alongside the rectilinear models, so a 360 rig needs no undistortion pass anywhere in
this pipeline.

**Camera *groups* are not components.** `--camera-mode folder` splits on image resolution
first, which is why the 251-image run above reports `Cameras: 2` from a single folder —
two intrinsics, not two reconstructions. What splits the output is a `sparse/N` with
`N > 0`. The predecessor's whole "groups are not components" section (§7.1) survives
intact; only the vocabulary moved.

### 7.2 Step 3's progress channel

`sfm auto` prints tagged, width-padded stage lines to stdout, live:

```
[extract] 117/251   00022.png   Features: 3807
[match]   ...
[map]     Registered image 33 (PnP inliers 1815/1919); images in the model: 251
```

Tags observed on one run: `[run]`, `[device]`, `[extract]`, `[match]`, `[focal]`,
`[map]`, `[bal]`, `[orient]`. `[extract] N/251` is the one countable pair; `[map]`'s
`images in the model: N` is the second. **The images are not processed in filename
order** (the run started 00227, 00165, 00005…), so the counter is the progress and the
filename is not.

`--progress-dir DIR` writes `model.bin` + `pairs.bin` snapshots while the run goes, for a
front end that wants to *show* a run rather than tail it. Start with stdout; the binary
channel is a later refinement (§13).

### 7.3 The world frame — measured, not predicted

`3DGS_App_26` spent three decisions getting orientation right and every one of them was
about RealityScan's conventions. None apply, and the answer here is simpler than any of
them, but it was **counted rather than argued** — that project's own lesson.

**The trained splat comes out in the same frame as the sparse cloud it was seeded from.**
Occupancy-grid overlap, the same method as that project's 2026-08-21 row: one grid sized
off the sparse cloud (1/40 of its 1–99 percentile extent, cell 0.2443), and the share of
the cloud's occupied cells the splat also occupies. 84 359 sparse points against 998 463
gaussians from a 7 000-iteration run on that same model:

| Rotation | Overlap |
|---|---|
| **identity** | **90.1 %** |
| `Rx+180` | 29.2 % |
| `Rz+180` | 35.5 % |
| `Rx+90` | 11.1 % |
| `Rx-90` | 12.0 % |
| `Ry+90` | 8.6 % |

That is what `--train-frame points` (the default) implies — the splats stay in the raw
dataset frame — and it is now a measurement instead of an inference. **So there is no
`rc_postprocess` equivalent, no per-object rotation in the viewer, and nothing rotates
anything on disk.** Every one of the predecessor's frame repairs is deleted rather than
ported.

**That shared frame is +Z up.** Two independent readings of the same model, agreeing to
cos 1.000:

| Reading | Result |
|---|---|
| Mean of every camera's world up (`-R[1,:]`, COLMAP convention), over 251 cameras | `(-0.000, 0.000, 1.000)` → **+Z**, with 0.974 agreement across the cameras |
| Thinnest principal axis of the sparse cloud — the ground-plane normal | `(0.028, 0.004, 1.000)` → **+Z** |

The mapper says so itself, and it is on by default (`--no-orient [on]`):
`[orient] Model 0: levelled and centred on the cameras, scaled by 0.3534`. So the model
is not in the seed pair's arbitrary gauge; it is levelled, centred and unit-scaled.

**three.js is Y-up, so the viewer applies one display rotation to everything:** `Rx-90`,
`(x, y, z) → (x, z, -y)`, which sends world +Z onto viewer +Y. One rotation, on the scene
root, for the sparse cloud and the splat and the camera overlay alike — not per object as
the predecessor needed, because here they are all in one frame. Nothing on disk moves. A
"Flip up" toggle stays, for the captures where the mapper's levelling found the wrong
vertical.

### 7.4 Masking — `spirula sam` (a phase of step 2 / a panel on step 3)

Modelled on how `3DGS_App_26` ran curation as a re-runnable phase of step 2 and mask
generation as a second run of step 3: **the expensive phase must never be redone to
change a threshold.** Masking writes `masks/`, which §5.2 shows both later tools adopt
with no flag.

`sam` has six subcommands, not one. Two of them matter here and they have very different
costs, which is why they are one setting with a mode rather than two features:

**`sam mask` — no model, no download, no licence question.** It masks the part of every
frame that is never scene: a fisheye border, a watermark, the rig in shot. "Needs no
model — it is in the same place in every frame, so it is a shape, not an object." This is
the companion of §1's 360 input, and it is free to run speculatively: on 251 rectilinear
images it answered `no border found …; name one with --shape` and exited **0** without
writing anything. `--shape` names ellipses and rectangles by hand (a leading `-` cuts one
out again); `--print` reports without writing. **Without `--replace` the masks are
*intersected* with what is already in the output folder**, which is how this stacks on
top of a model's masks rather than replacing them.

**`sam track` — needs a SAM checkpoint.** Runs over a frame directory with a text or
click prompt and writes a per-frame binary mask PNG. Its default polarity is already the
one a reconstruction wants: **the prompted objects are BLACK and everything else white**,
"which is what a reconstruction pipeline wants from 'mask out the people'".
`--keep-prompted` inverts it, for a prompt that names the subject instead of the
distractors. So there is no `--invert-masks` question to measure — it is a documented
default, and the flag names the other case.

**The checkpoint licences are not the same and both must be shown before a fetch.**
SAM 2.1 is Apache-2.0; **SAM 3 is Meta's own non-standard licence**. They are never
bundled, the tool shows the terms before fetching, and §10 carries a row for each.
`--model` points at a file downloaded by hand.

`sam devices` is how the setup panel proves the GPU baseline; `sam --help` exits **2**,
not 0, which a naive precondition check would read as a failure.

**Measured whole on 2026-08-28** — `docs/spirula/sam-mask-run.txt`. `sam mask` writes
`<stem>.png`, i.e. `frame_0001.png` beside `frame_0001.jpg`, which is the basename
convention `masks/` already holds. **Two lines for the entire run** — the image count,
then `masks written: N, in <dir>` — and 238 frames in **2.6 s**, so there is no per-frame
channel here and none is needed. The speculative run behaved exactly as this section
predicted: `no border found ...; name one with --shape`, exit 0, nothing written.

**`step_sam.py` writes its report to `analysis/mask_result.json`, not into `masks/`.**
Section 5 says `masks/` holds one greyscale PNG per frame, and it is a directory both
`sfm auto` and `train` are pointed at; a report file in it would contradict the layout the
readers are reading. Both go with a step 2 reset, so nothing is orphaned.

### 7.5 Geometry supervision — `spirula geometry` (a panel on step 4)

Per-image depth and normal maps feeding the trainer's geometry terms. Not a step: it
writes `normals/` and `depths/` **into the dataset folder** — `<project>/sfm/` — which
both dataset readers find by name, and nothing rewrites the reconstruction. `train`'s
`--depth-dir` and `--normal-dir` default to `depths` and `normals` relative to `--data`,
so with `--data <project>/sfm` the pairing costs no flag at all.

**Depth is OFF in the tool's own default** and stays off in ours: "the normals are what a
reconstruction usually wants, and depth doubles both the time on disk and the reading a
training run does". `--ray-depth auto` picks ray depth exactly when the frame was split
into pinhole faces, "which is the same call the trainer's `--input-depth-is-ray-depth`
makes when it is left unset" — so leaving both at `auto`/unset is the coherent pair.
`--split auto` splits a panorama always and a fisheye when one pinhole would keep less
than three quarters of the frame: §1's 360 input again.

**The model is fetched on first use, by `curl`, into a named cache.** Measured: with no
`--model`, the run reads the dataset (`sfmws: 251 images, 2 cameras`) and then

```
[moge] fetching moge2-vitb-normal.onnx (419.4 MB) from https://huggingface.co/...
```

into `%LOCALAPPDATA%\spirula-studio\models\`. Three consequences. The fetch is a **child
process**, so §2.6's process-tree kill is what makes this step abortable. The download
prints a **CR-redrawn percentage bar**, the one channel in this whole tool family that
has the §15.1 defect. And a failed fetch is clean and actionable — it names the URL and
the exact path to save the file to.

The cache path belongs in `config.json` so the setup panel can show it, pre-seed it and
report its size.

**It does NOT resolve images outside the dataset folder, and that is now measured.**
This was §13.1, the one thing said to be able to force a junction into §5's layout, and it
did. There is no `--image-dir` on this tool. Run against `<project>/sfm`, whose images live
in the sibling `<project>/frames`, it resolved `<project>/sfm\imagesrame_0001.jpg`,
answered `can't fopen` and `skipping` for all 238 images, and finished
`done: 0 written, 0 already there, in 0s` -- **exit 0**. `docs/spirula/geometry-run.txt`
holds both runs.

**So `step_geometry` junctions `sfm/images` to `frames/` for the length of the run and
removes it in a `finally`.** With the junction in place the identical command wrote **238
normal maps in 35 s** at `--max-size 512`, 55 MB. The link exists only while the command
does, so §5's layout on disk is exactly what §5 says it is and no copy, archive, reset or
preview ever meets one. A Windows junction needs neither administrator rights nor
Developer Mode, unlike `os.symlink`; `os.rmdir` removes it and leaves the target intact,
which was verified before it was relied on, because getting it wrong deletes `frames/`.

**Exit 0 is not success here**, as the failing run above shows, so the step judges the
folder and `done: N written` rather than the return code. The working channel is
`N / M images, T ms each, R left`, one line per image, which is what the bar rides;
`done: N written, M already there, in Ts` closes it. Without `--overwrite` a run continues
where the last one stopped, so an aborted pass is cheap to resume.

**A format switch writes beside, not over.** A `--normal-format png` run followed by a
`jpg` one left `sfm/normals/` holding **476 files for 238 frames**. Which of the two
`train --normal-dir` then reads is not something this app should guess at, so the run says
so and names the count.

### 7.6 Step 4 — `spirula train`

```
spirula --lang en train [<preset>] --data <project>/sfm --image-dir <project>/frames
        --output-dir-prefix <project>/train --output-dir-name run --disable-viewer 1 ...
```

**Always pass `--output-dir-name`.** With none, the build timestamps the directory
instead, and a step that cannot name its own output cannot find it again.

**Seven presets, and `--help` lists six.** `3dgs` (default), `360-camera`, `in-the-wild`,
`linear-color`, `synthetic`, `meshing` — plus **`academic-baseline`**, which is not in
the printed list and works: measured 2026-08-27, `train academic-baseline --help-all`
exits 0 and differs from `3dgs` in `--load-depths 0`, `--load-normals 0`,
`--eval-mode interval` and `--orientation-method gsplat`. The preset is the first
positional argument and it moves the defaults of everything under it, so the panel must
show the *selected preset's* values, not a frozen copy of `3dgs`'s.
`docs/spirula/train-help-all-<preset>.txt` is one capture per preset for exactly that.

**So `TrainDefaults` stores `None` for "the preset decides", and every tool knob in it
defaults to that.** `SfmDefaults` could hold the build's numbers as literals because
`sfm auto` has one global set of them; `train` has seven, and a model holding concrete
values cannot tell "the user asked for 3" from "3 is what `3dgs` happened to default to".
Caught in a test rather than in a run: a `TrainDefaults` carrying `3dgs`'s values with
`meshing` selected built
`train meshing … --sh-degree 3 --primitive 3dgs --background-mode black`, which is the
`meshing` preset selected and then entirely undone on its own command line.
`step_train.preset_defaults()` is the per-preset table, `_moved_from_preset` is the diff,
and `resolved_values()` is what the log lines and `train_result.json` report — because
"30 000 iterations" is what the run did whether or not the flag was sent.

**Output.** `<output-dir-prefix>/<output-dir-name>/step-%09d.ckpt/splat.ply`, plus a flat
`config.json` of every resolved flag beside it. `--save-only-latest-checkpoint` defaults
**1**, so one checkpoint survives a run — confirmed on a 3 000-iteration run, where
`step-000002000.ckpt/` was gone by the end and only `step-000003000.ckpt/` remained. A
`state.tar` is written beside `splat.ply` even with `--save-full-checkpoint 0`.

**The bar's `splats N` is not the count in the file.** It is the live count, and the
final prune runs after the last bar line: two 30 000-iteration runs printed the
1 000 000 cap and wrote **715 890** and **716 831** gaussians. `train_result.json`
carries both — `num_gaussians` from the line, `splat_count` off the PLY header — and
anything reporting what the run *produced* reads the second. The cap warning reads the
first, because reaching the cap during training is what it is about.

**`splat.ply` is a standard 3DGS PLY and `core/ply.py` reads it unmodified.** Proven, not
assumed — the copied parser was pointed at a real one:

```
kind: splat   count: 140,942   format: binary_little_endian   62 properties   248 B/vertex
first: x y z nx ny nz f_dc_0 f_dc_1 …   last: … opacity scale_0..2 rot_0..3
```

The property order is the INRIA one — `x y z nx ny nz f_dc_0..2 f_rest_0..44 opacity
scale_0..2 rot_0..3`, unused normals included. `ply.py` reads by property *name* and
detects the kind from the presence of `f_dc_0`, `opacity`, `scale_0`, `rot_0`, so the
order is not load-bearing, but it is written down here because a reader that assumed the
other common order would silently produce garbage.

**Masks: `--apply-loss-for-mask 1`, and the off position is not offered.** The flag
defaults to **0**, and 0 means *ignore*. The tool's own help: *"Off ignores them… On
trains them as empty, which removes the background and leaves just the subject."* That is
exactly the `ignore` / `segment` pair `3DGS_App_26` measured on 2026-08-26, three
13 000-iteration runs on one dataset, counting gaussians against a validated region box:

| Run | gaussians | in region | opacity > 0.5 in region | p99 radius |
|---|---|---|---|---|
| unmasked | 2 865 432 | 79.3 % | 60.6 % | 149.6 |
| masked, ignore | 2 861 781 | 79.0 % | 58.9 % | 147.0 |
| masked, segment | 2 665 071 | **96.3 %** | **98.9 %** | **19.5** |

`ignore` is within noise of no masks at all on every column, because it drops the masked
pixels from the loss and deletes nothing already there. An option that cannot be told
from `Off` is worse than a missing one. So `TrainDefaults.apply_loss_for_mask` ships
`True` and the UI does not offer the other position under the masked route.

**Unlike step 3, the masks cost a flag here, and it is the one unmeasured path in the
step.** `sfm auto` adopts a `masks/` sibling of the *image directory* by itself (§5.2),
but `train --mask-dir` defaults to `masks` relative to **`--data`** — i.e.
`<project>/sfm/masks`, which this layout never creates. So step 4 passes
`--mask-dir <project>/masks` **absolutely**, on the same symmetry argument §5.2 makes and
with the same caveat: identical help text to `--image-dir`, never measured (§13.4). The
run says so in the log, so a training that reports no masks names its own suspect.

The same asymmetry runs the other way for geometry: `sfm/depths` and `sfm/normals` sit
*inside* `--data`, so `--depth-dir` and `--normal-dir` keep their relative defaults and
cost nothing. What the step does send is a refusal — `--load-masks 0`, `--load-depths 0`,
`--load-normals 0` — for whichever directory is empty, rather than pointing the trainer
at one that is not there.

`--mask-boundary-offset` grows or shrinks the masks by a **fraction of the image size**
(signed). It has no LichtFeld Studio equivalent and is the nearest thing this project has
to the predecessor's Reconstruction Region — the *idea* survives, the implementation
shares nothing.

### 7.7 Step 4's progress channel — the one tool in this family that gets it right

```
step   1101/3000 ( 36%)  splats 58963  [elapsed 0:17 | ETA 0:26]  rgb_loss=0.09123  ssim=0.744  psnr=21.37
```

One line every 100 steps, starting at step 1, **CRLF-terminated**, written live. Measured
by sampling a redirected file mid-run: at step 301 the file held **12 complete lines,
971 bytes** — not a 4 KB block, not a redrawn bar, no ANSI. `Main.cpp` calls
`setvbuf(stdout, nullptr, _IONBF, 0)` whenever stdout is not a tty, with a comment saying
why.

**Three defects `3DGS_App_26` §15 was built around are simply absent**: no CR-redrawn
bar, no 4 KB CRT stall, no progress file. `proc.iter_lines()` reads it as-is — it splits
on CR as well as LF, which costs nothing here and is what the geometry download needs
(§7.5).

Two things to get right in the parser:

- **The ETA is real.** Unlike LichtFeld Studio's, it is derived from the actual step rate
  and reads sensibly throughout (`ETA 4:20` at step 1, `ETA 0:26` at 36 %). The clock
  format is `M:SS`, not the `01m:31s` shape a first reading might assume.
- **The metric values are not zero-padded.** `psnr=20` and `ssim=0` both occur, so the
  pattern must accept a bare integer, not `\d+\.\d+`.

Map the `N/M` pair onto **5–95 %**: a run loads its dataset before step 1 and writes a
checkpoint after the last, and a bar that sits at 0 through the first and 100 through the
second reports the wrong thing at both ends. Cap at 0.99 while running — the store reads
1.0 as "the step is done".

The end of a run is `Training complete. Steps: 3000   Time: 0:43`, and — with
`--disable-viewer 1` — the process then exits 0.

### 7.8 Step 5 — `spirula mesh`

```
spirula --lang en mesh <checkpoint> --data <project>/sfm --output <project>/mesh/mesh
        --format glb --color texture
```

`<checkpoint>` is a run directory, a `*.ckpt` directory, or a `splat.ply`. `--data` gives
the cameras that decide occupancy and colour; `--no-data` meshes from the gaussian
densities alone.

**Always pass `--output`.** Its default is `<checkpoint>/mesh`, and `<checkpoint>`
resolves to the `.ckpt` **directory** — measured, a run given the run directory wrote
`train/run/step-000007000.ckpt/mesh.glb`, i.e. inside the very directory that
`--save-only-latest-checkpoint` deletes on the next training run. This is the same shape
of trap as `--output-dir-name` in §7.6 and it costs the mesh rather than confusing it.

**Two format/colour pairs are refused, and the refusal kills the whole run.** Measured:
`--format glb,ply --color texture` answered

```
[meshing] error: PLY does not support textured meshes; use vertex color (or export OBJ/GLTF/GLB)
```

and exited **1 having written nothing at all — not even the glb it could have made**. So
this is not a per-format skip to warn about, it is a precondition the UI must enforce:
PLY carries no texture, OBJ carries no vertex colours.

Measured on the 7 000-iteration splat above (998 463 gaussians), `--format glb --color
texture`: **203.98 s**, 544 317 vertices, 612 886 faces, an 8192×8192 texture at 1.2 %
texel coverage, exit 0, `mesh.glb` 29.9 MB.

**The phase list was captured whole on 2026-08-28** — `docs/spirula/mesh-run.txt`, 419
lines from a 98 025-gaussian splat and 238 cameras in 18.15 s — and it corrects what this
section previously carried. Every line is `[meshing] <phase>: <detail>`, and the phases,
in the order they first appear, are `loading`, `point cloud`, **`Delaunay`**, `occupancy`,
`cut edges`, `bisection`, `marching tets`, `merge`, `cull unseen`, `cleanup`, `quality`,
`orient`, `texel density`, **`UV`**, `bake`, `color`, `texture`, `stats`, `wrote`, `done`.

Three things about that channel decide how it is parsed:

- **There are three camera loops, not one.** `occupancy`, `texel density` and `color` each
  print `cameras rendered: N/238`, every fourth camera. Together they are **360 of the
  419 lines** — 86 % of the run — against a 500-line LiveLog, which is §12's
  `_EXTRACT_NOISE` problem again. `step_mesh` keeps only the line that closes each block
  and rides the bar on the rest with an **empty message**, which `websocket.broadcast`
  omits from the payload and the store therefore never logs. 419 lines in, 65 out,
  replayed against this capture to prove it.
- **The phases are not monotone.** `merge`, `cull unseen` and `cleanup` each run twice,
  and `bisection` re-evaluates the occupancy grid once per iteration — so 180 consecutive
  `occupancy:` counter lines appear *inside* the bisection phase. A phase therefore only
  ever moves the bar forwards, or a run would go backwards three times.
- **The numbers are on four lines.** `stats:` carries vertices, faces, components and the
  boundary / non-manifold / mis-oriented edge tallies; `bake:` carries
  `covered texels: 4490440/16777216 (26.8%)` and the finished texture size; `UV:` names
  the size when `--texture-size 0` chose it; `done:` carries the total seconds. They go to
  `mesh/mesh_result.json`, for the same reason steps 3 and 4 write theirs.

### 7.9 The 3D viewer (steps 3, 4 and 5)

Step 3 shows the sparse cloud, step 4 the trained splat, step 5 the mesh. It is the only
place several failures are visible at all: a reconstruction that folded the camera path
on itself, a component at another scale, a training that converged onto something other
than the scene you shot.

**Nothing loads the step output directly.** The 7 000-iteration `splat.ply` measured
above is **247 MB** for a small project. `core/ply.py` streams the source and writes a
decimated binary copy into `projects/<slug>/preview/`, served by the `/static` mount:

| Source kind | Preview | Record | Renderer |
|---|---|---|---|
| gaussians (`f_dc_*`, `opacity`, `scale_*`, `rot_*`) | `.splat` | 32 B | `@mkkellogg/gaussian-splats-3d`, sorted and alpha-blended |
| plain cloud | `.pc3d` (ours) | 16 B — 3 float32 + rgba | `THREE.Points` |
| glTF mesh (`.glb` / `.gltf`) | **none — the source is served** | — | `GLTFLoader`, which ships inside `three` |

- **The renderer is chosen from what the file *is*, not from which step asked.**
- **Decimation is a uniform spread, never a head slice.** A PLY is not shuffled; the
  first million points are one corner of the scene. `viewer.preview_max_points` defaults
  to 1 M and "Full" always loads the whole file.
- **The preview is rebuilt when its source is rewritten**, tracked by mtime and size in a
  sidecar, never by age. Preview names carry an 8-hex fingerprint of that pair, so a new
  revision writes a new name rather than replacing a file somebody may be reading — on
  Windows the old name is pinned open and the rename fails with `WinError 5`.
  `api/file_handles.py` closes `AsyncFile` in place for the same reason: its `aclose()`
  is a thread call starting with a cancellation checkpoint, so any aborted download leaks
  the handle until the server exits.
- **`.splat` and `.pc3d` are registered as `application/octet-stream`**, or `StaticFiles`
  serves them as `text/plain; charset=utf-8`. `.glb` and `.gltf` are registered too —
  `model/gltf-binary` and `model/gltf+json` — rather than left to the platform registry,
  which on Windows answers whatever 3D application last wrote there.
- **The mesh is the one source with no preview file at all.** There is no record format
  to decimate a textured glb into, and it is small next to what it came from: the
  reference mesh was **11.6 MB** against the 24 MB splat and the 178 MB one before that.
  So `preview.status` reports it `ready` immediately with `url == source_url`, and none of
  the fingerprint / stamp / prune bookkeeping applies. Only glTF is offered: a
  `--format ply` mesh would be read by the PLY parser as a plain cloud and drawn as its
  vertices, which is a different picture rather than a cheaper one.

**The camera overlay** reads `sfm/sparse/0/images.bin` (§7.1: binary, not text). Frustums
are coloured per sequence and the path breaks at each cut. Everything in the view sits in
one frame and takes the single `Rx-90` of §7.3 — applied on **one scene-root node**, not
per object, which is what §7.3's measurement bought.

**A COLMAP camera looks down `+Z`, and that is measured too.** COLMAP is OpenCV-framed
(+X right, +Y down, +Z forward) where three.js looks down `-Z`, so the frustum the
predecessor built for RealityScan's OpenGL `transform_matrix` opens the wrong way here.
Counted rather than argued, by cheirality on the 300-image model: over 60 cameras,
**623 836** sparse points project inside the image frame with the camera looking down +Z
against **69 842** looking down -Z, a factor of **8.9**. Pointing it the other way is not
subtle — every frustum in an orbit opens away from the subject it was aimed at.

**The frustum is drawn at the lens the reconstruction solved**, read off `cameras.bin`:
`2·atan(width / 2f)`, which on the reference project is `PINHOLE 960×720 f=447.3` →
**94.0°** at 4:3. A fisheye or equirectangular group answers **no fov** — there is no
single horizontal angle a wire frustum stands for — and the overlay falls back to its own
shape rather than drawing 16:9 pinholes over a 360 rig (§1). `cameras.bin` has no length
field per record, so an unknown model id stops the read instead of guessing a stride;
width and height come before the parameters, so the aspect survives even then.

**The preview sources are found, not named.** `sfm` is whichever `sparse/N`
`colmap.find_model` accepts as complete, so a run killed between `cameras.bin` and
`points3D.bin` reports nothing to preview instead of feeding the parser a stub; `train` is
the **highest** `step-*.ckpt` under `train/`, globbed rather than assumed, because
`--save-only-latest-checkpoint` defaulting to 1 is the tool's default and not a promise
(§7.6). `mesh` is `.glb` before `.gltf` under `mesh/`, and nothing at all if the run was
asked only for PLY or OBJ.

**Step 5's mesh gets the third renderer, and it is JB's call taken on 2026-08-28.**
`GLTFLoader` ships inside `three`, so it costs no dependency and no §10 row. `MeshCanvas`
hangs the loaded glTF off the same scene root as the other two canvases and takes the same
single `Rx-90`: the mesh is extracted from the splat and coloured by the same cameras, so
it is in the frame §7.3 measured. Three things it does that the other canvases do not —
a **headlight** on the camera plus ambient, because a textured glb comes out of the loader
with a PBR material and an unlit scene renders it black; `DoubleSide` on every material,
because `--cull-unseen` leaves 63 000 boundary edges and a backface-culled hole is a black
hole; and a **wireframe toggle**, because a surface that looks solid and a surface that is
solid are the same picture until you see the edges. The level selector is hidden for it —
there is nothing decimated to open instead.

### 7.10 Step 6 — export + scene

Blender, `blender_splatforge.py`, inherited. Steps 5 and 6 share `export/`, so resetting 5
takes 6 with it — the same rule as `3DGS_App_26` §14.1.

**`export/` is filled by step 5's second half, and it holds hard links.** `step_export`
takes step 4's `train/run/step-*.ckpt/splat.ply` and step 5's `mesh/` outputs — not
`lfs_output/`, which went with LichtFeld Studio — and links rather than copies them, on
`step_conform._link_or_copy`'s argument one step later: the reference splat is 178 MB and
`export/` would otherwise be a second copy of bytes that already exist. It is safe both
ways round — a step 5 reset drops `export/` and leaves `train/` holding the splat, a step 4
reset drops `train/` and leaves `export/` holding the bytes — and nothing in the app ever
writes *into* an exported file. It does **not** reset step 5 itself: `run_mesh` already
did, before it wrote a byte, and a second reset would delete the mesh it is exporting.

**Step 6 asks for the splat by name, not by glob.** With `mesh --format ply` the export
holds `mesh.ply` too and it sorts first, so the predecessor's `glob("*.ply")[0]` would hand
Blender a surface mesh to import as a gaussian cloud. `find_export_splat` is the one that
knows. The README it writes lost its `{supersplat_url}` placeholder in the same commit:
`supersplat_url` left `config.json` with the two CUDA tools, and reading it raised
`AttributeError` on every step 6 — *after* Blender had already run.

---

## 8. Dashboard metrics (implement exactly)

| Metric | Definition |
|---|---|
| Source info | ffprobe: container, codec, resolution, fps, duration, bitrate, HDR |
| Frames removed | Count + % of extracted, split by reason (`blur`, `redundant`, `manual`) |
| Frames blurred | Count of `rejected:blur` + sharpness timeline with cut markers |
| Overlap quality | % of consecutive kept pairs inside the band; median displacement; list of `warning:gap` positions |
| Global quality | Composite 0–100: (kept mean sharpness vs source mean) × 0.4 + (overlap-band ratio) × 0.4 + (1 − rupture density) × 0.2. **Always display the three sub-scores next to it** — the composite alone is marketing, the sub-scores are the truth |
| SfM result | Exit code and its meaning (§7.1), registered/total, mean and median reprojection error, camera groups, and the count of `sparse/N` directories — more than one is a fragmented capture |
| Training | iteration/total, splat count, `rgb_loss`, `ssim`, `psnr` (§7.7) |
| Mesh | vertices, faces, components, boundary edges, texture size and texel coverage (§7.8) |
| Recommendations | Image count per sequence; with ≥ 2 sequences prefer `--data-type video` or raise `--overlap`; flag sequences < 30 images as risky; list `warning:gap` positions as likely reconstruction breaks; on exit 3, name the number and offer the re-run |

---

## 9. API

```
GET    /api/projects                   list
POST   /api/projects                   create
GET    /api/projects/{id}              one project
PATCH  /api/projects/{id}              partial update: deep-merged settings + curation overrides
DELETE /api/projects/{id}              delete the row, the directory and the archive
POST   /api/projects/{id}/copy         duplicate under a new name (§14)
GET    /api/projects/{id}/image-sets   the imported image sets in input/ (§6.7)
POST   /api/projects/{id}/import-folder  read a folder on this machine, server-side
POST   /api/projects/{id}/import-zip     unpack a dropped zip of images
POST   /api/projects/{id}/import-images  a selection of image files (browser folder picker)
DELETE /api/projects/{id}/image-sets/{name}  remove one set from input/
POST   /api/projects/{id}/reset        wipe steps {steps|null=all} — keeps input/
POST   /api/projects/{id}/archive      zip the directory away, keep the row disabled
POST   /api/projects/{id}/unarchive    unpack it back
POST   /api/pipeline/start             start a step
POST   /api/pipeline/control           abort
GET    /api/pipeline/status            running state
POST   /api/pipeline/analyze           re-run curation alone — never re-extracts
POST   /api/pipeline/masks             run `spirula sam` alone — never re-extracts (§7.4)
POST   /api/pipeline/geometry          run `spirula geometry` alone — never re-trains (§7.5)
GET    /api/settings/                  config.json  (installation)
PUT    /api/settings/                  update config.json
GET    /api/defaults/                  defaults.json (business defaults)
PUT    /api/defaults/                  deep-merge update
POST   /api/defaults/reset             factory reset (optional ?section=)
GET    /api/defaults/presets           capture presets
GET    /api/version/                   app name, version (commit date) and commit id
GET    /api/files/{project}/frames     frame list + curation verdicts
GET    /api/files/{project}/analysis   scores.json + selection.json + overrides
GET    /api/files/{project}/sources    input/ listing: probe + poster frame per video
GET    /api/files/{project}/masks      what masks/ holds, plus the last sam run's report
GET    /api/files/{project}/geometry   what sfm/{normals,depths}/ hold + the last run
GET    /api/files/{project}/train      train_result.json + what --data and --image-dir will read
GET    /api/files/{project}/mesh       mesh_result.json + the checkpoint and cameras step 5 will read
GET    /api/files/{project}/preview    3D preview state (?source=sfm|train|mesh&max_count=)
POST   /api/files/{project}/preview    build that preview — returns at once, poll the GET
GET    /api/files/{project}/cameras    camera poses of the last reconstruction, for the overlay
WS     /ws/logs                        progress, logs, metrics
GET    /static/<slug>/...              project files (thumbnails, exports)
```

`/masks` and `/geometry` are the same argument one step earlier as `/analyze`: **the
expensive phase must not be redone to change a threshold.**

---

## 10. Licence audit table

Audit as if the tool could be distributed tomorrow. FFmpeg, spirula and Blender are
invoked as **subprocesses**, never linked.

| Dependency | Licence | Status |
|---|---|---|
| FastAPI / Uvicorn / Pydantic | MIT / BSD-3 | ✅ ok |
| SQLModel | MIT | ✅ ok |
| websockets, aiofiles, httpx, watchdog | BSD / MIT / Apache-2.0 | ✅ ok |
| OpenCV (`opencv-python`) | Apache-2.0 | ✅ ok. **Not** the headless build: PySceneDetect depends on `opencv-python`, both wheels provide the same `cv2` package and cannot coexist |
| NumPy | BSD-3 | ✅ ok |
| PySceneDetect | BSD-3 | ✅ ok |
| FFmpeg (system exe) | LGPL-2.1+ (GPL if built with x264) | ✅ ok as subprocess — re-audit before any distribution |
| **Spirula Studio (`spirula.exe`)** | **GPL-3.0** | ✅ **subprocess only, never linked, never bundled** — same standing Blender has. Two footnotes below |
| Blender | GPL, external | ✅ subprocess only, never bundled |
| **SAM 2.1 checkpoint** (via `spirula sam`) | **Apache-2.0** | ⚠ never bundled; downloaded on first use into `%LOCALAPPDATA%\spirula-studio\models\`. Terms shown in the UI before the first fetch (§7.4) |
| **SAM 3 checkpoint** (via `spirula sam`) | **Meta's own, non-standard** | ⚠ never bundled; same route, and the licence is *not* Apache — it must be shown and accepted distinctly from SAM 2.1's |
| **MoGe / Metric3D checkpoints** (via `spirula geometry`) | to be confirmed per model | ⚠ downloaded from HuggingFace on first use — measured 2026-08-28: `moge2-vitb-normal.onnx`, 419.4 MB, from `huggingface.co/Ruicheng/moge-2-vitb-normal-onnx`, cached in `%LOCALAPPDATA%\spirula-studio\models\`. **Not yet audited — §13.5.** The step names the URL and says so before it fetches |
| React / Vite / Tailwind / shadcn/ui / Zustand / recharts | MIT | ✅ ok |
| three.js (`three`, `@types/three`) | MIT | ✅ ok — the 3D viewer (§7.9) |
| `@mkkellogg/gaussian-splats-3d` | MIT | ✅ ok — the sorted splat rasteriser (§7.9) |

Two footnotes on spirula, both checked so they are not re-checked later:

- `-DSS_ENABLE_PATENTED` (GPU video decode, AVC/HEVC patent exposure) is a **build**
  option and irrelevant to us: FFmpeg does all decoding and spirula is never handed a
  video.
- The checkpoints are the licence surface, not the binary. The binary is one GPL-3.0
  subprocess; the three model rows above are three separate questions.

Any new dependency → add a row here in the same commit.

---

## 11. Conventions

- **Commits:** conventional commits (`feat:`, `fix:`, `chore:`, `docs:`…), English.
- **Code:** identifiers and docstrings in English. Comments in French welcome.
- **UI language:** English throughout. Keep it English and consistent.
- **Python:** type hints everywhere, no FastAPI import inside `core/steps` or
  `core/curate`.
- **Frontend:** TypeScript strict, path alias `@/` → `src/`.
- **Typos in JB's prompts:** JB is dyslexic and types fast — interpret by intent, flag
  briefly only when a typo is genuinely ambiguous, never block on it.

---

## 12. Decisions log

| Date | Decision |
|---|---|
| 2026-08-27 | **This project exists because spirula is Vulkan and the predecessor's two tools are CUDA.** `3DGS_App_26` needs an NVIDIA GPU twice over — RealityScan to mesh, LichtFeld Studio to exist. `spirula.exe` imports `vulkan-1.dll` and no CUDA library, and `sam devices` on this workstation accepts the Intel UHD 770 as readily as the RTX 4060, both `ok` against its Vulkan 1.2 baseline. That is the whole non-goal in §1: RealityScan, LichtFeld Studio and any CUDA dependency are out of scope, not a compromise to revisit. One binary, 119 MB, six tools, plus FFmpeg for everything before step 3. |
| 2026-08-27 | **The repo starts fresh, and the artefacts are excluded from the first commit rather than removed from the index later.** The predecessor's import tracked 27 393 files of which 27 096 were build artefacts, and every commit afterwards carried Vite-cache and `.pyc` churn; undoing it cost a commit. `.gitignore` excludes `node_modules/`, `.venv/`, `__pycache__/`, `tools/`, `projects/` and `.version_stamp.json` from commit one. `tools/` is not negotiable here: `spirula.exe` is 119 MB, over GitHub's hard 100 MB per-file limit. |
| 2026-08-27 | **`--image-dir` takes an absolute path, so nothing copies the images.** Measured three ways on `v2026.8.23` against a `--data` folder holding a `sparse/0` and no `images/`: an absolute path outside it loaded all 251 cameras and trained; the default `images` failed with `ColmapParser: ds\images\00000.png does not exist (set --image-dir if needed)`, exit 1; a wrong absolute path failed the same way, naming what it resolved. A relative value is joined onto `--data`, an absolute one is used as-is, and neither falls back silently. **This decides the whole on-disk layout of steps 3-5** (§5): `frames/` is the image directory, `sfm/` is the workspace beside it, `masks/` is already the sibling `sfm auto` adopts by itself, and `train --data <project>/sfm` finds `sfm/sparse/0` through the tool's own probe order. The predecessor's duplicate undistorted image set — 226 MB on the small project measured here, tens of GB on a 4K one — has no counterpart and no reason to exist. |
| 2026-08-27 | **The world frame is measured and it is the identity, +Z up — so every orientation repair the predecessor carried is deleted, not ported.** `3DGS_App_26` spent three decisions on this and all three were about RealityScan's conventions. Counted the same way it counted them, on an occupancy grid over the sparse cloud (cell = 1/40 of its 1–99 pct extent): the trained splat overlaps its own seed cloud **90.1 %** at identity, against 29.2 % at `Rx+180`, 35.5 % at `Rz+180` and 8.6–12.0 % at ±90°. That is what `--train-frame points` implies and it is now a number. The up axis is `+Z` by two independent readings agreeing to cos 1.000 — the mean world-up of 251 cameras `(-0.000, 0.000, 1.000)` with 0.974 agreement between them, and the sparse cloud's thinnest principal axis `(0.028, 0.004, 1.000)` — and the mapper says so itself, `[orient] Model 0: levelled and centred on the cameras, scaled by 0.3534`, on by default. So: **no `rc_postprocess` equivalent, no per-object rotation in the viewer, nothing rotated on disk.** three.js is Y-up, so the viewer applies one `Rx-90`, `(x,y,z) → (x,z,-y)`, on the scene root, to the cloud and the splat and the camera overlay alike — they are all in one frame, which is exactly what the predecessor's `viewer/frame.ts` existed to work around not being true. |
| 2026-08-27 | **Every spirula invocation pins `--lang en`, and it is not a nicety.** The tool localizes every line it prints — `--lang`, else `SS_LANG`, else the OS — and this is a French Windows: `spirula --help` with no flag answers `Commandes :` / `ouvrir l'application` / `par défaut`. Every progress regex in §7.7 and §7.2 would match nothing, on this machine, silently, and the bar would sit at zero for the length of a training run. The flag is emitted by the command builder, not by each call site, so it cannot be forgotten in one of six. |
| 2026-08-27 | **`--disable-viewer 1` on every training run, because the trainer otherwise never exits.** `keep_viewer_alive` defaults 1 and `disable_viewer` defaults 0, so a *successful* run prints `Training complete. Viewer still running -- press Ctrl-C to exit.` and parks. Measured: a 1-iteration run was still alive when a 90 s timeout fired (exit 124), splat written, checkpoint saved, nothing wrong except that a subprocess-driven step would hang forever. The same run prints `Viewer at http://0.0.0.0:7007/` — it binds every interface, not localhost, which disabling it also settles. If the viewer is ever wanted in-app it gets proxied, never exposed. |
| 2026-08-27 | **`sfm auto`'s exit 3 warns and never fails the pipeline.** The tool grades its own reconstruction — 0 sound, 1 usage/runtime error, 2 nothing reconstructed, 3 partial (under half the images registered, or over 2 px mean reprojection). That is `3DGS_App_26` §7.1's `alignment_check.json` made native, and it gets the same treatment for the same reason: `-selectMaximalComponent` silently dropping components was what that check existed to catch, and failing the step over a handful of unregisterable frames blocks a pipeline the user may well want to continue. The number is named in the log and persisted to `sfm/sfm_result.json`, because the answer to "did this work" must outlive the scrollback. The reference run — 251 images, `--quality medium` — exited 0 at 251/251 and 0.50 px in 34.62 s. |
| 2026-08-27 | **`apply_loss_for_mask` ships ON, and the off position is not offered under the masked route.** The flag defaults to 0 and 0 means *ignore*, in the tool's own words: "Off ignores them… On trains them as empty, which removes the background and leaves just the subject." That is the `ignore` / `segment` pair `3DGS_App_26` measured on 2026-08-26 — three 13 000-iteration runs on one dataset where `ignore` sat at 79.0 % of gaussians inside the region box against **79.3 %** for no masks at all, 58.9 % against 60.6 % above opacity 0.5, p99 radius 147.0 against 149.6, all three within noise, while `segment` gave 96.3 %, 98.9 % and 19.5. An option that cannot be told from `Off` is worse than a missing one, and it is worse still with every indicator in the app reporting the masks as read. That project removed `ignore` from its `Literal`; here the equivalent is that the masked route sends 1 and the UI does not offer 0. |
| 2026-08-27 | **Two of the predecessor's mask repairs are deleted rather than ported, because spirula does not have the defects they repaired.** `rc_alpha.fit_dataset_masks` existed because LichtFeld Studio *refused* a mask whose dimensions did not match its image (`Mask '{}' is {}x{} but image '{}' is {}x{}`); spirula's `DataManager.cpp` **resizes** instead — nearest for masks, bilinear for depth and normal — so there is nothing to fit. And the whole constant-255-alpha bug of 2026-08-26, where four measured runs were silently unmasked because RealityScan wrote opaque RGBA and LFS's automatic alpha-as-mask outranked the files, **cannot happen**: there is no alpha-as-mask behaviour anywhere in spirula, masks come from `--mask-dir` files only. Both are recorded here so the day somebody reads the predecessor's code and wonders why the port is missing two files, the answer is on the page. |
| 2026-08-27 | **The two output paths that default to somewhere destructive are always passed explicitly.** `train --output-dir-name` and `mesh --output`. With no `--output-dir-name` the trainer timestamps its run directory, and a step that cannot name its own output cannot find it again. `mesh --output` is worse and was measured: its default is `<checkpoint>/mesh`, `<checkpoint>` resolves to the `.ckpt` **directory**, and a run given the run directory wrote `train/run/step-000007000.ckpt/mesh.glb` — inside the one directory `--save-only-latest-checkpoint` (default 1, confirmed: `step-000002000.ckpt/` was gone by the end of a 3 000-step run) deletes on the next training. A 204-second mesh silently destroyed by the next click on step 4. |
| 2026-08-27 | **PLY-with-texture and OBJ-with-vertex-colour are a UI precondition, not a warning, because the refusal costs the whole mesh.** Measured: `mesh --format glb,ply --color texture` answered `error: PLY does not support textured meshes; use vertex color (or export OBJ/GLTF/GLB)` and exited **1 having written nothing — not even the glb it was also asked for**. It is not a per-format skip. The step must refuse the combination before spending the 204 s the reference mesh took. |
| 2026-08-27 | **`sam mask` is the 360 story and it is free; `sam track` is the AI one and it has a licence question.** They are one setting with a mode rather than two features because their costs differ by everything. `sam mask` needs no model and no download — it masks what is never scene in any frame, a fisheye border or a watermark or the rig, "a shape, not an object" — and it is safe to run speculatively: on 251 rectilinear images it answered `no border found …; name one with --shape` and exited 0 without writing. Without `--replace` it *intersects* with the masks already there, which is how it stacks on top of a model's. `sam track` needs a SAM checkpoint, and **SAM 2.1 is Apache-2.0 while SAM 3 is Meta's own non-standard licence** — two rows in §10, shown and accepted separately before any fetch. Its default polarity is already what a reconstruction wants (prompted objects black, everything else white), so there is no invert question to measure; `--keep-prompted` names the other case. |
| 2026-08-27 | **`core/ply.py` is copied unchanged, and that was checked rather than assumed.** Pointed at a real `splat.ply` from a 3 000-iteration run it answers `kind: splat, count: 140942, binary_little_endian, 62 properties, 248 B/vertex`. The property order is the INRIA one — `x y z nx ny nz f_dc_0..2 f_rest_0..44 opacity scale_0..2 rot_0..3`, unused normals and all — which is *not* the order this project's brief predicted; it does not matter, because `ply.py` reads by property name and detects the kind from the presence of `f_dc_0`/`opacity`/`scale_0`/`rot_0`. Written down anyway, because a reader that assumed the other common order would produce plausible garbage rather than an error. |
| 2026-08-27 | **A knob still at the build's own default is not put on the command line, because naming a flag overrides the preset that would otherwise set it.** `sfm auto`'s help says it outright — "Anything they set can be overridden by naming the flag explicitly" — and the run says it too: the 300-image run below answered `The presets set --max-image-size to 2400 (was 0)` and `The presets set --aliked-max-features to 4096 (was 2048)`. So `SfmDefaults` ships the build's numbers (`pairs auto`, `camera-model opencv`, `camera-mode folder`, `max-image-size 0`, `max-features 8192`) and `step_sfm._moved_from_build_default` sends only what the user actually moved. The alternative — dumping the whole resolved block onto the command line — reads as harmless because every value equals the default, and it silently disables `--quality` and `--data-type`: `--quality medium` would still print its preset lines while `--max-features 8192` sat further along the same command line undoing them. `--quality` and `--data-type` are the exception and are always sent; they *are* the presets. The same rule will apply to `train`, where seven presets move far more than five flags. |
| 2026-08-27 | **Step 3 runs, measured on a real project: 300 images at `--quality high` in 51.09 s, 300/300 registered, 0.35 px mean reprojection, 109 132 points, one camera group, exit 0.** Frames were 25 % of a 4K DJI rush, so ~960×540. Two numbers to carry: `high` sets `--max-image-size` to **2400** (against 1600 for `medium` in §7.1's reference run), and the whole `sfm/` workspace is **202 MB** for 300 images — features dominate it, and it is all deleted and rebuilt by a re-run. Extraction 5.30 s, matching 8.34 s (5961/5962 pairs kept), mapping 37.45 s: mapping is the phase worth a bar, which is why it owns 0.38→0.95 of it. `core/colmap.py` was pointed at the model this produced and read it unmodified — `sparse/0`, 300 images, 109 132 points — so §7.9's camera overlay and the `.pc3d` preview have a validated parser before P1.5 starts. |
| 2026-08-27 | **`Reprojection error:` is why step 3's log classifier cannot match a bare `error`.** The line that carries the headline quality number of a *successful* reconstruction — `[run] Reprojection error: mean 0.351 px, median 0.245 px, over 730476 observations` — contains the token `error:` verbatim, so the obvious pattern paints every good run red in the LiveLog. `_ERROR_LINE` is `(?<!reprojection )\berror:`, and the smoke check asserts that exact line classifies INFO. Caught by the check rather than by a user, which is the whole point of writing the check against lines the tool really printed. |
| 2026-08-27 | **The extractor's per-image narration is dropped before it reaches the bus, because the LiveLog keeps 500 lines and one run printed 1682.** `sfm auto` narrates its keypoint pyramid three lines deep for every image — `Octaves:`, `Oriented:`, `Features:` — and only the fourth names the file they were about. On the 300-image run that is **900 lines of the 1682**, and they pushed the run's own header (`Quality: high  Image size limit: 2400 px`, `The presets set …`) out of the buffer before the reconstruction was a third done: the four lines that say what the run is doing, lost to the three that describe one image's octaves. `_EXTRACT_NOISE` drops them and every one of the 300 `[extract] N/300` counter lines survives, replayed against the real log to prove it. The durable answer lives in `sfm/sfm_result.json` and step 3's report panel either way — a log line is gone on the next page load. |
| 2026-08-27 | **`project_ops` moves to §14.1's table, and `masks/` stays with step 2.** `PROJECT_SUBDIRS` loses `rc_output/`, `lfs_output/` and `region/` and gains `sfm/`, `train/`, `mesh/`; `STEP_ARTEFACTS` becomes 3→`sfm/`, 4→`train/`, 5→`mesh/`+`export/`. `masks/` is listed under step 2, which *writes* it, and never under step 3, which only reads it: they are an input to the reconstruction rather than an output of it, so re-running the alignment must not cost the mask run that fed it. This is the table `step_sfm`'s `reset_steps(project, [3])` depends on, and it was wrong until this commit — a step-3 reset would have cleared RealityScan's dead `rc_output/` and left the sparse model in place. |
| 2026-08-27 | **The sparse model is COLMAP *binary*, and `--help` is captured per preset because the presets move the defaults.** `sfm auto` writes `cameras.bin` / `images.bin` / `points3D.bin`, not the text form RealityScan wrote — the camera overlay and every model reader here parse `.bin`. And `train`'s defaults are per-preset: `docs/spirula/` holds one `--help-all` capture for each of the seven, because a panel showing `3dgs`'s numbers while `meshing` is selected would be lying about `--primitive` (3dgut), `--sh-degree` (0), `--background-mode` (noise) and six regularisation weights. **There are seven presets and `--help` prints six** — `academic-baseline` is unlisted and works, exit 0, differing from `3dgs` in four flags. |

| 2026-08-27 | **The camera overlay's frustum opens down `+Z`, by cheirality rather than by convention-reading.** The poses now come from COLMAP's `images.bin` instead of RealityScan's `transforms.json`, and the two disagree about which way a camera faces: COLMAP is OpenCV-framed and looks down **+Z**, three.js looks down **-Z**, so the inherited `cameraRig.ts` built its image plane at `-depth` and would have opened every frustum away from the subject. Counted on the 300-image model — for each of 60 cameras, project the whole sparse cloud and count what lands inside the image: **623 836** points at +Z against **69 842** at -Z, **8.9×**. A weaker test on the same model (does the view direction point at the cloud's centroid?) answers +0.286 against -0.286 mean cosine — the right sign, but only 60 % of cameras, because this capture looks outward; the cheirality count is the one that settles it. The frustum is also drawn at the *solved* lens, `2·atan(w/2f)` off `cameras.bin` — 94.0° at 4:3 here — and a fisheye or equirectangular group returns **no fov** rather than a confident 16:9 lie (§1). |
| 2026-08-27 | **§7.3's measurement is cashed in as one scene-root node, and `frame.ts` shrinks from a per-object rule to a single rotation.** The predecessor needed per-object logic because it reconciled three frames — RC's Y-down export, LFS's Y-up splat, a third for the overlay. Here the cloud, the splat and the poses are in one frame (90.1 % occupancy overlap at identity), so `PointCloudCanvas` hangs everything off one `Group` carrying `Rx-90` and `SplatCanvas` passes the same rotation as a quaternion to the splat scene and to the rig. `isYDownFrame` and the `flipCloud`/`flipCameras` prop pair are deleted, not ported. "Flip up" survives as one flag, and it is now a question about the capture — the mapper levels on the cameras and can pick the wrong vertical — rather than a repair of a convention mismatch. Confirmed independently on this project: the sparse cloud's thinnest principal axis reads `(0.001, -0.095, 0.996)`, +Z up, a second dataset agreeing with §7.3's two readings. |
| 2026-08-27 | **The sparse cloud reaches the viewer through the `.pc3d` path unchanged, but the *source* is not a PLY and `ply.py` was not bent into pretending it is.** `sfm auto` writes COLMAP binary, so `colmap.iter_points` streams `points3D.bin` and a new `ply.write_cloud` consumes any `(x, y, z, r, g, b)` stream into the existing 16-byte record — the format stays in one module and the PLY reader keeps its job. Measured on the 300-image project: 61 859 points → **989 760 B** (61 859 × 16 + 16, exact), and the decimated level round-trips the centroid — 10 000 points centre `(0.193, 0.435, -0.091)` against the full cloud's `(0.190, 0.433, -0.093)`, which is the uniform-spread guarantee holding rather than the head slice §7.9 forbids. `/api/files/{id}/preview` now takes `source=sfm|train` and answers **400** naming both for anything else; the old `rc`/`lfs`/`export` table is gone. |

| 2026-08-27 | **`TrainDefaults` stores `None` for every tool knob, meaning "the preset decides", because the baseline moves with the preset.** `SfmDefaults` diffs against a literal `_BUILD_DEFAULTS` because `sfm auto` has one global set of defaults; `train` has seven, one per preset, and `meshing` alone moves `--primitive` (3dgut), `--sh-degree` (0) and `--background-mode` (noise). A model holding concrete numbers therefore cannot distinguish "the user asked for 3" from "3 is what `3dgs` defaulted to" — and the failure is silent and total. Caught by a test of the command builder before any run: `TrainDefaults(preset="meshing")` carrying `3dgs`'s stored values built `train meshing … --sh-degree 3 --primitive 3dgs --background-mode black`, selecting the preset and then undoing every part of it on the same command line. So `None` is the default of all eighteen knobs, `step_train.preset_defaults()` is the per-preset table (mirrored in `TrainSettings.tsx`, both read off `docs/spirula/train-help-all-<preset>.txt`), and the panel draws the *selected* preset's value in place of an unset knob rather than a frozen copy of `3dgs`'s. `resolved_values()` fills them in for the log line and `train_result.json`, because "30 000 iterations" is what the run did whether or not the flag was sent. The four `load_*` / `apply_*` switches are exempt: they are intent, resolved against what is actually on disk. |
| 2026-08-27 | **Step 4 runs, measured on the same project: 300 iterations at `--quality low` in 3.0 s, exit 0, 61 859 splats, psnr 24.00, ssim 0.739, `splat.ply` 14.6 MB, found at `train/run/step-000000300.ckpt/`.** Deliberately short — what needed proving was the channel, not the picture. Three things it settled. The bar mapped 0.053 → 0.950 across four lines, which is §7.7's 5–95 % window behaving; the `.splat` preview path took the result unmodified (`kind: splat`, 61 859 × 32 B = 1 979 488 B exactly), so §7.9's decimated viewer route is validated on a real trained file rather than on the sparse cloud alone; and **§7.7's warning about bare-integer metric values was not hypothetical** — step 1 printed `ssim=0  psnr=0` and the last line printed `psnr=24`, so a `\d+\.\d+` pattern would have dropped exactly the first and last points of every run's chart. The command line carried `--num-iterations 300 --quality low --load-masks 0 --load-depths 0 --load-normals 0` and nothing else: everything at the preset's own value stayed off it. |
| 2026-08-27 | **A message's type does not decide whether its *text* is shown either — the sibling of §15.2, and it was silently eating whole runs.** `websocket.py` types a message `metric` the moment it carries `data`, and the store's `metric` case only fed the chart. Step 4 carries data on every one of its bar lines, and the trainer says nothing else between loading the dataset and finishing, so the LiveLog would have been **empty for the entire length of a training run** — and step 3's one SUCCESS line, the one naming the registered count, was already being swallowed the same way. The store now pushes a log entry for any message with a `message`, before the switch, exactly where it already reads `progress` before the switch. Same reasoning as 2026-08-20's: the type says what a message is mainly about, not which of its fields may be used. |
| 2026-08-28 | **The pipeline runs end to end from the UI, and the three wall clocks are these.** One throwaway project on the reference rush — 79.5 s of 4K/100 fps 10-bit HEVC, `-hwaccel cuda`, no fallback. Step 2: 238 frames at 3 fps and 25 % scale in **80.4 s**. Step 3, `--quality high --data-type video`: **41.1 s** of tool inside 45.3 s of wall, **238/238 registered**, 0.341 px mean and 0.238 px median over 562 142 observations, 98 025 points, one camera group, exit 0. Step 4, `3dgs` at 30 000 iterations: **956 s**, exit 0, psnr 38.66, ssim 0.9713, rgb_loss 0.01237, `splat.ply` 177 775 619 B. The bar moved throughout: §7.7's window was watched live on the bus, `step 1` landing at exactly **5.0 %** and the run climbing to 95 % before the checkpoint. **Abort was tested on each of the three**, on the throwaway rather than on the reference project, because §14.1's re-run-is-a-reset rule means an aborted step 3 costs the sparse model: `ffmpeg` and `spirula` were both gone from the process table within seconds, the frame count froze, and every step reported `aborted` rather than `error`. What an aborted step 3 leaves is worth writing down — `sfm/features/` and **no `sfm_result.json`** — because that is exactly what makes `colmap.find_model` report nothing to preview instead of handing the parser a half-written model. |
| 2026-08-28 | **Both viewers were finally looked at in a browser, and the standing caveat on P1.5 and P1.6 is closed.** Everything about them had been verified numerically — the `.pc3d` byte count exact, the poses parsed, the frustum direction settled by cheirality at 8.9× — and none of it had been watched. Driven headed through Playwright on the workstation's own GPU, because software GL cannot sort 715 890 gaussians inside a screenshot timeout. The sparse cloud draws with its 300 frustums in one arc **opening onto the wall they were aimed at**, which is §7.9's measurement seen rather than counted; the trained splat renders as the basement window that was filmed, sorted and alpha-blended, with the camera overlay on top of it. No console error on either page. The screenshots are also what caught the two defects below and the stale RealityCapture help panel, none of which any server-side check could have found. |
| 2026-08-28 | **The LiveLog was showing every line twice, and the cause was two sockets in one page rather than two broadcasts.** Counted before guessing: a lone socket received each `step N/M` line **exactly once** (12 distinct texts, 12 messages, over 40 s of a training run), while the page showed each of them twice. `useWebSocket.connect` bailed only on `readyState === OPEN`, and StrictMode's mount→cleanup→mount cycle closes the first socket while it is still `CONNECTING` — so the second mount saw a non-OPEN socket and opened a second one beside it, and the first socket's late `onclose` then cleared `wsRef` and scheduled a *third*. `CONNECTING` now counts as "already have one", and an `onclose` from a socket that is no longer `wsRef.current` returns without reporting or reconnecting. Verified by URL rather than by eye: one live `ws://…/ws/logs` where there were two. This halved the LiveLog's usable 500-line buffer, which is the same buffer §12's 2026-08-27 `_EXTRACT_NOISE` row was written to protect. |
| 2026-08-28 | **The splat count the app reported was the cap, not the file, on every capped run.** `train_result.json` took `num_gaussians` from the last bar line, and that is the *live* count — the final prune runs after it. Two 30 000-iteration runs both printed `splats 1000000` and wrote **715 890** and **716 831** gaussians, ~28 % fewer, and the step-4 card repeated the cap next to a `splat.ply` size that could not hold that many. Worse, the card's amber "the run finished at its splat cap, raise Max splats" advice was being read off a number that did not describe the output. So `step_train` now reads the count off the PLY header (`ply.read_header`, header only, free on a 170 MB file, and never allowed to fail the step) into a new `splat_count`, the card shows that, and the cap warning stays keyed on `num_gaussians` — because reaching the cap *during* training is what the warning is actually about, and both numbers are true about different moments. |

| 2026-08-28 | **A working fps too low to place one frame in the source is refused before the run, because FFmpeg does not extract zero frames — it fails.** A project left `fps_mode: absolute` at **0.001** against a 139.21 s rush asked for one frame every 1000 s, and FFmpeg answered `[vost#0:0/mjpeg] Task finished with error code: -22 (Invalid argument)` / `Nothing was written into output file, because at least one of its streams received no packets` and exited non-zero, so step 2 died on an `-22 Invalid argument` tail that names nothing the user set. Measured on that same 3840x2880 h264 source: `fps=0.02` over 20 s wrote nothing and exited non-zero, the identical filter over 60 s wrote one frame and exited 0 — the `fps` filter samples at the centre of each period, so a run needs roughly `duration >= 1/(2*fps)`. `check_fps_yields_frames` refuses `fps x duration < 1` naming the fps that would work, and the panel's estimate turns amber at the same threshold instead of quietly reading `= 0 frames`. **The probe and the fps resolution moved above `_clear_previous_run`** for it: the check has to run before the reset or an unusable setting deletes the frames it cannot re-extract, which is §14.1's rule and the exact trap `resolve_ffmpeg_path` was hoisted out of on 2026-08-24. |
| 2026-08-28 | **The `-hwaccel` fallback explains itself where it happens, because a step that fails afterwards never reaches the end of the run.** The same project's log ended on `decoder->cvdl->cuvidCreateDecoder(...) failed -> CUDA_ERROR_INVALID_VALUE` / `Failed setup for format cuda`, in red, and that is what got reported as the cause of a failure that was really the fps above. §6.1 already had the measurement — FFmpeg treats `-hwaccel` as a preference and decodes in software — and it was re-confirmed on this source: 3 s at `-hwaccel cuda`, same NVDEC refusal, **exit 0 with correct frames**, software fallback at 1.41x. The reassurance was only broadcast *after* a successful run, i.e. never on the runs that need it. It now goes out on the first `_HWACCEL_FAILED` line instead, and `extract.json` keeps `hwaccel_fell_back` as before. |

| 2026-08-28 | **Step 5's phase list was captured whole rather than remembered, and it corrected three things this file already said.** `docs/spirula/mesh-run.txt`: 419 lines, 98 025 gaussians and 238 cameras in **18.15 s**, exit 0, `mesh.glb` 11.6 MB, 78 670 vertices / 84 166 faces / 5111 components, a 4096 px texture at 26.8 % texel coverage. §7.8 had listed the phases from inference and was wrong in three ways. **`Delaunay` and `UV` are phases** and were missing. **There are three camera loops, not one** — `occupancy`, `texel density` and `color` — and together they are **360 of the 419 lines**, 86 % of the run, against a 500-line LiveLog: the same trap `_EXTRACT_NOISE` was written for on 2026-08-27, so `step_mesh` keeps the line that closes each block and rides the bar on the other 354 with an **empty message**, which `websocket.broadcast` omits from the payload and the store therefore never logs (419 in, 65 out, replayed against the capture). And **the phases are not monotone**: `merge`, `cull unseen` and `cleanup` each run twice, and `bisection` re-evaluates the occupancy grid once per iteration, so 180 consecutive `occupancy:` counter lines land *inside* `bisection` — a phase therefore only ever moves the bar forwards, or a run would go backwards three times. The bar was watched over a real run: **monotone, 0.00 → 0.99, 423 points**. The measurement was cheap and the guess would have been free; the guess was wrong. |
| 2026-08-28 | **The mesh gets the third renderer — JB's call, and §13.6 closes.** `GLTFLoader` ships inside `three`, so it costs no dependency and no §10 row, and `MeshCanvas` hangs the glTF off the same scene root as the other two canvases under the same single `Rx-90`: the mesh is extracted from the splat and coloured by the same cameras, so §7.3's measurement covers it too. **It is also the one preview source with no preview file** — there is no record format to decimate a textured glb into, and it is small next to what it came from (11.6 MB against a 24 MB splat, and the reference run's 29.9 MB against 247 MB) — so `preview.status` reports it ready with `url == source_url` and skips every piece of fingerprint / stamp / prune bookkeeping. Only glTF is offered: a `--format ply` mesh would be read by the PLY parser as a plain cloud and drawn as its vertices, which is a different picture rather than a cheaper one. Three canvas details that are not decoration — a **headlight plus ambient**, because a textured glb arrives with a PBR material and an unlit scene renders it black; **`DoubleSide`**, because `--cull-unseen` left 63 326 boundary edges and a backface-culled hole is a black hole; and a **wireframe toggle**, because a surface that looks solid and one that is solid are the same picture until you see the edges. |
| 2026-08-28 | **`export/` holds hard links, and step 6 asks for the splat by name.** `step_export` scanned `lfs_output/`, a directory that went with LichtFeld Studio — it now takes step 4's `train/run/step-*.ckpt/splat.ply` and step 5's `mesh/` outputs through the same finders those steps use, and **links** rather than copies them: the reference splat is 178 MB and `export/` would otherwise be a second copy of bytes that already exist, which is `step_conform._link_or_copy`'s argument one step later. Verified on a real run — `nlink 2`, same bytes — and safe both ways round, because a step 5 reset drops `export/` and leaves `train/`, a step 4 reset drops `train/` and leaves `export/`, and nothing in the app writes *into* an exported file. It no longer resets step 5 either: `run_mesh` already did that before writing a byte, and a second reset would have deleted the mesh it was being asked to export. Two live bugs went with it: `glob("*.ply")[0]` would hand Blender `mesh.ply` — which sorts before `splat.ply` — to import as a gaussian cloud, and the README's `{supersplat_url}` placeholder raised `AttributeError` on every step 6 *after* Blender had already run, because `supersplat_url` left `config.json` with the two CUDA tools. |

| 2026-08-28 | **`spirula geometry` does not resolve images outside the dataset folder, so P3 ships the junction §13.1 said this could force — and it lives only for the length of the run.** There is no `--image-dir` on this tool. Pointed at `<project>/sfm` with the images in the sibling `<project>/frames`, it resolved `<project>/sfm\imagesrame_0001.jpg`, answered `can't fopen` and `skipping` for all 238, and finished `done: 0 written, 0 already there, in 0s` at **exit 0** — a silent, total failure. With `sfm/images` junctioned to `frames/` the identical command wrote **238 normal maps in 35 s** at `--max-size 512`, 55 MB. Both runs are in `docs/spirula/geometry-run.txt`. The junction is created before the command and removed in a `finally`, so **§5's layout on disk is exactly what §5 says it is**: no `copytree`, no `rmtree`, no zip and no `find_model` ever meets one, and there is still one copy of the frames. A Windows junction needs neither administrator rights nor Developer Mode, unlike `os.symlink`, and `os.rmdir` removes it leaving the target intact — verified before it was relied on, because getting it wrong deletes `frames/`. Two consequences beyond the layout: **exit 0 is not success here**, so the step judges `done: N written` and the folder rather than the return code; and the 419.4 MB `curl` fetch is a **child process**, so abort was tested on it — `curl.exe` and `spirula.exe` both gone from the process table, `ProcessAborted` not `error`, junction removed, frames intact. |
| 2026-08-28 | **A mask pairs to its frame by basename *and* by COLMAP's `.jpg.png`, and `sam mask` itself writes the basename form — §13.3 closes, and so does §13.4 for `--mask-dir`.** Both had been open since P1.6 put `--mask-dir <project>/masks` on the live command line on nothing but an argument from symmetry. `sfm extract --masks` over 20 frames dropped **16 929** keypoints "over masked images: 20", 88 230 → 71 301 features, and the COLMAP naming gave the byte-identical result — so the two conventions are interchangeable and what step 2's alpha extraction already writes is right. `sfm auto` prints `Masks: <dir>\masks` **with no flag passed** and drops that line under `--no-masks`, which is §5.2's automatic adoption seen rather than inferred. And `train --mask-dir` does take an absolute path outside `--data`: three 200-iteration runs each way, psnr **15.12 / 17.48 / 15.18** masked against **22.40 / 22.68 / 20.33** with a path that does not exist — not deterministic, but disjoint by ~2.9 dB, which is masked corners trained as empty space. **The negative control is the finding worth keeping: a wrong `--mask-dir` exits 0 and trains unmasked**, because `--load-masks` is "use the dataset's masks when they exist". Step 4 logs the mask count for exactly that reason. `docs/spirula/sam-mask-run.txt`. |
| 2026-08-28 | **`sam` and `geometry` attach to a wizard step without ever marking it done, and that is a correction rather than a decision.** The inherited `run_mask_generation` set `step_status["3"] = "done"` on success — so writing 238 mask PNGs would have put a green tick on a reconstruction that had never been run, and a *failed* mask pass would have painted a finished one red. Curation earns `/analyze`'s treatment because it really is the second phase of step 2; masking and geometry do not, because a mask run produces no reconstruction and a geometry run produces no splat. `_run_attached_pass` captures the step's prior status and hands it back — on success, on abort and on error alike — and only the run's own name (`masks`, `geometry`) carries live state to the LiveLog and the bar, which the store already mapped to steps 3 and 4. `masks/` stays step 2's directory in §14.1's table for the same reason: it is an input to the reconstruction, not an output of it. |
| 2026-08-28 | **Two more channels join `_EXTRACT_NOISE`'s rule, and one of them is this tool family's only CR-redrawn bar.** The failing geometry run printed **476 of its 483 non-bar lines** as one `load_image: cannot read` / `skipping` pair per image, against a 500-line LiveLog; they are counted rather than logged, and the count *is* the finding the step reports. The 419.4 MB checkpoint fetch arrives as **703 CR fragments** through `iter_lines`, which is the §15.1 defect `proc.iter_lines` was kept for — they ride their own 0.02→0.20 stretch of the bar with an empty message, which `websocket.broadcast` omits from the payload. Measured over a real run: monotone 0.00 → 0.99, 250 lines to the bus, none of them a bar fragment. `sam mask` needed the opposite treatment: **two lines for the whole run and no counter**, 238 frames in 2.6 s, so its bar is its two ends and `ProgressBar`'s indeterminate fallback covers the middle. |
| 2026-08-28 | **A `--normal-format` switch writes the new maps beside the old rather than over them, so the run says so.** A `png` run followed by a `jpg` one left `sfm/normals/` holding **476 files for 238 frames**: `--overwrite` is about recomputing a map, not about a file whose name no longer matches. Which of the two `train --normal-dir` then reads is not something to guess at on the user's behalf, so `step_geometry` counts the other format and names it, in the log and in `geometry_result.json`. The same reasoning put `mask_result.json` in `analysis/` rather than in `masks/`: §5 says `masks/` holds one greyscale PNG per frame, and it is a directory both `sfm auto` and `train` are pointed at. |

Any new structural decision → add a row here in the same commit.

---

## 13. Open questions and backlog

Prioritised worklist lives in [TODO.md](TODO.md). This file is the spec; that one is what
comes next. The genuinely open questions, in the order they block something:

1. ~~**Does `spirula geometry` resolve images outside the dataset folder?**~~ **Settled
   2026-08-28: no, and the junction it was said to threaten is what P3 shipped.** There is
   no `--image-dir` on this tool; it resolved `<dataset>\images\<name>`, skipped all 238
   images and finished `done: 0 written` at **exit 0**. `step_geometry` junctions
   `sfm/images` to `frames/` for the length of the run and removes it in a `finally`, so
   nothing else in the app ever meets one and §5's layout is unchanged. §7.5.
2. **A curation verdict is advisory, and that is a consequence nobody chose.** Step 3
   is handed the image *directory*, and §5.2's whole point is that there is no second,
   filtered copy of the frames anywhere — step 4 trains on the same `frames/`. So
   `selection.json` marks a frame `rejected:blur` and `sfm auto` reconstructs it anyway;
   only the gallery's Delete actually removes it. Step 3's panel says so where the user
   can see it rather than pretending otherwise. Three ways out, and it is **JB's call**:
   leave it advisory and lean on Delete; move the rejected frames to
   `frames/_rejected/` in step 2 (a move, not a copy, so §5.2 survives intact, and it
   is reversible); or accept them and rely on the mapper's own rejection. Nothing is
   implemented until that is decided.
3. ~~**Basename or full filename for a mask?**~~ **Settled 2026-08-28: both, and
   `sam mask` itself writes the basename form.** `sfm extract --masks` over 20 frames
   dropped **16 929** keypoints "over masked images: 20", 88 230 → 71 301 features, and the
   COLMAP convention `frame_0001.jpg.png` gave the byte-identical numbers. `sfm auto` also
   prints `Masks: <dir>\masks` with no flag passed, and `--no-masks` removes that line —
   so the automatic adoption of §5.2 is seen rather than inferred.
   `docs/spirula/sam-mask-run.txt`.
4. **`--mask-dir` is absolute-path-capable — settled 2026-08-28 (§5.2).** Six
   200-iteration runs, three each way, psnr bands disjoint by ~2.9 dB. `--depth-dir` and
   `--normal-dir` remain assumed by symmetry and are **not on the live path**, since
   `sfm/depths` and `sfm/normals` are inside `--data`. What the runs also turned up is
   worth more than the answer: **a wrong `--mask-dir` exits 0 and trains unmasked.**
5. **The MoGe / Metric3D checkpoint licences** (§10). Still open, and now on a live path:
   the default fetch is `moge2-vitb-normal.onnx`, 419.4 MB, from
   `huggingface.co/Ruicheng/moge-2-vitb-normal-onnx`. The step names the URL and says the
   licence is unaudited before it runs; the audit itself is still owed.
6. ~~**Does step 5's mesh get its own viewer mode, or a thumbnail?**~~ **Settled
   2026-08-28: the third renderer.** `GLTFLoader` ships inside `three`, so it costs no
   dependency and no §10 row; `MeshCanvas` is §7.9. The mesh has no decimated preview
   either — the file the tool wrote is what loads.
7. **The WS bus carries no project id, so the store applies every message to whatever
   project is open.** Seen in P1.7: step 4 of the reference project displayed a second
   project's bar at 56 % with a live ETA. `/api/pipeline/start` refuses a second run
   only *for the same project*, so nothing enforces §1's "one running job at a time"
   across projects. Two ways out — put `project_id` on every message and filter in the
   store, or refuse a start while any project is running — and it is **JB's call**.
8. **`--progress-dir`'s binary channel** (§7.2). `model.bin` + `pairs.bin` snapshots
   while `sfm auto` runs, for a front end that shows the reconstruction assembling rather
   than tailing its log. A refinement, after the stdout bar works.
9. **`spirula sfm` has five stages under `auto`** — `extract`, `match`, `map`, `merge`,
   `ba` — each runnable alone and each reading and writing COLMAP's formats, "so any one
   of them can be replaced by COLMAP's equivalent to bisect a failure". Not modelled;
   worth remembering the day a reconstruction fails and nobody can tell which stage lost
   it.

---

## 14. Project lifecycle — copy, reset, archive, delete

Four options on each tile of the Projects list (`⋮` menu). All refused while that project
has a job running, and all working on one slug.

| Option | What it does | What it keeps |
|---|---|---|
| **Copy** | Asks for a name, duplicates the directory and the row — wizard position, step statuses and `settings_json` included | everything but `preview/`, which is a cache |
| **Reset** | Deletes the artefacts of a step and of every step after it, then rewinds `current_step` | **always `input/`** — the source is never a casualty of a reset |
| **Archive** | Zips the directory into `projects/_archives/<slug>.zip`, removes the directory, keeps the row disabled | the zip, until restored or the project is deleted |
| **Delete** | Removes the row, the directory and the archive | nothing |

### 14.1 What a reset deletes

| Step | Directories | Files |
|---|---|---|
| 2 Extract | `frames/`, `masks/`, `analysis/`, `report/` | |
| 3 SfM | `sfm/` — the workspace, the sparse models, `sfm_result.json`. **Not `masks/`**: they are step 2's or the mask run's, and an input to this step rather than an output of it | |
| 4 Train | `train/` | |
| 5 Mesh | `mesh/`, `export/` | |
| 6 Scene | | `export/scene.blend`, `export/README_SPLATFORGE.txt` |

Step 1 is deliberately absent: it owns `input/`. Steps 5 and 6 share `export/` — 5 fills
it, 6 adds the Blender scene — so resetting 5 necessarily takes 6 with it. `preview/`
goes as soon as any step from 3 on is reset: it is built from those outputs and would
otherwise show the previous run's cloud next to an empty directory.

`sfm/depths/` and `sfm/normals/` sit inside `sfm/` and therefore go with a step 3 reset.
That is correct — they are per-image maps of the images this reconstruction registered —
but it means re-running step 3 costs the geometry pass too, and the step must say so
before it deletes them.

**Re-running a step is a reset of that step**, run *after* the tool and the input are
located and *before* the first byte is written. `input/` is never a casualty. The rule
across every step: **locate the tool and the input first, delete second.** The
predecessor learned this the hard way — its step 2 reset sat *above* `resolve_ffmpeg_path`,
which raises when there is no ffmpeg anywhere, so a bad tool path deleted the frames it
was then unable to re-extract.

### 14.2 The operations are modal

All four run behind a blocking dialog (`ProjectOperationDialog`), mounted by
`WizardShell` and driven from the store — not by the list, which unmounts the moment the
user changes step and used to take the only sign of progress with it. Nothing dismisses
it: no Escape, no click-outside, no close button.

That is not decoration. A copy moves gigabytes file by file and **there is nothing to
abort it with** — no child process to kill, unlike a pipeline step — so starting a step
or another operation on top of a running one is a half-written directory, not a queue.
Progress travels on the WS bus under the step name `project`, which the store routes to
the dialog rather than to `stepProgress`, reporting every 20 files plus every file over
8 MB (a project of five 1 GB splats trips neither rule on its own).

---

## 15. Progress reporting — what each tool can actually tell us

A bar that does not move is a bug report the user cannot file. Every step reports from a
channel **measured on this workstation**, or says it has none.

| Step | Channel | Denominator |
|---|---|---|
| 2 extract | FFmpeg `-progress pipe:1 -nostats` — `key=value` blocks on stdout, ~2/s | `out_time_us` against `probe.json`'s `duration_s`; `max_frames` too when capped, whichever is further along |
| 2 conform | FFmpeg `-progress pipe:1` over the image sequence — `frame=` | the image count, which is exact (§6.7) |
| 2 curate | `step_analyze._chunked`, every 24 frames | frame count |
| 2/3 masks | `spirula sam` stdout | `track` prints a per-frame counter; **`mask` prints two lines and no counter at all** — measured, 238 frames in 2.6 s, so its bar is its two ends |
| 3 SfM | `spirula sfm auto` stdout, tagged and live (§7.2) | `[extract] N/total`, then `[map] images in the model: N` |
| 4 geometry | `spirula geometry` stdout, `N / M images, T ms each, R left`; the **checkpoint download is a CR-redrawn `curl` bar** (§7.5), several hundred fragments per fetch, dropped from the bus and ridden as its own 0.02→0.20 stretch | per-image, 0.22→0.99 |
| 4 train | the `step N/M` line, every 100 steps, CRLF, unbuffered (§7.7) | `N/M`, mapped onto 5–95 % |
| 5 mesh | `spirula mesh` stdout, `[meshing] <phase>:` (§7.8) | `cameras rendered: N/total` inside each of the three camera loops; the other phases are named, not counted, and the ladder never moves backwards |

### 15.1 The carriage return, and why `proc.iter_lines()` is still here

In `3DGS_App_26`, FFmpeg, LichtFeld Studio and RealityScan all redrew a status line with
a bare CR on a line that never terminated. `readline()` splits on LF only, so it handed
back the whole run as one line, at exit — exactly when the progress it carried had
stopped being useful.

**Spirula does not have this defect**, and it is worth saying why rather than just
noticing: `Main.cpp` calls `setvbuf(stdout, nullptr, _IONBF, 0)` whenever stdout is not a
tty. Measured by sampling a redirected file mid-run: 12 complete CRLF-terminated lines,
971 bytes, at step 301 of 3000.

`proc.iter_lines()` is kept anyway, unchanged, because it costs nothing and two channels
still need it: FFmpeg's own `-progress` reader (unchanged from the predecessor) and
`spirula geometry`'s `curl` download bar, which is CR-redrawn (§7.5).

### 15.2 A message's type does not decide whether its progress is used

`websocket.py` picks `msg_type` by priority and tested `data` before `progress`, so a
line carrying both went out as `metric` — and the store's `metric` case never touched
`stepProgress`. The store reads `progress` above the `switch`, whatever the type.
Reordering the priority would have been the wrong fix: a message legitimately carries a
metric *and* a position, and the type says what it is mainly about, not which of its
fields may be read.

The training line here carries both on every one of its lines (§7.7), so this is not a
hypothetical.

### 15.3 Where the bars are flat, and the honest fallback

- **`spirula mesh`'s uncounted phases.** 204 s in the reference run, of which the three
  camera loops are one part and `texture` alone was 30.9 s. `merge` was the single
  longest phase of the 18 s capture (6.2 s of it) and it prints no denominator at all.
  The phases are named in the log, so the step reports *which* phase and rests the bar on
  that phase's floor rather than inventing a percentage inside it.
- **The curation fallback paths** — a forced `cut_source: "video"`, or the automatic
  fallback when the scdet scores are missing or truncated — where the source is decoded
  again and `progress_cb` is wired only into `detect_from_frames`.

Until a phase has a real number, `ProgressBar` is the honest fallback: a step that has
been `running` for 10 s without a progress message switches to indeterminate stripes and
an elapsed-time count, keeping the last percentage beside them when there was one.
"31 %, and nothing since" is more use than either a frozen bar or no number.
