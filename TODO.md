# TODO — SfM Splat Pipeline App

Prioritised worklist. [CLAUDE.md](CLAUDE.md) is the spec; this is what comes next.
Phases are ordered by what unblocks what, not by what is interesting.

Status: **P1 done, P2 done bar step 6 and one browser pass, P3 done bar `sam track`.** A video goes in one end
and a `splat.ply` and a textured `mesh.glb` come out the other, from the UI, at real
length, with the bar moving and abort working at every step. The P1.7 run: 79.5 s of
4K/100 fps 10-bit HEVC → 238 frames in **80.4 s** → 238/238 registered at 0.341 px in
**45.3 s** → 30 000 iterations in **956 s**, psnr 38.66, a 716 831-gaussian `splat.ply`.
Step 5 on the throwaway project's 98 025-gaussian splat: **18.15 s**, 78 670 vertices,
84 166 faces, a 4096 px texture at 26.8 % coverage, `mesh.glb` 11.6 MB, exit 0. P3 then
shipped both re-runnable passes and settled three of P4's open questions on the way —
`geometry` needs a junction, a mask pairs by basename, `--mask-dir` takes an absolute
path. Next: **step 6**, which is still the one step never run since it was rewired.

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
- [x] `core/steps/spirula.py`, `core/colmap.py` — the latter validated against a real
      300-image `sparse/0` written by step 3, so P1.5's parser is already proven.
- [x] **Frontend builds with steps 3-6 as empty shells.** `Step3_RC.tsx` and
      `Step4_LFS.tsx` are deleted; `Step4_Train`, `Step5_Mesh`, `Step6_Scene` are
      placeholders that render their step name and nothing else. `RCSettings.tsx` /
      `LFSSettings.tsx` are gone. `StepNav`'s labels were still "RS Alignment" and
      "LFS Training" until P1.5 — the wizard named two tools this app does not have.
- [ ] **`types/index.ts` still carries the dead RC/LFS type block** — `RCDefaults`,
      `RegionDefaults`, `RegionState`, `MaskGenerationDefaults`, `MaskReport`,
      `AlignmentReport`, `LFSDefaults`. Referenced by nothing (checked), so deleting
      them is a `tsc` away; left standing only to keep P1.5's diff about the viewer.
- [ ] `core/config.py`: `rc_exe_path`, `lfs_exe_path` and `supersplat_url` out;
      `spirula_exe_path` and `spirula_model_cache` in. Keep `ffmpeg_path` and
      `ffmpeg_hwaccel`.
- [ ] `setup.py` fetches `spirula.exe` from the GitHub release rather than cloning
      LichtFeld Studio, and auto-detects FFmpeg on `PATH` as before.
- [ ] Delete `docs/rs/` references and the RealityScan sections from `README.md`.
- [ ] **The in-app HELP panel is still the predecessor's, and it is on screen during
      every run.** Its rail reads `3 · RC` / `4 · LFS` / `5 · Export`, step 3 is titled
      "Reality Capture — Alignment & Export" and tells the user to set the RealityCapture
      path in Settings and read `rc_output/`, step 4 is "LFS — Gaussian Splat Training".
      Seen in the P1.7 browser pass, beside a step 3 panel correctly reporting a spirula
      run. Same commit should take the app's own name: `version.APP_NAME`,
      `AppTitle.tsx`'s fallback, `public/help/`, `requirements.txt` and
      `step_scene.py`'s export README all still say "3DGS Pipeline App".

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

### P1.4 Step 3 — `step_sfm.py` ✅

- [x] `spirula sfm auto <frames> -o <project>/sfm`, with `SfmDefaults` on the command
      line and `reset_steps(project, [3])` **after** the exe and `frames/` are located
      and before the first byte is written (§14.1).
- [x] **Only the knobs the user moved go on the command line.** Naming a flag overrides
      the preset that would otherwise set it, so a value equal to the build's own default
      is left off — otherwise `--max-features 8192` silently undoes `--quality medium`
      (§12, 2026-08-27). `--quality` and `--data-type` are always sent; they are the
      presets.
- [x] Parse the tagged stdout for the bar: `[extract] N/total`, then
      `[map] images in the model: N` (§7.2). Remember the extraction order is not the
      filename order.
