# CLAUDE.md — SfM Splat Pipeline App

> Local-first web app that drives a full video/photos → SfM → 3D Gaussian Splatting →
> mesh pipeline on **one external binary plus FFmpeg**: import, extract and curate
> frames, align, train, mesh, deliver.
>
> Owner: JB (baronstudio). Single user, Windows workstation, local GPU.
>
> Successor to `3DGS_App_26`, which is still alive and still used. The doctrine here is
> inherited from that project's own CLAUDE.md; everything in it about RealityScan and
> LichtFeld Studio is history, everything else is law. Where a measurement was made
> there and still applies, this file carries the number and says where it came from.

---

## 1. What this app is

A 5-step wizard (React) driving a FastAPI backend that orchestrates local binaries as
subprocesses:

```
video/images ─> [2] extract+curate ─> [3] SfM ─> [4] train ─> [5] mesh+export
                    FFmpeg (ours)      spirula   spirula      spirula
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
- No 3D *editor*. The viewer looks, it never writes — and the **crop tool of §7.6b
  is the one amendment to that, not a breach of it**: the viewer places the volumes
  and shows the cut live, a backend pass makes it, and what it makes is a second
  file beside the trained splat. The one place in this app that removes
  reconstructed data is `core/crop.py`, it is always reversible by deleting one
  directory, and nothing in the viewer writes a byte.
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
   (`taskkill /F /T`), because that is what closes the output and unblocks the reader. It
   is not optional here: `spirula geometry` shells out to `curl` for its checkpoint, so
   the process that holds the work is not always the one we spawned. It works on a pid
   and not on a handle we hold, which is what also makes it work on a run **re-attached**
   after a backend restart (§15.4) — measured, not assumed.
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

**`settings_json.crop` and `settings_json.viewpoint` are the two exceptions, and
both are deliberate.** The crop volumes (§7.6b) and the saved camera (§7.6d) are
stored there with no `defaults.json` counterpart, because a box placed around
*this* scene — or a camera parked in front of it — is not a default anything could
inherit. They live in the settings blob rather than under `train/` so they
**outlive a step 4 reset**, which correctly takes the cut file with it but has no
business discarding where the user put the box or which way they aimed the view:
the frame both are expressed in comes from the sparse model, which a re-train does
not touch.

**Capture metadata is a fourth thing, and it is neither of the three above.**
`footage_author` (how the footage was shot) and `description` are **columns on the
project row**, not a `settings_json` section: they have no `defaults.json`
counterpart to inherit from — there is no default author — and the project list
draws the author beside the name without parsing a blob. They are free text, read
by no step, carried by a copy, and edited in the wizard's **Project info** panel
under the step navigator, which also recaps everything else the row knows. Like
every other per-project panel they save on every change, debounced, with no Save
button (§4 above).

`SECTIONS` is `extract, curate, sfm, sam, geometry, train, mesh, export, viewer`.

The setup panel is opened by the **gear icon in the WizardShell top bar**, and it
carries three more sections that are not `defaults.json` sections at all, because
none of them is a business default: **Tools** is layer 1 above, **Checkpoints**
(§7.4b) is the neural weights installed on this machine, and **Hardware**
(§4.1) is the machine itself. All three are properties of the installation,
which is why they are here and not on a wizard step — asking for a checkpoint
from inside step 3 would ask the same question once per project.

**Hardware is a fourth thing again: it is not a layer at all, because there is
nothing in it to set.** Tools and Checkpoints are installation *state* — a path
somebody typed, a file somebody fetched — and both have a Save. Hardware is
read-only from end to end, so it has no draft, no Save button and no factory
reset, and the footer's save indicator is hidden under it rather than reporting
"Saved" about a section that cannot be.

### 4.1 The hardware panel and the sidebar gauges

What this workstation is, and what it is doing. `backend/core/hardware.py`
reads it; **Setup → Hardware** draws the whole of it, and a compact strip of
gauges under the step navigator draws the live half on every step.

**It costs no new dependency, which is §2.1 and the reason it is ctypes and
`winreg` rather than three lines of `psutil`.** Everything is a documented
Windows facility: `GetSystemTimes` for CPU load (0.26 ms), `GlobalMemoryStatusEx`
for RAM (0.02 ms), `GetLogicalProcessorInformationEx` for physical cores, the
DirectX and display-class registries for adapter names, VRAM and driver
versions (1.5 ms), and **PDH performance counters** for live GPU utilisation and
VRAM (1.4 ms). A poll of the whole live payload measured **1.4 ms** in-process
and 5.5–23 ms over HTTP, which is what makes a one-second gauge affordable
beside a 956-second training run.

**PDH rather than `nvidia-smi`, and that is §1 rather than a preference.** This
app exists because spirula is Vulkan and runs on Intel, AMD and Apple silicon;
a panel that could only gauge the NVIDIA card would contradict the project on
its own setup screen. One PDH counter reports **both** GPUs in this machine
through one code path. `nvidia-smi` was used only to check the numbers, and it
checked out: PDH's dedicated usage read **960.4 MB against its 955 MB** for the
same adapter. **Utilisation deliberately does not match and must not be claimed
to** — `nvidia-smi`'s is an instantaneous sample, PDH's is an average over the
interval between two collections, so three simultaneous readings answered
51/22/46 % against 25.9/20.4/27.9 %. They track the same load and disagree by
construction; the averaged one is what a gauge should draw.

Four findings that shape the code rather than decorate it:

- **An integrated GPU's VRAM is *shared*, not dedicated.** The UHD 770 reports a
  **128 MB** stub of dedicated memory against **12 142 MB** of shared system
  memory — which is also what `spirula sam devices` calls its "11.9 G". So the
  gauge divides by the *dedicated* pool for a discrete part and the *shared* one
  for an integrated part, and says which; the naive reading would peg the Intel
  card at a full bar the moment anything drew a window.
- **The DirectX registry accumulates stale adapters.** A driver update writes a
  new LUID key and never removes the old one: **six keys here for three live
  adapters**, four of them the same Intel UHD 770 sharing its name and therefore
  its driver version. **PDH is the filter** — an adapter Windows reports no
  counter for does not exist any more — and the registry is only the name
  lookup. Caught in the browser rather than in the payload: the first panel drew
  the same Intel card four times.
- **Utilisation is the *max* over engine types, never the sum.** The counter is
  per process *and* per engine (`3D`, `Compute`, `Copy`, `VideoDecode`), and
  adding them counts a decode and a draw as 200 %. Summing per engine type and
  taking the largest is what Task Manager reports. The per-engine breakdown is
  kept and shown, because it is what tells FFmpeg's `-hwaccel` decode (§6.1)
  apart from a training run.
- **One poller for the page, not one per component.** The panel and the sidebar
  strip want the same tick, and the backend keeps **one** PDH query whose
  reading is the delta since whoever polled last — so two independent
  `setInterval`s would each have averaged over half the interval and disagreed
  with each other. `useHardware.ts` holds the timer, the last sample and the
  subscriber list, and stops entirely when nothing is mounted or the tab is
  hidden. Same lesson as the two-sockets bug of §12 (2026-08-28), one hook
  along.

**The gauges are read-only and say so by never inventing a number.** A reading
with no value yet — the first poll has no interval to average the CPU over — is
drawn `—` rather than 0 %. Colour is keyed to meaning and not to magnitude: cyan
while ordinary, amber past 75 %, red past 90 %, because a GPU pinned at 100 %
through a training run is the tool working and should not shout, while memory
about to run out should.

---

## 5. Data layout

```
sfm-splat-app/
├── config.json                 # installation (binary paths, model cache)
├── defaults.json               # business defaults + capture presets
├── pipeline.db                 # SQLite: project registry + the run records (§15.4)
├── runs/                       # ⚙ per run, and outside projects/ on purpose (§15.4):
│                               #   <job_id>.ndjson   the bus, one line per message with
│                               #                     text — the log tail a reloaded page
│                               #                     restores
│                               #   <job_id>.tool.log the tool's own stdout, verbatim —
│                               #                     what makes a run survive the
│                               #                     backend, because a step replays its
│                               #                     parser over it. Pruned with the row
├── backend/
│   ├── main.py                 # FastAPI app, routers, /static mount
│   ├── api/routes/             # projects, pipeline, settings, defaults, files, models
│   ├── api/websocket.py        # broadcast bus
│   ├── api/file_handles.py     # the AsyncFile close fix (§7.8)
│   ├── core/jobs.py            # the run record: one row per run + its log (§15.4)
│   ├── core/config.py          # config.json  (AppConfig singleton)
│   ├── core/defaults.py        # defaults.json (AppDefaults) + fps resolver
│   ├── core/models_catalog.py  # the 12 checkpoints, read out of spirula.exe (§7.4b)
│   ├── core/model_store.py     # install / resume / verify / remove them
│   ├── core/proc.py            # spawn / iter_lines / kill_tree  (§15.1)
│   ├── core/dataset_images.py  # <dataset>/images → frames/, for one run (§7.5, §7.8)
│   ├── core/probe.py           # ffprobe wrapper (pure)
│   ├── core/pipeline_runner.py # orchestrator, abort
│   ├── core/steps/             # step_extract, step_conform, step_analyze,
│   │                           #   step_sfm, step_train, step_mesh,
│   │                           #   step_sam, step_geometry, step_export
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
    │   ├── run/               #   `--output-dir-name`: config.json + step-%09d.ckpt/
    │   ├── crop/              #   ⭑ the volume cut (§7.6b): splat.ply + crop_result.json.
    │   │                      #     Beside the trained splat, never over it; step 5
    │   │                      #     prefers it through `resolve_splat`
    │   └── export/            #   ⭑ the deliverable drawer (§7.6c): reduced copies in
    │                          #     five formats + export_result.json, plus a
    │                          #     <name>.viewpoint.json beside every format that
    │                          #     cannot carry the saved camera in a header (§7.6d).
    │                          #     **Nothing in the pipeline reads it** — never named
    │                          #     splat.ply, and invisible to `resolve_splat` and
    │                          #     `find_export_splat`
    ├── mesh/                   # step 5 — `--output` (§7.8: never left to default):
    │                          #   mesh.glb / .ply / .obj / .gltf + mesh_result.json
    ├── export/                 # step 5's delivery drawer: splat.ply + mesh.*
    │                          #   (hard links, §7.10)
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

- **`geometry` and `mesh` are the exception to all of this.** Neither has an
  `--image-dir` at all and both resolve `<dataset>\images\<name>`, which §7.5 and
  §7.8 measure and work around with the same junction, living only for the length
  of the run. `mesh` hid it until the crop shipped: handed a checkpoint *inside*
  `train/run/`, it reads the `image_dir` recorded in that run's own `config.json`
  and never consults the dataset — and `train/crop/splat.ply` has no `config.json`
  above it.

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

