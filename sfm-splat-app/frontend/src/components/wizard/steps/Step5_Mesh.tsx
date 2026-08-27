import React from 'react';
import StepShell from './StepShell';

const Step5_Mesh: React.FC = () => (
  <StepShell
    step={5}
    title="Mesh"
    command="spirula --lang en mesh &lt;ckpt&gt; --data sfm/ --output mesh/mesh"
    writes="mesh/ and export/"
    spec="§7.8"
    todo="P2"
  />
);

export default Step5_Mesh;