- [x] Drop the extractor's three-lines-per-image narration before it reaches the bus —
      900 lines of 1682 on the reference run, against a 500-line LiveLog.
- [x] **Handle exit 3 as a warning, never a failure** (§7.1). Persist the exit code, the
      registered/total pair, the reprojection error and the `sparse/N` count to
      `sfm/sfm_result.json`.
- [x] Warn when more than one `sparse/N` exists — a fragmented capture, and the thing to
      raise `--overlap` or switch `--data-type video` for.
- [x] `SfmSettings.tsx`: quality, data type, camera model (the 360/fisheye entries are
      the point), pairs, max features, use masks — plus `Step3_Sfm.tsx` with the report
      panel that outlives the scrollback, and the `sfm` section of `AppSetupPanel`.
- [x] `project_ops` moved to §14.1's artefact table — the reset `step_sfm` depends on
      was still pointing at `rc_output/`.
- [ ] **Verified on this workstation, not on a 360 source.** `--camera-model
      equirectangular` and the fisheye entries are offered and untested (P4).

### P1.5 Step 3's viewer ✅

- [x] Read `sfm/sparse/0/points3D.bin` and `images.bin` — **binary, not text** (§7.1).
      The predecessor's `cameras.py` parsed RealityScan's `transforms.json` and none of
      it survives.
- [x] `core/ply.py`'s `.pc3d` preview path is unchanged; the source is a COLMAP binary
      model rather than a PLY, so the conversion is new and the preview format is not.
      `ply.write_cloud` takes any `(x, y, z, r, g, b)` stream; `colmap.iter_points` is
      the one that feeds it. 61 859 points → 989 760 B, exact.
- [x] **One `Rx-90` on the scene root**, for everything (§7.3). `viewer/frame.ts`'s
      per-object logic deleted rather than ported; the "Flip up" toggle stays, and it is
      a question about the capture now rather than a convention repair.
- [x] **The frustum opens down `+Z`** — COLMAP is OpenCV-framed and the inherited rig was
      built for RealityScan's OpenGL matrix. Settled by cheirality, 8.9× (§12).
- [x] `colmap.read_cameras` + `frustum_shape`: the overlay draws the *solved* lens (94.0°
      at 4:3 here), and answers no fov for a fisheye or equirectangular group rather than
      guessing one.
- [x] Preview sources are `sfm` and `train`, found not named; `/preview` answers 400 on
      anything else. Step 5's mesh stays out until §13.6 is decided.
- [x] **Looked at in a browser** (P1.7). The sparse cloud draws, and the 300 frustums
      draw as one arc opening onto the wall they were aimed at — §7.9's cheirality
      result, seen rather than counted. No console error on the page.

### P1.6 Step 4 — `step_train.py` ✅

- [x] `spirula train <preset> --data <project>/sfm --image-dir <project>/frames
      --output-dir-prefix <project>/train --output-dir-name run --disable-viewer 1`.
- [x] **`--output-dir-name` and `--disable-viewer 1` are not optional** (§12,
      2026-08-27). Both are emitted by the builder, not by the settings.
- [x] Parse `step N/M (P%) splats S [elapsed E | ETA R] rgb_loss=… ssim=… psnr=…`
      (§7.7). Mapped onto 5–95 % — measured 0.053 → 0.950 across a 300-step run — and
      the bare-integer warning was real: step 1 printed `ssim=0  psnr=0` and the last
      line `psnr=24`, so a `\d+\.\d+` pattern would have lost the first and last point
      of every chart.
