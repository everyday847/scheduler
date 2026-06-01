import React from 'react';
import { PaletteRule, PALETTE_TYPE_LABELS, RuleFeasibility } from '../types';
import { FeasibilityPips } from './FeasibilityPips';

type Props = {
  rule: PaletteRule;
  onToggleActive: (ruleName: string) => void;
  onToggleStrength: (ruleName: string) => void;
  onEdit?: () => void;
  feasibility?: RuleFeasibility;
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
    case 'night_spacing':
      return `Max ${rule.params.maxNights} night(s) per ${rule.params.windowDays} days`;
    case 'night_blocked_services': {
      const parts: string[] = [];
      if (rule.params.exactServices?.length) parts.push(`exact: ${rule.params.exactServices.join(', ')}`);
      if (rule.params.substringServices?.length) parts.push(`substr: ${rule.params.substringServices.join(', ')}`);
      return parts.length ? parts.join('; ') : 'No blocked services';
    }
    case 'night_holiday_eligibility':
      return rule.params.allowedServices?.length ? `Allowed: ${rule.params.allowedServices.join(', ')}` : 'No eligible services';
    case 'night_penalties': {
      const weights = rule.params.weights;
      if (!weights || Object.keys(weights).length === 0) return 'No penalties configured';
      return Object.entries(weights).map(([k, v]) => `${k}=${v}`).join(', ');
    }
    case 'night_sunday_following':
      return rule.params.preferredServices?.length ? `Preferred: ${rule.params.preferredServices.join(', ')}` : 'No preferred services';
    case 'weekend_spacing':
      return `Max ${rule.params.maxWeekends} weekend(s) per ${rule.params.windowWeeks} weeks`;
    case 'weekend_blocked_services': {
      const parts2: string[] = [];
      if (rule.params.exactServices?.length) parts2.push(`exact: ${rule.params.exactServices.join(', ')}`);
      if (rule.params.substringServices?.length) parts2.push(`substr: ${rule.params.substringServices.join(', ')}`);
      return parts2.length ? parts2.join('; ') : 'No blocked services';
    }
    case 'weekend_stroke_eligibility':
      return rule.params.eligibleServices?.length ? `Eligible: ${rule.params.eligibleServices.join(', ')}` : 'No eligible services';
    case 'weekend_penalties': {
      const weights2 = rule.params.weights;
      if (!weights2 || Object.keys(weights2).length === 0) return 'No penalties configured';
      return Object.entries(weights2).map(([k, v]) => `${k}=${v}`).join(', ');
    }
    default:
      return '';
  }
}

export function RuleCard({ rule, onToggleActive, onToggleStrength, onEdit, feasibility }: Props) {
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
        {onEdit && <button className="edit-btn" onClick={onEdit}>Edit</button>}
        <FeasibilityPips
          solo={feasibility?.solo || 'unchecked'}
          pairwise={feasibility?.pairwise || 'unchecked'}
        />
      </div>
      <div className="rule-card-summary">
        <span className="rule-type-label">{label}</span>
        <span className="rule-summary-text">{ruleSummary(rule)}</span>
      </div>
    </div>
  );
}
