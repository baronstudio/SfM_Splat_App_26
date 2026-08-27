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
    controls: { target: THREE.Vector3; update(): void } | null;
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
