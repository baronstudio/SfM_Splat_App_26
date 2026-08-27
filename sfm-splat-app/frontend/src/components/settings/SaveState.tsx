import React, { useEffect, useState } from 'react';
import { Check, Loader2, TriangleAlert } from 'lucide-react';

interface SaveStateProps {
  saving: boolean;
  savedAt: number | null;
  error: string | null;
  /** What is being saved, so the hint says which layer it landed in. */
  label?: string;
}

/**
 * The per-project settings save state, in one line.
 *
 * These panels write on every change (CLAUDE.md §4, layer 3) and there is no
 * Save button to tell the user it happened — so the confirmation has to be
 * visible, and an autosave that failed silently is worse than no autosave.
 */
export const SaveState: React.FC<SaveStateProps> = ({
  saving,
  savedAt,
  error,
  label = 'Saved to this project',
}) => {
  const [recent, setRecent] = useState(false);

  useEffect(() => {
    if (!savedAt) return;
    setRecent(true);
    const t = setTimeout(() => setRecent(false), 2500);
    return () => clearTimeout(t);
  }, [savedAt]);

  if (error) {
    return (
      <span className="flex items-center gap-1 text-xs text-red-400" title={error}>
        <TriangleAlert className="h-3.5 w-3.5" />
        not saved
      </span>
    );
  }
  if (saving) {
    return (
      <span className="flex items-center gap-1 text-xs text-slate-400">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        saving…
      </span>
    );
  }
  if (recent) {
    return (
      <span className="flex items-center gap-1 text-xs text-green-400">
        <Check className="h-3.5 w-3.5" />
        {label}
      </span>
    );
  }
  return null;
};

export default SaveState;
