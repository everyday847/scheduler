import React from 'react';
import { PaletteRule, PALETTE_TYPE_LABELS } from '../types';
import { FeasibilityPips } from './FeasibilityPips';

type Props = {
  rule: PaletteRule;
  onToggleActive: (ruleName: string) => void;
  onToggleStrength: (ruleName: string) => void;
};

function ruleSummary(rule: PaletteRule): string {
  switch (rule.type) {
    case 'max_consecutive':
      return `${rule.shifts.join(', ')} ≤ ${rule.max_weeks} consecutive weeks`;
    case 'block_rotation':
      return `${rule.shifts.join(', ')} in ${rule.block_size}-week blocks`;
    case 'rotation_continuity':
      return `${rule.block_size}-week blocks: ${rule.choices.map(c => c.join('+')).join(' or ')}`;
    case 'coverage_target':
      return `${rule.shifts.join(', ')} — max ${rule.max_uncovered_weeks} uncovered weeks`;
    case 'prerequisite':
      return `${rule.min_prerequisite_weeks} weeks of ${rule.prerequisite_shifts.join('/')} before ${rule.target_shifts.join('/')}`;
    case 'windowed_balance':
      return `${rule.shifts.join(', ')} balance ≤ ${rule.max_difference} between wks ${rule.window_a[0]}–${rule.window_a[1]} and ${rule.window_b[0]}–${rule.window_b[1]}`;
    case 'staffing_per_week':
      return `${rule.relation} ${rule.count} on ${rule.shifts.join(', ')} per week`;
    default:
      return '';
  }
}

export function RuleCard({ rule, onToggleActive, onToggleStrength }: Props) {
  const label = PALETTE_TYPE_LABELS[rule.type as keyof typeof PALETTE_TYPE_LABELS] || rule.type;

  return (
    <div className={`rule-card ${rule.active ? '' : 'rule-inactive'}`}>
      <div className="rule-card-header">
        <input type="checkbox" checked={rule.active} onChange={() => onToggleActive(rule.name)} />
        <span className="rule-card-name">{rule.name}</span>
        <button className={`strength-toggle ${rule.strength}`}
          onClick={() => onToggleStrength(rule.name)}>
          {rule.strength}
        </button>
        <FeasibilityPips solo="unchecked" pairwise="unchecked" />
      </div>
      <div className="rule-card-summary">
        <span className="rule-type-label">{label}</span>
        <span className="rule-summary-text">{ruleSummary(rule)}</span>
      </div>
    </div>
  );
}
