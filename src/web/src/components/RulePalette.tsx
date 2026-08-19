import React from 'react';
import { PaletteRule, PaletteRuleType, PALETTE_TYPE_LABELS, PALETTE_TYPE_DESCRIPTIONS } from '../types';

export function createForbidCoverageRule(group: string): PaletteRule {
  return {
    name: `${group}: No coverage (weeks)`,
    type: 'staffing_per_week', groups: [group], strength: 'hard', active: true,
    shifts: ['NCC'], relation: 'at_most', count: 0, window: [0, 1],
  };
}

const WEEKLY_TYPES: PaletteRuleType[] = [
  'shift_total', 'staffing_per_week', 'coverage_target', 'max_consecutive',
  'block_rotation', 'rotation_continuity', 'prerequisite', 'windowed_balance',
  'no_isolated_week', 'group_count_balance',
];

const NIGHT_TYPES: PaletteRuleType[] = [
  'night_spacing', 'night_blocked_services', 'night_holiday_eligibility',
  'night_penalties', 'night_sunday_following',
];

const WEEKEND_TYPES: PaletteRuleType[] = [
  'weekend_spacing', 'weekend_blocked_services', 'weekend_stroke_eligibility',
  'weekend_penalties',
];

type Props = {
  group: string;
  onAdd: (rule: PaletteRule) => void;
  onCancel: () => void;
  category?: 'weekly' | 'night' | 'weekend';
};

function createDefaultRule(type: PaletteRuleType, group: string): PaletteRule {
  const label = PALETTE_TYPE_LABELS[type];
  const base = { name: `${group}: New ${label}`, groups: [group], strength: 'hard' as const, active: true };
  switch (type) {
    case 'shift_total':
      return { ...base, type, shifts: [], relation: 'exactly', count: 0 };
    case 'staffing_per_week':
      return { ...base, type, shifts: [], relation: 'at_least', count: 1 };
    case 'coverage_target':
      return { ...base, type, shifts: [], max_uncovered_weeks: 2 };
    case 'max_consecutive':
      return { ...base, type, shifts: [], max_weeks: 6 };
    case 'block_rotation':
      return { ...base, type, shifts: [], block_size: 4 };
    case 'rotation_continuity':
      return { ...base, type, block_size: 2, choices: [[]], allow_none: true };
    case 'prerequisite':
      return { ...base, type, prerequisite_shifts: [], target_shifts: [], min_prerequisite_weeks: 4 };
    case 'windowed_balance':
      return { ...base, type, shifts: [], window_a: [0, 26], window_b: [26, 52], max_difference: 4 };
    case 'no_isolated_week':
      return { ...base, type, shifts: [] };
    case 'group_count_balance':
      return { ...base, type, shifts: [], max_difference: 4 };
    case 'night_spacing':
      return { ...base, type, description: 'Max 1 night in any 3-consecutive-day window', params: { maxNights: 1, windowDays: 3 } };
    case 'night_blocked_services':
      return { ...base, type, description: 'Block services from night call', params: { exactServices: [], substringServices: [] } };
    case 'night_holiday_eligibility':
      return { ...base, type, description: 'Restrict night call to allowed services', params: { allowedServices: [] } };
    case 'night_penalties':
      return { ...base, type, description: 'Weight penalties for night call', params: { weights: {} } };
    case 'night_sunday_following':
      return { ...base, type, description: 'Preferred services for Sunday after night call', params: { preferredServices: [] } };
    case 'weekend_spacing':
      return { ...base, type, description: 'Limit weekend call frequency', params: { maxWeekends: 1, windowWeeks: 4 } };
    case 'weekend_blocked_services':
      return { ...base, type, description: 'Block services from weekend call', params: { exactServices: [], substringServices: [] } };
    case 'weekend_stroke_eligibility':
      return { ...base, type, description: 'Restrict weekend stroke to eligible services', params: { eligibleServices: [] } };
    case 'weekend_penalties':
      return { ...base, type, description: 'Weight penalties for weekend call', params: { weights: {} } };
  }
}

export function RulePalette({ group, onAdd, onCancel, category }: Props) {
  const types = category === 'night' ? NIGHT_TYPES
    : category === 'weekend' ? WEEKEND_TYPES
    : WEEKLY_TYPES;
  return (
    <div className="rule-palette">
      <div className="palette-header">
        <h3>Add a {category === 'night' ? 'night' : category === 'weekend' ? 'weekend' : ''} rule{category ? '' : ` for ${group}`}</h3>
        <button onClick={onCancel}>Cancel</button>
      </div>
      <div className="palette-options">
        {types.map(type => (
          <button key={type} className="palette-option"
            onClick={() => onAdd(createDefaultRule(type, group))}>
            <span className="palette-option-name">{PALETTE_TYPE_LABELS[type]}</span>
            <span className="palette-option-desc">{PALETTE_TYPE_DESCRIPTIONS[type]}</span>
          </button>
        ))}
        {(!category || category === 'weekly') && (
          <button key="forbid-coverage" className="palette-option"
            onClick={() => onAdd(createForbidCoverageRule(group))}>
            <span className="palette-option-name">No Coverage (weeks)</span>
            <span className="palette-option-desc">Forbid a group from a shift in a week range</span>
          </button>
        )}
      </div>
    </div>
  );
}
