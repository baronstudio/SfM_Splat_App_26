import React, { useCallback, useEffect, useRef } from 'react';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { buildCameraRig, type CameraRig } from './cameraRig';
import { applyUp, upPoint } from './frame';
import type { CameraPose } from '@/types';

/**
 * The mesh renderer: step 5's glTF surface, loaded by `three`'s own
 * `GLTFLoader`.
 *
 * The third renderer in the viewer, and the only one with no preview file
 * behind it. A textured glb is neither a point cloud nor a splat — there is no
 * record format to decimate it into — and the reference mesh was 11.6 MB
 * against the 178 MB splat it came from, so the browser loads what the tool
 * wrote (`core/preview.py`). `GLTFLoader` ships inside `three`, so this costs
 * no new dependency and no §10 row.
 *
 * The mesh is in the same +Z-up frame as the splat it was extracted from and as
 * the cameras that coloured it, so it takes the same single `Rx-90` on the
 * scene root as the other two canvases (`frame.ts`, CLAUDE.md §7.3).
 */

interface MeshCanvasProps {
  url: string;
  background: string;
  cameras: CameraPose[] | null;
  showCameras: boolean;
  showPath: boolean;
  flipUp: boolean;
  fovX?: number | null;
  aspect?: number | null;
  wireframe?: boolean;
  onLoaded?: (info: { vertices: number; meshes: number }) => void;
  onProgress?: (loaded: number, total: number) => void;
  onError?: (message: string) => void;
}

export const MeshCanvas: React.FC<MeshCanvasProps> = ({
  url, background, cameras, showCameras, showPath, flipUp, fovX, aspect,
  wireframe = false,
  onLoaded, onProgress, onError,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const rootRef = useRef<THREE.Group | null>(null);
  const cameraRef = useRef<THREE.PerspectiveCamera | null>(null);
  const controlsRef = useRef<OrbitControls | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const modelRef = useRef<THREE.Object3D | null>(null);
  const rigRef = useRef<CameraRig | null>(null);
  const framedRef = useRef(false);
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
    const root = new THREE.Group();
    root.name = 'scene-root';
    scene.add(root);

    const camera = new THREE.PerspectiveCamera(55, 1, 0.01, 5000);
    camera.position.set(0, 2, 6);

    // A textured glb comes out of the loader with a PBR material, so an unlit
    // scene renders it black. The key light rides on the camera — a headlight,
    // which is the one arrangement that never leaves the side you are looking
    // at in shadow — with enough ambient that the rest is still readable.
    scene.add(new THREE.AmbientLight(0xffffff, 1.1));
    const headlight = new THREE.DirectionalLight(0xffffff, 1.6);
    headlight.position.set(0, 0, 1);
    camera.add(headlight);
    scene.add(camera);

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
    rootRef.current = root;
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

    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      controls.update();
      renderer.render(scene, camera);
    };
    tick();

    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      sceneRef.current = null;
      rootRef.current = null;
      cameraRef.current = null;
      controlsRef.current = null;
      rendererRef.current = null;
    };
  }, []);

  // ── Background ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (sceneRef.current) sceneRef.current.background = new THREE.Color(background);
  }, [background]);

  // ── The mesh ──────────────────────────────────────────────────────────────
  useEffect(() => {
    const root = rootRef.current;
    if (!root || !url) return undefined;

    let cancelled = false;
    framedRef.current = false;
    const loader = new GLTFLoader();

    loader.load(
      url,
      (gltf) => {
        if (cancelled) return;

        if (modelRef.current) {
          root.remove(modelRef.current);
          disposeTree(modelRef.current);
        }
        const model = gltf.scene;
        root.add(model);
        modelRef.current = model;

        let vertices = 0;
        let meshes = 0;
        model.traverse((child) => {
          const mesh = child as THREE.Mesh;
          if (!mesh.isMesh) return;
          meshes += 1;
          const position = mesh.geometry?.getAttribute('position');
          if (position) vertices += position.count;
          // Backfaces are the inside of a surface the capture never saw, and
          // `--cull-unseen` leaves plenty of open boundary. Drawing both sides
          // is the difference between a hole and a black hole.
          forEachMaterial(mesh, (material) => {
            material.side = THREE.DoubleSide;
          });
        });

        const box = new THREE.Box3().setFromObject(model);
        if (!box.isEmpty()) {
          const centre = box.getCenter(new THREE.Vector3());
          const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 1e-3);
          boundsRef.current = { centre: [centre.x, centre.y, centre.z], radius };
          // The box is measured in the file's frame; the camera lives in the
          // rotated one, so the centre makes the same trip the root just took.
          frame(upPoint([centre.x, centre.y, centre.z], flipUp), radius);
          framedRef.current = true;
        }
        onLoaded?.({ vertices, meshes });
      },
      (event) => {
        if (!cancelled && event.total) onProgress?.(event.loaded, event.total);
      },
      (error: unknown) => {
        if (cancelled) return;
        onError?.(error instanceof Error ? error.message : 'Failed to load the mesh');
      },
    );

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  // ── Wireframe ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const model = modelRef.current;
    if (!model) return;
    model.traverse((child) => {
      const mesh = child as THREE.Mesh;
      if (!mesh.isMesh) return;
      forEachMaterial(mesh, (material) => {
        if ('wireframe' in material) {
          (material as THREE.MeshStandardMaterial).wireframe = wireframe;
        }
      });
    });
  }, [wireframe]);

  // ── Camera overlay ────────────────────────────────────────────────────────
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;

    if (rigRef.current) {
      root.remove(rigRef.current.group);
      rigRef.current.dispose();
      rigRef.current = null;
    }
    if (!showCameras || !cameras || cameras.length === 0) return undefined;

    const rig = buildCameraRig(cameras, { fovX, aspect, showPath });
    if (!rig) return undefined;
    root.add(rig.group);
    rigRef.current = rig;
    if (!framedRef.current) {
      frame(upPoint([rig.centre.x, rig.centre.y, rig.centre.z], flipUp), rig.radius * 1.4);
      framedRef.current = true;
    }

    return () => {
      if (rigRef.current) {
        root.remove(rigRef.current.group);
        rigRef.current.dispose();
        rigRef.current = null;
      }
    };
  }, [cameras, showCameras, showPath, flipUp, fovX, aspect, frame]);

  // ── Up axis ───────────────────────────────────────────────────────────────
  useEffect(() => {
    if (rootRef.current) applyUp(rootRef.current, flipUp);
    const bounds = boundsRef.current;
    if (bounds) frame(upPoint(bounds.centre, flipUp), bounds.radius);
  }, [flipUp, frame]);

  return <div ref={containerRef} className="w-full h-full" />;
};

/** A mesh's material may be one or an array of them. */
function forEachMaterial(mesh: THREE.Mesh, fn: (m: THREE.Material) => void): void {
  const material = mesh.material;
  if (Array.isArray(material)) material.forEach(fn);
  else if (material) fn(material);
}

/** Give back the GPU buffers a replaced glTF was holding. */
function disposeTree(object: THREE.Object3D): void {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (!mesh.isMesh) return;
    mesh.geometry?.dispose();
    forEachMaterial(mesh, (material) => {
      // The texture atlas is 4096x4096 and the loader gives each mesh its own
      // reference to it; leaking one is 64 MB of VRAM per reload.
      const standard = material as THREE.MeshStandardMaterial;
      standard.map?.dispose();
      material.dispose();
    });
  });
}

export default MeshCanvas;
