import React, { useState } from 'react';
import { ShiftTotalRule, Relation, RuleFeasibility, PaletteRule } from '../types';
import { FeasibilityPips } from './FeasibilityPips';

type Props = {
  rules: ShiftTotalRule[];
  feasibility: Record<string, RuleFeasibility>;
  allShifts: string[];
  group: string;
  onToggleActive: (ruleName: string) => void;
  onToggleStrength: (ruleName: string) => void;
  onChangeCount: (ruleName: string, count: number) => void;
  onChangeRelation: (ruleName: string, relation: Relation) => void;
  onUpdateRule: (ruleName: string, updated: PaletteRule) => void;
  onAddRule: (rule: PaletteRule) => void;
  onRemoveRule: (ruleName: string) => void;
};

export function CoverageTotalsTable({
  rules, feasibility, allShifts, group,
  onToggleActive, onToggleStrength, onChangeCount, onChangeRelation,
  onUpdateRule, onAddRule, onRemoveRule,
}: Props) {
  const [editingShifts, setEditingShifts] = useState<string | null>(null);

  return (
    <table className="config-table totals-table">
      <thead>
        <tr>
          <th style={{width: '2rem'}}></th>
          <th>Shifts</th>
          <th style={{width: '6rem'}}>Relation</th>
          <th style={{width: '4rem'}}>Weeks</th>
          <th style={{width: '4rem'}}>Strength</th>
          <th style={{width: '5rem'}}></th>
        </tr>
      </thead>
      <tbody>
        {rules.map((r) => (
          <tr key={r.name} className={r.active ? '' : 'rule-inactive'}>
            <td>
              <input type="checkbox" checked={r.active} onChange={() => onToggleActive(r.name)} />
            </td>
            <td>
              {editingShifts === r.name ? (
                <ShiftPicker
                  selected={r.shifts}
                  allShifts={allShifts}
                  onDone={(shifts) => {
                    onUpdateRule(r.name, { ...r, shifts });
                    setEditingShifts(null);
                  }}
                  onCancel={() => setEditingShifts(null)}
                />
              ) : (
                <span className="shifts-cell" onClick={() => setEditingShifts(r.name)} title="Click to edit shifts">
                  {r.shifts.join(', ')}
                  {r.window && <span className="window-tag">wks {r.window[0]}–{r.window[1]}</span>}
                </span>
              )}
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
            <td className="totals-actions">
              <FeasibilityPips
                solo={feasibility[r.name]?.solo || 'unchecked'}
                pairwise={feasibility[r.name]?.pairwise || 'unchecked'}
              />
              <button className="remove-row-btn" onClick={() => onRemoveRule(r.name)}
                title="Remove this total">×</button>
            </td>
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr>
          <td colSpan={6}>
            <button className="add-btn" onClick={() => {
              onAddRule({
                name: `${group}: New Shift Total`,
                type: 'shift_total',
                groups: [group],
                shifts: [],
                relation: 'exactly',
                count: 0,
                strength: 'hard',
                active: true,
              });
            }}>+ add total</button>
          </td>
        </tr>
      </tfoot>
    </table>
  );
}

function ShiftPicker({ selected, allShifts, onDone, onCancel }: {
  selected: string[];
  allShifts: string[];
  onDone: (shifts: string[]) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState<string[]>([...selected]);

  return (
    <div className="shift-picker">
      <div className="chip-select">
        {allShifts.map(s => (
          <label key={s} className="chip-option">
            <input type="checkbox"
              checked={draft.includes(s)}
              onChange={e => {
                setDraft(e.target.checked ? [...draft, s] : draft.filter(x => x !== s));
              }} />
            <span>{s}</span>
          </label>
        ))}
      </div>
      <div className="shift-picker-actions">
        <button className="primary" onClick={() => onDone(draft)}>Done</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
