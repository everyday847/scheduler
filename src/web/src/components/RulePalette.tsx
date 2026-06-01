import React from 'react';
import { PaletteRule, PaletteRuleType, PALETTE_TYPE_LABELS, PALETTE_TYPE_DESCRIPTIONS } from '../types';

type Props = {
  group: string;
  onAdd: (rule: PaletteRule) => void;
  onCancel: () => void;
};

const PALETTE_TYPES: PaletteRuleType[] = [
  'shift_total', 'staffing_per_week', 'coverage_target', 'max_consecutive',
  'block_rotation', 'rotation_continuity', 'prerequisite', 'windowed_balance',
];

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
  }
}

export function RulePalette({ group, onAdd, onCancel }: Props) {
  return (
    <div className="rule-palette">
      <div className="palette-header">
        <h3>Add a rule for {group}</h3>
        <button onClick={onCancel}>Cancel</button>
      </div>
      <div className="palette-options">
        {PALETTE_TYPES.map(type => (
          <button key={type} className="palette-option"
            onClick={() => onAdd(createDefaultRule(type, group))}>
            <span className="palette-option-name">{PALETTE_TYPE_LABELS[type]}</span>
            <span className="palette-option-desc">{PALETTE_TYPE_DESCRIPTIONS[type]}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