### 7.4b The checkpoint manager — Setup → Checkpoints

`spirula.exe` is 119 MB of tools and **not one gram of weights** (§5.1). Two of the
six tools want a checkpoint and they behave differently, both badly for an app
driving them:

* **`sam track --model` takes a file and never fetches one.** Before this, the
  mask panel's own hint read *"the .pt / .onnx file you downloaded"* — the user
  had to find a checkpoint on the web, know which of six they wanted, and paste
  an absolute path.
* **`geometry --model` fetches a known id *mid-run*** — 419.4 MB through a `curl`
  child, on the machine's first geometry pass, behind the one CR-redrawn bar in
  this tool family (§7.5) and with an unaudited-licence WARNING going past in the
  log.

So installing a checkpoint is a **global setup concern**, drawn beside the tool
paths and never on a wizard step: it is a property of this machine, like where
FFmpeg is, and asking for it from inside step 3 asks the same question once per
project. The step panels keep their `--model` fields; this is where the file they
name comes from.

**The catalogue is read out of the installed binary, not off a web page.**
`spirula.exe` carries, for every checkpoint it knows, the local filename, the URL
and — for the geometry models — a sha256, as three null-terminated strings in a
row. That is the tool's own integrity registry, and using it is what makes a file
this app installs indistinguishable from one spirula fetched itself. Proven
rather than assumed: the `moge2-vitb-normal.onnx` a real `geometry` run had
already downloaded hashes to `bbf14e07…f35a21` over 419 411 850 bytes, **exactly**
the value compiled into the exe. Twelve rows, and every `size_bytes` is
`X-Linked-Size` off a live HEAD against its own URL (2026-08-30), so the panel
states a real download size before it fetches anything:

| Family | Rows | Sizes |
|---|---|---|
| `sam` — `sam track --model <file>` | `sam3-q4_0`, `sam3-f16`, `sam2.1-{large,base-plus,small,tiny}` | 79.3 MB → 1.84 GB |
| `geometry` — `geometry --model <id\|file>` | `moge2-{vits,vitb,vitl}`, `metric3d-vit-{small,large,giant2}` | 75.8 MB → 2.76 GB |

**Where they go is spirula's own model directory, and that is the whole interop
argument** — `%LOCALAPPDATA%\spirula-studio\models\`, measured off a real run's
`[moge] saved …` line. **No environment variable moves it**: the binary's own list
is `SS_LANG, SS_VK_DEVICE, SS_NO_AUTO_FETCH, SS_NN_LOG…` and none of them names a
model directory. So a checkpoint installed under the manifest's own name is one
the tool finds by itself with no flag — and `/use` additionally writes the
**absolute path** into `defaults.json`, which works whether or not the cache sits
where the tool would look. That is also what stops the mid-run fetch: a
`geometry` handed a path never opens the network.

Four things it does that a browser download into `Downloads/` does not:

- **It resumes, and it resumes the tool's own leftovers.** Measured 2026-08-30,
  HuggingFace answers `206 Partial Content` with `Accept-Ranges: bytes`. The
  partial is `<name>.part`, which is **spirula's own convention** — its aborted
  `moge2-vitl` fetch had left exactly that file, 1 232 896 bytes, in the cache —
  so a part written by either side is finished by the other. Watched end to end:
  that 1.2 MB part grew to 22 204 416, Cancel left it at 22 204 416, and the next
  Download **resumed at 22 204 416 rather than 0**.
- **It verifies before it installs, and never renames a bad file into place.**
  The geometry rows carry the binary's sha256; the SAM rows carry none, so the
  byte count is the check. A file that fails is kept as `.part` and named — a
  truncated checkpoint that loads is worse than one that is missing. The verified
  part is moved onto the final name with `os.replace`, atomic on one volume, so
  no reader ever sees a half file under the name the tool looks up.
- **Four licences, accepted separately** (§10). SAM 2.1's Apache-2.0 says nothing
  about SAM 3's bespoke Meta licence, and a single "I agree" spanning both would
  answer the harder question by accident. The Download button is dead until the
  matching switch is on **and the route re-checks it**, so the gate cannot be
  walked past by calling the API. The unaudited row says so in amber rather than
  wearing a green tick.
- **Manual install is a path, not an upload** — §6.7's argument one feature
  along: the app runs on the workstation that holds the file, so a 2 GB
  checkpoint already on this disk is a local copy. It is checked against the
  manifest *before* it is installed, because a hand-downloaded file is the one
  most likely to be the wrong one and the manifest is what can tell.

**One at a time, refused rather than queued** (§2.5) — two 2 GB fetches over one
link finish no sooner for overlapping — and **start-and-poll rather than the WS
bus**, mirroring `/preview`: the bus carries no project id (§13.7) and every
consumer maps a step name onto the open project's bar, so a download belonging to
no project must not move one. Measured whole: `sam2.1-tiny`, 79 320 544 bytes in
**14.2 s**, verified, installed, and `/use` writing both the path and the licence
that was accepted for it into `defaults.json`.

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

### 7.6b The crop — box and sphere volumes over the trained splat

A training run reconstructs everything the cameras saw, which on a real capture is
the subject *and* the room behind it, the operator's feet, and a halo of
low-opacity floaters where the frustums stop overlapping. None of that is a defect
of the run and none of it belongs in the mesh step 5 extracts. The crop is how it
goes.

**It is a pass, not a step**, the third of the shape `sam` and `geometry` already
have (§7.4, §7.5): re-runnable on its own, attached to wizard step 4, and it never
marks that step anything. Same argument as everywhere else — a 30 000-iteration run
measured **956 s** and the crop after it measures **0.46 s**, so tying them
together would make moving a box cost sixteen minutes.

**The cut writes beside, never over**, and that is the whole safety argument:
`train/crop/splat.ply` next to `train/run/step-*.ckpt/splat.ply`. It buys four
things for one duplicated file — a crop is undone by deleting one directory; a
re-crop starts from the trained splat, so dragging a volume back out restores what
it excluded; the two files cannot be confused, because `find_splat` only ever finds
the trained one and `find_crop` only ever finds the cut one; and a step 4 reset
takes the crop with it, correctly, since the splat it was cut from has just gone.
`step_crop.resolve_splat` is the single place that chooses, **and both readers name
what they got** — a mesh of 715 890 gaussians and a mesh of the 300 000 that
survived a crop are the same command line and very different results.

**The rows are copied verbatim.** A splat PLY carries 62 properties per vertex, 45
of them spherical harmonics the preview path deliberately drops (§7.9), and a crop
must lose none of them. So the kept records move as raw bytes of the source dtype
and the header is the source's own bytes with `element vertex N` changed — whatever
spirula wrote, we write back. Proven on the reference splat: 574 817 gaussians,
142 556 147 B, cut to **441 084 in 0.46 s**, output byte count exactly
`data_offset + count x 248` and 62 properties still there.

Two passes, not one, and that is forced: **a PLY states its vertex count before the
vertices**, and a crop cannot know that number until it has tested every gaussian.
The first pass builds the mask from x, y, z alone; the second copies. Both are
memory-mapped and chunked through the executor, so the event loop — and therefore
the abort route — stays answerable, which is `step_analyze._chunked`'s argument
(§15). This is also the **first pass in the app whose abort is only the cooperative
flag**: `sam` and `geometry` are subprocesses that §2.6's tree kill unblocks, and
there is no process here to kill.

**The rule the stack is evaluated by**, identical in `core/crop.py` and in the
preview shader:

```
kept = (inside at least one `keep` volume, or there are none)
       and (inside no `delete` volume)
