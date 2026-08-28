import React, { useEffect, useRef } from 'react';
import * as THREE from 'three';
import * as GaussianSplats3D from '@mkkellogg/gaussian-splats-3d';
import { buildCameraRig, type CameraRig } from './cameraRig';
import { applyUp, upQuaternion } from './frame';
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
 */

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
  onLoaded, onProgress, onError,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<GaussianSplats3D.Viewer | null>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rigRef = useRef<CameraRig | null>(null);

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

    const load = viewer.addSplatScene(url, {
      format: GaussianSplats3D.SceneFormat.Splat,
      // A scene transform, not a rewrite of the splats: `getSplatCenter(i, v,
      // true)` below returns the transformed centres, so the framing follows.
      rotation: upQuaternion(flipUp),
      showLoadingUI: false,
      progressiveLoad: true,
      onProgress: (percent: number) => onProgress?.(percent),
    });

    load
      .then(() => {
        if (disposed) return;
        viewer.start();
        const bounds = splatBounds(viewer.splatMesh);
        if (bounds) {
          const direction = new THREE.Vector3(0.8, 0.5, 1).normalize();
          viewer.camera.position.copy(bounds.centre)
            .addScaledVector(direction, bounds.radius * 2.0);
          viewer.camera.near = Math.max(bounds.radius / 500, 1e-3);
          viewer.camera.far = bounds.radius * 200;
          viewer.camera.updateProjectionMatrix();
          if (viewer.controls) {
            viewer.controls.target.copy(bounds.centre);
            viewer.controls.update();
          }
        }
        onLoaded?.();
      })
      .catch((error: unknown) => {
        if (disposed) return;
        onError?.(error instanceof Error ? error.message : 'Failed to load the splat');
      });

    return () => {
      disposed = true;
      try {
        load.abort();
      } catch {
        // Already settled — nothing to abort.
      }
      viewer.dispose().catch(() => undefined).finally(() => mount.remove());
      viewerRef.current = null;
      sceneRef.current = null;
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

  return <div ref={containerRef} className="w-full h-full" />;
};

export default SplatCanvas;
