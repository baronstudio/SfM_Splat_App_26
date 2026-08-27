import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  FolderInput, FolderOpen, Images, Layers, Loader2, Trash2, HardDriveDownload,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import apiClient from '@/api/client';
import { usePipelineStore } from '@/store/pipelineStore';
import type { ImageSet } from '@/types';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

const IMAGE_EXTS = ['.jpg', '.jpeg', '.png', '.tif', '.tiff'];

function isImage(file: File): boolean {
  const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
  return IMAGE_EXTS.includes(ext);
}

interface Props {
  projectId: string;
  projectName: string;
  /** Called after any import or deletion, so the caller can re-read `input/`. */
  onChanged?: () => void;
}

/**
 * Import a set of already-extracted frames: a folder on this machine, a zip, or
 * a selection of files.
 *
 * Three doors because they are three different costs. A **folder path** is read
 * server-side and never travels over HTTP — the app runs on the workstation
 * that holds the files (§1), so a 20 GB set is a local copy, not an upload. A
 * **zip** is one upload and one unpack, which is what a set that arrived from
 * somewhere else already looks like. A **file selection** (including the
 * browser's folder picker) is the slow lane, kept because it is the only one
 * that works from another machine on the LAN.
 *
 * Whatever the door, the images land renamed as `input/<set>/<set>_0001.png` —
 * a conforming, zero-padded sequence, which is what lets step 2 conform the
 * whole set in one FFmpeg process (§6.7).
 */