```

so a stack reads "keep this room, minus that lamp, minus those floaters", **delete
always wins**, and a single `delete` sphere is a complete crop on its own. Eight
volumes is the cap, because the shader carries a fixed-length uniform array and the
two ends have to agree on one number. A crop that keeps *nothing* is refused before
a byte is written — an empty result is always a mistake, and writing it would hand
step 5 a valid PLY of zero gaussians to spend four minutes meshing.

**The volumes are stored in the dataset frame, and that is not a detail.** The
viewer applies one `Rx-90` for three.js's Y-up plus the "Flip up" toggle (§7.3), so
a volume stored as authored would land somewhere else the next time the scene was
opened with the flip in the other position. `cropVolumes.ts` converts on both
sides; `crop.py` tests the `x, y, z` the tools wrote. A "sphere" with unequal
half-extents is an ellipsoid on purpose — that is what a scaled sphere *is*, and
refusing it would mean ignoring two thirds of a drag the user just made.

**Where the volumes live: `settings_json` under `crop`.** It is the only §4 layer-3
section with no `defaults.json` counterpart, because a volume placed around *this*
scene is not a default anything could inherit. What it is, is project data that has
to **outlive a step 4 reset** — `train/crop/` goes with `train/`, and should, but
the frame comes from the sparse model, which a re-train does not touch, so the
volumes are still exactly right and asking for them to be placed again with the
gizmo would be gratuitous. It also means a copied project carries its crop.

**The live cut is a shader patch, and it is contained rather than hidden.**
`@mkkellogg/gaussian-splats-3d` builds its splat material itself and exposes no
hook, so `cropShader.ts` edits the vertex shader by string, at the line where it
has just decoded `splatCenter` and is about to leave model space — an anchor that
occurs **exactly once** in the built source and is checked for that before
anything is patched, so a library upgrade degrades to "no live preview, Apply still
cuts the file" rather than to a blank canvas. Three things make it sound:

- **`splatCenter` is in viewer world space**, and that is a property of how this app
  configures the viewer rather than an assumption: with `dynamicScene: false` the
  library bakes each scene's transform into the splat data, so the rotation
  `SplatCanvas` passes to `addSplatScene` is already in there. It is the same space
  `getSplatCenter(i, v, true)` reports and the same space the gizmo works in.
- **The material is rebuilt whenever the mesh is**, which a progressive load does
  more than once, so the patch is re-applied from the animation loop rather than
  installed once in an effect — where it would be thrown away mid-load, silently,
  and the preview would come back uncropped.
- **The rejection is the library's own idiom**, `gl_Position = vec4(0.0, 0.0, 2.0,
  1.0); return;`, which is already what it does for a splat outside the clip volume.

The alternative was to filter the loaded `.splat` buffer on the CPU and hand the
library a new scene per drag frame, which is a 32 MB re-upload at the viewer's
default level. This is one `mat4` multiply per gaussian per frame on hardware
already doing a covariance decode per gaussian per frame, and nothing at all when
the stack is empty.

`TransformControls` ships inside `three`, so the gizmo costs no dependency and no
§10 row — the same argument that settled `GLTFLoader` for the mesh canvas. Each
volume is a faint translucent solid (which is what the raycaster picks: a
`LineSegments` is nearly impossible to click and an invisible mesh is skipped by
`Raycaster` outright) under a bright wireframe, both with `depthTest` off, because
the splats render with `depthWrite: false` and a volume inside the cloud would
otherwise be a cage you can see only the near half of.

**Measured whole in a browser on 2026-08-30**, headed on this workstation's GPU for
the reason §12's 2026-08-28 row gives. On the 98 025-gaussian throwaway: adding the
default keep box took the canvas PNG from 349 292 B to **158 036 B** (-54.8 %) and
shrinking it to 0.6 units took it to **36 818 B** (-89.5 %); toggling "Live cut"
off with the same volumes in place put it back to 279 387 B, which is what proves
the shader is the thing doing the hiding. Dragging the translate gizmo moved the
stored centre from `(0.174, 0.444, -0.096)` to `(0.405, 0.629, -0.096)`. Apply cut
98 025 to **3 009 in 0.07 s** and the panel reported the output path. **No console
error on any of it.**

**Two states the panel exists to make impossible to miss.** *Stale*: a crop is on
disk and the volumes have moved since, so the file is not wrong but *old*, and
step 5 would read it without knowing — nothing re-runs on its own, the panel
says so, and Apply is one click. *No live preview*: the shader patch did not take,
so the volumes draw but nothing hides, which is said out loud rather than left to
be read as "the crop selects everything".

### 7.6c The export — a deliverable copy, in five formats

A trained `splat.ply` is a **working file**: 62 float properties per vertex,
248 bytes each, **177 542 251 B** for the 715 890 gaussians of the reference run.
That is the right thing for step 5 to mesh and the wrong thing to hand anybody —
a web viewer, a client, a phone. So step 4 gets one more pass, and what it writes
is a third file beside the trained splat and the crop.

**It is the opposite of the crop, and that is the whole design.** A crop is
*pipeline data*: `resolve_splat` hands it to step 5 and it meshes what it
gets. An export is *terminal* — smaller on purpose, by dropping spherical
harmonics the mesher's colour pass wants and quantising into formats no
mesher reads at all. So it lives in `train/export/`, it is **never named
`splat.ply`**, and neither `find_splat` (which globs `step-*.ckpt/splat.ply`)
nor `find_export_splat` can see it. `preview._find_splat` skips the directory
outright for the same reason: a `.compressed.ply` would parse as a plain point
cloud and draw as its vertices.

It **reads the crop when there is one** — the source is `resolve_splat`, and the
log line names which file it got. Proven rather than assumed: a 98 025-gaussian
splat cut to 19 702 exported as `<slug>_crop_sh0.ply` with `source_cropped:
true` and exactly 19 702 rows.

**And it warns when there is one only on screen.** The live cut is a *shader
test on the preview* (§7.6b), so volumes placed and never applied leave a scene
that looks trimmed and a `resolve_splat` that still answers the trained splat —
which is the one confusion this feature can plausibly cause, and it caused it on
the first real run: a 573 956-gaussian export taken with a keep box sitting
un-applied on the viewer. Nothing is guessed and nothing is refused; the panel
says so above the button and the run broadcasts a WARNING, because a log line
outlives the panel that could also have said it. A crop that exists but is
*stale* gets the second half of the same treatment.

**The panel is the last thing on step 4's page**, below the viewer that carries
the crop. Everything above it produces the splat — the run, then the cut — and
this is the only thing that produces a file for somebody else; asking for an
export format before the scene has been looked at or trimmed is the wrong
order.

**Nothing spirula offers is involved.** There is no `spirula export`; everything
in `train --help-all`'s `[Run & Output]` is about checkpoints, and
`--quantization-level` is how colours are stored *during* training. Every
reduction below is ours.

#### What each reduction actually costs

Measured 2026-08-30 on `soupirail_alfredriom`, 715 890 gaussians, 62 properties,
248 B/vertex, 177 542 251 B:

| Option | Result | Cost |
|---|---|---|
| **SH 3 → 0** (drop all 45 `f_rest_*`) | 68 B/vertex, **48 680 936 B**, 3.65× | 0.29 s |
| **SH 3 → 1** (keep 9 of 45) | 104 B/vertex, **74 453 198 B**, 2.38× | 0.48 s |
| **`.splat`** 32 B record | **22 908 480 B**, 7.75× | 0.33 s |
| **Opacity floor** α ≥ 0.005 | drops **1.2 %** | one column |
| **Opacity floor** α ≥ 0.05 | drops **43.2 %** | one column |
| **Target count**, importance | 100 000 of 715 890 at SH 0 → 6 800 416 B, **26.1×** | 0.24 s |

and on the 98 025-gaussian throwaway (24 311 730 B), through
`@playcanvas/splat-transform`:

| Format | Result | Cost |
|---|---|---|
| **`.sog`** | **1 181 769 B**, 20.6× | 4.74 s (k-means over the SH) |
| **`.spz`** | **1 469 281 B**, 16.5× | 0.26 s |
| **`.compressed.ply`** | **6 008 890 B**, 4.0× | 0.37 s |

**Every knob ships off.** The default export is the trained splat byte for byte,
in the trainer's own format: "give me the file" has to be one setting rather than
a combination. Two of them carry a caveat the panel says out loud:

- **The opacity floor is not free housekeeping here.** Spirula's gaussians are
  low-opacity by construction — median linear alpha **0.059** under
  `--opacity-reg 0.01` against a 1 M cap — so the 1/255 threshold every other
  3DGS toolchain ships as a freebie drops 1.2 % of the reference file, and
  anything high enough to matter is an edit of the picture.
- **The importance score is approximate and is labelled so.** α × ellipsoid
  volume, because the proper significance score counts how often each gaussian is
  actually hit by a training ray and that needs a rasteriser, i.e. a trainer. It
  does move the right way — keeping the top 50 % raised the mean alpha of the
  survivors from 0.098 to 0.128 — and nothing is re-fitted afterwards, so a deep
  cut thins the picture rather than simplifying it.

#### The SH block is renumbered, and that was measured the hard way

The layout is **channel-major**, `f_rest_{c*15+k}`, the INRIA convention —
verified rather than assumed: the per-index RMS profile of the 45 coefficients
repeats with period **15**, not 3 (`0.0632 0.0810 0.0641…` three times over). So
a degree cut is a subset of columns per channel, never a head slice, which would
keep the whole red channel and nothing of green or blue.

**And the survivors must be renumbered contiguously.** A degree-1 PLY written
with the source's own indices — `f_rest_0,1,2,15,16,17,30,31,32` — was refused by
`splat-transform` with `readPly: unrecognized f_rest_* count 33`: a reader sizes
the SH block from the *highest index it sees*, not from how many properties there
are. The file had the right nine columns of data in it and was unreadable by
everything but us. A degree-1 export carries `f_rest_0..8`.

#### Two families of format, one pipeline

`ply` and `splat` are `core/splat_export.py` — numpy over a memory map, no
dependency, and the same two-pass chunked shape §7.6b uses for the same reason (a
PLY states its vertex count *before* its vertices). Rows that drop no property
are copied **verbatim** in the source's own dtype under the source's own header,
so an unreduced export is the trainer's 62 properties bit for bit.

`sog`, `spz` and `compressed-ply` are **`@playcanvas/splat-transform`** (§10),
reached by writing the reduced PLY into a staging file and converting it. That
ordering means every knob behaves identically in all five formats and the
external tool only ever re-encodes a file we just wrote. It is resolved **before**
the reduction runs, not after — §14.1's "locate the tool first, work second" one
feature along.

Both Python routes to those formats were checked and both are refused: **`spz`**
(PyPI) ships one `cp313-manylinux` wheel and would need a Rust toolchain here,
and **`sogs`** (PyPI) imports `torch`, `torchpq` and `plas` at module scope —
`torchpq` is **CUDA**, which is §1's hard non-goal — on top of a GPLv3 `plyfile`
it would pull into our own process.

`train/export/` is inside `train/`, so a step 4 reset takes it (§14.1) — right,
since the file is a copy of a splat that reset just deleted. The *plan* lives in
`defaults.json` + `settings_json` under `export`, an ordinary §4 section unlike
the crop's volumes, because "SH 0, .sog" is exactly the sort of thing that should
follow you between projects.

### 7.6d The saved viewpoint — one camera, stored once, shipped with the file

A splat has no front. A trained scene opens whichever way the framing code
points it, and the one person who knows which way it is worth being seen
from is the person who just spent five minutes orbiting it. So the viewer's
toolbar gets one more button, **Save view**, and what it saves rides out with
the export.

**It is the third thing stored in the dataset frame, and for the third time
that is the whole care of it.** The viewer draws everything under one `Rx-90`
scene root plus "Flip up" (§7.3), so a camera read straight off
`viewer.camera` is in *viewer* space and would point somewhere else the next
time the scene was opened with the flip the other way — the trap §7.6b stores
the crop volumes in the dataset frame to avoid. `viewpoint.ts` converts on both
sides, `core/viewpoint.py` never has to know about three.js, and the stored
`up` comes back as **`(0, 0, 1)`** — measured in a browser, which is +Z and
therefore the conversion working rather than a constant somebody typed.

**Where it lives: `settings_json` under `viewpoint`.** The second §4 layer-3
section with no `defaults.json` counterpart, next to the crop's volumes and for
the same reason — a camera parked in front of *this* scene is not a default
another project could inherit — and it outlives a step 4 reset for the same
reason too, since the frame comes from the sparse model a re-train does not
touch. There is no debounce and there is no Save button anywhere else in this
app, and both are right: every other panel saves a *stream* of edits, this
saves one deliberate act.

**Restoring is exact, and it took a measurement to make it so.**
`@mkkellogg/gaussian-splats-3d` builds its OrbitControls with
`enableDamping = true, dampingFactor = 0.05`, and a damped `update()` keeps
applying the residual of the last drag *after* a teleport: measured in a
browser, "Saved" clicked 2.5 s after a drag landed **0.025 away** from the
stored position and went on drifting. The fork exposes `clearDampedRotation()`
and `clearDampedPan()`; calling both before the camera moves took the same
measurement to **0.000000** on position and target alike. They are declared
optional in the typings, so a library that drops them degrades to the
approximate restore rather than to a crash.

**Restoring across a flip turns the scene over first.** `flip_up` is stored not
because the numbers need it — they are frame-independent — but because the
*scene* does: the splat canvas reads its rotation once, at load, so a restore
under the other vertical sets the toggle and lands the camera when the canvas
comes back (`restoreOnLoad`).

#### What "shipped with the file" means, per format

| Format | Where the viewpoint goes |
|---|---|
| `ply` | **the header**, as `comment viewpoint …` lines |
| `splat`, `sog`, `spz`, `compressed-ply` | **`<name>.viewpoint.json` beside it** |

PLY comments are part of the format and every reader skips the ones it does not
know, so this costs a few dozen bytes and breaks nothing — measured on the
reference crop, 312 347 gaussians: both header routes carry them (the verbatim
copy of the source's own header *and* the header rebuilt for an SH cut), the
file re-reads at exactly `data_offset + count x stride`, and
`@playcanvas/splat-transform` read the commented PLY back without complaint
(`312K gaussians · 3 SH bands`, exit 0). One line per key, space-separated,
because a comment is free text and the only thing that makes it readable
elsewhere is being trivially parseable.

Everything else has nowhere to put it — `.splat` is a headerless stream of
32-byte records, and the three compressed formats are written by a tool that
would drop whatever we put in the PLY we hand it — so those get a sidecar. A
sidecar is honest about what it is; inventing a private container would not be.

**A stored viewpoint that will not parse is said out loud, not dropped.**
`core/viewpoint.py` refuses a camera standing on its own target, a
non-finite coordinate and a field of view outside 1–179°, and the run
broadcasts a WARNING and exports without it — it is the one part of an export
the user placed by hand, so its absence has to be visible. The panel says the
same thing before the run, through the same parser, so the two cannot disagree.

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

**`mesh` reads the images through the dataset folder, exactly like `geometry`, and
step 5 junctions them in for the length of the run.** There is no `--image-dir` on
this tool either — `mesh --help` lists none, whatever the shared ColmapParser's
"set --image-dir if needed" says. Measured 2026-08-30 on `poubelle_garnier_v2`: with
the crop's `train/crop/splat.ply` as the checkpoint the run read the cameras and then
died on `ColmapParser: <project>\sfm\images\frame_0001.jpg does not exist`, **exit 1**,
nothing written. **A checkpoint under `train/run/` escapes it for a reason worth
writing down: the tool reads the `image_dir` recorded in that run's own
`config.json`** — `train/run/config.json` carries the absolute `frames/` path step 4
passed — so step 5 worked from the trained splat and failed on every cropped project,
because `train/crop/` has no `config.json` above it. `core/dataset_images.py` holds
the junction both steps now share; with it in place the identical command wrote
**363 468 vertices / 402 011 faces and a 26.3 MB `mesh.glb` in 98.34 s** from 312 347
cropped gaussians and 300 cameras.

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

### 7.10 Step 5's second half — `export/`

**The pipeline ends here.** There was a step 6 that ran Blender headless over
`blender_splatforge.py` to assemble a `scene.blend`; it was removed on 2026-08-30 (§12)
and nothing replaced it. `export/` is now step 5's own delivery drawer and the last thing
the pipeline writes.

**`export/` is filled by step 5's second half, and it holds hard links.** `step_export`
takes step 4's `train/run/step-*.ckpt/splat.ply` and step 5's `mesh/` outputs — not
`lfs_output/`, which went with LichtFeld Studio — and links rather than copies them, on
`step_conform._link_or_copy`'s argument one step later: the reference splat is 178 MB and
`export/` would otherwise be a second copy of bytes that already exist. It is safe both
ways round — a step 5 reset drops `export/` and leaves `train/` holding the splat, a step 4
reset drops `train/` and leaves `export/` holding the bytes — and nothing in the app ever
writes *into* an exported file. It does **not** reset step 5 itself: `run_mesh` already
did, before it wrote a byte, and a second reset would delete the mesh it is exporting.

**The splat is asked for by name, not by glob.** With `mesh --format ply` the export holds
`mesh.ply` too and it sorts first, so the predecessor's `glob("*.ply")[0]` would hand a
caller asking for the gaussian cloud a surface mesh instead. `find_export_splat` is the one
that knows, and it survives the step that used to call it because `/api/files/{id}/export`
labels the drawer's contents with it.

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
GET    /api/pipeline/status            running state — plus `job`, the run to restore (§15.4)
GET    /api/pipeline/jobs              the recent runs, newest first (?project_id=&limit=)
GET    /api/pipeline/jobs/{id}/log     one run's log: the tail, or everything ?after= a line
POST   /api/pipeline/analyze           re-run curation alone — never re-extracts
POST   /api/pipeline/masks             run `spirula sam` alone — never re-extracts (§7.4)
POST   /api/pipeline/geometry          run `spirula geometry` alone — never re-trains (§7.5)
POST   /api/pipeline/crop              cut the splat to the crop volumes — never re-trains (§7.6b)
POST   /api/pipeline/export-splat      write a deliverable copy of the splat (§7.6c)
GET    /api/settings/                  config.json  (installation)
PUT    /api/settings/                  update config.json
GET    /api/defaults/                  defaults.json (business defaults)
PUT    /api/defaults/                  deep-merge update
POST   /api/defaults/reset             factory reset (optional ?section=)
GET    /api/defaults/presets           capture presets
GET    /api/models/                    the checkpoint catalogue, the licences, the cache
                                       and what is installed in it (§7.4b)
POST   /api/models/{id}/download       fetch one — returns at once, poll the GET.
                                       Refuses without the matching licence accepted
POST   /api/models/{id}/cancel         stop it; what was fetched stays a resumable .part
POST   /api/models/{id}/verify         re-read the files and re-check them against the manifest
POST   /api/models/{id}/adopt          install one downloaded by hand, from a path on this machine
POST   /api/models/{id}/use            point defaults.json's sam/geometry `model` at it
DELETE /api/models/{id}                remove its files from the cache
GET    /api/models/in-use              which checkpoint each family's default names
GET    /api/version/                   app name, version (commit date + commit count) and commit id
GET    /api/hardware/                  CPU, memory, graphics adapters and spirula's Vulkan
                                       verdict on them (§4.1). Cached per process; ?refresh=1
                                       re-reads it, including the `sam devices` subprocess
GET    /api/hardware/live              one gauge tick: CPU %, RAM, per-adapter GPU % and VRAM.
                                       Measured at 1.4 ms, which is what makes it pollable
GET    /api/files/{project}/frames     frame list + curation verdicts
GET    /api/files/{project}/analysis   scores.json + selection.json + overrides
GET    /api/files/{project}/sources    input/ listing: probe + poster frame per video
GET    /api/files/{project}/masks      what masks/ holds, plus the last sam run's report
GET    /api/files/{project}/geometry   what sfm/{normals,depths}/ hold + the last run
GET    /api/files/{project}/train      train_result.json + what --data and --image-dir will read
GET    /api/files/{project}/crop       the crop volumes, the last cut, and whether it is stale (§7.6b)
GET    /api/files/{project}/export-splat   the export drawer, the saved viewpoint (§7.6d)
                                       and where this format would carry it, what the
                                       next export reads, and
                                       whether splat-transform is installed (§7.6c)
DELETE /api/files/{project}/export-splat   empty train/export/ — nothing downstream notices
GET    /api/files/{project}/mesh       mesh_result.json + the checkpoint and cameras step 5 will read
GET    /api/files/{project}/preview    3D preview state (?source=sfm|train|mesh&max_count=)
POST   /api/files/{project}/preview    build that preview — returns at once, poll the GET
GET    /api/files/{project}/cameras    camera poses of the last reconstruction, for the overlay
WS     /ws/logs                        progress, logs, metrics
GET    /static/<slug>/...              project files (thumbnails, exports)
```

