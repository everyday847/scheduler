// src/web/src/types.ts

export type PaletteRuleType =
  | 'shift_total'
  | 'staffing_per_week'
  | 'coverage_target'
  | 'max_consecutive'
  | 'block_rotation'
  | 'rotation_continuity'
  | 'prerequisite'
  | 'windowed_balance';

export type Strength = 'hard' | 'soft';
export type Relation = 'exactly' | 'at_least' | 'at_most';

export interface PaletteRuleBase {
  name: string;
  type: PaletteRuleType | 'full_assignment' | 'vacation_request_policy';
  groups?: string[];
  strength: Strength;
  active: boolean;
}

export interface ShiftTotalRule extends PaletteRuleBase {
  type: 'shift_total';
  shifts: string[];
  relation: Relation;
  count: number;
  window?: [number, number];
}

export interface StaffingPerWeekRule extends PaletteRuleBase {
  type: 'staffing_per_week';
  shifts: string[];
  relation: Relation;
  count: number;
  window?: [number, number];
}

export interface CoverageTargetRule extends PaletteRuleBase {
  type: 'coverage_target';
  shifts: string[];
  max_uncovered_weeks: number;
}

export interface MaxConsecutiveRule extends PaletteRuleBase {
  type: 'max_consecutive';
  shifts: string[];
  max_weeks: number;
}

export interface BlockRotationRule extends PaletteRuleBase {
  type: 'block_rotation';
  shifts: string[];
  block_size: number;
}

export interface RotationContinuityRule extends PaletteRuleBase {
  type: 'rotation_continuity';
  block_size: number;
  choices: string[][];
  allow_none: boolean;
}

export interface PrerequisiteRule extends PaletteRuleBase {
  type: 'prerequisite';
  prerequisite_shifts: string[];
  target_shifts: string[];
  min_prerequisite_weeks: number;
}

export interface WindowedBalanceRule extends PaletteRuleBase {
  type: 'windowed_balance';
  shifts: string[];
  window_a: [number, number];
  window_b: [number, number];
  max_difference: number;
}

export type PaletteRule =
  | ShiftTotalRule
  | StaffingPerWeekRule
  | CoverageTargetRule
  | MaxConsecutiveRule
  | BlockRotationRule
  | RotationContinuityRule
  | PrerequisiteRule
  | WindowedBalanceRule;

export type FeasibilityStatus = 'unchecked' | 'pass' | 'fail' | 'checking';

export interface RuleFeasibility {
  solo: FeasibilityStatus;
  pairwise: FeasibilityStatus;
}

export interface AnnualConfig {
  fellow_groups: Record<string, string[]>;
  shifts: string[];
  num_weeks: number;
  horizon_start?: string;
  rules: PaletteRule[];
  night_call: NightCallEntry[];
  weekend_call: WeekendCallEntry[];
  holiday_dates: string[];
  fellow_week_pairs: Record<string, string[]>;
}

export interface NightCallEntry {
  group: string;
  total_nights: number;
  friday_nights: number;
}

export interface WeekendCallEntry {
  group: string;
  ncc_total: number;
  stroke_total: number;
}

export const PALETTE_TYPE_LABELS: Record<PaletteRuleType, string> = {
  shift_total: 'Shift Total',
  staffing_per_week: 'Staffing Per Week',
  coverage_target: 'Coverage Target',
  max_consecutive: 'Max Consecutive',
  block_rotation: 'Block Rotation',
  rotation_continuity: 'Rotation Continuity',
  prerequisite: 'Prerequisite',
  windowed_balance: 'Windowed Balance',
};

export const PALETTE_TYPE_DESCRIPTIONS: Record<PaletteRuleType, string> = {
  shift_total: 'Set how many weeks of a shift',
  staffing_per_week: 'Require N fellows on a shift each week',
  coverage_target: 'A shift should be covered in at least N weeks',
  max_consecutive: 'Limit consecutive weeks on a rotation',
  block_rotation: 'All-or-none within N-week blocks',
  rotation_continuity: 'Stay on one team within blocks',
  prerequisite: 'Complete rotation A before B',
  windowed_balance: 'Balance shifts across two time periods',
};
