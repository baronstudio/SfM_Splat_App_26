import * as THREE from 'three';
import { upRotationX } from './frame';

/**
 * The saved camera of the splat preview (CLAUDE.md §7.6d).
 *
 * One button on the viewer's toolbar, one object in `settings_json`, and one
 * thing the export writes into — or beside — the file it hands somebody else.
 *
 * ── The frame, which is the same care the crop volumes needed ───────────────
 *
 * The viewer draws everything under a single `Rx-90` scene root plus the
 * "Flip up" toggle (`frame.ts`, §7.3), so a camera read straight off
 * `viewer.camera` is in **viewer** space and would point somewhere else the
 * next time the scene was opened with the flip the other way. A viewpoint is
 * therefore stored in the **dataset** frame — the +Z-up frame spirula wrote,
 * the frame the PLY's `x, y, z` are in, and the frame `cropVolumes.ts` already
 * stores its boxes in — and converted on both sides here.
 *
 * `flip_up` rides along not because the numbers need it but because the *scene*
 * does: restoring a viewpoint saved under the other vertical has to turn the
 * scene back over too, or the camera lands correctly and the world is upside
 * down under it.
 */

export type Vec3 = [number, number, number];

export interface Viewpoint {
  /** Dataset frame. */
  position: Vec3;
  /** Dataset frame — what the orbit controls turn around. */
  target: Vec3;
  /** Dataset frame. The viewer's own up is three.js's +Y, i.e. dataset ±Z. */
  up: Vec3;
  /** Vertical field of view, degrees. */
  fov_y: number;
  /** Which vertical was on when it was saved. */
  flip_up: boolean;
  saved_at: string;
}

/** The scene-root rotation as a quaternion — dataset → viewer. */
function upQuat(flipUp: boolean): THREE.Quaternion {
  return new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(1, 0, 0), upRotationX(flipUp),
  );
}

function toDataset(v: THREE.Vector3, flipUp: boolean): Vec3 {
  const out = v.clone().applyQuaternion(upQuat(flipUp).invert());
  return [out.x, out.y, out.z];
}

function toViewer(v: Vec3, flipUp: boolean): THREE.Vector3 {
  return new THREE.Vector3(...v).applyQuaternion(upQuat(flipUp));
}

/** Read the camera the user has parked, in the dataset frame. */
export function captureViewpoint(
  camera: THREE.PerspectiveCamera, target: THREE.Vector3, flipUp: boolean,
): Viewpoint {
  return {
    position: toDataset(camera.position, flipUp),
    target: toDataset(target, flipUp),
    up: toDataset(camera.up, flipUp),
    fov_y: camera.fov,
    flip_up: flipUp,
    saved_at: new Date().toISOString(),
  };
}

/** The bits of the splat viewer's orbit controls a restore touches. */
export interface OrbitLike {
  target: THREE.Vector3;
  update(): void;
  /**
   * The residual of the last drag. `@mkkellogg/gaussian-splats-3d` builds its
   * OrbitControls with `enableDamping = true` and `dampingFactor = 0.05`, and
   * damped `update()` calls keep applying that residual *after* a teleport —
   * measured, a restore taken 2.5 s after a drag landed 0.025 away from the
   * saved position and drifted on. Its fork exposes these two to clear it;
   * both are optional here so a library that drops them degrades to the
   * approximate restore rather than to a crash.
   */
  clearDampedRotation?(): void;
  clearDampedPan?(): void;
}

/**
 * Put the camera back where it was saved.
 *
 * The near and far planes are left where the framing code set them: they are a
 * property of the scene's size, not of where the camera is standing, and
 * recomputing them from a saved position would undo the sizing that load did.
 */
export function applyViewpoint(
  camera: THREE.PerspectiveCamera,
  controls: OrbitLike | null,
  view: Viewpoint, flipUp: boolean,
): void {
  // Before anything moves: a restore has to land *exactly* on the saved pose,
  // or "Saved" and "Save view" disagree about the same view.
  controls?.clearDampedRotation?.();
  controls?.clearDampedPan?.();
  const target = toViewer(view.target, flipUp);
  camera.position.copy(toViewer(view.position, flipUp));
  camera.up.copy(toViewer(view.up, flipUp).normalize());
  camera.fov = view.fov_y;
  camera.updateProjectionMatrix();
  if (controls) {
    controls.target.copy(target);
    controls.update();
  } else {
    camera.lookAt(target);
  }
}

function triple(raw: unknown, fallback: Vec3): Vec3 {
  if (!Array.isArray(raw) || raw.length !== 3) return fallback;
  if (!raw.every((v) => typeof v === 'number' && Number.isFinite(v))) return fallback;
  return raw as Vec3;
}

/** Rebuild a viewpoint from whatever `settings_json` holds, or null. */
export function fromStored(raw: unknown): Viewpoint | null {
  if (!raw || typeof raw !== 'object') return null;
  const e = raw as Record<string, unknown>;
  const position = triple(e.position, [0, 0, 0]);
  const target = triple(e.target, [0, 0, 0]);
  // The same refusal `core/viewpoint.py` makes: a camera standing on its own
  // target has no direction, and restoring it would leave a blank canvas.
  if (position.every((v, i) => v === target[i])) return null;
  const fov = typeof e.fov_y === 'number' && Number.isFinite(e.fov_y) ? e.fov_y : 50;
  return {
    position,
    target,
    up: triple(e.up, [0, 0, 1]),
    fov_y: Math.min(Math.max(fov, 1), 179),
    flip_up: Boolean(e.flip_up),
    saved_at: typeof e.saved_at === 'string' ? e.saved_at : '',
  };
}

/** One line for the panel, in the dataset frame's own numbers. */
export function describeViewpoint(view: Viewpoint): string {
  const fmt = (v: Vec3) => v.map((c) => c.toFixed(2)).join(', ');
  return `(${fmt(view.position)}) → (${fmt(view.target)}), ${Math.round(view.fov_y)}°`;
}
