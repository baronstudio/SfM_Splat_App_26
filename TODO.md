# TODO — SfM Splat Pipeline App

Prioritised worklist. [CLAUDE.md](CLAUDE.md) is the spec; this is what comes next.
Phases are ordered by what unblocks what, not by what is interesting.

Status: **P0 done** — the tool is verified, the doctrine is written, the modules are
copied. Nothing in the pipeline runs yet.

---

## P0 — Foundations ✅

- [x] Read `3DGS_App_26/CLAUDE.md` end to end, plus `step_lfs.py` and `step_extract.py`
      as the two shapes every step here will take.
- [x] Download and unzip `spirula-2026.8.23-windows-vulkan-x86_64.zip` into
      `tools/spirula/`. One file, 119 462 912 bytes.
- [x] Capture the raw `--help` of every command into `docs/spirula/` — 22 files,
      including one `--help-all` per training preset.
- [x] **Settle the open question that decides the disk layout**: `--image-dir` accepts an
      absolute path (CLAUDE.md §5.2). Three runs, one positive and two negative controls.
- [x] Measure the world frame and the up axis (CLAUDE.md §7.3): identity at 90.1 %, +Z up
      by two independent readings agreeing to cos 1.000.
- [x] Verify the five traps against the installed build: `--lang en`, `--disable-viewer`,
      the `0.0.0.0` bind, `apply_loss_for_mask`, and mask resizing.
- [x] Prove `core/ply.py` reads spirula's `splat.ply` unmodified.
- [x] `git init`, remote, `.gitignore` excluding artefacts from commit one.
- [x] Copy the modules CLAUDE.md inherits; drop every RealityScan/LichtFeld one.
- [x] Write `CLAUDE.md` and this file.

---

## P1 — One project trains one splat end to end from a video (steps 1-4)

The phase that makes this an app rather than a repo. Nothing below it matters until a
video goes in one end and a `splat.ply` comes out the other.

### P1.1 Make the copied code run again

- [ ] **Rewire the backend to import.** The copy left dangling references to the dropped
      modules: `api/routes/files.py` (`colmap_dataset`, `rc_alpha`),
      `api/routes/projects.py` (`rc_region` — the whole region endpoint group goes),
      `api/routes/pipeline.py` (the masks route repoints at `spirula sam`),
      `core/cameras.py` (reads RealityScan's `transforms.json`; must read
      `sfm/sparse/0/images.bin`), `core/pipeline_runner.py` (`_STEP_NAMES`,
      `_STEP_RUNNERS`), `core/defaults.py` (done — the five spirula blocks are in).
- [ ] **Frontend builds with steps 3-6 as empty shells.** `Step3_RC.tsx` and
      `Step4_LFS.tsx` are deleted; `Step3_Sfm`, `Step4_Train`, `Step5_Mesh`,
      `Step6_Scene` are placeholders that render their step name and nothing else.
      `RCSettings.tsx` / `LFSSettings.tsx` are gone; the settings drawer loses those
      sections until P1.4 and P1.6 add theirs.
- [ ] `core/config.py`: `rc_exe_path`, `lfs_exe_path` and `supersplat_url` out;
      `spirula_exe_path` and `spirula_model_cache` in. Keep `ffmpeg_path` and
      `ffmpeg_hwaccel`.
- [ ] `setup.py` fetches `spirula.exe` from the GitHub release rather than cloning
      LichtFeld Studio, and auto-detects FFmpeg on `PATH` as before.
- [ ] Delete `docs/rs/` references and the RealityScan sections from `README.md`.

### P1.2 Steps 1 and 2 work unchanged

- [ ] Create a project, import a video, extract and curate. This is inherited code and
      should need nothing but the config rename — **verify that rather than assume it**,
      including the image-set branch (§6.7) and the alpha → `masks/` extraction.
- [ ] Confirm the `-hwaccel` fallback warning still fires (`config.json` here should be
      `cuda` on this workstation, `none` on a fresh clone).

### P1.3 `core/steps/spirula.py` — the shared command builder

