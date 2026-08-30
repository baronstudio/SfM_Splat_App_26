import { useState, useEffect, useCallback } from 'react';

// This should match the AppConfig model in backend/core/config.py
export interface AppConfig {
  tools: {
    /** The one binary behind steps 3 to 5 (CLAUDE.md §5.1). `rc_exe_path` and
     *  `lfs_exe_path` went with the two CUDA tools this project exists to
     *  avoid (§12, 2026-08-27) and are no longer written or read. */
    spirula_exe_path: string | null;
    /** Where `spirula sam` and `spirula geometry` look for their checkpoints.
     *  Empty means the tool's own directory, which is where its automatic
     *  fetch lands — see the Checkpoints section. */
    spirula_model_cache: string | null;
    ffmpeg_path: string;
    ffmpeg_hwaccel: string;
    /** Optional: `@playcanvas/splat-transform` for the compressed exports
     *  (CLAUDE.md §7.6c). Empty means "find it in tools/, then on PATH". */
    splat_transform_path: string | null;
  };
}

export const useSettings = () => {
  const [settings, setSettings] = useState<AppConfig | null>(null);

  const fetchSettings = useCallback(async () => {
    try {
      const response = await fetch('/api/settings/');
      if (response.ok) {
        const data = await response.json();
        setSettings(data);
      }
    } catch (error) {
      console.error("Failed to fetch settings:", error);
    }
  }, []);

  const updateSettings = useCallback(async (newSettings: Partial<AppConfig>) => {
    try {
      const response = await fetch('/api/settings/', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(newSettings),
      });
      if (response.ok) {
        const data = await response.json();
        setSettings(data.config);
      }
    } catch (error) {
      console.error("Failed to update settings:", error);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  return { settings, fetchSettings, updateSettings };
};
