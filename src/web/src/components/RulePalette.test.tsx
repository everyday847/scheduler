import { createForbidCoverageRule } from './RulePalette';

test('forbid-coverage rule is a windowed at_most-0 staffing rule', () => {
  const r: any = createForbidCoverageRule('CCM');
  expect(r.type).toBe('staffing_per_week');
  expect(r.relation).toBe('at_most');
  expect(r.count).toBe(0);
  expect(r.shifts).toEqual(['NCC']);
  expect(r.window).toEqual([0, 1]);
  expect(r.groups).toEqual(['CCM']);
});
