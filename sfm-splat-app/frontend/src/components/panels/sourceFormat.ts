import type { SourceFile, SourceProbe } from '@/types';

/** Shared by the source panel and the mini player, so one file reads the same
 *  way in the list and in the player title bar. */

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(h > 0 ? 2 : 1, '0');
  return h > 0
    ? `${h}h ${mm}m ${String(s).padStart(2, '0')}s`
    : `${mm}m ${String(s).padStart(2, '0')}s`;
}

export function formatBitrate(bps: number): string {
  return bps >= 1_000_000
    ? `${(bps / 1_000_000).toFixed(1)} Mb/s`
    : `${Math.round(bps / 1000)} kb/s`;
}

/**
 * Frames in the source file, and whether that is a count or an estimate.
 *
 * `nb_frames` is what the container declares; MP4 carries it, but plenty of
 * formats write nothing, `0`, or `N/A` — so duration × fps is the fallback, and
 * it is labelled as one rather than passed off as a count.
 */
export function sourceFrameCount(
  p: SourceProbe | null | undefined,
): { count: number; exact: boolean } | null {
  const declared = Number(p?.nb_frames);
  if (Number.isFinite(declared) && declared > 0) return { count: declared, exact: true };
  if (p?.duration_s && p?.fps) {
    return { count: Math.round(p.duration_s * p.fps), exact: false };
  }
  return null;
}

/** 30367 -> "30,367" — the UI is English throughout (§11). */
export function formatCount(n: number): string {
  return n.toLocaleString('en-US');
}

/** Resolution shorthand — what the RS recommendation of §7 is phrased in. */
export function resolutionClass(width: number | null, height: number | null): string | null {
  if (!width || !height) return null;
  const long = Math.max(width, height);
  if (long >= 7000) return '8K';
  if (long >= 3400) return '4K';
  if (long >= 2400) return '2.5K';
  if (long >= 1900) return 'HD';
  return 'SD';
}

/** One dense line: `3840×2880 · hevc · 100 fps · 119.6 Mb/s · 4.23 GB`. */
export function sourceLine(source: SourceFile): string {
  const p = source.probe;
  const parts: string[] = [];
  if (p?.width && p?.height) parts.push(`${p.width}×${p.height}`);
  if (p?.codec) parts.push(p.codec);
  if (p?.fps) parts.push(`${p.fps} fps`);
  if (p?.bitrate) parts.push(formatBitrate(p.bitrate));
  parts.push(formatBytes(source.size_bytes));
  return parts.join(' · ');
}
