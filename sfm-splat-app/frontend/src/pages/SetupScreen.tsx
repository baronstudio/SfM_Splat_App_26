import React from 'react';
import { useSettings } from '@/hooks/useSettings';
import { useModels } from '@/hooks/useModels';
import { Button } from '@/components/ui/button';

/**
 * First run, before there is a project to open (`App.tsx` skips it once one
 * exists).
 *
 * It used to gate Proceed on `rc_exe_path && lfs_exe_path` — RealityScan and
 * LichtFeld Studio, the CUDA pair this project exists to be free of (CLAUDE.md
 * §12, 2026-08-27). Those keys left `config.json` with the tools, so the button
 * could never enable again: a fresh install was a dead end on its own first
 * screen. The two things a pipeline actually needs here are **spirula.exe and
 * FFmpeg**, and nothing else is required to reach step 5.
 *
 * The checkpoints are shown and are deliberately **not** a gate. Nothing in the
 * default route wants one: `sfm`, `train` and `mesh` need no model at all, and
 * `sam mask` — the lens-border pass of §7.4 — needs none either. They are what
 * the optional passes cost, and that is what this says.
 */

const Line: React.FC<{ name: string; ok: boolean; detail: string }> = ({ name, ok, detail }) => (
    <div className="flex items-baseline justify-between gap-4">
        <span className="text-gray-300">{name}</span>
        <span className="text-right">
            <span className={ok ? 'text-green-400' : 'text-red-400'}>
                {ok ? '✅' : '❌'} {detail}
            </span>
        </span>
    </div>
);

export const SetupScreen: React.FC<{ onProceed?: () => void }> = ({ onProceed }) => {
    const { settings } = useSettings();
    // The catalogue is read once here: this screen reports, it does not install.
    // Downloading is the setup panel's Checkpoints section, which is reachable
    // from the gear icon as soon as Proceed is taken.
    const { overview } = useModels(true);

    if (!settings) {
        return (
            <div className="min-h-screen bg-gray-900 text-white flex items-center justify-center">
                Reading the installation…
            </div>
        );
    }

    const hasSpirula = !!settings.tools.spirula_exe_path;
    const hasFfmpeg = !!settings.tools.ffmpeg_path;
    const canProceed = hasSpirula && hasFfmpeg;
    const installed = (overview?.models ?? []).filter((m) => m.state === 'ready');

    return (
        <div className="min-h-screen bg-gray-900 text-white flex flex-col items-center justify-center p-4">
            <h1 className="text-4xl font-bold mb-8">SfM Splat Pipeline</h1>

            <div className="border border-gray-700 rounded-lg p-4 bg-gray-800/30 w-full max-w-lg">
                <h2 className="text-lg font-semibold text-center mb-4 text-white">🔧 Required</h2>
                <div className="font-mono text-sm space-y-2">
                    <Line
                        name="Spirula Studio"
                        ok={hasSpirula}
                        detail={hasSpirula ? 'configured' : 'set its path in Settings'}
                    />
                    <Line
                        name="FFmpeg"
                        ok={hasFfmpeg}
                        detail={hasFfmpeg ? 'configured' : 'set its path in Settings'}
                    />
                </div>
                <p className="text-xs text-gray-500 mt-4">
                    One binary drives SfM, training and meshing; FFmpeg does everything before
                    it. Nothing else is needed to take a video through to a mesh.
                </p>

                <h2 className="text-lg font-semibold text-center mt-6 mb-3 text-white">
                    🧠 Checkpoints — optional
                </h2>
                <div className="font-mono text-sm space-y-2">
                    <Line
                        name="Installed"
                        ok={installed.length > 0}
                        detail={
                            installed.length > 0
                                ? `${installed.length} of ${overview?.models.length ?? 0}`
                                : 'none yet'
                        }
                    />
                </div>
                <p className="text-xs text-gray-500 mt-4">
                    Only the object-tracking masks and the depth/normal pass want one. Install
                    them from the gear icon → Checkpoints, whenever you first need them.
                </p>
            </div>

            <Button className="mt-8" disabled={!canProceed} onClick={onProceed}>
                Proceed to Pipeline
            </Button>
            {!canProceed && (
                <p className="text-xs text-gray-500 mt-3">
                    Set the two paths above in config.json, or from the Settings panel.
                </p>
            )}
        </div>
    );
};