`/masks`, `/geometry`, `/crop` and `/export-splat` are the same argument one step
earlier as `/analyze`: **the expensive phase must not be redone to change a
threshold.** The last of the four is also the only one whose output no later step
reads.

---

## 10. Licence audit table

Audit as if the tool could be distributed tomorrow. FFmpeg and spirula are invoked as
**subprocesses**, never linked.

| Dependency | Licence | Status |
|---|---|---|
| FastAPI / Uvicorn / Pydantic | MIT / BSD-3 | ✅ ok |
| SQLModel | MIT | ✅ ok |
| websockets, aiofiles, httpx, watchdog | BSD / MIT / Apache-2.0 | ✅ ok |
| OpenCV (`opencv-python`) | Apache-2.0 | ✅ ok. **Not** the headless build: PySceneDetect depends on `opencv-python`, both wheels provide the same `cv2` package and cannot coexist |
| NumPy | BSD-3 | ✅ ok |
| PySceneDetect | BSD-3 | ✅ ok |
| FFmpeg (system exe) | LGPL-2.1+ (GPL if built with x264) | ✅ ok as subprocess — re-audit before any distribution |
| **Spirula Studio (`spirula.exe`)** | **GPL-3.0** | ✅ **subprocess only, never linked, never bundled**. Two footnotes below |
| ~~Blender~~ | GPL, external | ❌ **removed 2026-08-30 with wizard step 6** (§12). No longer invoked anywhere; `blender_exe_path` is out of `config.json` |
| **`@playcanvas/splat-transform`** (npm) | **MIT** | ✅ **optional, subprocess only, never linked, never bundled** — the three compressed export formats of §7.6c (SOG, SPZ, compressed PLY) and nothing else. Installed into the gitignored `tools/splat-transform/` with `npm install --prefix tools/splat-transform @playcanvas/splat-transform`; needs Node.js. PLY and `.splat` exports need nothing at all |
| ↳ `@adobe/spz` (its dependency) | ISC | ✅ ok — pulled in by the above, never imported by us |
| ↳ `webgpu` (its dependency) | MIT | ✅ ok — same standing |
| ~~`spz`~~ (PyPI) | MIT OR Apache-2.0 | ❌ **rejected 2026-08-30**: one wheel published, `cp313-manylinux_2_34_x86_64`. No Windows wheel, so it means a Rust toolchain on the target machine — the *build* dependency §5.1 refuses for spirula itself |
| ~~`sogs`~~ (PyPI) | Apache-2.0 | ❌ **rejected 2026-08-30**: declares `numpy, pillow, plyfile, tyro` and then imports `torch`, `torchpq` and `plas` at module scope. **`torchpq` is CUDA** — §1's hard non-goal — and its declared `plyfile` is **GPLv3**, which importing would pull into our own process rather than leave in a subprocess |
| **SAM 2.1 checkpoints** (via `spirula sam`) | **Apache-2.0** | ⚠ never bundled. Four rows — tiny / small / base+ / large, 79.3 MB to 450.9 MB — installed by **Setup → Checkpoints** (§7.4b) into `%LOCALAPPDATA%\spirula-studio\models\`. Terms shown and accepted before any fetch, and the route re-checks the acceptance |
| **SAM 3 checkpoints** (via `spirula sam`) | **Meta's own, non-standard** | ⚠ never bundled; same route (`sam3-q4_0` 706.6 MB, `sam3-f16` 1.84 GB), and the licence is *not* Apache — it is a **separate switch** in the panel from SAM 2.1's, because one acceptance spanning both would answer the harder question by accident. Both come from `huggingface.co/PABannier/sam3.cpp`, whose repo card declares no licence of its own: the terms are Meta's, taken from upstream |
| **Metric3D checkpoints** (via `spirula geometry`) | **CC0-1.0** | ✅ audited 2026-08-30 through the HuggingFace API: all three `onnx-community/metric3d-vit-{small,large,giant2}` cards declare `cc0-1.0`, a public-domain dedication with nothing to comply with. Never bundled; installed by §7.4b. The upstream Metric3D research code is a separate question this app never touches |
| **MoGe-2 checkpoints** (via `spirula geometry`) | **MIT upstream — the mirrors are thinner** | ⚠ audited 2026-08-30 and **not clean**: `Ruicheng/moge-2-vitl-normal-onnx` declares `mit` on its card, and the `vits` / `vitb` mirrors — the second of which is the 419.4 MB file spirula fetches by default — **declare no licence at all**. MoGe-2 upstream is MIT, so the terms are taken from upstream rather than from the repository the file actually comes from. §7.4b's panel says exactly that, in amber, and §13.5 stays open on this one row alone |
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
| 2026-08-30 | **The splat gets a crop tool, and §1's "the viewer looks, it never writes" is amended rather than broken — because the viewer still never writes.** JB asked for box and sphere volumes placed in 3D with an invert, keeping or removing the gaussians inside them. The viewer places them and shows the cut; `core/crop.py` makes it, server-side, over the full PLY; and what it makes is **a second file beside the trained splat**, `train/crop/splat.ply`, which is what keeps the whole thing reversible — a crop is undone by deleting one directory, a re-crop starts from the trained splat rather than from the last crop, and `step_crop.resolve_splat` is the single place steps 5 and 6 choose between them, **both naming which file they got**. The preview could not have done the cut: `SceneViewer` opens a decimated `.splat` of at most a million records against a `splat.ply` that measured **142 556 147 B / 574 817 gaussians** on the reference project, so the browser sees a sample and only the backend sees every gaussian. **The rows are copied verbatim** — 62 properties, 45 of them spherical harmonics the preview path drops — with the source's own header and one number changed: measured, that splat cut to **441 084 gaussians in 0.46 s**, output size exactly `data_offset + count x 248`, 62 properties intact, and a tagged column proved the kept rows were the right ones. Two passes are forced, because a PLY states its vertex count *before* the vertices. The stack rule is `(inside a keep volume, or there are none) and (inside no delete volume)` — **delete always wins** — implemented once in numpy and once in GLSL, capped at 8 because the shader's uniform array is fixed-length, and a crop keeping nothing is refused before a byte is written. The volumes are stored in the **dataset frame** so the `Rx-90` and "Flip up" of §7.3 cannot move them, and in `settings_json` rather than in `train/`, so they **outlive the step 4 reset** that correctly takes the cut file with it. |
| 2026-08-30 | **The live cut is a string patch of the splat library's vertex shader, and it was watched in a browser rather than reasoned about.** `@mkkellogg/gaussian-splats-3d` exposes no hook, so `cropShader.ts` injects the volume test at the line where the shader has just decoded `splatCenter` — an anchor that occurs **exactly once** in the built source and is checked for before anything is patched, so a library upgrade degrades to "no live preview, Apply still cuts the file" instead of to a blank canvas. It works on `splatCenter` directly because `dynamicScene: false` makes the library **bake** the scene transform into the splat data, which is the same space `getSplatCenter(i, v, true)` reports and the same space the gizmo works in; and it is re-applied from the animation loop because the library **rebuilds the material whenever it rebuilds the mesh**, which a progressive load does more than once — installed once in an effect it would be silently thrown away mid-load and the preview would come back uncropped. The alternative, filtering the `.splat` buffer on the CPU, is a 32 MB re-upload per drag frame. Driven headed through Playwright on this workstation's GPU, for the reason the 2026-08-28 row gives: on the 98 025-gaussian throwaway the canvas PNG went **349 292 B → 158 036 B** (-54.8 %) on adding the default keep box and **→ 36 818 B** (-89.5 %) at 0.6 units, and toggling "Live cut" off with the same volumes in place put it back to **279 387 B** — which is what proves the shader is the thing hiding them rather than something else. The translate gizmo moved the stored centre `(0.174, 0.444, -0.096) → (0.405, 0.629, -0.096)`, Apply cut 98 025 to **3 009 in 0.07 s**, and there was **no console error** on any of it. `TransformControls` ships inside `three`, so the gizmo costs no dependency and no §10 row — the same argument that settled `GLTFLoader` on 2026-08-28. |

| 2026-08-30 | **Step 4 gets a deliverable export, and it is the mirror image of the crop: nothing in the pipeline reads it.** JB asked for the trained PLY to be exportable, with reduction if it was possible. It was, in five formats. The crop writes *pipeline data* — `resolve_splat` hands it to steps 5 and 6 — whereas this writes into `train/export/` under a name neither `find_splat` nor `find_export_splat` can match, and `preview._find_splat` skips the directory outright because a `.compressed.ply` would parse there as a plain cloud and draw as its vertices. **Spirula contributes nothing to it**: there is no `spirula export`, and `--quantization-level` is a *training* setting. So every reduction is ours, measured on the 715 890-gaussian reference splat (177 542 251 B): **SH 3→0 is 68 B/vertex, 48 680 936 B, 3.65×, in 0.29 s** with no geometry loss at all — 72.6 % of every vertex is spherical harmonics — SH 3→1 is 2.38×, the existing 32-byte `.splat` record is 7.75×, and a 100 000-gaussian target at SH 0 is 26.1×. **Two of the knobs carry a measurement that contradicts the obvious default.** The 1/255 opacity floor every other 3DGS toolchain ships as free housekeeping drops **1.2 %** here and 0.05 drops **43.2 %**, because spirula trains at low opacity by design (median linear alpha 0.059 under `--opacity-reg 0.01`); and the importance score is α × ellipsoid volume, an honest approximation of LightGaussian's hit-counted one, which needs a rasteriser we refuse to add. Both ship **off**, and both say so in the panel. Rows that drop no property are copied verbatim under the source's own header, so an unreduced export is the trainer's 62 properties bit for bit. **The panel lives at the bottom of step 4's page, below the viewer that carries the crop** — everything above it makes the splat, and this is the only thing that makes a file for somebody else. It reads the crop through `resolve_splat` (measured: 98 025 cut to 19 702, exported at exactly 19 702) and it **warns when volumes are placed but not applied**, which is not hypothetical — the first real run exported 573 956 gaussians with a keep box sitting un-applied on the viewer, where the live shader had already made the scene look cut. |
| 2026-08-30 | **The surviving SH columns are renumbered, and the file that proved it was readable only by us.** The `f_rest` layout is channel-major, `f_rest_{c*15+k}` — verified rather than assumed, by the per-index RMS profile of the 45 coefficients repeating with period **15** and not 3. A degree cut is therefore a per-channel subset and never a head slice (which would keep all of red and none of green or blue). But a degree-1 PLY written with the source's *own* indices — `f_rest_0,1,2,15,16,17,30,31,32` — was refused by `splat-transform`: **`readPly: unrecognized f_rest_* count 33`**. A reader sizes the SH block from the highest index it sees, not from how many properties are declared, so the nine right columns of data under their original names are an unreadable file everywhere but here. `kept_properties` returns `(output name, source name, code)` triples for exactly this, and a degree-1 export carries `f_rest_0..8`. Caught by running the conversion rather than by reasoning about the format. |
| 2026-08-30 | **The three compressed formats are `@playcanvas/splat-transform`, MIT, a subprocess — and both Python routes to them were measured and refused.** `spz` (PyPI, MIT/Apache-2.0) publishes exactly one wheel, `cp313-manylinux_2_34_x86_64`, so on this Windows workstation it means building a Rust crate — the *build* toolchain §5.1 refuses for spirula itself. `sogs` (PyPI, Apache-2.0) declares `numpy, pillow, plyfile, tyro` and then imports `torch`, **`torchpq`** and `plas` at module scope: `torchpq` is CUDA, and a CUDA dependency is §1's whole reason this project exists — plus a GPLv3 `plyfile` it would pull into our own process rather than leave in a subprocess, which is precisely the line §10 draws around spirula and Blender. The npm CLI has the standing FFmpeg, spirula and Blender have, needs no GPU compute, and writes all three from one invocation: measured on the 98 025-gaussian throwaway (24 311 730 B), **`.sog` 1 181 769 B (20.6×) in 4.74 s**, **`.spz` 1 469 281 B (16.5×) in 0.26 s**, **`.compressed.ply` 6 008 890 B (4.0×) in 0.37 s**. It is resolved *before* the reduction runs, on §14.1's locate-the-tool-first rule, and it is optional: PLY and `.splat` need nothing installed and the panel greys the other three out with the install line rather than offering a button that fails after the work. |

| 2026-08-30 | **Wizard step 6 is removed: no Blender, no `scene.blend`, and the pipeline now ends at the mesh.** JB's call. Step 6 shelled out to Blender headless with `blender_splatforge.py` to import `export/splat.ply` through the SplatForge add-on and save a `scene.blend` beside it — and it is the one step that had never been run since it was rewired off `lfs_output/` (TODO.md P2.3). What it produced was a convenience wrapper around a manual import, and it cost a third external binary on the machine, a `blender_exe_path` in `config.json`, a `blender` section in `defaults.json`, a §10 licence row, and a shared-directory rule (`export/` belonging to two steps) that every reset, every log line and half of `step_export`'s docstring had to explain. **`export/` is now step 5's alone** — it meshes, then fills the drawer with the splat it meshed and the mesh it wrote — so §14.1 loses a row rather than gaining a caveat, and `run_mesh`'s "this re-run also clears the Blender scene" warning goes with it. Nothing downstream is lost: `export/splat.ply` and `export/mesh.glb` are the same hard links they always were, and importing one into Blender by hand is the same two clicks the generated scene was wrapping. `find_export_splat` survives its only remaining caller, `/api/files/{id}/export`, because `mesh.ply` still sorts before `splat.ply` and something has to say which is which. Deleted: `backend/core/steps/step_scene.py`, `backend/scripts/blender_splatforge.py`, `frontend/src/components/wizard/steps/Step6_Scene.tsx`, `frontend/public/help/step6.html`. |

| 2026-08-30 | **The splat viewer gets "Save view", and what it saves is shipped with the export rather than kept in the app.** JB asked for a button on step 4's splat preview to store the viewpoint, for the export to use. A splat has no front — the framing code points the camera wherever the bounds say — so the one thing this feature carries is the judgement of the person who just orbited the scene. It is stored in the **dataset frame**, the third thing to be (after the crop volumes and the tools' own `x, y, z`), and for the third time that is the whole care of it: measured in a browser, the stored `up` comes back **`(0, 0, 1)`**, which is +Z and therefore the `Rx-90` conversion working rather than a constant somebody typed. It lives in `settings_json` under `viewpoint` — the second §4 layer-3 section with no `defaults.json` counterpart, for the crop's reason, and it outlives a step 4 reset for the crop's reason too. **Restoring it is exact, and that took a measurement.** `@mkkellogg/gaussian-splats-3d` runs its OrbitControls with `enableDamping = true, dampingFactor = 0.05`, and a damped `update()` keeps applying the last drag's residual *after* a teleport: "Saved" clicked 2.5 s after a drag landed **0.025 away** from the stored position and drifted on. Its fork exposes `clearDampedRotation()` and `clearDampedPan()`; calling both first took the same measurement to **0.000000** on position and target alike — drag away, click Saved, click Save view, and the numbers are the ones stored before the drag. **What "used on the export" means is decided by the format, not by us.** A native `.ply` carries it in the header as `comment viewpoint …` lines — measured on the 312 347-gaussian reference crop, on both header routes (the verbatim copy of spirula's own header and the header rebuilt for an SH cut), the file re-reading at exactly `data_offset + count x stride` and `@playcanvas/splat-transform` reading the commented PLY back without complaint. Everything else — `.splat`'s headerless 32-byte records and the three formats the external tool re-encodes — gets a `<name>.viewpoint.json` beside it, because a sidecar is honest and a private container would not be. A stored viewpoint that will not parse **warns and is left out** rather than failing the export or vanishing from it: it is the one part of an export the user placed by hand. |
| 2026-08-30 | **The checkpoints get a manager in the global setup panel, and its catalogue is read out of `spirula.exe` rather than off a web page.** JB asked for a downloader to simplify the post-install of this class of dependency, and to put it in the global parameters rather than a step's sub-panel — which is right twice over: a checkpoint is a property of this machine, like the FFmpeg path, and asking for one from inside step 3 asks the same question once per project. The binary turned out to carry its own manifest — local filename, URL and, for the geometry models, a **sha256** — as three null-terminated strings in a row, twelve rows in all. Proven before it was trusted: the `moge2-vitb-normal.onnx` a real `geometry` run had already fetched hashes to `bbf14e07…f35a21` over 419 411 850 bytes, **exactly** the value in the exe, so a file this panel installs is byte-indistinguishable from one the tool downloaded itself. Every size is `X-Linked-Size` off a live HEAD, so the panel names a real download before fetching. **The `.part` convention is spirula's own**, not ours — its aborted `moge2-vitl` fetch had left one, 1 232 896 bytes — and HuggingFace answers `206 Partial Content`, so this resumes the tool's leftovers as readily as its own: watched end to end, that part grew to 22 204 416, Cancel left it there, and the next Download **resumed at 22 204 416 rather than 0**. `sam2.1-tiny` installed whole in **14.2 s**, verified, with `/use` writing the absolute path *and* the licence accepted for it into `defaults.json` — the absolute path being what stops `geometry` opening the network mid-run, since **no environment variable moves the tool's own cache** (`SS_LANG, SS_VK_DEVICE, SS_NO_AUTO_FETCH, SS_NN_LOG…` and nothing for a model directory). Four licences and four separate switches, gated in the panel *and* re-checked in the route. |
| 2026-08-30 | **The audit §13.5 owed came back mixed, and the panel says which half.** Queried through the HuggingFace API: all three `onnx-community/metric3d-vit-*` cards declare **CC0-1.0**, which closes that family outright. MoGe-2 did not — `moge-2-vitl-normal-onnx` declares `mit`, and the `vits` and `vitb` mirrors **declare no licence at all**, the second of them being the 419.4 MB file spirula fetches by default. Upstream MoGe-2 is MIT, so the terms are taken from upstream rather than from the repository the bytes come from, and that is a weaker statement than a licence on the artefact. It is written in the row, and the panel draws that licence with an amber "not audited" flag instead of a tick — the alternative was a green tick on an inference. §13.5 shrinks to one family instead of closing. |
| 2026-08-30 | **The first-run screen could never be got past on a fresh install, and it was found by making it compile.** `SetupScreen` gated Proceed on `rc_exe_path && lfs_exe_path` — RealityScan and LichtFeld Studio, whose keys left `config.json` when the tools did (§12, 2026-08-27) — so `canProceed` was permanently false and the only screen a new install sees was a dead end. It survived P1 because `App.tsx` skips it the moment a project exists, and this workstation has had projects since the first day. It now gates on **spirula.exe and FFmpeg**, which are what a pipeline actually needs, and reports the checkpoints as explicitly **optional**: `sfm`, `train`, `mesh` and `sam mask` want none, so a first run should not be blocked on a 700 MB download it may never use. The setup panel's Tools section lost the same two dead rows and gained the spirula path and the checkpoint cache. |
| 2026-08-30 | **Step 5 could not mesh a cropped splat, and the junction §7.5 built for `geometry` is what it needed too.** `Poubelle_Garnier_V2` failed step 5 at **exit 1** with nothing written: `ColmapParser: <project>\sfm\images\frame_0001.jpg does not exist (set --image-dir if needed)` — a flag `mesh --help` does not list, on a tool that resolves `<dataset>\images\<name>` exactly as `geometry` does. **What hid it for two days is worth the row on its own:** handed a checkpoint under `train/run/`, `mesh` reads the `image_dir` recorded in that run's own `config.json` (measured: `train/run/config.json` carries the absolute `frames/` path step 4 passed) and never consults the dataset at all — so `zz_abort_test` meshed from `step-000000200.ckpt/splat.ply` and every project that had been **cropped** failed, because §7.6b writes `train/crop/splat.ply` and there is no `config.json` above it. The crop shipped on 2026-08-30 and took step 5 with it. The fix is `_ImageJunction` promoted out of `step_geometry` into `core/dataset_images.py` and entered by both runs: created before the command, removed in `__exit__`, so §5's layout on disk is still exactly what §5 says it is and there is still one copy of the frames. Measured with it in place, the identical failing command wrote **363 468 vertices / 402 011 faces, `mesh.glb` 26.3 MB, in 98.34 s** from the 312 347 cropped gaussians and 300 cameras, and the whole step run through `run_mesh` finished exit 0 with the junction created during the run and gone after it. The alternative — passing the `.ckpt` directory instead of the `splat.ply` — would have papered over it for uncropped projects and left the crop unmeshable, which is the case that matters. |
| 2026-08-30 | **A new project inherited the previous one's green ticks, and then the frontend wrote them into its row — so `step_status` was fiction rather than a report.** Reported by JB: create a project, extract the frames, and steps 3, 4 and 5 come up done. Confirmed in the data before anything was changed — `Soupirail_Alfredriom_006`, created 2026-08-30, holds **300 frames and four empty directories** (`sfm/`, `train/`, `mesh/`, `export/`) against a row reading `{"2":"done","3":"done","4":"done","5":"done"}` at `current_step: 2`. Two independent defects, one on top of the other. **The store is a singleton and `stepStatuses` is per-project state in it**, cleared only by `hydrateFromProject` — which the three creation paths never call, because a new row has nothing to hydrate from; so the wizard simply kept displaying the last project's statuses. **And `useWebSocket` persisted the whole in-memory dict** on every SUCCESS or ERROR, which promoted that display state to DB truth the first time any step reported. The fix is one at each end: `setCurrentProject` resets the per-project state whenever the id actually changes (statuses, bars, train metrics, export files, `pipelineRunning`), and both writers — the WS handler and step 1's Validate — now author **only the step they are about**, merged onto the row the backend already holds and upserted back. The row is the authority for every other step, and it has to be: an attached pass restores the step it borrowed (§7.4, §7.5) and a reset pops keys, neither of which a status message knows about. The rule that follows: **no client sends a step_status dict it did not derive from the server's own copy.** |
| 2026-08-30 | **The app's version is `YYYY.MM.DD.N` — the commit's date and its number in the history — and there is still no version number typed anywhere.** JB asked to be able to track the running build easily. `/api/version/` already derived everything from the local clone rather than from github.com, which is what makes it describe the code actually running and work with no network; what it could not do is tell two builds of the same day apart, and this project commits several times a day — the date alone repeated and only the 8-hex sha in the top bar distinguished them. `git rev-list --count HEAD` is appended, so the version is **unique and monotone per commit** while staying readable as a date: this commit is `v2026.08.30.8`. No tags, no semver, no `package.json` version (it says `0.0.0` and nothing reads it) — the repository is the single source of the number, and inventing a second one somewhere would be a thing to keep in sync by hand. The count is appended rather than required: a clone git can date but not count still gets a version, and a **shallow** clone counts only what it has, which is why the sha stays beside it in the title bar. `AppTitle` needed no change — it draws whatever `version` says. |
| 2026-08-30 | **The app is exposed on the LAN by binding one port, and what that took was deleting the hardcoded `localhost` §1 already forbade.** JB asked for a staging box on the local network. `start.bat` binding `0.0.0.0` was the easy half and would have been useless on its own: `api/client.ts` carried `baseURL: 'http://localhost:8000/api'` and `useWebSocket.ts` carried `ws://localhost:8000/ws/logs`, so a browser on **another** machine asked *its own* localhost for the API and got nothing — §1's "no hardcoded `localhost` in the frontend API client — it talks to its own origin" written as law and not implemented. Both are now same-origin (`/api`, and `ws(s)://<location.host>/ws/logs`), which the Vite proxy already routed, so **only the UI port has to be reachable**: the proxy runs on the server and its targets moved to `127.0.0.1`, and the backend needs no LAN listener at all for the normal path. Measured end to end from the LAN address rather than from the console: the page **200**, the page under a *hostname* Host header **200** (Vite 5.4 refuses an unknown name with an opaque "Blocked request", hence `allowedHosts`), `/api/version/` through the proxy **200 with the real payload**, and the `/ws/logs` upgrade **101** — the last one being the whole LiveLog and every progress bar. CORS moved from two literal origins to a regex over loopback plus the three private IPv4 ranges plus a dotless hostname, for the person who points a browser straight at :8000: measured, `http://192.168.1.50:5173` is echoed back and `https://attacker.io` gets **400 and no header**. **§1's "no VPS / remote deployment" is untouched** — this is one trusted subnet, the app still has no auth and still runs local binaries and reads server-side folders on request, and `start.bat` says exactly that next to the URL it prints. That banner names the IPv4 of the interface **that has a default gateway**, not the first one `ipconfig` lists: this workstation answers `192.168.56.1` first, a VirtualBox host-only adapter, which is a URL that looks right and works from exactly one machine. `start.bat 127.0.0.1` puts it back to private, and `--strictPort` refuses to slide to 5174, because a staging URL handed to somebody else has to keep working. |

