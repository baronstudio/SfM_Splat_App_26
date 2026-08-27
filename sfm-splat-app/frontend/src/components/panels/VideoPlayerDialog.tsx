import React, { useState } from 'react';
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { AlertTriangle, X } from 'lucide-react';
import { staticUrl } from '@/api/client';
import type { SourceFile } from '@/types';
import { formatDuration, sourceLine } from './sourceFormat';

interface VideoPlayerDialogProps {
  source: SourceFile | null;
  onClose: () => void;
}

/**
 * Mini player for one source video, over the step it was opened from.
 *
 * It streams the file straight from the `/static` mount — Starlette answers
 * range requests, so seeking works without copying gigabytes anywhere. What it
 * cannot do is decode for the browser: the DJI rushes this app is built around
 * are HEVC, which Chrome plays only where the platform decoder does. So the
 * failure is handled rather than left as a black rectangle — the poster frame
 * and the codec that caused it are more use than an empty player.
 *
 * Freely dismissible, unlike ProjectOperationDialog (§14.2): nothing is being
 * written while it is open.
 */
export const VideoPlayerDialog: React.FC<VideoPlayerDialogProps> = ({ source, onClose }) => {
  const [failed, setFailed] = useState(false);

  if (!source) return null;

  const probe = source.probe;

  return (
    <DialogPrimitive.Root open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-[60] bg-black/80 backdrop-blur-sm" />
        <DialogPrimitive.Content
          className="fixed left-1/2 top-1/2 z-[61] w-[min(56rem,92vw)] -translate-x-1/2 -translate-y-1/2
                     rounded-lg border border-slate-700 bg-slate-900 shadow-xl overflow-hidden"
        >
          <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-800">
            <DialogPrimitive.Title className="flex-1 text-sm font-medium text-slate-100 truncate">
              {source.filename}
            </DialogPrimitive.Title>
            <DialogPrimitive.Close
              className="text-slate-500 hover:text-slate-100 transition-colors"
              aria-label="Close"
            >
              <X className="w-4 h-4" />
            </DialogPrimitive.Close>
          </div>

          <DialogPrimitive.Description className="sr-only">
            Preview of the source video {source.filename}
          </DialogPrimitive.Description>

          <div className="bg-black flex items-center justify-center min-h-[16rem]">
            {failed ? (
              <div className="flex flex-col items-center gap-3 p-6 text-center">
                {source.thumb_url && (
                  <img
                    src={staticUrl(source.thumb_url)}
                    alt=""
                    className="rounded border border-slate-700 max-h-48"
                  />
                )}
                <p className="flex items-center gap-2 text-sm text-amber-400">
                  <AlertTriangle className="w-4 h-4 shrink-0" />
                  This browser cannot decode {probe?.codec ?? 'this file'}
                  {probe?.pix_fmt ? ` (${probe.pix_fmt})` : ''}.
                </p>
                <p className="text-xs text-slate-500 max-w-md">
                  The frame above is the poster FFmpeg extracted, so the file itself is
                  readable — only the browser is not. Extraction and curation decode it
                  through FFmpeg and are unaffected.
                </p>
              </div>
            ) : (
              /* key on the URL so switching source resets the element rather
                 than seeking the previous one's buffer */
              <video
                key={source.url}
                src={staticUrl(source.url)}
                poster={source.thumb_url ? staticUrl(source.thumb_url) : undefined}
                controls
                autoPlay
                preload="metadata"
                onError={() => setFailed(true)}
                className="max-h-[70vh] w-full"
              />
            )}
          </div>

          <div className="flex items-baseline justify-between gap-4 px-4 py-2 text-xs text-slate-400 border-t border-slate-800">
            <span className="truncate font-mono">{sourceLine(source)}</span>
            {probe?.duration_s != null && (
              <span className="shrink-0 tabular-nums text-slate-500">
                {formatDuration(probe.duration_s)}
              </span>
            )}
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
};

export default VideoPlayerDialog;
