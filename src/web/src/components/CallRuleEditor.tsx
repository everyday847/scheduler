import React, { useState } from 'react';
import { CallRule, CallRuleType, Relation, Strength } from '../types';

type Props = {
  rule: CallRule;
  allShifts: string[];
  allFellows: string[];
  allGroups: string[];
  onSave: (updated: CallRule) => void;
  onCancel: () => void;
  onRemove: () => void;
};

export function CallRuleEditor({ rule, allShifts, allFellows, allGroups, onSave, onCancel, onRemove }: Props) {
  const [draft, setDraft] = useState<CallRule>({ ...rule });

  const update = (patch: Partial<CallRule>) => {
    setDraft(prev => ({ ...prev, ...patch }));
  };

  return (
    <div className="rule-editor">
      <div className="rule-editor-header">
        <h3>Edit Call Rule</h3>
        <div className="rule-editor-actions">
          <button className="primary" onClick={() => onSave(draft)}>Save</button>
          <button onClick={onCancel}>Cancel</button>
          <button className="danger-btn" onClick={onRemove}>Remove</button>
        </div>
      </div>

      <div className="rule-editor-fields">
        <label className="editor-field">
          <span>Name</span>
          <input type="text" value={draft.name}
            onChange={e => update({ name: e.target.value })} />
        </label>

        <label className="editor-field">
          <span>Type</span>
          <select value={draft.type} onChange={e => update({ type: e.target.value as CallRuleType })}>
            <option value="per_fellow_shift_total">Per-Fellow Shift Total</option>
            <option value="specific_night_assignment">Pin Night</option>
            <option value="blocked_night">Block Night</option>
            <option value="specific_weekend_assignment">Pin Weekend</option>
            <option value="blocked_weekend">Block Weekend</option>
            <option value="friday_call_assignment">Pin Friday Call</option>
            <option value="group_night_requirement">Group Night Requirement</option>
            <option value="weekend_stroke_prerequisite">Weekend Stroke Prerequisite</option>
            <option value="weekend_ncc_prerequisite">Weekend NCC Prerequisite</option>
            <option value="dual_stroke_window">Dual Stroke Window</option>
          </select>
        </label>

        {renderTypeFields(draft, allShifts, allFellows, allGroups, update)}

        <label className="editor-field">
          <input type="checkbox" checked={draft.active}
            onChange={e => update({ active: e.target.checked })} />
          <span>Active</span>
        </label>
      </div>
    </div>
  );
}