| 2026-09-01 | **A project carries two free-text fields the pipeline never reads, and they are columns rather than settings.** JB asked for a footage author and a description on each project. `settings_json` was the wrong home for both: §4 layer 3 is per-step overrides of a `defaults.json` section, and neither of these has a section to override — the crop volumes and the saved viewpoint are already the two deliberate exceptions and a third would make the exception the rule. So `footage_author` and `description` are columns on `project`, added through the one-`ALTER`-each migration `database.py` already carries for `archived_at` / `archive_path`, and `copy_project` clones them with the rest of the row. **Only the author is on the tile**, on the project name's own line, because a tile that carries a paragraph stops being a list; the description and the full recap live in a collapsible **Project info** section under the step navigator, which is where they are the same two questions whatever step is open. It saves on every change like every other layer-3 panel — debounced, flushed on unmount, on a project switch and on `beforeunload`, with the `SaveState` hint standing in for the Save button that does not exist. `relativeDate` / `absoluteDate` moved out of `ProjectList` into `lib/dates.ts` so the panel and the tile cannot drift on the UTC stamp the backend does not send. |

| 2026-09-01 | **The setup panel gains a Hardware section and the step navigator gains live gauges, and the whole thing costs no dependency.** JB asked for the CPU and GPU information in the global setup panel, and for small coloured CPU / GPU / VRAM gauges at the foot of the left panel. The obvious implementation is `psutil` plus `nvidia-smi`, and both were refused: §2.1 makes every dependency a §10 row, and **`nvidia-smi` can only see the NVIDIA card**, which would contradict §1 — the reason this project exists is that spirula is Vulkan and runs on Intel, AMD and Apple silicon — on the very screen that shows off the hardware. Everything is therefore a documented Windows facility read through `ctypes` and `winreg`: `GetSystemTimes` (0.26 ms), `GlobalMemoryStatusEx` (0.02 ms), `GetLogicalProcessorInformationEx`, the DirectX and display-class registries (1.5 ms), and **PDH performance counters** for GPU utilisation and VRAM. One PDH counter reports **both** GPUs in this machine through one path, and the whole live payload samples in **1.4 ms** in-process, 5.5–23 ms over HTTP — affordable once a second beside a 956-second training run. `nvidia-smi` was used only as the check, and the check passed on memory: PDH read **960.4 MB against its 955 MB** on the same adapter. **Utilisation was deliberately not claimed to match**, because it cannot: `nvidia-smi` samples instantaneously and PDH averages over the interval between collections, so three simultaneous readings answered 51/22/46 % against 25.9/20.4/27.9 %. Four things the measurements forced. **An integrated GPU's VRAM is shared** — the UHD 770 reports a 128 MB dedicated stub against 12 142 MB shared, which is also what `sam devices` calls its "11.9 G" — so the gauge divides by the right pool and names it, or the Intel bar sits full. **The DirectX registry keeps a key per driver install and never removes the old one**: six keys for three live adapters, four of them the same Intel part sharing its name *and* its driver version, so PDH's live counters are the filter and the registry is only the name lookup — caught in a browser, where the first panel drew the same card four times, and not in any payload. **Utilisation is the max over engine types and never the sum**, or a decode plus a draw reads 200 %; the per-engine breakdown is kept because it is what tells §6.1's `-hwaccel` decode apart from a training run. And **one poller serves the whole page**: the backend holds a single PDH query whose reading is the delta since the last poll, so two independent timers would each have averaged over half the interval and disagreed — `useHardware.ts` owns the timer and stops when nothing is mounted or the tab is hidden, which is §12's two-sockets bug (2026-08-28) one hook along. The panel is read-only end to end, so it has no Save, no reset and no save indicator; a value not yet measured draws `—` and never 0 %. Driven headed in a browser: gauges live and moving, both GPUs drawn, **no console error**. |

