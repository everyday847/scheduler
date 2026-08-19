// src/web/src/types.ts

export type PaletteRuleType =
  | 'shift_total'
  | 'staffing_per_week'
  | 'coverage_target'
  | 'max_consecutive'
  | 'block_rotation'
  | 'rotation_continuity'
  | 'prerequisite'
  | 'windowed_balance'
  | 'no_isolated_week'
  | 'group_count_balance'
  | 'night_spacing'
  | 'night_blocked_services'
  | 'night_holiday_eligibility'
  | 'night_penalties'
  | 'night_sunday_following'
  | 'weekend_spacing'
  | 'weekend_blocked_services'
  | 'weekend_stroke_eligibility'
  | 'weekend_penalties';

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
  block_offset?: number;
  nf_days_per_block?: [number, number];
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

export interface NoIsolatedWeekRule extends PaletteRuleBase {
  type: 'no_isolated_week';
  shifts: string[];
}

export interface GroupCountBalanceRule extends PaletteRuleBase {
  type: 'group_count_balance';
  shifts: string[];
  max_difference: number;
  window?: [number, number];
}

export interface NightSpacingParams {
  maxNights: number;
  windowDays: number;
}

export interface NightBlockedServicesParams {
  exactServices: string[];
  substringServices: string[];
}

export interface NightHolidayEligibilityParams {
  allowedServices: string[];
}

export interface NightPenaltiesParams {
  weights: Record<string, number>;
}

export interface NightSundayFollowingParams {
  preferredServices: string[];
}

export interface WeekendSpacingParams {
  maxWeekends: number;
  windowWeeks: number;
}

export interface WeekendBlockedServicesParams {
  exactServices: string[];
  substringServices: string[];
}

export interface WeekendStrokeEligibilityParams {
  eligibleServices: string[];
}

export interface WeekendPenaltiesParams {
  weights: Record<string, number>;
}

export interface NightSpacingRule extends PaletteRuleBase {
  type: 'night_spacing';
  description?: string;
  params: NightSpacingParams;
}

export interface NightBlockedServicesRule extends PaletteRuleBase {
  type: 'night_blocked_services';
  description?: string;
  params: NightBlockedServicesParams;
}

export interface NightHolidayEligibilityRule extends PaletteRuleBase {
  type: 'night_holiday_eligibility';
  description?: string;
  params: NightHolidayEligibilityParams;
}

export interface NightPenaltiesRule extends PaletteRuleBase {
  type: 'night_penalties';
  description?: string;
  params: NightPenaltiesParams;
}

export interface NightSundayFollowingRule extends PaletteRuleBase {
  type: 'night_sunday_following';
  description?: string;
  params: NightSundayFollowingParams;
}

export interface WeekendSpacingRule extends PaletteRuleBase {
  type: 'weekend_spacing';
  description?: string;
  params: WeekendSpacingParams;
}

export interface WeekendBlockedServicesRule extends PaletteRuleBase {
  type: 'weekend_blocked_services';
  description?: string;
  params: WeekendBlockedServicesParams;
}

export interface WeekendStrokeEligibilityRule extends PaletteRuleBase {
  type: 'weekend_stroke_eligibility';
  description?: string;
  params: WeekendStrokeEligibilityParams;
}

export interface WeekendPenaltiesRule extends PaletteRuleBase {
  type: 'weekend_penalties';
  description?: string;
  params: WeekendPenaltiesParams;
}

export type PaletteRule =
  | ShiftTotalRule
  | StaffingPerWeekRule
  | CoverageTargetRule
  | MaxConsecutiveRule
  | BlockRotationRule
  | RotationContinuityRule
  | PrerequisiteRule
  | WindowedBalanceRule
  | NoIsolatedWeekRule
  | GroupCountBalanceRule
  | NightSpacingRule
  | NightBlockedServicesRule
  | NightHolidayEligibilityRule
  | NightPenaltiesRule
  | NightSundayFollowingRule
  | WeekendSpacingRule
  | WeekendBlockedServicesRule
  | WeekendStrokeEligibilityRule
  | WeekendPenaltiesRule;

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

export type CallRuleType =
  | 'per_fellow_shift_total'
  | 'specific_night_assignment'
  | 'blocked_night'
  | 'specific_weekend_assignment'
  | 'blocked_weekend'
  | 'friday_call_assignment'
  | 'group_night_requirement'
  | 'weekend_stroke_prerequisite'
  | 'weekend_ncc_prerequisite'
  | 'dual_stroke_window';

export interface CallRule {
  type: CallRuleType;
  name: string;
  active: boolean;
  fellow?: string;
  shifts?: string[];
  relation?: Relation;
  count?: number;
  strength?: Strength;
  dates?: string[];
  weeks?: number[];
  groups?: string[];
  exempt_fellows?: string[];
  exempt_groups?: string[];
  window?: [number, number];
  supervisors?: string[];
  role?: string;
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
  no_isolated_week: 'No Isolated Week',
  group_count_balance: 'Group Count Balance',
  night_spacing: 'Night Spacing',
  night_blocked_services: 'Night Blocked Services',
  night_holiday_eligibility: 'Night Holiday Eligibility',
  night_penalties: 'Night Penalties',
  night_sunday_following: 'Night Sunday Following',
  weekend_spacing: 'Weekend Spacing',
  weekend_blocked_services: 'Weekend Blocked Services',
  weekend_stroke_eligibility: 'Weekend Stroke Eligibility',
  weekend_penalties: 'Weekend Penalties',
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
  no_isolated_week: 'Forbid lone 1-week stints of a shift',
  group_count_balance: 'Balance a shift count across fellows',
  night_spacing: 'Limit night call frequency',
  night_blocked_services: 'Block specified services from night call',
  night_holiday_eligibility: 'Restrict night call to allowed services',
  night_penalties: 'Weight penalties for night call assignments',
  night_sunday_following: 'Preferred services for Sunday following night call',
  weekend_spacing: 'Limit weekend call frequency',
  weekend_blocked_services: 'Block specified services from weekend call',
  weekend_stroke_eligibility: 'Restrict weekend stroke call to eligible services',
  weekend_penalties: 'Weight penalties for weekend call assignments',
};
