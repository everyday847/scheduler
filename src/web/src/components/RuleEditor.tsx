import React, { useState, useEffect } from 'react';
import { PaletteRule } from '../types';

type Props = {
  rule: PaletteRule;
  allShifts: string[];
  allGroups: string[];
  onSave: (updated: PaletteRule) => void;
  onCancel: () => void;
  onRemove: () => void;
};

export function RuleEditor({ rule, allShifts, allGroups, onSave, onCancel, onRemove }: Props) {
  const [draft, setDraft] = useState<PaletteRule>({ ...rule } as PaletteRule);

  const update = (patch: Partial<PaletteRule>) => {
    setDraft(prev => ({ ...prev, ...patch } as PaletteRule));
  };

  return (
    <div className="rule-editor">
      <div className="rule-editor-header">
        <h3>Edit Rule</h3>
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
          <span>Groups</span>
          <div className="chip-select">
            {allGroups.map(g => (
              <label key={g} className="chip-option">
                <input type="checkbox"
                  checked={draft.groups?.includes(g) ?? false}
                  onChange={e => {
                    const groups = draft.groups || [];
                    update({ groups: e.target.checked ? [...groups, g] : groups.filter(x => x !== g) });
                  }} />
                <span>{g}</span>
              </label>
            ))}
          </div>
        </label>

        {renderTypeFields(draft, allShifts, update)}
      </div>
    </div>
  );
}

