import React from 'react';
import { NfParameters } from '../types';

// Known scalar dials (label, key, kind). Extend as needed.
const SCALAR_DIALS: { key: string; label: string; kind: 'bool' | 'int' | 'text' }[] = [
  { key: 'call_tier_day_granular', label: 'Call tier (day-granular)', kind: 'bool' },
  { key: 'nf_week_off_cap', label: 'Week off cap', kind: 'int' },
  { key: 'nf_one_third_nf_weight', label: '1/3 balance weight', kind: 'int' },
  { key: 'nf_ncc1_continuity', label: 'NCC1 continuity', kind: 'text' },
  { key: 'nf_max_consecutive_call_days', label: 'Max consecutive call days', kind: 'int' },
];

function roundHalfEven(x: number): number {
  const f = Math.floor(x), d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

function deriveNfBand(p: { ncc_weeks?: [number, number]; density?: [number, number]; nf_fraction?: number; nf_tolerance?: number }): string {
  if (!p.ncc_weeks || !p.density) return '(needs weeks + density)';
  const frac = p.nf_fraction ?? 1 / 3;
  const tol = p.nf_tolerance ?? 0;
  const sLo = Math.ceil(p.ncc_weeks[0] * p.density[0]);
  const sHi = Math.floor(p.ncc_weeks[1] * p.density[1]);
  const nLo = Math.max(0, roundHalfEven(sLo * frac) - tol);
  const nHi = roundHalfEven(sHi * frac) + tol;
  return `service [${sLo}, ${sHi}] → NF [${nLo}, ${nHi}]`;
}

type Props = {
  solverOptions: Record<string, any>;
  nfParameters: NfParameters;
  groups: string[];
  onChange: (solverOptions: Record<string, any>, nfParameters: NfParameters) => void;
};

export function SolverOptionsEditor({ solverOptions, nfParameters, groups, onChange }: Props) {
  const setDial = (k: string, v: any) => onChange({ ...solverOptions, [k]: v }, nfParameters);
  const setParam = (g: string, patch: any) =>
    onChange(solverOptions, { ...nfParameters, [g]: { ...nfParameters[g], ...patch } });
  return (
    <div className="rules-section">
      <h2>Solver Options</h2>
      <div className="rule-list">
        {SCALAR_DIALS.map(d => (
          <div key={d.key} className="rule-card">
            <span className="rule-card-title">{d.label}</span>
            {d.kind === 'bool' && (
              <input type="checkbox" checked={!!solverOptions[d.key]}
                onChange={e => setDial(d.key, e.target.checked)} />)}
            {d.kind === 'int' && (
              <input type="number" value={solverOptions[d.key] ?? ''}
                onChange={e => setDial(d.key, e.target.value === '' ? undefined : Number(e.target.value))} />)}
            {d.kind === 'text' && (
              <input type="text" value={solverOptions[d.key] ?? ''}
                onChange={e => setDial(d.key, e.target.value || undefined)} />)}
          </div>
        ))}
      </div>
      <h2>NF Parameters (derived bands)</h2>
      <div className="rule-list">
        {groups.map(g => {
          const p = nfParameters[g] || {};
          return (
            <div key={g} className="rule-card">
              <span className="rule-card-title">{g}</span>
              <label>weeks lo/hi
                <input type="number" value={p.ncc_weeks?.[0] ?? ''}
                  onChange={e => setParam(g, { ncc_weeks: [Number(e.target.value), p.ncc_weeks?.[1] ?? 0] })} />
                <input type="number" value={p.ncc_weeks?.[1] ?? ''}
                  onChange={e => setParam(g, { ncc_weeks: [p.ncc_weeks?.[0] ?? 0, Number(e.target.value)] })} />
              </label>
              <label>density lo/hi
                <input type="number" step="0.1" value={p.density?.[0] ?? ''}
                  onChange={e => setParam(g, { density: [Number(e.target.value), p.density?.[1] ?? 0] })} />
                <input type="number" step="0.1" value={p.density?.[1] ?? ''}
                  onChange={e => setParam(g, { density: [p.density?.[0] ?? 0, Number(e.target.value)] })} />
              </label>
              <label>tolerance
                <input type="number" value={p.nf_tolerance ?? ''}
                  onChange={e => setParam(g, { nf_tolerance: e.target.value === '' ? undefined : Number(e.target.value) })} />
              </label>
              <span className="rule-card-desc">{deriveNfBand(p)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