- [x] **`null` means "the preset decides", in `TrainDefaults` and in the panel.** The
      preset is the baseline and it moves, so a knob holding a concrete number cannot be
      told from one the user never touched. Caught by a builder test: `meshing` with
      `3dgs`'s stored values emitted `--sh-degree 3 --primitive 3dgs --background-mode
      black` and undid the preset it had just selected (§12, 2026-08-27).
- [x] Send `--apply-loss-for-mask 1` whenever `masks/` is non-empty, and do not offer the
      other position. `--mask-dir` takes the **absolute** path of `<project>/masks`,
      because unlike `sfm auto` the trainer resolves it relative to `--data` — the one
      unmeasured path in this step, and the log says so (P4).
- [x] `reset_steps(project, [4])` after the exe and the dataset are located.
- [x] Find the splat under `train/run/step-*.ckpt/splat.ply` — globbed and ranked by
      step, not assumed.
- [x] `TrainSettings.tsx`, with the per-preset default table mirroring
      `step_train._PRESET_DEFAULTS`; `Step4_Train.tsx` with the input strip, the
      recharts loss/ssim/psnr chart and the report panel that outlives the scrollback;
      `GET /api/files/{id}/train`; the `train` section of `AppSetupPanel` (the same
      component, so the preset table exists twice, not four times).
- [x] The store's `metric` messages now reach the LiveLog. They carried the *whole* of a
      training run's output and were being dropped on the floor (§12, 2026-08-27).
- [x] The step-4 viewer shows the splat: `preview.build(..., 'train')` on the real
      `splat.ply` gives `kind: splat`, 61 859 × 32 B = 1 979 488 B exactly.
- [x] **Looked at in a browser** (P1.7). The recharts loss/ssim/psnr chart draws live
      through a run, and the 716 831-gaussian splat renders sorted and alpha-blended —
      a basement window, its stone reveal and the grating beside it, recognisably the
      thing that was filmed. No console error on the page.

### P1.7 The end-to-end run ✅

Run on `zz_abort_test`, a throwaway project sharing the reference rush by hard link so
the reference project was never a casualty of an abort. **It is still on disk, 1.5 GB —
delete it from the Projects list when its evidence has been read.**

- [x] One video → `frames/` → `sfm/sparse/0` → `train/run/step-*.ckpt/splat.ply`,
      visible in the viewer, with a moving bar at every step. Wall clocks in the
      decisions log (§12, 2026-08-28).
- [x] Abort works at each of them, killing the process **tree** (§2.6). Step 2 left
      `ffmpeg` gone and the frame count frozen; step 3 left `sfm/` holding `features/`
      alone and **no `sfm_result.json`**, which is what makes `colmap.find_model` report
      nothing to preview rather than hand the parser a stub; step 4 left
      `train/run/config.json` and no checkpoint. All three reported `aborted`, not
      `error`.
- [x] Record the wall-clock of each step on a real project in the decisions log.
- [x] **Both viewers looked at in a browser** — the standing caveat on P1.5 and P1.6.
      The sparse cloud draws with its 300 frustums opening onto the wall they were
      aimed at (§7.9's cheirality, visible rather than counted); the 716 831-gaussian
      splat renders sorted and alpha-blended. Screenshots driven headed through
      Playwright, on the workstation's own GPU.

**Three defects the run turned up.** Two are fixed here; the third is JB's call.

- [x] **The LiveLog showed every line twice.** Not a double broadcast — counted on a
      lone socket, the backend sent each `step N/M` line exactly once — but two live
      sockets in one page. `useWebSocket.connect` tested only for `OPEN`, and
      StrictMode's mount→cleanup→mount cycle closes the first socket while it is still
      `CONNECTING`, so the second mount saw a non-OPEN socket and opened another beside
      it. Fixed by treating `CONNECTING` as "already have one" and by ignoring an
      `onclose` from a socket that is no longer the current one. Verified: one live
      `/ws/logs` socket, where there were two.
- [x] **The splat count on the step-4 card was the cap, not the file.** The trainer's
      last bar line is the *live* count and the final prune runs after it: two
      30 000-iteration runs printed `splats 1000000` and wrote **715 890** and
      **716 831** — ~28 % fewer. `train_result.json` now carries `splat_count` read off
      the PLY header, the card shows it, and the cap warning stays keyed on the live
      number because reaching the cap *during* training is what it is about. The two
      existing `train_result.json` files predate the field and still show the cap until
      their project is re-trained.
- [ ] **The bus has no project id, and the store applies every message to whatever
      project is open.** Step 4 of the *reference* project displayed the throwaway
      project's bar at 56 % with a live ETA. `/api/pipeline/start` refuses a second run
      only *for the same project*, so two projects can run at once even though §1 says
      one job at a time. Two ways out — carry `project_id` on every message and filter
      in the store, or refuse a start while any project is running — and it is **JB's
      call** which.

---

## P2 — Mesh and scene (steps 5-6)

### P2.1 Step 5 — `step_mesh.py` ✅

- [x] `spirula mesh <ckpt> --data <project>/sfm --output <project>/mesh/mesh`, with
      `reset_steps(project, [5])` **after** the exe and the checkpoint are located.
      **`--output` is mandatory** — its default writes inside the `.ckpt` directory the
      next training deletes (§12, 2026-08-27). The checkpoint passed is the `splat.ply`
      `find_splat` located, not the run directory.
- [x] **Refuse PLY+texture and OBJ+vertex-colour before the run**, in the UI *and* in the
      step. The tool exits 1 having written nothing at all, not even the formats it could
      have made — verified that `run_mesh` raises before it touches `mesh/`.
- [x] **The phase list captured whole** rather than inferred: `docs/spirula/mesh-run.txt`,
      419 lines. It corrected §7.8 in three ways — `Delaunay` and `UV` were missing,
      there are **three** camera loops and not one, and the phases are **not monotone**
      (§12, 2026-08-28).
- [x] Drop the camera counter from the bus — 360 of 419 lines, against a 500-line
      LiveLog — and ride the bar on it with an empty message. 419 in, 65 out, replayed
      against the capture. Bar watched over a real run: monotone, 0.00 → 0.99, 423 points.
- [x] `mesh/mesh_result.json`: vertices, faces, components, the three edge tallies,
      texture size, texel coverage, elapsed, the files on disk and what the tool said it
      wrote. `GET /api/files/{id}/mesh` serves it with the checkpoint and cameras the run
      will read.
- [x] `MeshSettings.tsx` (shared with the setup panel's new Mesh section, so `meshRefusal`
      exists once), `Step5_Mesh.tsx` with the input strip, the report panel and the
      export listing.
- [ ] **Not looked at in a browser.** Every part of it is verified server-side — the run,
      the parse, the bar, the endpoints, the glTF header, the `model/gltf-binary` on
      `/static` — and no human has watched `MeshCanvas` draw. Same standing caveat P1.5
      and P1.6 carried until P1.7 closed it.

### P2.2 The mesh viewer ✅

- [x] **JB's call, 2026-08-28: the third renderer.** `GLTFLoader` ships inside `three`,
      so no dependency and no §10 row. §13.6 closes.
- [x] `preview.SOURCES` gains `mesh`, and it is the one source with **no preview file**:
      `status` reports it ready with `url == source_url` and skips the whole cache path.
      Only `.glb` / `.gltf` — a mesh PLY would be drawn as a point cloud.
- [x] `MeshCanvas.tsx` on the same scene root and the same `Rx-90`; headlight plus
      ambient, `DoubleSide`, a wireframe toggle, no level selector.
- [x] `.glb` and `.gltf` registered as `model/gltf-binary` / `model/gltf+json`.

### P2.3 `export/` and step 6

- [x] `step_export.py` rewired off the dead `lfs_output/`: it takes step 4's `splat.ply`
      and step 5's `mesh/` outputs and **hard-links** them into `export/` (verified
      `nlink 2`). It no longer resets step 5 — `run_mesh` already did, and a second reset
      would delete the mesh it is exporting.
- [x] `step_scene.py`: `find_export_splat` instead of `glob("*.ply")[0]`, which would have
      handed Blender `mesh.ply` to import as a gaussian cloud; and the README's
      `{supersplat_url}` placeholder removed, which raised `AttributeError` on every step 6
      after Blender had already run.
- [ ] **Step 6 has not been run since.** Both fixes above are on its path and neither has
      been exercised against a real Blender. That is the next thing.
- [ ] `ExportDefaults.format` / `.pattern` are shown in the setup panel and read by
      nothing. Either make export honour them or delete the section.

---

## P3 — Masking and geometry supervision ✅

Both attach to existing steps rather than adding screens, and both are separately
re-runnable: **the expensive phase must never be redone to change a threshold.**

- [x] `step_sam.py` + `POST /api/pipeline/masks`, modelled line for line on `/analyze` —
      and one correction to what was inherited: **neither pass ever marks its wizard step
      done.** `run_mask_generation` used to set `step_status["3"] = "done"`, which would
      put a green tick on a reconstruction that had never been run. `_run_attached_pass`
      captures the prior status and hands it back, on success, abort and error alike.
- [x] `sam mask` first, and measured whole (`docs/spirula/sam-mask-run.txt`): it writes
      `<stem>.png`, two lines for the entire run, **238 frames in 2.6 s**. The speculative
      run answers `no border found` and exits 0 having written nothing, which the step
      reports as the expected answer for a rectilinear capture rather than as a failure.
- [x] `sam track` second, with the SAM 2.1 / SAM 3 licences shown and accepted
      **separately** — asked as *which one*, never as a tick box, and refused by
      `check_settings` on the backend too, since a run started from anywhere else must hit
      the same gate. **Not run: there is no SAM checkpoint on this workstation**, and
      nothing here downloads one. The command builder and the four refusals are tested;
      the run itself is the standing caveat, and it needs a hand-fetched checkpoint.
- [x] `step_geometry.py` + `POST /api/pipeline/geometry`, writing `sfm/depths/` and
      `sfm/normals/` — **and the junction §13.1 warned about, which is now measured and
      necessary.** `geometry` has no `--image-dir`: without the link it skipped all 238
      images and exited **0**. So `sfm/images` is junctioned to `frames/` for the length
      of the run and removed in a `finally`, and **exit 0 is not read as success**.
- [x] The checkpoint download is a `curl` child with a CR-redrawn bar — **703 fragments**
      through `iter_lines` on the 419.4 MB fetch. Dropped from the bus, ridden as its own
      0.02→0.20 stretch. Abort tested against a live download: `curl.exe` and
      `spirula.exe` both gone from the process table, `ProcessAborted` rather than
      `error`, the junction removed and `frames/` intact.
- [x] Step 3's reset takes `sfm/depths` and `sfm/normals` with it, and `step_sfm` already
      said so by name before deleting them (§14.1) — verified rather than re-implemented.
- [ ] **Not looked at in a browser.** `MaskSettings`, `GeometrySettings` and the two step
      panels typecheck and build, and every backend half is verified against real runs —
      the same standing caveat P1.5, P1.6 and P2.1 carried.

---

## P4 — Open questions to settle by measurement

In the order they block something. See CLAUDE.md §13.

- [x] ~~**Does `spirula geometry` resolve images outside the dataset folder?**~~ **No —
      settled in P3, 2026-08-28.** It skipped all 238 images and exited 0. `step_geometry`
      junctions `sfm/images` to `frames/` for the length of the run only, so §5's layout
      is unchanged and there is still one copy of the frames.
- [ ] **A curation verdict is advisory — JB's call what to do about it.** Step 3 is
      handed the image *directory* and there is no filtered copy of the frames anywhere
      (§5.2), so `rejected:blur` reconstructs anyway unless the frame is deleted. Leave
      it advisory, move rejects to `frames/_rejected/` in step 2, or accept it? Step 3's
      panel says so on screen meanwhile. See CLAUDE.md §13.2.
- [x] ~~**Basename or full filename for a mask?**~~ **Both — settled in P3, 2026-08-28.**
      88 230 → 71 301 features over 20 frames, 16 929 keypoints dropped, and the COLMAP
      naming gives the byte-identical result. `sam mask` writes the basename form itself,
      and `sfm auto` prints `Masks: <dir>\masks` with no flag passed.
- [x] **`--mask-dir` is absolute-path-capable — settled in P3, 2026-08-28.** Six
      200-iteration runs, three each way: psnr 15.12/17.48/15.18 masked against
      22.40/22.68/20.33 with a path that does not exist. `--depth-dir` and `--normal-dir`
      stay assumed by symmetry and are not on the live path. **The negative control is the
      finding: a wrong `--mask-dir` exits 0 and trains unmasked**, so step 4 logs the
      mask count.
- [ ] **Audit the MoGe / Metric3D checkpoint licences** and give them real rows in §10.
      Now on a live path: the default fetch is `moge2-vitb-normal.onnx`, 419.4 MB, from
      `huggingface.co/Ruicheng/moge-2-vitb-normal-onnx`. The step names the URL and says
      the licence is unaudited before it runs; the audit itself is still owed.
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