function renderTypeFields(
  rule: CallRule,
  allShifts: string[],
  allFellows: string[],
  allGroups: string[],
  update: (patch: Partial<CallRule>) => void,
) {
  switch (rule.type) {
    case 'per_fellow_shift_total':
      return (
        <>
          <label className="editor-field">
            <span>Fellow</span>
            <select value={rule.fellow || ''} onChange={e => update({ fellow: e.target.value })}>
              <option value="">-- select fellow --</option>
              {allFellows.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <div className="editor-field">
            <span>Shifts</span>
            <div className="chip-select">
              {allShifts.map(s => (
                <label key={s} className="chip-option">
                  <input type="checkbox"
                    checked={(rule.shifts || []).includes(s)}
                    onChange={e => {
                      const shifts = rule.shifts || [];
                      update({ shifts: e.target.checked ? [...shifts, s] : shifts.filter(x => x !== s) });
                    }} />
                  <span>{s}</span>
                </label>
              ))}
            </div>
          </div>
          <div className="editor-row">
            <label className="editor-field">
              <span>Relation</span>
              <select value={rule.relation || 'exactly'} onChange={e => update({ relation: e.target.value as Relation })}>
                <option value="exactly">exactly</option>
                <option value="at_least">at least</option>
                <option value="at_most">at most</option>
              </select>
            </label>
            <label className="editor-field">
              <span>Count</span>
              <input type="number" value={rule.count ?? 0} min={0}
                onChange={e => update({ count: Number(e.target.value) })} />
            </label>
          </div>
          <label className="editor-field">
            <span>Strength</span>
            <select value={rule.strength || 'hard'} onChange={e => update({ strength: e.target.value as Strength })}>
              <option value="hard">hard</option>
              <option value="soft">soft</option>
            </select>
          </label>
        </>
      );

    case 'specific_night_assignment':
    case 'blocked_night':
      return (
        <>
          <label className="editor-field">
            <span>Fellow</span>
            <select value={rule.fellow || ''} onChange={e => update({ fellow: e.target.value })}>
              <option value="">-- select fellow --</option>
              {allFellows.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label className="editor-field">
            <span>Dates (comma-separated YYYY-MM-DD)</span>
            <input type="text" value={(rule.dates || []).join(', ')}
              onChange={e => update({ dates: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
          </label>
        </>
      );

    case 'friday_call_assignment':
      return (
        <>
          <label className="editor-field">
            <span>Fellow</span>
            <select value={rule.fellow || ''} onChange={e => update({ fellow: e.target.value })}>
              <option value="">-- select fellow --</option>
              {allFellows.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          <label className="editor-field">
            <span>Weeks (comma-separated numbers)</span>
            <input type="text" value={(rule.weeks || []).join(', ')}
              onChange={e => update({ weeks: e.target.value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n)) })} />
          </label>
        </>
      );

    case 'specific_weekend_assignment':
    case 'blocked_weekend':
      return (
        <>
          <label className="editor-field">
            <span>Fellow</span>
            <select value={rule.fellow || ''} onChange={e => update({ fellow: e.target.value })}>
              <option value="">-- select fellow --</option>
              {allFellows.map(f => <option key={f} value={f}>{f}</option>)}
            </select>
          </label>
          {rule.type === 'specific_weekend_assignment' && (
            <label className="editor-field">
              <span>Role</span>
              <select value={rule.role || ''} onChange={e => update({ role: e.target.value })}>
                <option value="NCC1">NCC1</option>
                <option value="NCC2">NCC2</option>
                <option value="Stroke">Stroke</option>
              </select>
            </label>
          )}
          <label className="editor-field">
            <span>Weeks (comma-separated numbers)</span>
            <input type="text" value={(rule.weeks || []).join(', ')}
              onChange={e => update({ weeks: e.target.value.split(',').map(s => parseInt(s.trim())).filter(n => !isNaN(n)) })} />
          </label>
        </>
      );

    case 'group_night_requirement':
      return (
        <>
          <div className="editor-field">
            <span>Allowed Groups</span>
            <div className="chip-select">
              {allGroups.map(g => (
                <label key={g} className="chip-option">
                  <input type="checkbox"
                    checked={(rule.groups || []).includes(g)}
                    onChange={e => {
                      const groups = rule.groups || [];
                      update({ groups: e.target.checked ? [...groups, g] : groups.filter(x => x !== g) });
                    }} />
                  <span>{g}</span>
                </label>
              ))}
            </div>
          </div>
          <label className="editor-field">
            <span>Dates (comma-separated YYYY-MM-DD)</span>
            <input type="text" value={(rule.dates || []).join(', ')}
              onChange={e => update({ dates: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
          </label>
        </>
      );

    case 'dual_stroke_window':
      return (
        <>
          <div className="editor-row">
            <label className="editor-field">
              <span>Window Start (week)</span>
              <input type="number" value={rule.window?.[0] ?? 1} min={0}
                onChange={e => update({ window: [Number(e.target.value), rule.window?.[1] ?? 11] })} />
            </label>
            <label className="editor-field">
              <span>Window End (week)</span>
              <input type="number" value={rule.window?.[1] ?? 11} min={0}
                onChange={e => update({ window: [rule.window?.[0] ?? 1, Number(e.target.value)] })} />
            </label>
          </div>
          <label className="editor-field">
            <span>Supervisors (comma-separated)</span>
            <input type="text" value={(rule.supervisors || []).join(', ')}
              onChange={e => update({ supervisors: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
          </label>
        </>
      );

    case 'weekend_stroke_prerequisite':
      return (
        <label className="editor-field">
          <span>Exempt Fellows (comma-separated)</span>
          <input type="text" value={(rule.exempt_fellows || []).join(', ')}
            onChange={e => update({ exempt_fellows: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
        </label>
      );

    case 'weekend_ncc_prerequisite':
      return (
        <label className="editor-field">
          <span>Exempt Groups (comma-separated)</span>
          <input type="text" value={(rule.exempt_groups || []).join(', ')}
            onChange={e => update({ exempt_groups: e.target.value.split(',').map(s => s.trim()).filter(Boolean) })} />
        </label>
      );

    default:
      return null;
  }
}

export function createDefaultCallRule(allFellows: string[]): CallRule {
  return {
    type: 'per_fellow_shift_total',
    name: 'New Per-Fellow Rule',
    active: true,
    fellow: allFellows[0] || '',
    shifts: [],
    relation: 'exactly',
    count: 0,
    strength: 'hard',
  };
}
