import * as THREE from 'three';
import { upRotationX } from './frame';

/**
 * The crop volumes: what they are, and which frame they are stored in.
 *
 * A volume is a unit box or a unit sphere placed by a transform — centre,
 * rotation, half-extent per local axis — and a `mode` saying whether the
 * gaussians inside it are the ones kept or the ones removed. The stack is
 * evaluated by one rule, which `backend/core/crop.py` implements identically:
 *
 *     kept = (inside at least one `keep` volume, or there are none)
 *            and (inside no `delete` volume)
 *
 * so a stack reads "keep this room, minus that lamp, minus those floaters", and
 * **delete always wins** — a delete sphere dropped inside a keep box does the
 * obvious thing rather than nothing.
 *
 * ── The frame, which is the part worth being careful about ──────────────────
 *
 * Everything drawn in this viewer sits in one +Z-up dataset frame and takes a
 * single `Rx-90` on the scene root to reach three.js's Y-up (`frame.ts`,
 * CLAUDE.md §7.3) — plus "Flip up", which is the *other* vertical and rotates
 * the same root the other way.
 *
 * So a volume placed by the gizmo is authored in **viewer** space, and a volume
 * stored as authored would land somewhere else the next time the scene is
 * opened with the flip in the other position. Volumes are therefore stored in
 * the **dataset** frame — the frame the tools wrote and the frame the backend
 * tests `x, y, z` in — and converted on the way in and out. That is what makes
 * a crop mean the same thing to the shader, to the panel and to the PLY.
 *
 * Half-extents are in the volume's own local frame and so survive both
 * conversions untouched.
 */

export type CropKind = 'box' | 'sphere';
export type CropMode = 'keep' | 'delete';
export type CropGizmoMode = 'translate' | 'rotate' | 'scale';

/** Kept in step with `crop.MAX_VOLUMES` and the shader's uniform array. */
export const MAX_CROP_VOLUMES = 8;

export interface CropVolume {
  /** Client-side identity, so a selection survives an edit. The backend ignores it. */
  id: string;
  kind: CropKind;
  mode: CropMode;
  /** Dataset frame. */
  center: [number, number, number];
  /** Half-extent per local axis. A sphere with unequal ones is an ellipsoid. */
  half: [number, number, number];
  /** Dataset frame, `(x, y, z, w)`. */
  rotation: [number, number, number, number];
}

let counter = 0;

export function newVolumeId(): string {
  counter += 1;
  return `v${Date.now().toString(36)}${counter}`;
}

/** The scene-root rotation as a quaternion — dataset → viewer. */
function upQuat(flipUp: boolean): THREE.Quaternion {
  return new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(1, 0, 0), upRotationX(flipUp),
  );
}

/** The volume's world matrix in **viewer** space: translate · rotate · scale(half). */
export function worldMatrix(volume: CropVolume, flipUp: boolean): THREE.Matrix4 {
  const up = upQuat(flipUp);
  const position = new THREE.Vector3(...volume.center).applyQuaternion(up);
  const quaternion = up.clone().multiply(new THREE.Quaternion(...volume.rotation));
  return new THREE.Matrix4().compose(
    position, quaternion, new THREE.Vector3(...volume.half),
  );
}

/**
 * Read a volume back out of an `Object3D` the gizmo has been dragging.
 *
 * The object carries the viewer-frame transform; this undoes the scene root's
 * rotation so what is stored is dataset-frame again. `Math.abs` on the scale is
 * not defensive tidying: three's scale gizmo will happily drag a handle through
 * its own origin and hand back a negative extent, which is a mirrored volume
 * that looks identical and that `crop.parse_volumes` refuses outright.
 */
export function fromObject(
  volume: CropVolume, object: THREE.Object3D, flipUp: boolean,
): CropVolume {
  const inverse = upQuat(flipUp).invert();
  const center = object.position.clone().applyQuaternion(inverse);
  const quaternion = inverse.clone().multiply(object.quaternion);
  return {
    ...volume,
    center: [center.x, center.y, center.z],
    rotation: [quaternion.x, quaternion.y, quaternion.z, quaternion.w],
    half: [
      Math.max(Math.abs(object.scale.x), MIN_HALF),
      Math.max(Math.abs(object.scale.y), MIN_HALF),
      Math.max(Math.abs(object.scale.z), MIN_HALF),
    ],
  };
}

/**
 * The smallest half-extent the panel will store.
 *
 * Not an arbitrary epsilon: a volume thinner than this is one the user cannot
 * see and cannot grab again, and zero is the value the backend refuses. The
 * gizmo can produce both in a single fast drag.
 */
export const MIN_HALF = 1e-4;

/** A new volume filling a fraction of the scene the viewer has framed. */
export function defaultVolume(
  kind: CropKind, mode: CropMode,
  centre: THREE.Vector3 | null, radius: number, flipUp: boolean,
): CropVolume {
  const size = Math.max(radius, MIN_HALF) * 0.6;
  const viewer = centre ? centre.clone() : new THREE.Vector3();
  const dataset = viewer.applyQuaternion(upQuat(flipUp).invert());
  return {
    id: newVolumeId(),
    kind,
    mode,
    center: [dataset.x, dataset.y, dataset.z],
    half: [size, size, size],
    rotation: [0, 0, 0, 1],
  };
}

/** Strip the client-side id — what gets PATCHed into `settings_json`. */
export function toStored(volumes: CropVolume[]): Omit<CropVolume, 'id'>[] {
  return volumes.map(({ id: _id, ...rest }) => rest);
}

/** Rebuild the working list from whatever `settings_json` holds, ids included. */
export function fromStored(raw: unknown): CropVolume[] {
  if (!Array.isArray(raw)) return [];
  const out: CropVolume[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const e = entry as Record<string, unknown>;
    const kind = e.kind === 'sphere' ? 'sphere' : 'box';
    const mode = e.mode === 'delete' ? 'delete' : 'keep';
    const center = triple(e.center, [0, 0, 0]);
    const half = triple(e.half, [1, 1, 1]).map((v) => Math.max(Math.abs(v), MIN_HALF));
    const rotation = quad(e.rotation, [0, 0, 0, 1]);
    out.push({
      id: typeof e.id === 'string' ? e.id : newVolumeId(),
      kind, mode, center,
      half: half as [number, number, number],
      rotation,
    });
    if (out.length >= MAX_CROP_VOLUMES) break;
  }
  return out;
}

function triple(raw: unknown, fallback: [number, number, number]): [number, number, number] {
  if (!Array.isArray(raw) || raw.length !== 3) return fallback;
  const out = raw.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0));
  return out as [number, number, number];
}

function quad(
  raw: unknown, fallback: [number, number, number, number],
): [number, number, number, number] {
  if (!Array.isArray(raw) || raw.length !== 4) return fallback;
  const out = raw.map((v) => (typeof v === 'number' && Number.isFinite(v) ? v : 0));
  const n = Math.hypot(...out);
  return (n > 1e-9 ? out.map((v) => v / n) : fallback) as [number, number, number, number];
}

/** True when two stacks describe the same cut — used to spot an unapplied edit. */
export function sameVolumes(a: CropVolume[], b: CropVolume[]): boolean {
  return JSON.stringify(toStored(a).map(round)) === JSON.stringify(toStored(b).map(round));
}

function round(v: Omit<CropVolume, 'id'>) {
  const r = (n: number) => Math.round(n * 1e6) / 1e6;
  return {
    kind: v.kind, mode: v.mode,
    center: v.center.map(r), half: v.half.map(r), rotation: v.rotation.map(r),
  };
}