| 2026-09-01 | **The staging box is fed by file copy, not by `git pull`, and `scripts/sync_staging.sh` is that delivery — written now because `version.py` had been documenting it for two days and it did not exist.** Staging is `\\Ws_tech4art_jbb\travail\DEV\SfM_Splat_App_26`, the PC that runs the reconstructions and serves the app on the LAN. It *is* a clone, so a pull looks like the obvious answer, and it is not: git over the share refuses outright — `detected dubious ownership`, the directory belongs to that machine's SID and not to this one's, so every command would need a `safe.directory` exception — and the pull would then have to reconcile, on a machine nobody is sitting at, **four files staging owns**. Those four are not drift: the API port moved 8000 → **8001** there on 2026-08-31 because `Manager.exe` holds `0.0.0.0:8000` on that workstation and a bind answers **WinError 10013** — access denied, not 10048, so it is not even a port clash to wait out. `main.py`, `vite.config.ts`, `start.bat` and `start.sh` carry that port and are **protected**: the script reports them and copies nothing, because a push that silently put 8000 back would take the server down and say nothing. `config.json` is *permanent* rather than protected — it is tracked, and staging's ffmpeg lives at a different path — so it is never read in either direction. **Line endings are not a change**: this worktree holds LF and that clone was checked out with autocrlf, so a byte comparison called a third of the tree modified for ever; `diff --strip-trailing-cr` took the first push from 32 files to **24**, and the eight it dropped — `version.py`, `AppTitle.tsx`, `frontend/index.html` among them — had no change in them at all. And because the sync never touches `.git`, staging's own HEAD answers for whenever somebody last ran git there; `.version_stamp.json` is what it cannot know, and §12's 2026-08-30 version format is why it carries `commit_count` as well as the date: the first stamp reads **2026.09.01.14**. The guard before any `.py` is written is the staging server's own `/api/pipeline/status` — `--reload` restarts the backend, which kills a running step and orphans the spirula child on the GPU, since `core/proc.py` holds the kill registry in memory. |