function renderTypeFields(
  rule: PaletteRule,
  allShifts: string[],
  update: (patch: any) => void,
) {
  switch (rule.type) {
    case 'shift_total':
    case 'staffing_per_week':
      return (
        <>
          <ShiftsField shifts={rule.shifts} allShifts={allShifts}
            onChange={shifts => update({ shifts })} />
          <div className="editor-row">
            <label className="editor-field">
              <span>Relation</span>
              <select value={rule.relation} onChange={e => update({ relation: e.target.value })}>
                <option value="exactly">exactly</option>
                <option value="at_least">at least</option>
                <option value="at_most">at most</option>
              </select>
            </label>
            <label className="editor-field">
              <span>{rule.type === 'staffing_per_week' ? 'Fellows' : 'Weeks'}</span>
              <input type="number" value={rule.count} min={0}
                onChange={e => update({ count: Number(e.target.value) })} />
            </label>
          </div>
          {'window' in rule && rule.window && (
            <div className="editor-row">
              <label className="editor-field">
                <span>Window Start</span>
                <input type="number" value={rule.window[0]} min={0}
                  onChange={e => update({ window: [Number(e.target.value), rule.window![1]] })} />
              </label>
              <label className="editor-field">
                <span>Window End</span>
                <input type="number" value={rule.window[1]} min={0}
                  onChange={e => update({ window: [rule.window![0], Number(e.target.value)] })} />
              </label>
            </div>
          )}
        </>
      );

    case 'max_consecutive':
      return (
        <>
          <ShiftsField shifts={rule.shifts} allShifts={allShifts}
            onChange={shifts => update({ shifts })} />
          <label className="editor-field">
            <span>Maximum consecutive weeks</span>
            <input type="number" value={rule.max_weeks} min={1}
              onChange={e => update({ max_weeks: Number(e.target.value) })} />
          </label>
        </>
      );

    case 'block_rotation':
      return (
        <>
          <ShiftsField shifts={rule.shifts} allShifts={allShifts}
            onChange={shifts => update({ shifts })} />
          <label className="editor-field">
            <span>Block size (weeks)</span>
            <input type="number" value={rule.block_size} min={1}
              onChange={e => update({ block_size: Number(e.target.value) })} />
          </label>
        </>
      );

    case 'rotation_continuity':
      return (
        <>
          <label className="editor-field">
            <span>Block size (weeks)</span>
            <input type="number" value={rule.block_size} min={1}
              onChange={e => update({ block_size: Number(e.target.value) })} />
          </label>
          <div className="editor-field">
            <span>Team options</span>
            {rule.choices.map((choice, i) => (
              <div key={i} className="choice-row">
                <input type="text" value={choice.join(', ')}
                  onChange={e => {
                    const choices = [...rule.choices];
                    choices[i] = e.target.value.split(',').map(s => s.trim()).filter(Boolean);
                    update({ choices });
                  }} />
                <button onClick={() => {
                  const choices = rule.choices.filter((_, j) => j !== i);
                  update({ choices });
                }}>×</button>
              </div>
            ))}
            <button className="add-btn" onClick={() => update({ choices: [...rule.choices, []] })}>+ option</button>
          </div>
          <label className="editor-field">
            <input type="checkbox" checked={rule.allow_none}
              onChange={e => update({ allow_none: e.target.checked })} />
            <span>Allow no assignment</span>
          </label>
        </>
      );

    case 'prerequisite':
      return (
        <>
          <ShiftsField label="Prerequisite shifts" shifts={rule.prerequisite_shifts} allShifts={allShifts}
            onChange={prerequisite_shifts => update({ prerequisite_shifts })} />
          <ShiftsField label="Target shifts" shifts={rule.target_shifts} allShifts={allShifts}
            onChange={target_shifts => update({ target_shifts })} />
          <label className="editor-field">
            <span>Minimum prerequisite weeks</span>
            <input type="number" value={rule.min_prerequisite_weeks} min={1}
              onChange={e => update({ min_prerequisite_weeks: Number(e.target.value) })} />
          </label>
        </>
      );

    case 'coverage_target':
      return (
        <>
          <ShiftsField shifts={rule.shifts} allShifts={allShifts}
            onChange={shifts => update({ shifts })} />
          <label className="editor-field">
            <span>Max uncovered weeks</span>
            <input type="number" value={rule.max_uncovered_weeks} min={0}
              onChange={e => update({ max_uncovered_weeks: Number(e.target.value) })} />
          </label>
        </>
      );

    case 'windowed_balance':
      return (
        <>
          <ShiftsField shifts={rule.shifts} allShifts={allShifts}
            onChange={shifts => update({ shifts })} />
          <div className="editor-row">
            <label className="editor-field">
              <span>Window A start</span>
              <input type="number" value={rule.window_a[0]} min={0}
                onChange={e => update({ window_a: [Number(e.target.value), rule.window_a[1]] })} />
            </label>
            <label className="editor-field">
              <span>Window A end</span>
              <input type="number" value={rule.window_a[1]} min={0}
                onChange={e => update({ window_a: [rule.window_a[0], Number(e.target.value)] })} />
            </label>
          </div>
          <div className="editor-row">
            <label className="editor-field">
              <span>Window B start</span>
              <input type="number" value={rule.window_b[0]} min={0}
                onChange={e => update({ window_b: [Number(e.target.value), rule.window_b[1]] })} />
            </label>
            <label className="editor-field">
              <span>Window B end</span>
              <input type="number" value={rule.window_b[1]} min={0}
                onChange={e => update({ window_b: [rule.window_b[0], Number(e.target.value)] })} />
            </label>
          </div>
          <label className="editor-field">
            <span>Max difference</span>
            <input type="number" value={rule.max_difference} min={0}
              onChange={e => update({ max_difference: Number(e.target.value) })} />
          </label>
        </>
      );

    case 'night_spacing':
    case 'weekend_spacing': {
      const params = (rule as any).params || {};
      const isNight = rule.type === 'night_spacing';
      return (
        <>
          <label className="editor-field">
            <span>{isNight ? 'Max nights' : 'Max weekends'}</span>
            <input type="number" value={params.maxNights ?? params.maxWeekends ?? 1} min={1}
              onChange={e => update({
                params: { ...params, [isNight ? 'maxNights' : 'maxWeekends']: Number(e.target.value) }
              })} />
          </label>
          <label className="editor-field">
            <span>{isNight ? 'Window (days)' : 'Window (weeks)'}</span>
            <input type="number" value={params.windowDays ?? params.windowWeeks ?? 2} min={2}
              onChange={e => update({
                params: { ...params, [isNight ? 'windowDays' : 'windowWeeks']: Number(e.target.value) }
              })} />
          </label>
        </>
      );
    }

    case 'night_blocked_services':
    case 'weekend_blocked_services': {
      const params = (rule as any).params || {};
      return (
        <>
          <label className="editor-field">
            <span>Exact services</span>
            <TagInput values={params.exactServices || []}
              onChange={vals => update({ params: { ...params, exactServices: vals } })} />
          </label>
          <label className="editor-field">
            <span>Substring services</span>
            <TagInput values={params.substringServices || []}
              onChange={vals => update({ params: { ...params, substringServices: vals } })} />
          </label>
        </>
      );
    }

    case 'night_holiday_eligibility':
    case 'weekend_stroke_eligibility': {
      const params = (rule as any).params || {};
      const isNight = rule.type === 'night_holiday_eligibility';
      return (
        <label className="editor-field">
          <span>{isNight ? 'Allowed services' : 'Eligible services'}</span>
          <TagInput values={params.allowedServices || params.eligibleServices || []}
            onChange={vals => update({
              params: { ...params, [isNight ? 'allowedServices' : 'eligibleServices']: vals }
            })} />
        </label>
      );
    }

    case 'night_penalties':
    case 'weekend_penalties': {
      const params = (rule as any).params || {};
      const weights = params.weights || {};
      const keys = Object.keys(weights);
      return (
        <div className="editor-field">
          <span>Weights</span>
          {keys.length === 0 && <p className="hint">No weights configured. Add one below.</p>}
          {keys.map((key) => (
            <div key={key} className="editor-row">
              <input type="text" value={key}
                onChange={e => {
                  const newWeights = { ...weights };
                  const value = newWeights[key];
                  delete newWeights[key];
                  newWeights[e.target.value] = value;
                  update({ params: { ...params, weights: newWeights } });
                }} />
              <input type="number" value={weights[key]} min={0}
                onChange={e => update({
                  params: { ...params, weights: { ...weights, [key]: Number(e.target.value) } }
                })} />
              <button onClick={() => {
                const newWeights = { ...weights };
                delete newWeights[key];
                update({ params: { ...params, weights: newWeights } });
              }}>×</button>
            </div>
          ))}
          <button className="add-btn" onClick={() => {
            update({ params: { ...params, weights: { ...weights, '': 1 } } });
          }}>+ Weight</button>
        </div>
      );
    }

    case 'night_sunday_following': {
      const params = (rule as any).params || {};
      return (
        <label className="editor-field">
          <span>Preferred services</span>
          <TagInput values={params.preferredServices || []}
            onChange={vals => update({ params: { ...params, preferredServices: vals } })} />
        </label>
      );
    }

    default:
      return null;
  }
}

function TagInput({ values, onChange }: { values: string[]; onChange: (v: string[]) => void }) {
  const [text, setText] = useState(values.join(', '));
  useEffect(() => {
    setText(values.join(', '));
  }, [values]);
  return (
    <input type="text" value={text}
      onChange={e => setText(e.target.value)}
      onBlur={() => onChange(text.split(',').map(s => s.trim()).filter(Boolean))}
      placeholder="Comma-separated values" />
  );
}

function ShiftsField({ shifts, allShifts, onChange, label }: {
  shifts: string[]; allShifts: string[]; onChange: (s: string[]) => void; label?: string;
}) {
  return (
    <div className="editor-field">
      <span>{label || 'Shifts'}</span>
      <div className="chip-select">
        {allShifts.map(s => (
          <label key={s} className="chip-option">
            <input type="checkbox"
              checked={shifts.includes(s)}
              onChange={e => {
                onChange(e.target.checked ? [...shifts, s] : shifts.filter(x => x !== s));
              }} />
            <span>{s}</span>
          </label>
        ))}
      </div>
    </div>
  );
}
