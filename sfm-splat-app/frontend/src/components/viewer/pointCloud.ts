/**
 * The `PC3D` preview record, written by `backend/core/ply.py`.
 *
 * 16-byte header — magic, version, count, record size — then `count` records
 * of 3 float32 position and 4 uint8 rgba. It exists because the RC cloud ships
 * as ASCII PLY: 18 MB of text for 283k points that the browser would have to
 * re-parse on every mount.
 */

const MAGIC = 0x50433344; // 'PC3D' read big-endian
export const CLOUD_HEADER_BYTES = 16;

export interface PointCloudData {
  count: number;
  positions: Float32Array;
  /** Normalised 0-1 RGB, which is what `vertexColors` wants. */
  colors: Float32Array;
}

export function parsePointCloud(buffer: ArrayBuffer): PointCloudData {
  if (buffer.byteLength < CLOUD_HEADER_BYTES) {
    throw new Error('Preview file is truncated');
  }
  const header = new DataView(buffer);
  if (header.getUint32(0, false) !== MAGIC) {
    throw new Error('Not a PC3D preview file');
  }
  const version = header.getUint32(4, true);
  if (version !== 1) {
    throw new Error(`Unsupported PC3D version ${version}`);
  }
  const count = header.getUint32(8, true);
  const record = header.getUint32(12, true);
  const available = Math.floor((buffer.byteLength - CLOUD_HEADER_BYTES) / record);
  const usable = Math.min(count, available);

  const floats = new Float32Array(buffer, CLOUD_HEADER_BYTES);
  const bytes = new Uint8Array(buffer, CLOUD_HEADER_BYTES);
  const floatStride = record / 4;

  // De-interleaved rather than handed to THREE.InterleavedBuffer: two
  // interleaved attributes over the same array upload it to the GPU twice,
  // and at five million points that is 160 MB of VRAM for nothing.
  const positions = new Float32Array(usable * 3);
  const colors = new Float32Array(usable * 3);
  for (let i = 0; i < usable; i += 1) {
    const f = i * floatStride;
    positions[i * 3] = floats[f];
    positions[i * 3 + 1] = floats[f + 1];
    positions[i * 3 + 2] = floats[f + 2];
    const b = i * record + 12;
    colors[i * 3] = bytes[b] / 255;
    colors[i * 3 + 1] = bytes[b + 1] / 255;
    colors[i * 3 + 2] = bytes[b + 2] / 255;
  }
  return { count: usable, positions, colors };
}

/** Per-axis percentile bounds — an RC cloud always has a few points in orbit. */
export function robustBounds(positions: Float32Array, low = 0.02, high = 0.98) {
  const count = positions.length / 3;
  const step = Math.max(1, Math.floor(count / 50_000));
  const samples = Math.floor((count + step - 1) / step);
  const axes = [new Float32Array(samples), new Float32Array(samples), new Float32Array(samples)];

  let n = 0;
  for (let i = 0; i < count; i += step) {
    axes[0][n] = positions[i * 3];
    axes[1][n] = positions[i * 3 + 1];
    axes[2][n] = positions[i * 3 + 2];
    n += 1;
  }
  if (n === 0) return null;

  const min: number[] = [];
  const max: number[] = [];
  for (const axis of axes) {
    const sorted = axis.subarray(0, n).slice().sort();
    min.push(sorted[Math.floor((n - 1) * low)]);
    max.push(sorted[Math.floor((n - 1) * high)]);
  }
  const centre: [number, number, number] = [
    (min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2,
  ];
  const radius = Math.max(
    Math.hypot(max[0] - min[0], max[1] - min[1], max[2] - min[2]) / 2,
    1e-4,
  );
  return { centre, radius };
}

/**
 * Fetch reporting progress. The full-quality LFS preview is 160 MB — a viewer
 * that says nothing for twenty seconds reads as a viewer that is broken.
 */
export async function fetchWithProgress(
  url: string,
  onProgress: (loaded: number, total: number) => void,
  signal?: AbortSignal,
): Promise<ArrayBuffer> {
  const response = await fetch(url, { signal });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  const total = Number(response.headers.get('content-length') ?? 0);
  if (!response.body) return response.arrayBuffer();

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let loaded = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    loaded += value.byteLength;
    onProgress(loaded, total);
  }

  const out = new Uint8Array(loaded);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out.buffer;
}
