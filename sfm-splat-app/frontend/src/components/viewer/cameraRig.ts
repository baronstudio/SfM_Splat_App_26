import * as THREE from 'three';
import type { CameraPose } from '@/types';

/**
 * The camera overlay: one wire frustum per registered camera, plus the path
 * they were shot along.
 *
 * `transform_matrix` in the RC export is camera-to-world in the OpenGL frame —
 * three.js's own frame — so the basis goes in untouched and the frustum opens
 * down -Z like a real camera. Everything is merged into two draw calls: at 300
 * cameras a `THREE.Group` of 300 meshes costs more to traverse than the splats
 * cost to draw.
 */

/** Cyan first: with one sequence every camera is cyan, which is the common case. */
const SEQUENCE_COLOURS = [0x22d3ee, 0xa78bfa, 0x34d399, 0xf472b6, 0xfbbf24, 0x60a5fa];
const GAP_COLOUR = 0xf59e0b;   // amber-500 — same colour as the coverage warning
const PATH_DIM = 0.45;         // the path is context, the frustums are the subject

export interface CameraRigOptions {
  fovX?: number | null;
  aspect?: number | null;
  showPath?: boolean;
  /** Frustum length, in world units. Derived from the camera spread if absent. */
  size?: number;
}

export interface CameraRig {
  group: THREE.Group;
  /** Centre of the camera cloud — the subject, for an orbit or a walk alike. */
  centre: THREE.Vector3;
  /** Distance from the centre to the furthest camera. */
  radius: number;
  dispose(): void;
}

function sequenceColour(pose: CameraPose): number {
  if (pose.gap_edge) return GAP_COLOUR;
  if (pose.sequence_id === null || pose.sequence_id === undefined) return SEQUENCE_COLOURS[0];
  return SEQUENCE_COLOURS[pose.sequence_id % SEQUENCE_COLOURS.length];
}

function matrixOf(pose: CameraPose): THREE.Matrix4 {
  const b = pose.basis;
  const p = pose.position;
  // basis is row-major 3x3; Matrix4.set takes row-major too.
  return new THREE.Matrix4().set(
    b[0], b[1], b[2], p[0],
    b[3], b[4], b[5], p[1],
    b[6], b[7], b[8], p[2],
    0, 0, 0, 1,
  );
}

export function buildCameraRig(
  poses: CameraPose[],
  options: CameraRigOptions = {},
): CameraRig | null {
  const cameras = poses.filter((p) => Array.isArray(p.position) && p.position.length >= 3);
  if (cameras.length === 0) return null;

  const centre = new THREE.Vector3();
  for (const pose of cameras) {
    centre.add(new THREE.Vector3(pose.position[0], pose.position[1], pose.position[2]));
  }
  centre.divideScalar(cameras.length);

  let radius = 0;
  for (const pose of cameras) {
    radius = Math.max(
      radius,
      centre.distanceTo(new THREE.Vector3(pose.position[0], pose.position[1], pose.position[2])),
    );
  }
  if (!Number.isFinite(radius) || radius <= 0) radius = 1;

  // Big enough to read as a camera, small enough not to hide the cloud.
  const depth = options.size ?? Math.max(radius * 0.035, 1e-4);
  const fovX = options.fovX && options.fovX > 0 ? options.fovX : 1.2;
  const aspect = options.aspect && options.aspect > 0 ? options.aspect : 16 / 9;
  const halfWidth = Math.tan(fovX / 2) * depth;
  const halfHeight = halfWidth / aspect;

  // Apex + the four image-plane corners, in camera space (looking down -Z).
  const local = [
    new THREE.Vector3(0, 0, 0),
    new THREE.Vector3(-halfWidth, -halfHeight, -depth),
    new THREE.Vector3(halfWidth, -halfHeight, -depth),
    new THREE.Vector3(halfWidth, halfHeight, -depth),
    new THREE.Vector3(-halfWidth, halfHeight, -depth),
  ];
  const edges: [number, number][] = [
    [0, 1], [0, 2], [0, 3], [0, 4],   // the four rays
    [1, 2], [2, 3], [3, 4], [4, 1],   // the image plane
  ];

  const frustumPositions = new Float32Array(cameras.length * edges.length * 2 * 3);
  const frustumColours = new Float32Array(cameras.length * edges.length * 2 * 3);
  const colour = new THREE.Color();
  const world = new THREE.Vector3();
  let cursor = 0;

  cameras.forEach((pose, index) => {
    const matrix = matrixOf(pose);
    colour.setHex(sequenceColour(pose));
    // The first camera gets a brighter apex so "where the shot starts" is
    // visible without hovering anything.
    const gain = index === 0 ? 1 : 0.85;
    for (const [a, b] of edges) {
      for (const vertexIndex of [a, b]) {
        world.copy(local[vertexIndex]).applyMatrix4(matrix);
        frustumPositions[cursor] = world.x;
        frustumPositions[cursor + 1] = world.y;
        frustumPositions[cursor + 2] = world.z;
        frustumColours[cursor] = colour.r * gain;
        frustumColours[cursor + 1] = colour.g * gain;
        frustumColours[cursor + 2] = colour.b * gain;
        cursor += 3;
      }
    }
  });

  const group = new THREE.Group();
  group.name = 'camera-rig';

  const frustumGeometry = new THREE.BufferGeometry();
  frustumGeometry.setAttribute('position', new THREE.BufferAttribute(frustumPositions, 3));
  frustumGeometry.setAttribute('color', new THREE.BufferAttribute(frustumColours, 3));
  const frustumMaterial = new THREE.LineBasicMaterial({
    vertexColors: true,
    transparent: true,
    opacity: 0.9,
    depthWrite: false,
  });
  group.add(new THREE.LineSegments(frustumGeometry, frustumMaterial));

  let pathGeometry: THREE.BufferGeometry | null = null;
  let pathMaterial: THREE.Material | null = null;

  if (options.showPath !== false && cameras.length > 1) {
    // One segment between consecutive cameras of the same sequence. A cut
    // breaks the line on purpose: frame k and k+1 across it are unrelated, and
    // joining them would draw a move the camera never made.
    const points: number[] = [];
    const colours: number[] = [];
    for (let i = 0; i < cameras.length - 1; i += 1) {
      const a = cameras[i];
      const b = cameras[i + 1];
      if (a.sequence_id !== null && b.sequence_id !== null && a.sequence_id !== b.sequence_id) {
        continue;
      }
      points.push(a.position[0], a.position[1], a.position[2],
        b.position[0], b.position[1], b.position[2]);
      colour.setHex(a.gap_edge && b.gap_edge ? GAP_COLOUR : sequenceColour(a));
      for (let k = 0; k < 2; k += 1) {
        colours.push(colour.r * PATH_DIM, colour.g * PATH_DIM, colour.b * PATH_DIM);
      }
    }
    if (points.length > 0) {
      pathGeometry = new THREE.BufferGeometry();
      pathGeometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
      pathGeometry.setAttribute('color', new THREE.Float32BufferAttribute(colours, 3));
      pathMaterial = new THREE.LineBasicMaterial({
        vertexColors: true,
        transparent: true,
        opacity: 0.8,
        depthWrite: false,
      });
      group.add(new THREE.LineSegments(pathGeometry, pathMaterial));
    }
  }

  return {
    group,
    centre,
    radius,
    dispose() {
      frustumGeometry.dispose();
      frustumMaterial.dispose();
      pathGeometry?.dispose();
      pathMaterial?.dispose();
    },
  };
}
