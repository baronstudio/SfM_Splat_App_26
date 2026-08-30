import * as THREE from 'three';
import { TransformControls } from 'three/examples/jsm/controls/TransformControls.js';
import {
  fromObject, worldMatrix,
  type CropGizmoMode, type CropVolume,
} from './cropVolumes';

/**
 * The crop volumes as objects you can see and drag.
 *
 * `TransformControls` ships inside `three` (r169, `examples/jsm`), so this costs
 * no dependency and no row in CLAUDE.md §10 — the same argument that settled
 * `GLTFLoader` for the mesh canvas on 2026-08-28.
 *
 * Each volume is one `Group` at the volume's viewer-space transform, holding two
 * children that do different jobs:
 *
 * * a **faint translucent solid**, which is what the raycaster picks (a
 *   `LineSegments` is nearly impossible to click, and an invisible mesh is
 *   skipped by `Raycaster` outright) and which also shows the volume as a volume
 *   rather than as a cage;
 * * a **bright wireframe**, which is what you actually aim with. Both draw with
 *   `depthTest` off: the splats render with `depthWrite: false`, so a volume
 *   inside the cloud would otherwise be a cage you can only see the near half of.
 *
 * The geometries are unit-sized — a 2×2×2 box, a radius-1 sphere — so the
 * group's `scale` *is* the volume's half-extent and the gizmo's scale handles
 * edit it directly, with no conversion anywhere.
 */

const KEEP_COLOUR = 0x22d3ee;    // cyan-400 — the gaussians this keeps
const DELETE_COLOUR = 0xf87171;  // red-400  — the gaussians this removes

// Drawn after everything else, and through it. See the note above.
const RENDER_ORDER = 900;

// A pointer that moved further than this between down and up was a camera
// orbit, not a click on a volume.
const CLICK_SLOP_PX = 4;

export interface CropGizmoHandle {
  /** Reconcile the drawn objects with the current stack and selection. */
  update(
    volumes: CropVolume[],
    selectedId: string | null,
    flipUp: boolean,
    mode: CropGizmoMode,
    visible: boolean,
  ): void;
  dispose(): void;
}

interface Entry {
  group: THREE.Group;
  solid: THREE.Mesh;
  wire: THREE.LineSegments | THREE.Line;
  kind: CropVolume['kind'];
}

