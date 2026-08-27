import React from 'react';
import StepShell from './StepShell';

const Step3_Sfm: React.FC = () => (
  <StepShell
    step={3}
    title="SfM"
    command="spirula --lang en sfm auto frames/ -o sfm/"
    writes="sfm/{features/, matches.bin, sparse/0..N, sfm_result.json}"
    spec="§7.1–7.2"
    todo="P1.4"
  />
);

export default Step3_Sfm;
