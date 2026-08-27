import React, { useCallback, useEffect, useRef, useState } from 'react';
import { UploadCloud, X, FolderOpen, Trash2, Film, FileText, Loader2, CheckCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { usePipelineStore } from '@/store/pipelineStore';
import { useProjects } from '@/hooks/useProjects';
import apiClient from '@/api/client';
import { ProjectList } from '@/components/projects/ProjectList';
import { ImageSetImport } from '@/components/panels/ImageSetImport';

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function toSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

const ACCEPTED_EXTS = ['.mp4', '.mov', '.srt'];

/** A zip of images is not a source file — it is an image set (§6.7), and it
 *  goes through the import routes rather than through `upload-input`. */
const ZIP_EXT = '.zip';

interface InputFile {
  filename: string;
  size_bytes: number;
}

// ---------------------------------------------------------------------------
// Sub-component: manage sources for an existing project
// ---------------------------------------------------------------------------
const ManageSources: React.FC = () => {
  const { currentProjectId, projects, stepStatuses, setCurrentStep, confirmStep } = usePipelineStore();
  const currentProject = projects.find((p) => p.id === currentProjectId);

  const [files, setFiles] = useState<InputFile[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(true);
  const [isDragging, setIsDragging] = useState(false);
  const [uploading, setUploading] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [setCount, setSetCount] = useState(0);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const fetchFiles = useCallback(async () => {
    if (!currentProjectId) return;
    try {
      const res = await apiClient.get<{ files: InputFile[] }>(
        `/projects/${currentProjectId}/input-files`
      );
      setFiles(res.data.files);
    } finally {
      setLoadingFiles(false);
    }
  }, [currentProjectId]);

  const fetchSetCount = useCallback(async () => {
    if (!currentProjectId) return;
    try {
      const res = await apiClient.get<{ sets: unknown[] }>(
        `/projects/${currentProjectId}/image-sets`,
      );
      setSetCount(res.data.sets.length);
    } catch {
      setSetCount(0);
    }
  }, [currentProjectId]);

  useEffect(() => {
    fetchFiles();
    fetchSetCount();
  }, [fetchFiles, fetchSetCount]);

  const validateFile = (file: File): boolean => {
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    return ACCEPTED_EXTS.includes(ext);
  };

  const uploadFile = async (file: File) => {
    if (!currentProjectId) return;
    if (!validateFile(file)) {
      setError(`"${file.name}" is not accepted. Use .mp4, .mov, or .srt.`);
      return;
    }
    setError(null);
    setUploading(file.name);
    setUploadProgress(0);
    try {
      const form = new FormData();
      form.append('file', file);
      await apiClient.post(`/projects/${currentProjectId}/upload-input`, form, {
        headers: { 'Content-Type': 'multipart/form-data' },
        onUploadProgress: (e) => {
          if (e.total) setUploadProgress(Math.round((e.loaded / e.total) * 100));
        },
      });
      await fetchFiles();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Upload failed';
      setError(msg);
    } finally {
      setUploading(null);
      setUploadProgress(0);
    }
  };

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => setIsDragging(false), []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) uploadFile(file);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [currentProjectId]
  );

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) uploadFile(file);
    e.target.value = '';
  };

  const handleDelete = async (filename: string) => {
    if (!currentProjectId) return;
    if (!window.confirm(`Delete "${filename}" from this project?`)) return;
    try {
      await apiClient.delete(`/projects/${currentProjectId}/input-files/${encodeURIComponent(filename)}`);
      setFiles((prev) => prev.filter((f) => f.filename !== filename));
    } catch {
      setError(`Failed to delete "${filename}"`);
    }
  };

  const hasVideo = files.some((f) => /\.(mp4|mov)$/i.test(f.filename));
  // Either kind of source lets step 2 run: a video to extract from, or a set of
  // pre-extracted frames to conform (§6.7).
  const hasSource = hasVideo || setCount > 0;

  const handleValidate = async () => {
    confirmStep(1);
    setCurrentStep(2);
    if (currentProjectId) {
      const statusForApi: Record<string, string> = {};
      Object.entries(stepStatuses).forEach(([k, v]) => { statusForApi[k] = v; });
      statusForApi['1'] = 'done';
      try {
        await apiClient.put(`/projects/${currentProjectId}`, {
          step_status: statusForApi,
          current_step: 2,
        });
      } catch {
        // non-blocking — navigation already happened
      }
    }
  };

  return (
    <div className="flex flex-col gap-6 p-6 max-w-2xl mx-auto">
      <div>
        <h2 className="text-xl font-semibold text-slate-100">Step 1 — Sources</h2>
        {currentProject && (
          <p className="text-xs text-slate-400 mt-1">
            <FolderOpen className="inline w-3 h-3 mr-1" />
            {currentProject.name} — projects/{toSlug(currentProject.name)}/input/
          </p>
        )}
      </div>

      {/* Current files list */}
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-slate-400 uppercase tracking-wide">
          Current source files
        </p>
        {loadingFiles ? (
          <div className="flex items-center gap-2 text-slate-500 text-sm py-4">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading…
          </div>
        ) : files.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-600 bg-slate-800/30 px-4 py-6 text-center text-sm text-slate-500">
            No source files yet — drop or browse a video below
          </div>
        ) : (
          <ul className="flex flex-col gap-2">
            {files.map((f) => {
              const isVideo = /\.(mp4|mov)$/i.test(f.filename);
              return (
                <li
                  key={f.filename}
                  className="flex items-center gap-3 rounded-md bg-slate-800 border border-slate-700 px-3 py-2"
                >
                  {isVideo
                    ? <Film className="w-4 h-4 text-cyan-400 shrink-0" />
                    : <FileText className="w-4 h-4 text-slate-400 shrink-0" />}
                  <span className="flex-1 text-sm text-slate-200 truncate">{f.filename}</span>
                  <span className="text-xs text-slate-500 shrink-0">{formatBytes(f.size_bytes)}</span>
                  <button
                    onClick={() => handleDelete(f.filename)}
                    className="text-slate-500 hover:text-red-400 transition-colors ml-1 shrink-0"
                    title="Remove file"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Upload zone */}
      <div
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !uploading && fileInputRef.current?.click()}
        className={`relative flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-8 cursor-pointer transition-colors
          ${isDragging
            ? 'border-cyan-400 bg-cyan-950/30'
            : uploading
              ? 'border-slate-600 bg-slate-800/30 cursor-not-allowed'
              : 'border-slate-600 bg-slate-800/30 hover:border-slate-500'
          }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".mp4,.mov,.srt"
          className="hidden"
          onChange={handleFileInput}
        />
        {uploading ? (
          <>
            <Loader2 className="w-6 h-6 text-cyan-400 animate-spin" />
            <p className="text-sm text-slate-300 truncate max-w-xs">{uploading}</p>
            <div className="w-full max-w-xs bg-slate-700 rounded-full h-1.5">
              <div
                className="bg-cyan-500 h-1.5 rounded-full transition-all"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
            <p className="text-xs text-slate-500">{uploadProgress}%</p>
          </>
        ) : (
          <>
            <UploadCloud className="w-7 h-7 text-slate-500" />
            <p className="text-sm text-slate-400">
              Drop a <span className="text-slate-300">.mp4</span> /{' '}
              <span className="text-slate-300">.mov</span> to add or replace, or click to browse
            </p>
            <p className="text-xs text-slate-500">Also supports .srt subtitle files</p>
          </>
        )}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error}
        </p>
      )}

      {currentProjectId && (
        <ImageSetImport
          projectId={currentProjectId}
          projectName={currentProject?.name ?? ''}
          onChanged={fetchSetCount}
        />
      )}

      <Button
        onClick={handleValidate}
        disabled={!hasSource}
        className="bg-green-700 hover:bg-green-600 text-white gap-2"
      >
        <CheckCircle className="w-4 h-4" />
        Validate &amp; Continue to Extract Frames
      </Button>
      {!hasSource && (
        <p className="text-xs text-slate-500 text-center -mt-4">
          Add a .mp4 / .mov video, or import a set of images, to continue
        </p>
      )}
      {hasVideo && setCount > 0 && (
        <p className="text-xs text-amber-400/90 text-center -mt-4">
          This project holds both a video and an image set. Step 2 reads the
          image set; the video is left alone.
        </p>
      )}

      {/* Same list as the create screen: with a project open it is the only way
          back to copy / reset / archive / delete without leaving the wizard. */}
      <ProjectList embedded heading="All projects" />
    </div>
  );
};

// ---------------------------------------------------------------------------
// Sub-component: create a new project
// ---------------------------------------------------------------------------
const CreateProject: React.FC = () => {
  const { projects, setCurrentProject, setCurrentStep } = usePipelineStore();
  const { createProject } = useProjects();

  const [droppedFile, setDroppedFile] = useState<File | null>(null);
  const [projectName, setProjectName] = useState('');
  const [nameError, setNameError] = useState('');
  const [isDragging, setIsDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const dropRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const slug = toSlug(projectName);

  // A project can start from a video or from a zip of already-extracted
  // frames; the two take different routes on the way in (see handleStart).
  const validateFile = (file: File): boolean => {
    const ext = file.name.slice(file.name.lastIndexOf('.')).toLowerCase();
    return ACCEPTED_EXTS.includes(ext) || ext === ZIP_EXT;
  };

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback(() => setIsDragging(false), []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file && validateFile(file)) setDroppedFile(file);
  }, []);

  const handleFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file && validateFile(file)) setDroppedFile(file);
  };

  const handleNameChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setProjectName(e.target.value);
    setNameError(e.target.value.trim().length < 3 ? 'Name must be at least 3 characters' : '');
  };

  const handleStart = async () => {
    if (projectName.trim().length < 3) {
      setNameError('Name must be at least 3 characters');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const project = await createProject(projectName.trim());
      if (droppedFile) {
        const isZip = droppedFile.name.toLowerCase().endsWith(ZIP_EXT);
        const form = new FormData();
        form.append('file', droppedFile);
        // A zip is unpacked into an image set; a video is just a file in input/.
        await apiClient.post(
          `/projects/${project.id}/${isZip ? 'import-zip' : 'upload-input'}`,
          form,
          {
            headers: { 'Content-Type': 'multipart/form-data' },
            onUploadProgress: (e) => {
              if (e.total) setUploadProgress(Math.round((e.loaded / e.total) * 100));
            },
          },
        );
      }
      setCurrentProject(project.id);
      setCurrentStep(2);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to create project';
      setError(msg);
    } finally {
      setLoading(false);
      setUploadProgress(0);
    }
  };

  const uploadLabel = droppedFile && loading
    ? uploadProgress > 0
      ? `Uploading… ${uploadProgress}%`
      : 'Creating project…'
    : loading
      ? 'Creating project…'
      : 'Start Import';

  return (
    <div className="flex flex-col gap-6 p-6 max-w-2xl mx-auto">
      <h2 className="text-xl font-semibold text-slate-100">Step 1 — Import Source</h2>

      {/* Drop Zone */}
      <div
        ref={dropRef}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !droppedFile && fileInputRef.current?.click()}
        className={`relative flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-10 cursor-pointer transition-colors
          ${isDragging
            ? 'border-cyan-400 bg-cyan-950/30'
            : droppedFile
              ? 'border-green-500 bg-green-950/20'
              : 'border-slate-600 bg-slate-800/50 hover:border-slate-500'
          }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".mp4,.mov,.srt,.zip"
          className="hidden"
          onChange={handleFileInput}
        />
        {droppedFile ? (
          <>
            <UploadCloud className="w-8 h-8 text-green-400" />
            <div className="text-center">
              <p className="text-sm font-medium text-slate-100">{droppedFile.name}</p>
              <p className="text-xs text-slate-400">{formatBytes(droppedFile.size)}</p>
            </div>
            {loading && uploadProgress > 0 && (
              <div className="w-full max-w-xs bg-slate-700 rounded-full h-1.5">
                <div
                  className="bg-cyan-500 h-1.5 rounded-full transition-all"
                  style={{ width: `${uploadProgress}%` }}
                />
              </div>
            )}
            {!loading && (
              <button
                className="absolute top-2 right-2 text-slate-400 hover:text-slate-100"
                onClick={(e) => { e.stopPropagation(); setDroppedFile(null); }}
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </>
        ) : (
          <>
            <UploadCloud className="w-8 h-8 text-slate-500" />
            <p className="text-sm text-slate-400">
              Drag &amp; drop <span className="text-slate-300">.mp4</span> /{' '}
              <span className="text-slate-300">.mov</span>, or a{' '}
              <span className="text-slate-300">.zip</span> of images, or click to browse
            </p>
            <p className="text-xs text-slate-500">
              A zip is unpacked into an image set — folders of stills can also be
              imported from disk once the project exists
            </p>
          </>
        )}
      </div>

      {/* Project name */}
      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-slate-300">Project name</label>
        <input
          type="text"
          value={projectName}
          onChange={handleNameChange}
          placeholder="e.g. My Video 2025"
          className="rounded-md bg-slate-800 border border-slate-600 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:outline-none focus:border-cyan-500"
        />
        {nameError && <p className="text-xs text-red-400">{nameError}</p>}
        {slug && !nameError && (
          <p className="text-xs text-slate-500">
            <FolderOpen className="inline w-3 h-3 mr-1" />
            folder: <span className="text-slate-400">projects/{slug}/</span>
          </p>
        )}
      </div>

      {error && (
        <p className="text-sm text-red-400 bg-red-950/30 border border-red-800 rounded px-3 py-2">
          {error}
        </p>
      )}

      <Button
        onClick={handleStart}
        disabled={loading || projectName.trim().length < 3}
        className="bg-cyan-600 hover:bg-cyan-500 text-white"
      >
        {loading && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
        {uploadLabel}
      </Button>

      {/* Existing projects — the shared list, so copy / reset / archive /
          delete and the path + date labels are the same everywhere (§14) */}
      {projects.length > 0 && <ProjectList embedded heading="Resume existing project" />}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Root component: switches between Create and Manage modes
// ---------------------------------------------------------------------------
const Step1_Import: React.FC = () => {
  const { currentProjectId } = usePipelineStore();
  return currentProjectId ? <ManageSources /> : <CreateProject />;
};

export default Step1_Import;

