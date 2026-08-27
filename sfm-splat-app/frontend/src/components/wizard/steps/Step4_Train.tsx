import React from 'react';
import StepShell from './StepShell';

const Step4_Train: React.FC = () => (
  <StepShell
    step={4}
    title="Train"
    command="spirula --lang en train &lt;preset&gt; --data sfm/ --image-dir frames/ --disable-viewer 1"
    writes="train/run/step-%09d.ckpt/splat.ply"
    spec="§7.6–7.7"
    todo="P1.6"
  />
);

export default Step4_Train;
