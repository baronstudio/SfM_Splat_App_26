/**
 * Minimal typings for @mkkellogg/gaussian-splats-3d, which ships none.
 *
 * Only what SplatCanvas actually calls is declared — a hand-written mirror of a
 * whole library goes stale silently, and an over-complete one lies about what
 * we depend on.
 */
declare module '@mkkellogg/gaussian-splats-3d' {
  import * as THREE from 'three';

  export const SceneFormat: {
    Splat: 0;
    KSplat: 1;
    Ply: 2;
    Spz: 3;
  };

  export const SceneRevealMode: {
    Default: 0;
    Gradual: 1;
    Instant: 2;
  };

  export const LogLevel: {
    None: 0;
    Error: 1;
    Warning: 2;
    Info: 3;
    Debug: 4;
  };

  export interface AbortablePromise<T> extends Promise<T> {
    abort(): void;
  }

  export interface SplatMesh {
    getSplatCount(): number;
    getSplatCenter(index: number, out: THREE.Vector3, applyTransform: boolean): void;
    /**
     * The splat shader, which `viewer/cropShader.ts` patches to hide the
     * gaussians a crop excludes (CLAUDE.md §7.6b).
     *
     * `SplatMesh extends THREE.Mesh`, so this is the mesh's own material — but
     * it is declared here because the library **rebuilds it** on every non-
     * incremental mesh build, which is what makes the patch something to
     * re-apply rather than to install once.
     */
    material: THREE.ShaderMaterial | null;
  }

  export interface ViewerOptions {
    rootElement?: HTMLElement;
    cameraUp?: number[];
    initialCameraPosition?: number[];
    initialCameraLookAt?: number[];
    selfDrivenMode?: boolean;
    useBuiltInControls?: boolean;
    ignoreDevicePixelRatio?: boolean;
    halfPrecisionCovariancesOnGPU?: boolean;
    threeScene?: THREE.Scene;
    renderer?: THREE.WebGLRenderer;
    camera?: THREE.Camera;
    gpuAcceleratedSort?: boolean;
    integerBasedSort?: boolean;
    sharedMemoryForWorkers?: boolean;
    dynamicScene?: boolean;
    antialiased?: boolean;
    focalAdjustment?: number;
    logLevel?: number;
    sphericalHarmonicsDegree?: number;
    enableSIMDInSort?: boolean;
    inMemoryCompressionLevel?: number;
    freeIntermediateSplatData?: boolean;
  }

  export interface AddSplatSceneOptions {
    format?: number;
    splatAlphaRemovalThreshold?: number;
    showLoadingUI?: boolean;
    progressiveLoad?: boolean;
    position?: number[];
    rotation?: number[];
    scale?: number[];
    onProgress?: (percent: number, message: string, stage: number) => void;
  }

  export class Viewer {
    constructor(options?: ViewerOptions);
    camera: THREE.PerspectiveCamera;
    // OrbitControls. `enabled` is turned off for the length of a crop-gizmo
    // drag, or the camera orbits while a handle is being pulled and the volume
    // reads as refusing to move (`viewer/cropGizmo.ts`).
    controls: {
      target: THREE.Vector3;
      update(): void;
      enabled: boolean;
      // This build's OrbitControls is a fork with damping on
      // (`dampingFactor = 0.05`), and these two drop the residual of the last
      // drag. `viewer/viewpoint.ts` calls them before it teleports the camera,
      // because a damped `update()` keeps applying that residual afterwards and
      // a restore then lands near the saved view rather than on it.
      clearDampedRotation?(): void;
      clearDampedPan?(): void;
    } | null;
    renderer: THREE.WebGLRenderer;
    threeScene: THREE.Scene;
    splatMesh: SplatMesh;
    addSplatScene(path: string, options?: AddSplatSceneOptions): AbortablePromise<void>;
    start(): void;
    stop(): void;
    setRenderMode(mode: number): void;
    dispose(): Promise<void>;
  }
}
