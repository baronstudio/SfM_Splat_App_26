import './index.css'
import { SettingsProvider } from './providers/SettingsProvider';
import { SetupScreen } from './pages/SetupScreen';
import { useState, useContext, useEffect } from 'react';
import { SettingsContext } from './providers/SettingsProvider';
import { MainPage } from './pages/MainPage';
import { useWebSocket } from './hooks/useWebSocket';
import { useProjects } from './hooks/useProjects';
import { usePipelineStore } from './store/pipelineStore';

const PROCEEDED_KEY = '3dgs_proceeded';

function AppContent() {
  const settingsContext = useContext(SettingsContext);
  const [proceeded, setProceeded] = useState<boolean>(
    () => localStorage.getItem(PROCEEDED_KEY) === 'true'
  );

  // Initialize WebSocket (auto-connects) and projects (auto-fetches) once at App level
  useWebSocket();
  useProjects();

  const projects = usePipelineStore((s) => s.projects);
  const settings = settingsContext?.settings;

  // Auto-skip SetupScreen once projects are loaded from DB
  useEffect(() => {
    if (!proceeded && projects.length > 0) {
      localStorage.setItem(PROCEEDED_KEY, 'true');
      setProceeded(true);
    }
  }, [projects.length, proceeded]);

  const handleProceed = () => {
    localStorage.setItem(PROCEEDED_KEY, 'true');
    setProceeded(true);
  };

  const handleBackToHome = () => {
    localStorage.removeItem(PROCEEDED_KEY);
    setProceeded(false);
  };

  // Show SetupScreen until the user explicitly clicks "Proceed" (or projects exist)
  if (!proceeded) {
    return <SetupScreen onProceed={handleProceed} />;
  }

  // Settings might still be loading after the user proceeds — show a fallback
  if (!settings) {
    return (
      <div className="flex items-center justify-center h-screen bg-slate-900 text-slate-400">
        Loading…
      </div>
    );
  }

  return <MainPage onBackToHome={handleBackToHome} />;
}

function App() {
  return (
    <SettingsProvider>
      <AppContent />
    </SettingsProvider>
  );
}

export default App;


