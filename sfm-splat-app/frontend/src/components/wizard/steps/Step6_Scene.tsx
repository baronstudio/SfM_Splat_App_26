import React from 'react';
import StepShell from './StepShell';

const Step6_Scene: React.FC = () => (
  <StepShell
    step={6}
    title="Export + Scene"
    command="blender --background --python blender_splatforge.py"
    writes="export/scene.blend and export/README_SPLATFORGE.txt"
    spec="§7.10"
    todo="P2"
  >
    <p className="text-muted-foreground text-sm">
      Steps 5 and 6 share <code className="font-mono">export/</code> — step 5
      fills it, step 6 adds the Blender scene to it — so resetting step 5
      necessarily takes step 6 with it (CLAUDE.md §14.1).
    </p>
  </StepShell>
);

export default Step6_Scene;