export const ImageSetImport: React.FC<Props> = ({ projectId, projectName, onChanged }) => {
  const { startProjectOp, failProjectOp, endProjectOp } = usePipelineStore();

  const [sets, setSets] = useState<ImageSet[]>([]);
  const [loading, setLoading] = useState(true);
  const [folderPath, setFolderPath] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const zipInputRef = useRef<HTMLInputElement>(null);
  const filesInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const fetchSets = useCallback(async () => {
    try {
      const res = await apiClient.get<{ sets: ImageSet[] }>(
        `/projects/${projectId}/image-sets`,
      );
      setSets(res.data.sets);
    } catch {
      setSets([]);
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    fetchSets();
  }, [fetchSets]);

  const finish = useCallback(async () => {
    await fetchSets();
    onChanged?.();
  }, [fetchSets, onChanged]);

  /** Every import goes through the same modal: none of them can be interrupted
   *  (there is no child process to kill, §14.2) and all of them write into the
   *  directory step 2 reads. */
  const runImport = async (label: string, request: () => Promise<unknown>) => {
    setError(null);
    setBusy(label);
    setUploadPercent(0);
    startProjectOp({ projectId, title: 'Importing images', projectName });
    try {
      await request();
      endProjectOp();
      await finish();
    } catch (err: unknown) {
      const detail =
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
        (err instanceof Error ? err.message : 'Import failed');
      setError(detail);
      failProjectOp(detail);
    } finally {
      setBusy(null);
      setUploadPercent(0);
    }
  };

  const importFolder = () => {
    const path = folderPath.trim();
    if (!path) {
      setError('Type or paste the folder path to import.');
      return;
    }
    runImport(path, async () => {
      await apiClient.post(`/projects/${projectId}/import-folder`, { path });
      setFolderPath('');
    });
  };

  const uploadConfig = {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (e: { loaded: number; total?: number }) => {
      if (e.total) setUploadPercent(Math.round((e.loaded / e.total) * 100));
    },
  };

  const importZip = (file: File) =>
    runImport(file.name, async () => {
      const form = new FormData();
      form.append('file', file);
      await apiClient.post(`/projects/${projectId}/import-zip`, form, uploadConfig);
    });

  const importFiles = (files: File[], name: string) =>
    runImport(`${files.length} images`, async () => {
      const form = new FormData();
      files.forEach((f) => form.append('files', f));
      form.append('name', name);
      await apiClient.post(`/projects/${projectId}/import-images`, form, uploadConfig);
    });

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const dropped = Array.from(e.dataTransfer.files);
      const zip = dropped.find((f) => f.name.toLowerCase().endsWith('.zip'));
      if (zip) {
        importZip(zip);
        return;
      }
      const images = dropped.filter(isImage);
      if (images.length) {
        importFiles(images, images[0].name.replace(/\.[^.]+$/, ''));
        return;
      }
      setError('Drop a .zip of images, or the image files themselves.');
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [projectId],
  );

  const handleDelete = async (set: ImageSet) => {
    if (!window.confirm(
      `Delete the image set "${set.name}" (${set.image_count} images) from input/?`,
    )) return;
    try {
      await apiClient.delete(
        `/projects/${projectId}/image-sets/${encodeURIComponent(set.name)}`,
      );
      await finish();
    } catch {
      setError(`Failed to delete "${set.name}"`);
    }
  };

  const pickedFiles = (e: React.ChangeEvent<HTMLInputElement>, fallbackName: string) => {
    const chosen = Array.from(e.target.files ?? []).filter(isImage);
    e.target.value = '';
    if (!chosen.length) {
      setError('No image among the selected files.');
      return;
    }
    // The browser's folder picker exposes `webkitRelativePath`, whose first
    // segment is the folder the user chose — the closest thing to a name it
    // will give us, and better than naming the set after its first image.
    const relative = (chosen[0] as File & { webkitRelativePath?: string })
      .webkitRelativePath;
    const name = relative ? relative.split('/')[0] : fallbackName;
    importFiles(chosen, name);
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs font-medium text-slate-400 uppercase tracking-wide">
        Image sets — pre-extracted frames
      </p>

      {loading ? (
        <div className="flex items-center gap-2 text-slate-500 text-sm py-2">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading…
        </div>
      ) : sets.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {sets.map((set) => (
            <li
              key={set.name}
              className="flex items-center gap-3 rounded-md bg-slate-800 border border-slate-700 px-3 py-2"
            >
              <Layers className="w-4 h-4 text-violet-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-200 truncate">
                  {set.name}
                  <span className="text-slate-500"> / {set.pattern}</span>
                </p>
                <p className="text-xs text-slate-500 truncate">
                  {set.image_count} images · {formatBytes(set.total_bytes)}
                  {set.width ? ` · ${set.width}×${set.height}` : ''}
                  {set.origin === 'zip' && set.origin_name ? ` · from ${set.origin_name}` : ''}
                  {set.has_alpha ? ' · alpha' : ''}
                </p>
              </div>
              <button
                onClick={() => handleDelete(set)}
                className="text-slate-500 hover:text-red-400 transition-colors shrink-0"
                title="Remove this image set"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {/* Door 1 + 2: drop a zip, or drop / browse the images themselves. */}
      <div
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={handleDrop}
        className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-6 transition-colors
          ${isDragging ? 'border-violet-400 bg-violet-950/20' : 'border-slate-600 bg-slate-800/30'}`}
      >
        {busy ? (
          <>
            <Loader2 className="w-6 h-6 text-violet-400 animate-spin" />
            <p className="text-sm text-slate-300 truncate max-w-xs">{busy}</p>
            {uploadPercent > 0 && (
              <>
                <div className="w-full max-w-xs bg-slate-700 rounded-full h-1.5">
                  <div
                    className="bg-violet-500 h-1.5 rounded-full transition-all"
                    style={{ width: `${uploadPercent}%` }}
                  />
                </div>
                <p className="text-xs text-slate-500">{uploadPercent}%</p>
              </>
            )}
          </>
        ) : (
          <>
            <Images className="w-6 h-6 text-slate-500" />
            <p className="text-sm text-slate-400 text-center">
              Drop a <span className="text-slate-300">.zip</span> of images, or the
              image files themselves
            </p>
            <div className="flex gap-2 mt-1">
              <Button
                variant="outline"
                className="h-8 text-xs gap-1.5"
                onClick={() => zipInputRef.current?.click()}
              >
                <HardDriveDownload className="w-3.5 h-3.5" /> Browse .zip
              </Button>
              <Button
                variant="outline"
                className="h-8 text-xs gap-1.5"
                onClick={() => filesInputRef.current?.click()}
              >
                <Images className="w-3.5 h-3.5" /> Browse images
              </Button>
              <Button
                variant="outline"
                className="h-8 text-xs gap-1.5"
                onClick={() => folderInputRef.current?.click()}
              >
                <FolderOpen className="w-3.5 h-3.5" /> Browse folder
              </Button>
            </div>
            <p className="text-[11px] text-slate-600 text-center">
              A browsed folder is uploaded file by file. For a set already on this
              machine, use the path below instead — nothing is copied over HTTP.
            </p>
          </>
        )}
        <input
          ref={zipInputRef}
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) importZip(file);
          }}
        />
        <input
          ref={filesInputRef}
          type="file"
          accept={IMAGE_EXTS.join(',')}
          multiple
          className="hidden"
          onChange={(e) => pickedFiles(e, 'images')}
        />
        <input
          ref={folderInputRef}
          type="file"
          multiple
          className="hidden"
          // Not in React's typings, and the only way a browser will hand over a
          // whole directory. Chromium and Edge honour it; Firefox has `directory`.
          {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
          onChange={(e) => pickedFiles(e, 'images')}
        />
      </div>

      {/* Door 3: a path on this machine, read server-side. */}
      <div className="flex flex-col gap-1.5">
        <label className="text-xs font-medium text-slate-400">
          …or a folder on this machine
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={folderPath}
            onChange={(e) => setFolderPath(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') importFolder(); }}
            placeholder="G:\shoots\riverbed\stills"
            disabled={!!busy}
            className="flex-1 rounded-md bg-slate-800 border border-slate-600 px-3 py-2 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-violet-500 disabled:opacity-50"
          />
          <Button
            onClick={importFolder}
            disabled={!!busy || !folderPath.trim()}
            className="bg-violet-700 hover:bg-violet-600 text-white gap-2"
          >
            <FolderInput className="w-4 h-4" />
            Import
          </Button>
        </div>
        <p className="text-[11px] text-slate-600">
          Read directly from disk — sub-folders included, images renamed into one
          numbered sequence. The originals are left where they are.
        </p>
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error}
        </p>
      )}
    </div>
  );
};

export default ImageSetImport;
