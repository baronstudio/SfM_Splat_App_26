import * as THREE from 'three';
import { MAX_CROP_VOLUMES, worldMatrix, type CropVolume } from './cropVolumes';

/**
 * The live crop: gaussians outside the volumes stop being drawn, in the shader.
 *
 * The alternative was to filter the loaded `.splat` buffer on the CPU and hand
 * the library a new scene on every change, which is a 32 MB re-upload per drag
 * frame at the viewer's default level. This costs one `mat4` multiply and a
 * comparison per gaussian per frame, on hardware that is already doing a
 * covariance decode per gaussian per frame, and nothing at all when the stack
 * is empty.
 *
 * ── Why this is allowed to reach into the library ──────────────────────────
 *
 * `@mkkellogg/gaussian-splats-3d` builds its splat material itself and exposes
 * no hook, so the vertex shader is patched by string. That is a real coupling
 * and it is contained rather than hidden:
 *
 * * The anchor is the line where the shader has just decoded `splatCenter` and
 *   is about to leave model space — `vec4 viewCenter = transformModelViewMatrix
 *   * vec4(splatCenter, 1.0);` — which occurs **exactly once** in the built
 *   source. `patchSplatMaterial` checks that and refuses to patch a shader it
 *   does not recognise, so a library upgrade degrades to "no live preview,
 *   the crop still applies on Apply" instead of to a blank canvas.
 * * `splatCenter` is in **viewer world space**, and that is a property of how
 *   this app configures the viewer rather than an assumption: with
 *   `dynamicScene: false` the library bakes each scene's transform into the
 *   splat data (`fillSplatDataArrays` applies it when `dynamicMode` is off), so
 *   the scene rotation `SplatCanvas` passes to `addSplatScene` is already in
 *   there. It is the same space `getSplatCenter(i, v, true)` reports and the
 *   same space the gizmo works in, which is what lets the test below be a
 *   plain compare against a `mat4` built from the gizmo's own matrix.
 * * The library rebuilds the material whenever it rebuilds the mesh — a
 *   progressive load does this more than once — so the patch is idempotent and
 *   re-applied on every sync rather than once at mount.
 *
 * The rejection is the library's own idiom for "drop this splat":
 * `gl_Position = vec4(0.0, 0.0, 2.0, 1.0); return;`, which is what it already
 * does for a splat outside the clip volume or in a hidden scene.
 */

const ANCHOR = 'vec4 viewCenter = transformModelViewMatrix * vec4(splatCenter, 1.0);';
const MAIN = 'void main () {';
const PATCH_FLAG = '__cropPatched';

const DECLARATIONS = `
uniform int cropCount;
uniform mat4 cropInverse[${MAX_CROP_VOLUMES}];
uniform int cropKind[${MAX_CROP_VOLUMES}];
uniform int cropMode[${MAX_CROP_VOLUMES}];
`;

// One rule, the same one `core/crop.py` applies to the file:
//   kept = (inside a keep volume, or there are none) and (inside no delete volume)
const TEST = `
    if (cropCount > 0) {
        bool cropHasKeep = false;
        bool cropInKeep = false;
        bool cropInDelete = false;
        for (int ci = 0; ci < ${MAX_CROP_VOLUMES}; ci++) {
            if (ci >= cropCount) break;
            vec3 q = (cropInverse[ci] * vec4(splatCenter, 1.0)).xyz;
            bool inside = (cropKind[ci] == 1)
                ? dot(q, q) <= 1.0
                : max(max(abs(q.x), abs(q.y)), abs(q.z)) <= 1.0;
            if (cropMode[ci] == 1) {
                cropInDelete = cropInDelete || inside;
            } else {
                cropHasKeep = true;
                cropInKeep = cropInKeep || inside;
            }
        }
        if ((cropHasKeep && !cropInKeep) || cropInDelete) {
            gl_Position = vec4(0.0, 0.0, 2.0, 1.0);
            return;
        }
    }
`;

type SplatMaterial = THREE.ShaderMaterial & {
  userData: Record<string, unknown>;
};

function identityMatrices(): THREE.Matrix4[] {
  return Array.from({ length: MAX_CROP_VOLUMES }, () => new THREE.Matrix4());
}

/**
 * Patch one material, once. Returns false when the shader is not the one this
 * module knows how to edit — the caller falls back to wireframes only.
 */
export function patchSplatMaterial(material: THREE.ShaderMaterial | null): boolean {
  if (!material) return false;
  const target = material as SplatMaterial;
  if (target.userData[PATCH_FLAG]) return true;

  const source = target.vertexShader;
  if (typeof source !== 'string') return false;
  // Both anchors must be there and unambiguous, or we are patching a shader we
  // have not read.
  if (source.split(ANCHOR).length !== 2 || source.split(MAIN).length !== 2) {
    return false;
  }

  target.vertexShader = source
    .replace(MAIN, `${DECLARATIONS}\n${MAIN}`)
    .replace(ANCHOR, `${TEST}\n            ${ANCHOR}`);

  target.uniforms.cropCount = { value: 0 };
  target.uniforms.cropInverse = { value: identityMatrices() };
  target.uniforms.cropKind = { value: new Int32Array(MAX_CROP_VOLUMES) };
  target.uniforms.cropMode = { value: new Int32Array(MAX_CROP_VOLUMES) };

  target.userData[PATCH_FLAG] = true;
  target.needsUpdate = true;
  return true;
}

/**
 * Push the current stack into the material's uniforms, patching it first if the
 * library has rebuilt it since the last call.
 *
 * The inverse is `(translate · rotate · scale(half))⁻¹`, so the shader's test is
 * against the unit box and the unit sphere and the whole placement — including a
 * rotation the volume was authored with — travels in the matrix.
 */
export function syncCropUniforms(
  material: THREE.ShaderMaterial | null,
  volumes: CropVolume[],
  flipUp: boolean,
): boolean {
  if (!patchSplatMaterial(material)) return false;
  const uniforms = (material as SplatMaterial).uniforms;

  const count = Math.min(volumes.length, MAX_CROP_VOLUMES);
  const matrices = uniforms.cropInverse.value as THREE.Matrix4[];
  const kinds = uniforms.cropKind.value as Int32Array;
  const modes = uniforms.cropMode.value as Int32Array;

  for (let i = 0; i < count; i += 1) {
    const volume = volumes[i];
    matrices[i].copy(worldMatrix(volume, flipUp)).invert();
    kinds[i] = volume.kind === 'sphere' ? 1 : 0;
    modes[i] = volume.mode === 'delete' ? 1 : 0;
  }
  uniforms.cropCount.value = count;
  return true;
}