One module, before any step uses it, because four steps invoke the same binary and
CLAUDE.md §7.0 has four rules that must not be re-implemented four times.

- [ ] Resolve and validate `spirula_exe_path`; fail with the path it looked for (§2.2).
- [ ] **Emit `--lang en` from the builder, not from the call sites.** The one place it
      can be forgotten is six places.
- [ ] Log `spirula --version` at the top of every run and return it in the step result
      (§2.7).
- [ ] Bool flags render as `0`/`1`; `sfm auto`'s bare `--no-x` switches are the
      exception and get their own helper.

### P1.4 Step 3 — `step_sfm.py`

- [ ] `spirula sfm auto <frames> -o <project>/sfm`, with `SfmDefaults` on the command
      line and `reset_steps(project, [3])` **after** the exe and `frames/` are located
      and before the first byte is written (§14.1).
- [ ] Parse the tagged stdout for the bar: `[extract] N/total`, then
      `[map] images in the model: N` (§7.2). Remember the extraction order is not the
      filename order.
- [ ] **Handle exit 3 as a warning, never a failure** (§7.1). Persist the exit code, the
      registered/total pair, the reprojection error and the `sparse/N` count to
      `sfm/sfm_result.json`.
- [ ] Warn when more than one `sparse/N` exists — a fragmented capture, and the thing to
      raise `--overlap` or switch `--data-type video` for.
- [ ] `SfmSettings.tsx`: quality, data type, camera model (the 360/fisheye entries are
      the point), pairs, max features, use masks.

### P1.5 Step 3's viewer

- [ ] Read `sfm/sparse/0/points3D.bin` and `images.bin` — **binary, not text** (§7.1).
      The predecessor's `cameras.py` parsed RealityScan's `transforms.json` and none of
      it survives.
- [ ] `core/ply.py`'s `.pc3d` preview path is unchanged; the source is a COLMAP binary
      model rather than a PLY, so the conversion is new and the preview format is not.
- [ ] **One `Rx-90` on the scene root**, for everything (§7.3). Delete `viewer/frame.ts`'s
      per-object logic rather than porting it; keep the "Flip up" toggle.

### P1.6 Step 4 — `step_train.py`

- [ ] `spirula train <preset> --data <project>/sfm --image-dir <project>/frames
      --output-dir-prefix <project>/train --output-dir-name run --disable-viewer 1`.
- [ ] **`--output-dir-name` and `--disable-viewer 1` are not optional** (§12,
      2026-08-27). A missing viewer flag hangs the step forever.
- [ ] Parse `step N/M (P%) splats S [elapsed E | ETA R] rgb_loss=… ssim=… psnr=…`
      (§7.7). Map `N/M` onto 5–95 %, cap at 0.99 while running, and accept bare-integer
      metric values (`psnr=20`, `ssim=0` both occur).
- [ ] Send `--apply-loss-for-mask 1` whenever `masks/` is non-empty, and do not offer the
      other position (§12, 2026-08-27).
- [ ] `reset_steps(project, [4])` after the exe and the dataset are located.
- [ ] Find the splat under `train/run/step-*.ckpt/splat.ply` — one checkpoint survives a
      run, but glob and take the highest step rather than assuming it.
- [ ] `TrainSettings.tsx`: **the panel's defaults follow the selected preset**, read from
      `docs/spirula/train-help-all-<preset>.txt`'s values now baked into
      `TrainDefaults` — not a frozen copy of `3dgs`'s (§12, 2026-08-27).
- [ ] The step-4 viewer shows the splat. 247 MB for a small project, so the decimated
      preview of §7.9 is load-bearing on day one, not later.

### P1.7 The end-to-end run

- [ ] One video → `frames/` → `sfm/sparse/0` → `train/run/step-*.ckpt/splat.ply`,
      visible in the viewer, with a moving bar at every step.
- [ ] Abort works at each of them, killing the process **tree** (§2.6).
- [ ] Record the wall-clock of each step on a real project in the decisions log.

---

## P2 — Mesh and scene (steps 5-6)

