import * as THREE from 'three';

/**
 * Which way is up in each of the files the viewer loads.
 *
 * RealityScan works Z-up and `rc_postprocess.align_pointcloud_to_cameras`
 * rotates the sparse cloud by `Rx+90`, `(x, y, z) -> (x, -z, y)`, to put it
 * back onto the cameras of `transforms.json`. That rotation was picked so the
 * two agree with each other — and they do — but it sends RC's +Z onto **-Y**:
 * the whole RC export is Y-*down*, and three.js draws it upside down.
 *
 * LichtFeld Studio then applies its own `Rx+180`, `(x, y, z) -> (x, -y, -z)`,
 * when it reads the NeRF transforms (measured on a real project: the sign of
 * cov(x,z), cov(x,y) and cov(y,z) between `rc_output/pointcloud.ply` and
 * `lfs_output/splat_9000.ply` flips exactly as that rotation predicts). So the
 * trained splat comes out Y-up and needs nothing — while the camera overlay,
 * which always comes from `transforms.json`, needs the same `Rx+180` to sit on
 * top of it.
 *
 * Hence one rule, applied per object rather than per step: **content in the RC
 * frame is rotated 180° around X for display, content in the LFS frame is
 * not.** Nothing on disk is touched — LFS trains on the frame it expects, and
 * this is a display transform only.
 */

/** Quaternion of `Rx+180`, as `@mkkellogg/gaussian-splats-3d` wants it. */
export const UP_FIX_QUATERNION: [number, number, number, number] = [1, 0, 0, 0];

/** True for the files written in RealityScan's (post-normalisation) frame. */
export function isYDownFrame(source: 'rc' | 'lfs' | 'export'): boolean {
  return source === 'rc';
}

/** Set (or clear) the display rotation on an object. */
export function applyUpFix(object: THREE.Object3D, flip: boolean): void {
  object.rotation.x = flip ? Math.PI : 0;
}

/** The same rotation applied to a bare point — for framing maths. */
export function upFixPoint(point: number[], flip: boolean): number[] {
  return flip ? [point[0], -point[1], -point[2]] : point;
}
