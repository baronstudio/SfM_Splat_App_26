import React, { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';
import { buildCameraRig, type CameraRig } from './cameraRig';
import { applyUp, upQuaternion } from './frame';
import { buildCropGizmo, type CropGizmoHandle } from './cropGizmo';
import { syncCropUniforms } from './cropShader';
import type { CropGizmoMode, CropVolume } from './cropVolumes';
import { applyViewpoint, captureViewpoint, type Viewpoint } from './viewpoint';
import type { CameraPose } from '@/types';

/**
 * The gaussian renderer: depth-sorted, alpha-blended splats, sorting done in a
 * worker.
 *
 * It reads the `.splat` preview written by `backend/core/ply.py`, never the
 * training PLY — spirula's is 247 MB for a small project (CLAUDE.md §7.9) and
 * carries 45 spherical-harmonic coefficients per gaussian that a preview does
 * not use.
 *
 * `sharedMemoryForWorkers` is off on purpose: it needs COOP/COEP headers that
 * neither the Vite dev server nor the FastAPI static mount sends, and without
 * them the library falls over instead of falling back.
 *
 * On step 4 it also carries the **crop tool** (§7.6b): draggable box and sphere
 * volumes over the splat, with the gaussians they exclude hidden live in the
 * vertex shader. Nothing here writes anything — the cut itself is a backend
 * pass over the full PLY — so the rule the viewer has always kept still holds.
 */

export interface SplatCropProps {
  volumes: CropVolume[];
  selectedId: string | null;
  gizmoMode: CropGizmoMode;
  /** Draw the volumes and the gizmo. Off leaves the live cut running. */
  showVolumes: boolean;
  /** Hide the excluded gaussians. Off shows the whole splat under the volumes. */
  livePreview: boolean;
  onChange(volume: CropVolume): void;
  onSelect(id: string | null): void;
  /**
   * Whether the live cut could be installed at all. False means the splat
   * library's shader is not the one `cropShader.ts` knows how to patch, and the
   * panel says so rather than showing a preview that is quietly a lie.
   */
  onLiveSupport(supported: boolean): void;
}

/**
 * What the toolbar's "Save view" button needs from the canvas (§7.6d): the
 * camera as the user left it, and the way back to it.
 *
 * A ref rather than a pair of props, because both are *events* — a click, not a
 * state the canvas should re-render for — and because `capture` has to answer
 * with a value rather than push one up.
 */
export interface ViewpointApi {
  /** The current camera in the dataset frame, or null before the load. */
  capture(): Viewpoint | null;
  restore(view: Viewpoint): void;
}

interface SplatCanvasProps {
  url: string;
  background: string;
  cameras: CameraPose[] | null;
  showCameras: boolean;
  showPath: boolean;
  /**
   * Turn the scene over — the other vertical (`frame.ts`). The splat and the
   * overlay take the same rotation, because they are in the same frame; the
   * splat's is read once, when the scene is added, so the parent remounts the
   * canvas on a flip rather than expecting this to be live.
   */
  flipUp: boolean;
  fovX?: number | null;
  aspect?: number | null;
  crop?: SplatCropProps | null;
  /** Filled in with the capture/restore pair once the viewer exists (§7.6d). */
  viewpointApi?: React.MutableRefObject<ViewpointApi | null>;
  /**
   * A viewpoint to jump to as soon as the splat is loaded, in place of the
   * default framing. This is how a restore survives the remount a "Flip up"
   * change forces: the saved vertical is set first, and the camera lands after
   * the canvas has come back.
   */
  restoreOnLoad?: Viewpoint | null;
  onRestored?: () => void;
  /** Median centre and 90th-percentile radius of the loaded splat, once known. */
  onBounds?: (centre: [number, number, number], radius: number) => void;
  onLoaded?: () => void;
  onProgress?: (percent: number) => void;
  onError?: (message: string) => void;
}

/** Median centre and 90th-percentile radius of a sample of the splats. */
function splatBounds(mesh: GaussianSplats3D.SplatMesh) {
  const count = mesh.getSplatCount();
  if (!count) return null;
  const step = Math.max(1, Math.floor(count / 4000));
  const centre = new THREE.Vector3();
  const scratch = new THREE.Vector3();
  const samples: THREE.Vector3[] = [];

  for (let i = 0; i < count; i += step) {
    mesh.getSplatCenter(i, scratch, true);
    if (!Number.isFinite(scratch.x)) continue;
    samples.push(scratch.clone());
    centre.add(scratch);
  }
  if (samples.length === 0) return null;
  centre.divideScalar(samples.length);

  const distances = samples.map((s) => s.distanceTo(centre)).sort((a, b) => a - b);
  const radius = Math.max(distances[Math.floor((distances.length - 1) * 0.9)], 1e-3);
  return { centre, radius };
}

export const SplatCanvas: React.FC<SplatCanvasProps> = ({
  url, background, cameras, showCameras, showPath, flipUp, fovX, aspect,
  crop, viewpointApi, restoreOnLoad, onRestored,
  onBounds, onLoaded, onProgress, onError,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<GaussianSplats3D.Viewer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const overlayRef = useRef<THREE.Scene | null>(null);
  const rigRef = useRef<CameraRig | null>(null);
  const gizmoRef = useRef<CropGizmoHandle | null>(null);
  const [ready, setReady] = useState(0);

  // The crop props reach the animation loop through a ref: the loop is started
  // once, and re-creating it on every gizmo drag would drop a frame per drag.
  const cropRef = useRef<SplatCropProps | null | undefined>(crop);
  cropRef.current = crop;
  const cropDirty = useRef(true);
  const cropSupported = useRef<boolean | null>(null);

  // Same reasoning as `cropRef`: the load callback below is created once, and a
  // restore asked for while the file is still streaming must still happen.
  const restoreRef = useRef<Viewpoint | null | undefined>(restoreOnLoad);
  restoreRef.current = restoreOnLoad;

  useEffect(() => {
    const host = containerRef.current;
    if (!host || !url) return undefined;

    let disposed = false;
    // The viewer builds its own canvas into whatever it is given and cleans up
    // asynchronously; a throwaway child keeps that out of React's DOM.
    const mount = document.createElement('div');
    mount.style.width = '100%';
    mount.style.height = '100%';
    host.appendChild(mount);

    const threeScene = new THREE.Scene();
    sceneRef.current = threeScene;
    // Everything that must be drawn *over* the gaussians rather than among
    // them. See the render wrapper below for why it cannot just live in
    // `threeScene` with `depthTest` off.
    const overlayScene = new THREE.Scene();
    overlayRef.current = overlayScene;

    const viewer = new GaussianSplats3D.Viewer({
      rootElement: mount,
      threeScene,
      cameraUp: [0, 1, 0],
      selfDrivenMode: true,
      useBuiltInControls: true,
      sharedMemoryForWorkers: false,
      gpuAcceleratedSort: false,
      dynamicScene: false,
      halfPrecisionCovariancesOnGPU: true,
      logLevel: GaussianSplats3D.LogLevel.None,
    });
    viewerRef.current = viewer;

    // ── The overlay pass ──────────────────────────────────────────────────
    // `Viewer.render()` makes **two** `renderer.render` calls: `threeScene`
    // first, then the splat mesh, with `autoClear` off between them. So the
    // gaussians are painted over anything in the shared scene no matter what
    // it asks for — `depthTest: false` and `renderOrder` only order objects
    // *within* one call, and the splats write no depth for a second call to
    // test against. That is why the crop gizmo drew behind the splat even
    // though every one of its materials is depth-test-free.
    //
    // A third call after theirs is the whole fix: same camera, same target,
    // `autoClear` off so it composites onto the frame they just drew, and a
    // depth clear so the handles are never occluded by the camera rig either.
    // The wrapper degrades to nothing if the library ever stops exposing
    // `render` as an instance method — the gizmo would simply be behind the
    // splat again, which is where it already was.
    const baseRender = typeof viewer.render === 'function'
      ? (viewer.render.bind(viewer) as () => void)
      : null;
    if (baseRender) {
      viewer.render = () => {
        baseRender();
        const renderer = viewer.renderer;
        if (!renderer || overlayScene.children.length === 0) return;
        const savedAutoClear = renderer.autoClear;
        renderer.autoClear = false;
        renderer.clearDepth();
        renderer.render(overlayScene, viewer.camera);
        renderer.autoClear = savedAutoClear;
      };
    }

    // The toolbar's two viewpoint actions. Installed before the load rather
    // than after it, so the buttons are never wired to a viewer that has since
    // been disposed; both answer honestly while the splat is still streaming.
    if (viewpointApi) {
      viewpointApi.current = {
        capture: () => {
          const live = viewerRef.current;
          if (!live?.camera) return null;
          return captureViewpoint(
            live.camera, live.controls?.target ?? new THREE.Vector3(), flipUp,
          );
        },
        restore: (view) => {
          const live = viewerRef.current;
          if (!live?.camera) return;
          applyViewpoint(live.camera, live.controls ?? null, view, flipUp);
        },
      };
    }

    const load = viewer.addSplatScene(url, {
      format: GaussianSplats3D.SceneFormat.Splat,
      // A scene transform, not a rewrite of the splats: `getSplatCenter(i, v,
      // true)` below returns the transformed centres, so the framing follows.
      // It is also why the crop shader can test `splatCenter` directly — with
      // `dynamicScene: false` the library bakes this into the splat data, so
      // the shader's centres are already in the frame the gizmo works in.
      rotation: upQuaternion(flipUp),
      showLoadingUI: false,
      progressiveLoad: true,
      onProgress: (percent: number) => onProgress?.(percent),
    });

    // The crop uniforms are pushed from here rather than from an effect,
    // because the library **rebuilds the splat material** whenever it rebuilds
    // the mesh — which a progressive load does more than once. A patch applied
    // in an effect would be thrown away mid-load, silently, and the preview
    // would come back uncropped. `syncCropUniforms` re-patches when it has to,
    // and the flag below keeps this to one copy of eight matrices per change.
    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      const mesh = viewerRef.current?.splatMesh;
      const material = (mesh?.material ?? null) as THREE.ShaderMaterial | null;
      if (!material) return;
      const patched = Boolean((material.userData ?? {}).__cropPatched);
      if (patched && !cropDirty.current) return;

      const settings = cropRef.current;
      const live = settings?.livePreview ? settings.volumes : [];
      const supported = syncCropUniforms(material, live, flipUp);
      cropDirty.current = false;
      if (cropSupported.current !== supported) {
        cropSupported.current = supported;
        settings?.onLiveSupport(supported);
      }
    };
    frame = requestAnimationFrame(tick);

    load
      .then(() => {
        if (disposed) return;
        viewer.start();
        const bounds = splatBounds(viewer.splatMesh);
        // A pending restore wins over the default framing — it is the whole
        // point of it — but the bounds are still read, because the crop panel
        // sizes a new volume off them and the near/far planes come from them.
        const restoring = restoreRef.current ?? null;
        if (bounds) {
          const direction = new THREE.Vector3(0.8, 0.5, 1).normalize();
          viewer.camera.near = Math.max(bounds.radius / 500, 1e-3);
          viewer.camera.far = bounds.radius * 200;
          if (!restoring) {
            viewer.camera.position.copy(bounds.centre)
              .addScaledVector(direction, bounds.radius * 2.0);
          }
          viewer.camera.updateProjectionMatrix();
          if (viewer.controls && !restoring) {
            viewer.controls.target.copy(bounds.centre);
            viewer.controls.update();
          }
          onBounds?.(
            [bounds.centre.x, bounds.centre.y, bounds.centre.z], bounds.radius,
          );
        }
        if (restoring) {
          applyViewpoint(
            viewer.camera, viewer.controls ?? null, restoring, flipUp,
          );
          onRestored?.();
        }
        setReady((n) => n + 1);
        onLoaded?.();
      })
      .catch((error: unknown) => {
        if (disposed) return;
        onError?.(error instanceof Error ? error.message : 'Failed to load the splat');
      });

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      try {
        load.abort();
      } catch {
        // Already settled — nothing to abort.
      }
      viewer.dispose().catch(() => undefined).finally(() => mount.remove());
      if (viewpointApi) viewpointApi.current = null;
      viewerRef.current = null;
      sceneRef.current = null;
      overlayRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // ── Background ────────────────────────────────────────────────────────────
  useEffect(() => {
    const viewer = viewerRef.current;
    if (viewer?.renderer) viewer.renderer.setClearColor(new THREE.Color(background), 1);
  }, [background, url]);

  // ── Camera overlay ────────────────────────────────────────────────────────
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return undefined;

    if (rigRef.current) {
      scene.remove(rigRef.current.group);
      rigRef.current.dispose();
      rigRef.current = null;
    }
    if (!showCameras || !cameras || cameras.length === 0) return undefined;

    const rig = buildCameraRig(cameras, { fovX, aspect, showPath });
    if (!rig) return undefined;
    // The same rotation the splat scene was given above, by the same rule.
    applyUp(rig.group, flipUp);
    scene.add(rig.group);
    rigRef.current = rig;

    return () => {
      if (rigRef.current) {
        scene.remove(rigRef.current.group);
        rigRef.current.dispose();
        rigRef.current = null;
      }
    };
  }, [cameras, showCameras, showPath, flipUp, fovX, aspect, url]);

  // ── The crop gizmo ────────────────────────────────────────────────────────
  // Built after the load resolves, because it needs the viewer's camera, its
  // canvas and its orbit controls, and none of those exist before `start()`.
  const cropEnabled = Boolean(crop);
  useEffect(() => {
    const viewer = viewerRef.current;
    // The overlay, not `threeScene`: the volumes and their handles are the tool
    // rather than the data, and they are drawn after the gaussians.
    const overlay = overlayRef.current;
    if (!cropEnabled || !ready || !viewer?.renderer || !overlay) return undefined;

    const handle = buildCropGizmo({
      scene: overlay,
      camera: viewer.camera,
      domElement: viewer.renderer.domElement,
      orbit: viewer.controls ?? null,
      onChange: (volume) => cropRef.current?.onChange(volume),
      onSelect: (id) => cropRef.current?.onSelect(id),
    });
    gizmoRef.current = handle;

    return () => {
      handle.dispose();
      gizmoRef.current = null;
    };
  }, [cropEnabled, ready, url]);

  useEffect(() => {
    // Any change to the stack is a change to the live cut as well as to the
    // objects on screen, so both are marked from one place.
    cropDirty.current = true;
    gizmoRef.current?.update(
      crop?.volumes ?? [],
      crop?.selectedId ?? null,
      flipUp,
      crop?.gizmoMode ?? 'translate',
      Boolean(crop?.showVolumes),
    );
  }, [crop, flipUp]);

  return <div ref={containerRef} className="w-full h-full" />;
};

export default SplatCanvas;