- [ ] `step_mesh.py`: `spirula mesh <ckpt> --data <project>/sfm --output
      <project>/mesh/mesh`. **`--output` is mandatory** — its default writes inside the
      `.ckpt` directory the next training deletes (§12, 2026-08-27).
- [ ] **Refuse PLY+texture and OBJ+vertex-colour in the UI**, before the run. The tool
      exits 1 having written nothing at all, not even the formats it could have made.
- [ ] Bar from `[meshing] color: cameras rendered: N/total`; name the other phases rather
      than faking a percentage for them (§15.3).
- [ ] Decide the mesh viewer question (§13.4) — third renderer or thumbnail. **JB's
      call.**
- [ ] `step_export.py` and `step_scene.py`: inherited, and steps 5/6 share `export/`.

---

## P3 — Masking and geometry supervision

Both attach to existing steps rather than adding screens, and both are separately
re-runnable: **the expensive phase must never be redone to change a threshold.**

- [ ] `step_sam.py` + `POST /api/pipeline/masks`, modelled line for line on `/analyze`.
- [ ] `sam mask` first — no model, no download, no licence question, and it is the 360 /
      fisheye story (§7.4). Ship this before `sam track`.
- [ ] `sam track` second, with the SAM 2.1 / SAM 3 licences shown and accepted
      **separately** before any fetch (§10).
- [ ] `step_geometry.py` + `POST /api/pipeline/geometry`, writing `sfm/depths/` and
      `sfm/normals/`.
- [ ] The checkpoint download is a `curl` child with a CR-redrawn bar — the one channel
      in this tool family with the §15.1 defect. Report it, and make sure abort kills it.
- [ ] Step 3's reset takes `sfm/depths` and `sfm/normals` with it. **The step must say so
      before it deletes them** (§14.1).

---

## P4 — Open questions to settle by measurement

In the order they block something. See CLAUDE.md §13.

- [ ] **Does `spirula geometry` resolve images outside the dataset folder?** The one
      thing that could force a junction into the §5 layout. Finish the 419 MB checkpoint
      download and re-run.
- [ ] **Are `--mask-dir` / `--depth-dir` / `--normal-dir` absolute-path-capable like
      `--image-dir`?** Assumed by symmetry, not measured. Cheap.
- [ ] **Audit the MoGe / Metric3D checkpoint licences** and give them real rows in §10.
- [ ] Measure a 360 / fisheye capture end to end — `--camera-model equirectangular`,
      `sam mask` on the lens border, `geometry --split auto`, `train 360-camera`. Every
      piece is claimed to work and none of it has been run on a real 360 source.
- [ ] Time `--quality high` against `medium` on a real project. `medium` gave 251/251 at
      0.50 px in 34.6 s; `high` is the build's default and its cost here is unknown.

---

## P5 — Polish, and things the predecessor learned late

- [ ] Project lifecycle: copy / reset / archive / delete, modal (§14.2).
- [ ] The three settings layers actually writing layer 3 — `useProjectSettings` PATCHing
      a diff, no Save button (§4). The predecessor shipped this on paper for four days
      before noticing every `settings_json` was `{}`.
- [ ] `/api/version` + the `.version_stamp.json` route, if this app is ever synced to a
      second machine by file copy.
- [ ] `ProgressBar`'s 10-second indeterminate fallback, with `sfm`, `train`, `mesh`,
      `masks` and `geometry` all present in its step-name map. The predecessor's was
      missing `curate` and that step's bar never turned green.
- [ ] Report generation (`report/report.json` + `report.md`).

---

## Not doing

- `spirula sam extract` as step 2. Ours carries a measured 5× `-hwaccel`, the `scdet` cut
  capture riding along with the extraction, and the whole curation model (CLAUDE.md §1).
- `spirula gui`. This app *is* the front end.
- The `--progress-dir` binary channel, until the stdout bar works (§13.5).
- Modelling `sfm`'s five individual stages. Worth remembering the day a reconstruction
  fails and nobody can tell which stage lost it (§13.6), not worth building now.