/** Three orthogonal rings — a sphere you can read the orientation of. */
function sphereWireGeometry(): THREE.BufferGeometry {
  const points: number[] = [];
  const SEGMENTS = 48;
  for (let axis = 0; axis < 3; axis += 1) {
    for (let i = 0; i < SEGMENTS; i += 1) {
      for (const t of [i, i + 1]) {
        const a = (t / SEGMENTS) * Math.PI * 2;
        const c = Math.cos(a);
        const s = Math.sin(a);
        if (axis === 0) points.push(0, c, s);
        else if (axis === 1) points.push(c, 0, s);
        else points.push(c, s, 0);
      }
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

function buildEntry(volume: CropVolume): Entry {
  const solidGeometry = volume.kind === 'sphere'
    ? new THREE.SphereGeometry(1, 32, 24)
    : new THREE.BoxGeometry(2, 2, 2);
  // Derived from the solid rather than from a second box: `EdgesGeometry` reads
  // its source and keeps nothing, so a throwaway one would leak a GPU buffer
  // per volume built.
  const wireGeometry = volume.kind === 'sphere'
    ? sphereWireGeometry()
    : new THREE.EdgesGeometry(solidGeometry);

  const solid = new THREE.Mesh(solidGeometry, new THREE.MeshBasicMaterial({
    transparent: true, opacity: 0.06, depthTest: false, depthWrite: false,
    side: THREE.DoubleSide,
  }));
  const wire = new THREE.LineSegments(wireGeometry, new THREE.LineBasicMaterial({
    transparent: true, opacity: 0.75, depthTest: false, depthWrite: false,
  }));

  solid.renderOrder = RENDER_ORDER;
  wire.renderOrder = RENDER_ORDER + 1;

  const group = new THREE.Group();
  group.add(solid);
  group.add(wire);
  return { group, solid, wire, kind: volume.kind };
}

function paint(entry: Entry, volume: CropVolume, selected: boolean): void {
  const colour = volume.mode === 'delete' ? DELETE_COLOUR : KEEP_COLOUR;
  const solid = entry.solid.material as THREE.MeshBasicMaterial;
  const wire = entry.wire.material as THREE.LineBasicMaterial;
  solid.color.setHex(colour);
  wire.color.setHex(colour);
  solid.opacity = selected ? 0.12 : 0.05;
  wire.opacity = selected ? 1.0 : 0.5;
}

function disposeEntry(entry: Entry): void {
  entry.solid.geometry.dispose();
  (entry.solid.material as THREE.Material).dispose();
  entry.wire.geometry.dispose();
  (entry.wire.material as THREE.Material).dispose();
}

export interface CropGizmoOptions {
  scene: THREE.Scene;
  camera: THREE.Camera;
  domElement: HTMLElement;
  /** The viewer's own orbit controls, disabled for the length of a gizmo drag. */
  orbit: { enabled: boolean } | null;
  /** Fired continuously while a handle is dragged, and once when it is let go. */
  onChange(volume: CropVolume): void;
  /** Fired when a click lands on a volume, or on nothing. */
  onSelect(id: string | null): void;
}

export function buildCropGizmo(options: CropGizmoOptions): CropGizmoHandle {
  const { scene, camera, domElement, orbit } = options;

  const entries = new Map<string, Entry>();
  let volumes: CropVolume[] = [];
  let selectedId: string | null = null;
  let flipUp = false;
  let disposed = false;

  const controls = new TransformControls(camera, domElement);
  const helper = controls.getHelper();
  helper.renderOrder = RENDER_ORDER + 2;
  scene.add(helper);

  // Without this the camera orbits while a handle is being dragged, which reads
  // as the volume refusing to move. Three's own examples do exactly this.
  controls.addEventListener('dragging-changed', (event) => {
    if (orbit) orbit.enabled = !(event.value as boolean);
  });

  controls.addEventListener('objectChange', () => {
    if (!selectedId) return;
    const entry = entries.get(selectedId);
    const volume = volumes.find((v) => v.id === selectedId);
    if (!entry || !volume) return;
    options.onChange(fromObject(volume, entry.group, flipUp));
  });

  // ── Click to select ────────────────────────────────────────────────────────
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let downAt: { x: number; y: number } | null = null;

  const onPointerDown = (event: PointerEvent) => {
    downAt = { x: event.clientX, y: event.clientY };
  };

  const onPointerUp = (event: PointerEvent) => {
    const start = downAt;
    downAt = null;
    // A drag is a camera move or a gizmo handle, never a selection.
    if (!start || controls.dragging) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) > CLICK_SLOP_PX) return;

    const rect = domElement.getBoundingClientRect();
    pointer.set(
      ((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1,
    );
    raycaster.setFromCamera(pointer, camera as THREE.PerspectiveCamera);

    const targets: THREE.Object3D[] = [];
    for (const entry of entries.values()) targets.push(entry.solid);
    const hit = raycaster.intersectObjects(targets, false)[0];
    if (!hit) {
      options.onSelect(null);
      return;
    }
    for (const [id, entry] of entries) {
      if (entry.solid === hit.object) {
        options.onSelect(id);
        return;
      }
    }
  };

  domElement.addEventListener('pointerdown', onPointerDown);
  domElement.addEventListener('pointerup', onPointerUp);

  function drop(id: string): void {
    const entry = entries.get(id);
    if (!entry) return;
    if (controls.object === entry.group) controls.detach();
    scene.remove(entry.group);
    disposeEntry(entry);
    entries.delete(id);
  }

  return {
    update(nextVolumes, nextSelected, nextFlip, mode, visible) {
      if (disposed) return;
      volumes = nextVolumes;
      selectedId = nextSelected;
      flipUp = nextFlip;

      const live = new Set(nextVolumes.map((v) => v.id));
      for (const id of [...entries.keys()]) if (!live.has(id)) drop(id);

      for (const volume of nextVolumes) {
        let entry = entries.get(volume.id);
        // A box that became a sphere is a different geometry, not a new
        // transform: rebuild rather than try to swap it under the group.
        if (entry && entry.kind !== volume.kind) {
          drop(volume.id);
          entry = undefined;
        }
        if (!entry) {
          entry = buildEntry(volume);
          scene.add(entry.group);
          entries.set(volume.id, entry);
        }
        // Never write the transform back onto the object being dragged — the
        // gizmo owns it until the pointer is released, and pushing the value
        // that a drag produced back into it makes the handle stutter.
        if (!(controls.dragging && volume.id === selectedId)) {
          worldMatrix(volume, nextFlip).decompose(
            entry.group.position, entry.group.quaternion, entry.group.scale,
          );
        }
        entry.group.visible = visible;
        paint(entry, volume, visible && volume.id === selectedId);
      }

      const selected = nextSelected ? entries.get(nextSelected) : undefined;
      if (visible && selected) {
        // Guarded: `update` runs on every parent render, and both of these
        // dispatch a change event that would otherwise fire continuously.
        if (controls.object !== selected.group) controls.attach(selected.group);
        if (controls.mode !== mode) controls.setMode(mode);
        // Scale is forced local by TransformControls itself; world is the
        // readable choice for the other two.
        controls.setSpace('world');
      } else if (controls.object) {
        controls.detach();
      }
      helper.visible = visible && Boolean(selected);
    },

    dispose() {
      disposed = true;
      domElement.removeEventListener('pointerdown', onPointerDown);
      domElement.removeEventListener('pointerup', onPointerUp);
      controls.detach();
      scene.remove(helper);
      // `TransformControls.dispose()` throws in three r169: the class became a
      // `Controls` rather than an `Object3D`, and its dispose still calls
      // `this.traverse(...)`. The throw lands inside React's passive-effect
      // unmount, so it took the *next* page down with it — leaving step 4 for
      // step 5 rendered "this.traverse is not a function" instead of the mesh.
      // Its two halves are done here by hand, on the helper that actually holds
      // the geometry.
      controls.disconnect();
      helper.traverse((child) => {
        const node = child as Partial<THREE.Mesh>;
        node.geometry?.dispose();
        const material = node.material as THREE.Material | THREE.Material[] | undefined;
        if (Array.isArray(material)) material.forEach((m) => m.dispose());
        else material?.dispose();
      });
      for (const id of [...entries.keys()]) drop(id);
      if (orbit) orbit.enabled = true;
    },
  };
}
