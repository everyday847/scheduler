import React from 'react';

export type PipStatus = 'unchecked' | 'pass' | 'fail' | 'checking';

export function FeasibilityPips({ solo, pairwise }: { solo: PipStatus; pairwise: PipStatus }) {
  return (
    <span className="feasibility-pips">
      <span className={`pip pip-${solo}`} title={`Solo: ${solo}`} />
      <span className={`pip pip-${pairwise}`} title={`Pairwise: ${pairwise}`} />
    </span>
  );
}
