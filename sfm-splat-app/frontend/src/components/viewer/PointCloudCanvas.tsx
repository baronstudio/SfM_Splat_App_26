import React, { useCallback, useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { buildCameraRig, type CameraRig } from './cameraRig';
import { applyUpFix, upFixPoint } from './frame';
import { fetchWithProgress, parsePointCloud, robustBounds } from './pointCloud';
import type { CameraPose } from '@/types';

/**
 * The sparse-cloud renderer: `THREE.Points` over a `PC3D` preview, with the
 * registered cameras drawn on top.
 *
 * Splats get their own canvas (`SplatCanvas`) — a gaussian cloud drawn as
 * points is a different picture, not a cheaper one.
 */

interface PointCloudCanvasProps {
  url: string;
  pointSize: number;
  background: string;
  cameras: CameraPose[] | null;
  showCameras: boolean;
  showPath: boolean;
  /** Draw the cloud 180 deg around X — see `frame.ts`. */
  flipCloud: boolean;
  /** Same for the camera overlay; the two frames are not always the same one. */
  flipCameras: boolean;
  fovX?: number | null;
  aspect?: number | null;
  /** The parsed positions, for whatever the parent wants to measure on them. */
  onPositions?: (positions: Float32Array) => void;
  onLoaded?: (count: number) => void;
  onProgress?: (loaded: number, total: number) => void;
  onError?: (message: string) => void;
}

export const PointCloudCanvas: React.FC<PointCloudCanvasProps> = ({
  url, pointSize, background, cameras, showCameras, showPath,
  flipCloud, flipCameras, fovX, aspect,
  onPositions,
  onLoaded, onProgress, onError,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const pointsRef = useRef<THREE.Points | null>(null);
  const rigRef = useRef<CameraRig | null>(null);
  // Framing is the cloud's job, but the cloud arrives asynchronously and the
  // rig may get there first — remember what the cloud decided.
  const framedRef = useRef(false);
  // Kept so flipping the up axis can re-frame: rotating the scene under a
  // camera that stays put swings the subject out of view.
  const boundsRef = useRef<{ centre: number[]; radius: number } | null>(null);

  /** Put a sphere of `radius` around `centre` in view. */
  const frame = useCallback((centre: number[], radius: number) => {
    const camera = cameraRef.current;
    const controls = controlsRef.current;
    if (!camera || !controls) return;
    const target = new THREE.Vector3(centre[0], centre[1], centre[2]);
    const direction = new THREE.Vector3(0.8, 0.5, 1).normalize();
    camera.position.copy(target).addScaledVector(direction, radius * 2.4);
    camera.near = Math.max(radius / 500, 1e-3);
    camera.far = radius * 200;
    camera.updateProjectionMatrix();
    controls.target.copy(target);
    controls.update();
  }, []);

  // ── Renderer, once per mount ───────────────────────────────────────────────
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(55, 1, 0.01, 5000);
    camera.position.set(0, 2, 6);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.domElement.style.display = 'block';
    renderer.domElement.style.width = '100%';
    renderer.domElement.style.height = '100%';
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.rotateSpeed = 0.6;

    sceneRef.current = scene;
    cameraRef.current = camera;
    controlsRef.current = controls;
    rendererRef.current = renderer;

    const resize = () => {
      const { clientWidth, clientHeight } = container;
      if (clientWidth === 0 || clientHeight === 0) return;
      renderer.setSize(clientWidth, clientHeight, false);
      camera.aspect = clientWidth / clientHeight;
      camera.updateProjectionMatrix();
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(container);

    let frame = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      rendererRef.current = null;
    };
  }, []);

  // ── Background ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (sceneRef.current) sceneRef.current.background = new THREE.Color(background);
  }, [background]);

  // ── The cloud ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene || !url) return undefined;

    const abort = new AbortController();
    framedRef.current = false;

    fetchWithProgress(url, (loaded, total) => onProgress?.(loaded, total), abort.signal)
      .then((buffer) => {
        if (abort.signal.aborted) return;
        const cloud = parsePointCloud(buffer);
        const geometry = new THREE.BufferGeometry();
        geometry.setAttribute('position', new THREE.BufferAttribute(cloud.positions, 3));
        geometry.setAttribute('color', new THREE.BufferAttribute(cloud.colors, 3));
        const material = new THREE.PointsMaterial({
          size: pointSize,
          sizeAttenuation: false,
          vertexColors: true,
        });
        const points = new THREE.Points(geometry, material);
        // The bounding sphere three.js would compute is the one the far-flung
        // stray points define, and it frustum-culls the whole cloud the moment
        // you zoom in. The robust bounds below are the honest extent.
        points.frustumCulled = false;

        if (pointsRef.current) {
          scene.remove(pointsRef.current);
          pointsRef.current.geometry.dispose();
          (pointsRef.current.material as THREE.Material).dispose();
        }
        scene.add(points);
        pointsRef.current = points;

        applyUpFix(points, flipCloud);

        const bounds = robustBounds(cloud.positions);
        if (bounds) {
          boundsRef.current = bounds;
          frame(upFixPoint(bounds.centre, flipCloud), bounds.radius);
          framedRef.current = true;
        }
        onPositions?.(cloud.positions);
        onLoaded?.(cloud.count);
      })
      .catch((error: unknown) => {
        if (abort.signal.aborted) return;
        onError?.(error instanceof Error ? error.message : 'Failed to load the preview');
      });

    return () => {
      abort.abort();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

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
    applyUpFix(rig.group, flipCameras);
    scene.add(rig.group);
    rigRef.current = rig;
    // Only when the cloud has not framed the view yet: with a cloud on screen,
    // moving the camera because a checkbox was ticked is disorienting.
    if (!framedRef.current) {
      frame(upFixPoint([rig.centre.x, rig.centre.y, rig.centre.z], flipCameras), rig.radius * 1.4);
      framedRef.current = true;
    }

    return () => {
      if (rigRef.current) {
        scene.remove(rigRef.current.group);
        rigRef.current.dispose();
        rigRef.current = null;
      }
    };
  }, [cameras, showCameras, showPath, flipCameras, fovX, aspect, frame]);

  // -- Up axis ---------------------------------------------------------------
  useEffect(() => {
    if (pointsRef.current) applyUpFix(pointsRef.current, flipCloud);
    const bounds = boundsRef.current;
    if (bounds) frame(upFixPoint(bounds.centre, flipCloud), bounds.radius);
  }, [flipCloud, frame]);


  // The Reconstruction Region and its `TransformControls` gizmo used to live
  // here. Deleted rather than ported (CLAUDE.md §12, 2026-08-27): the box was a
  // RealityScan input, and the masking it fed is `spirula sam` now (§7.4).
  //
  // One hard-won note kept, because it will bite again the day anything else
  // needs a gizmo: **do not call `TransformControls.dispose()`** in three
  // 0.169. Upstream moved the class from `Object3D` onto the new `Controls`
  // base and left `dispose()` calling `this.traverse(…)`, a method it no longer
  // has, so the teardown throws `this.traverse is not a function` and takes the
  // React tree down to the error boundary. `disconnect()` is the half that
  // matters; the geometries hang off `getHelper()`, which *is* an `Object3D`.

  // ── Point size ─────────────────────────────────────────────────────────────

  useEffect(() => {
    const points = pointsRef.current;
    if (points) (points.material as THREE.PointsMaterial).size = pointSize;
  }, [pointSize]);

  return <div ref={containerRef} className="w-full h-full" />;
};

export default PointCloudCanvas;
