import * as THREE from 'three';

/**
 * One display rotation, on the scene root, for everything the viewer draws.
 *
 * The predecessor needed a *per-object* rule here, and this file used to hold
 * it: RealityScan's export was Y-down, LichtFeld Studio's splat was Y-up, and
 * the camera overlay came from a third file in a third convention. All of that
 * is deleted rather than ported, because the thing it worked around is not true
 * any more and that was **measured, not assumed** (CLAUDE.md §7.3):
 *
 * - the trained splat overlaps its own seed cloud **90.1 %** at identity,
 *   against 29.2 % at `Rx+180` and 8.6-12.0 % at ±90°, so the splat, the sparse
 *   cloud and the poses are all in one frame;
 * - that frame is **+Z up**, by two independent readings agreeing to cos 1.000
 *   — the mean world-up of 251 cameras and the sparse cloud's thinnest
 *   principal axis — and the mapper says so itself (`[orient] levelled and
 *   centred on the cameras`), on by default.
 *
 * three.js is Y-up, so the whole scene takes `Rx-90`, `(x, y, z) -> (x, z, -y)`,
 * which sends world +Z onto viewer +Y. Nothing on disk moves: this is a display
 * transform, and the tools keep reading the frame they wrote.
 *
 * "Flip up" is the other vertical, `Rx+90`, for the captures where the mapper's
 * levelling found the wrong one. It is a genuine question about the scene, not
 * a repair of a convention mismatch.
 */

const QUARTER = Math.PI / 2;

/** Rotation about X, in radians, for the scene root. */
export function upRotationX(flipped: boolean): number {
  return flipped ? QUARTER : -QUARTER;
}

/**
 * The same rotation as an `(x, y, z, w)` quaternion, which is the form
 * `@mkkellogg/gaussian-splats-3d` takes for a scene transform.
 */
export function upQuaternion(flipped: boolean): [number, number, number, number] {
  const half = upRotationX(flipped) / 2;
  return [Math.sin(half), 0, 0, Math.cos(half)];
}

/** Put the scene root (or anything standing in for it) in the display frame. */
export function applyUp(object: THREE.Object3D, flipped: boolean): void {
  object.rotation.set(upRotationX(flipped), 0, 0);
}

/** The same rotation applied to a bare point — for framing maths. */
export function upPoint(point: number[], flipped: boolean): number[] {
  const [x, y, z] = point;
  // Rx-90: (x, z, -y).  Rx+90: (x, -z, y).
  return flipped ? [x, -z, y] : [x, z, -y];
}
