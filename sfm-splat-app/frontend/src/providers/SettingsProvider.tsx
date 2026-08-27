import React, { createContext } from 'react';
import { useSettings } from '@/hooks/useSettings';

export const SettingsContext = createContext<ReturnType<typeof useSettings> | null>(null);

export const SettingsProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const settings = useSettings();
  return (
    <SettingsContext.Provider value={settings}>
      {children}
    </SettingsContext.Provider>
  );
};
