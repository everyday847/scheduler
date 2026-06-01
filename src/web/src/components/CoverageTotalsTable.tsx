import React from 'react';
import { ShiftTotalRule, Relation, RuleFeasibility } from '../types';
import { FeasibilityPips } from './FeasibilityPips';

type Props = {
  rules: ShiftTotalRule[];
  feasibility: Record<string, RuleFeasibility>;
  onToggleActive: (ruleName: string) => void;
  onToggleStrength: (ruleName: string) => void;
  onChangeCount: (ruleName: string, count: number) => void;
  onChangeRelation: (ruleName: string, relation: Relation) => void;
};

export function CoverageTotalsTable({ rules, feasibility, onToggleActive, onToggleStrength, onChangeCount, onChangeRelation }: Props) {
  if (rules.length === 0) return null;

  return (
    <div className="coverage-totals">
      <h3>Coverage Totals</h3>
      <table className="config-table totals-table">
        <thead>
          <tr>
            <th style={{width: '2rem'}}></th>
            <th>Shifts</th>
            <th style={{width: '6rem'}}>Relation</th>
            <th style={{width: '4rem'}}>Weeks</th>
            <th style={{width: '4rem'}}>Strength</th>
            <th style={{width: '3rem'}}></th>
          </tr>
        </thead>
        <tbody>
          {rules.map((r) => (
            <tr key={r.name} className={r.active ? '' : 'rule-inactive'}>
              <td>
                <input type="checkbox" checked={r.active} onChange={() => onToggleActive(r.name)} />
              </td>
              <td>
                {r.shifts.join(', ')}
                {r.window && <span className="window-tag">wks {r.window[0]}–{r.window[1]}</span>}
              </td>
              <td>
                <select className="relation-select" value={r.relation}
                  onChange={(e) => onChangeRelation(r.name, e.target.value as Relation)}>
                  <option value="exactly">exactly</option>
                  <option value="at_least">at least</option>
                  <option value="at_most">at most</option>
                </select>
              </td>
              <td>
                <input type="number" className="count-input" value={r.count} min={0}
                  onChange={(e) => onChangeCount(r.name, Number(e.target.value))} />
              </td>
              <td>
                <button className={`strength-toggle ${r.strength}`}
                  onClick={() => onToggleStrength(r.name)}>
                  {r.strength}
                </button>
              </td>
              <td>
                <FeasibilityPips
                  solo={feasibility[r.name]?.solo || 'unchecked'}
                  pairwise={feasibility[r.name]?.pairwise || 'unchecked'}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