| 2026-09-03 | **A run is a record on disk, because "the page closed and it stopped" turned out to be "the page closed and the run went invisible".** JB reported that leaving or closing the page shuts the computation down server-side. Read end to end before anything was written: it does not. The run is an `asyncio.Task` in the uvicorn process, tied to neither the request that started it nor the socket — `/ws/logs` disconnect only drops that client from the broadcast list — and **no `beforeunload` in this frontend aborts anything**, the only caller of `/pipeline/control abort` being the toolbar button. What died was the whole view: `pipelineRunning`, the bar and the 500-line LiveLog are all state in the store, so a reload left the step disabled on `running`, the log blank, the bar at zero and **no Abort button** — which renders on `pipelineRunning`. Indistinguishable from a dead job, and read as one. So `core/jobs.py` writes one `job` row and one `runs/<id>.ndjson` per run, teed from the bus by `JobRecord.wrap(broadcast)` — the steps are untouched, because they already take the bus by injection (§2.4) — `/api/pipeline/status` answers the run to restore, and `useRunRecovery` puts it back on mount and on every project switch. Measured on a real 238-image `sfm auto`: a status read **12 s in** gave `sfm`, step 3, **progress 0.380**, 273 lines and `[match] 2391/4810 pairs matched`; the log route returned **493 of 493** lines; an abort mid-run closed the row **`aborted` at 0.715** with the tool gone from the process table. Four decisions inside it: empty messages are not stored (§7.8's 354-of-419 bar lines), the metric payload **is**, so a restored run gets its chart back and not just its scrollback; the row closes from the same `finally` that demotes a stale step status, plus a startup sweep; and **`project_id` now rides on every run message** with the store dropping anything that is not the open project's — the display half of §13.7, a precondition here rather than a bonus. The log sits **outside `projects/`** because it describes a run and not a reconstruction, so §14.1 gains no row. **It is not a queue** — §1 stands, one job at a time, `_claim_slot` unchanged — and **it does not yet survive the backend**: that is TODO P7.2, and its blocker is that `proc.spawn` hands the child a pipe, which cannot outlive its reader. Found on the way and fixed at the root rather than worked around: the cp1252 console raises `UnicodeEncodeError` on the ✔ this now stores as a bound parameter under the engine's `echo=True`, which is the same defect `pipeline_runner._debug` handles by hand — one `sys.stdout.reconfigure(errors="replace")` at the top of `main.py` settles it for every printer in the process. |

| 2026-09-03 | **A run now survives the backend, and what makes it possible is that the transcript is a file: the step replays its own parser over it and finishes a run another process started.** P7.2, and every premise was measured before any of it was built. A pipe cannot outlive its reader, so `--reload` firing or the Backend window closing killed the task and orphaned the tool on the GPU with everything it had said; `proc.spawn` inside a run context now redirects the child into `runs/<job_id>.tool.log` and starts it **`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`**, and the row remembers the pid, the image name and the process **creation time** — the last of these being what makes a recycled pid impossible to mistake for the child rather than merely unlikely. Four probes settled it: a process holding **no handle** on the file tails what a *foreign* child writes into it (28 reads over 6 s, **75 ms** median lag); a line costs **10 ms** median against the pipe it replaces; a child outlived its parent and a second process read its image name, its creation time and its real **exit code 7** through `GetExitCodeProcess`, while a bogus pid opened as nothing; and FFmpeg's `-progress pipe:1` still arrives live when pipe:1 is a file (18 blocks over a 10 s run), which is what step 2's bar rides. **Re-attaching is a replay, and that is why not one step changed**: the step is re-entered from the top, its reset is *refused* (`proc.adopting()` → `reset_steps` returns nothing, or it would delete the very `sfm/` the live tool is writing), `spawn` attaches to the pid instead of starting anything, and `iter_lines` reads the transcript from byte 0 — the step's own parser sees every line again, at speed, then slides into live tailing and finalises normally. Watched end to end: the backend **hard-killed** (`taskkill /F`, no cleanup) 31 % into a 6000-iteration training run, `spirula.exe` pid 12784 kept training with no backend in existence, the new one logged `re-attached 1 live run(s)`, `/status` answered `train running, adopted 1, progress 0.695`, and the run wrote a complete `train_result.json` — **exit 0, 6000 steps, 984 842 gaussians off the PLY header, psnr 31.84, 105 s**. `iterations_requested` came back **6000** rather than the project's stored value, which is the job row's own `settings_json`: a re-attached run reports what *this* run did. Abort was tested on an adopted run too, because §2.6 is not optional — `taskkill /F /T` works on a pid, so the tool went from the process table and the row closed `aborted`. Five things are deliberately not adoptable and the reasons differ: `curate`, `crop` and `splat_export` are numpy in this process and their state died with it (a live `splat-transform` is **killed** rather than left working for a result nobody will collect); `extract` starts up to three commands on the image-set branch, so a replay would re-run the first while the live one writes — the row counts `spawns` and adoption refuses more than one; and **a child that already finished**, which is the case the first run turned up rather than one that was predicted — a `spirula sfm auto` completed with **no backend at all** (31.43 s, `RESULT: OK -- 100% of the images registered`, `sparse/0` on disk) and nothing was there to collect it. Windows keeps no exit code once the last handle closes and this app does not invent one (§2.2), so that row is not finalised: it says exactly that, names the transcript, and the step must be re-run. Closing it means reading each tool's own completion marker, which the steps already parse — TODO P7.3, written down rather than guessed at. |

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
5. **The MoGe checkpoint licences.** **Half-settled 2026-08-30.** Audited through the
   HuggingFace API: the three `onnx-community/metric3d-vit-*` cards declare **CC0-1.0**,
   so that family is closed. MoGe-2 is not — `moge-2-vitl-normal-onnx` declares `mit`
   while the `vits` and `vitb` mirrors **declare no licence at all**, and `vitb` is the
   419.4 MB file spirula fetches by default. Upstream MoGe-2 is MIT, so the terms are
   inferred from upstream rather than read off the artefact, which is weaker than an
   audit. §7.4b's panel flags that licence amber rather than ticking it, and §10 carries
   the finding. What is still owed is a licence *on the mirrors themselves*, or a
   decision to fetch from a repository that has one.
6. ~~**Does step 5's mesh get its own viewer mode, or a thumbnail?**~~ **Settled
   2026-08-28: the third renderer.** `GLTFLoader` ships inside `three`, so it costs no
   dependency and no §10 row; `MeshCanvas` is §7.9. The mesh has no decimated preview
   either — the file the tool wrote is what loads.
7. **The WS bus carries no project id, so the store applies every message to whatever
   project is open.** Seen in P1.7: step 4 of the reference project displayed a second
   project's bar at 56 % with a live ETA. `/api/pipeline/start` refuses a second run
   only *for the same project*, so nothing enforces §1's "one running job at a time"
   across projects. **Half-settled 2026-09-03**: the first way out — `project_id` on
   every message, filtered in the store — shipped with §15.4, because a reconnecting
   client has no other way to tell its own run from another project's. So the *display*
   half is closed. Whether to also **refuse a start while any project is running** is
   still **JB's call**.
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
| 4 Train | `train/` — the run, `train/crop/` (the volume cut, §7.6b) **and** `train/export/` (the deliverable copies, §7.6c, sidecars included). All of it is derived from a splat this reset has just deleted. The volumes, the saved viewpoint (§7.6d) and the export plan live in `settings_json` and survive |  |
| 5 Mesh | `mesh/`, `export/` | |

Step 1 is deliberately absent: it owns `input/`. Step 5 owns `export/` as well as `mesh/`
— it meshes, then fills the drawer — so one reset takes both. `preview/`
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
| 4 crop | `step_crop`, one chunk of 262 144 gaussians per executor hop, empty message | gaussians, over two passes: 0.02→0.50 masking, 0.50→0.99 copying |
| 4 export | `step_splat_export`, one chunk of 262 144 gaussians per executor hop, empty message; the three compressed formats add `splat-transform`'s lines and its **CR-redrawn k-means bar**, dropped from the bus (§7.6c) | gaussians over two passes — 0.02→0.99 native, or 0.02→0.70 then the conversion's 0.72→0.99 |
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

### 15.4 The run is a record, so it outlives the page

Everything above is a *channel*. This is where the reading of it is kept, because
a channel nobody is listening to is a bar that does not exist.

**Closing the page never stopped a run, and that had to be read before it could
be fixed.** A run is an `asyncio.Task` created in the uvicorn process
(`api/routes/pipeline.py`) and parked in `_running_tasks`; it is tied to neither
the HTTP request that started it nor the socket, `/ws/logs` disconnect only
drops that client from the broadcast list, and **nothing in the frontend aborts
anything on `beforeunload`** — the one caller of `/pipeline/control abort` is the
toolbar button. The tool keeps working.

**What died was the view, completely, which is indistinguishable from a dead
job.** `pipelineRunning` is state in the Zustand store, and so are the bar and
the 500-line LiveLog. Come back mid-training and the step is disabled because
the row says `running`, the log is empty, the bar is at zero, and there is **no
Abort button**, because it renders on `pipelineRunning`. A dead end reachable by
reloading a page.

So `core/jobs.py` writes the run down. One `job` row per run — kind, the wizard
step it reports into, state, progress, last message, error, started/finished —
and one `runs/<job_id>.ndjson` beside it, teed from the bus itself:
`JobRecord.wrap(broadcast)` returns a broadcaster with the same signature, so
nothing under `core/steps/` changes or learns what a job is (§2.4's injection,
used as intended). `/api/pipeline/status` then answers `job`, and
`useRunRecovery` restores the step status, the bar, the log tail and
`pipelineRunning` on mount and on every project switch.

Measured 2026-09-03 on `zz_abort_test`, a real `sfm auto` over 238 frames: a
status read **12 s in** answered `running: true`, `sfm`, step 3, **progress
0.380**, 273 lines, last message `[match] 2391/4810 pairs matched`; the log
route returned **493 of 493** lines, 392 of them carrying a progress value; and
an abort mid-run closed the row **`aborted` at 0.715** with the tool gone from
the process table. A re-analysis on the same project wrote **3 lines and
finished `done` at 1.0** in 80 ms.

Four things it does that a naive "write a log file" would not:

- **Empty messages are dropped, not stored.** `step_mesh` rides the bar on 354
  of its 419 lines with an empty message precisely so the LiveLog never sees
  them (§7.8); the file holds what the panel holds.
- **The metric payload is teed with the text**, so a restored run gets its
  *chart* back and not just its scrollback. The trainer puts both on every bar
  line (§7.7) and a 30 000-iteration run is 300 of them — the longest thing in
  this app is also the one most likely to be reloaded through.
- **The row closes however the task left.** `finish()` is called from the same
  `finally` that demotes a stale step status, and a job left on `running` is
  that dead end one layer down; `close_orphaned_jobs()` sweeps at startup what a
  killed backend left behind, so a job reported by `/status` is live.
- **`project_id` rides on every message a run broadcasts**, and the store drops
  one that is not the open project's. That is the display half of §13.7, and it
  is a *precondition* here rather than a bonus: a client that has just
  reconnected has no other way to tell its own run from another project's.

**The log lives in `runs/`, outside `projects/`**, because it describes a run
rather than a reconstruction: §14.1 gains no row, and no reset, copy or archive
has to reason about it. It is pruned to the last 200 runs, and `prune()` also
sweeps a log file no row claims — Windows refuses to unlink a file another
handle still holds, so a delete landing while its writer is live would otherwise
leave the file behind with its row gone.

**None of this is a queue.** §1's "no job queue" is untouched: one user, one job
at a time, still enforced by `_claim_slot`. It is the same single job, made
durable enough to be found again.

**And it survives the backend, because the transcript is a file and the child
is detached** — P7.2, and the mechanism is one measurement deep rather than
clever. A pipe cannot outlive its reader: `--reload` firing or the Backend
window closing took the task with it and orphaned the tool on the GPU with
everything it had said. So `proc.spawn`, inside a run context, redirects the
child's output into `runs/<job_id>.tool.log` and starts it
`DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, and the row remembers the pid,
the image name and the process **creation time**.

Measured first, built second (2026-09-03):

| Question | Answer |
|---|---|
| Can a process holding no handle tail a file a *foreign* child writes? | Yes — 28 reads over 6 s, **75 ms median lag** at a 200 ms poll |
| What does a line cost against the pipe it replaces? | median **10 ms**, p95 20 ms, bounded by the reader's own poll (50 ms live) |
| Does the child outlive its parent? | Yes: the parent exited, the child kept writing |
| Can a later process identify it from a pid alone? | Yes — image name **and** creation time both matched; a bogus pid opens as nothing |
| Can it read the exit code of a process it never spawned? | Yes — **7**, through `GetExitCodeProcess` |
| Does FFmpeg's `-progress pipe:1` still arrive live when pipe:1 is a file? | Yes — 18 blocks over a 10 s run, `out_time_us` climbing, exit 0 |

**Re-attaching is a replay of the step over its own transcript, and that is why
no step changed.** At startup `jobs.adoption_candidates()` splits the rows left
`running` into what is still alive and what is not; `pipeline.adopt_orphaned_runs()`
re-enters the runner for each survivor with `adopt=<row>`. The step then runs
from the top: it re-reads its inputs, **its reset is refused**
(`proc.adopting()` → `reset_steps` returns nothing, or it would delete the very
`sfm/` the live tool is writing), `spawn` attaches to the pid instead of
starting anything, and `iter_lines` reads the transcript **from byte 0** — so
the step's own parser sees every line it already saw, at speed, and slides into
live tailing when it catches up. It then judges the exit code and writes its
result file exactly as it would have. The derived ndjson is dropped and rebuilt
by the replay rather than appended to, because it is derived.

Watched end to end on `zz_abort_test`, a 6000-iteration training run: the
backend was **hard-killed** (`taskkill /F`, no cleanup at all) at 31 %,
`spirula.exe` pid 12784 kept training with **no backend in existence**, the new
backend logged `re-attached 1 live run(s)`, `/status` answered `train running,
adopted 1, pid 12784, progress 0.695`, and the run finished and wrote a
complete `train_result.json` — **exit 0, 6000 steps, 984 842 gaussians read off
the PLY header, psnr 31.84, 105 s**. Started by one backend, finalised by
another. `iterations_requested` came back **6000** rather than the project's
stored value, which is the job's own `settings_json` doing its job: a
re-attached run reports what *this* run did. Abort was then tested on an adopted
run, because §2.6 is not optional: `taskkill /F /T` works on a pid and not on a
handle we hold, so the tool went from the process table and the row closed
`aborted`.

Five things are deliberately **not** adoptable, and the reasons differ:

- **`curate`, `crop`, `splat_export`** — their work is numpy in this process,
  and it died with it. A live child of one of these (`splat-transform`) is
  **killed** rather than left, because it is working for a result nobody will
  collect.
- **`extract`** — the image-set branch of step 2 starts up to three commands
  (`step_conform`), and a replay would re-run the first while the live one is
  still writing. It is also the cheapest step to simply run again: 238 frames in
  80 s. The row records `spawns` and adoption refuses anything above one.
- **A child that already finished.** This is the case the first P7.2 run turned
  up rather than one that was predicted: a `spirula sfm auto` completed with no
  backend at all — 31.43 s, `RESULT: OK -- 100% of the images registered`,
  `sparse/0` complete on disk — and nothing was there to collect it. Windows
  keeps no exit code once the last handle closes, and this app does not invent
  one (§2.2), so the run is not finalised: the row says exactly that, names the
  transcript, and the step has to be re-run to regenerate its report. What could
  close this is each tool's own completion marker — the trainer's
  `Training complete.`, `sfm`'s `RESULT:`, `mesh`'s `done:` — which the steps
  already parse; it is written down in TODO P7.3 rather than guessed at here.

`reconcile_orphaned_steps` therefore takes a `skip` set: a step belonging to a
run that was just re-attached is not stale, it is running.


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
