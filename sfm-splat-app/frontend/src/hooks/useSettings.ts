import { useState, useEffect, useCallback } from 'react';

// This should match the AppConfig model in backend/core/config.py
interface AppConfig {
  tools: {
    rc_exe_path: string | null;
    lfs_exe_path: string | null;
    ffmpeg_path: string;
    ffmpeg_hwaccel: string;
    blender_exe_path: string | null;
    supersplat_url: string;
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
