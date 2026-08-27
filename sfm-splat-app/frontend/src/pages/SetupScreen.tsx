import React from 'react';
import { useSettings } from '@/hooks/useSettings';
import { Button } from '@/components/ui/button';

const ToolStatus: React.FC = () => {
    const { settings } = useSettings();

    if (!settings) {
        return <div>Loading tool status...</div>;
    }

    const tools = [
        { name: 'FFmpeg', path: settings.tools.ffmpeg_path },
        { name: 'RealityScan', path: settings.tools.rc_exe_path },
        { name: 'LichtFeld Studio', path: settings.tools.lfs_exe_path },
        { name: 'Blender', path: settings.tools.blender_exe_path },
    ];

    return (
        <div className="border border-gray-700 rounded-lg p-4 mt-6 bg-gray-800/30 w-full max-w-md mx-auto">
            <h2 className="text-lg font-semibold text-center mb-4 text-white">
                🔧 Tools
            </h2>
            <div className="font-mono text-sm space-y-2">
                {tools.map(tool => (
                    <div key={tool.name} className="flex justify-between items-center">
                        <span className="text-gray-300">{tool.name.padEnd(18, ' ')}</span>
                        {tool.path ? (
                            <span className="text-green-400">✅ configured</span>
                        ) : (
                            <span className="text-red-400">❌ not configured</span>
                        )}
                    </div>
                ))}
            </div>
            <p className="text-xs text-gray-500 mt-4 text-center">
                RealityScan and LichtFeld Studio are required to run the pipeline. Set their
                paths in Settings.
            </p>
        </div>
    );
};


export const SetupScreen: React.FC<{ onProceed?: () => void }> = ({ onProceed }) => {
    const { settings } = useSettings();
    const canProceed =
        settings != null && !!settings.tools.rc_exe_path && !!settings.tools.lfs_exe_path;

    return (
        <div className="min-h-screen bg-gray-900 text-white flex flex-col items-center justify-center p-4">
            <h1 className="text-4xl font-bold mb-8">3DGS Pipeline Setup</h1>
            <ToolStatus />
            <Button className="mt-8" disabled={!canProceed} onClick={onProceed}>
                Proceed to Pipeline
            </Button>
        </div>
    );
};
