import React from 'react';

type ImportData = {
  fellow_names: string[];
  num_weeks: number;
  shifts_found: string[];
  assignments: Record<string, string[]>;
};

type Props = {
  data: ImportData;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ImportSchedule({ data, onConfirm, onCancel }: Props) {
  return (
    <div className="import-preview">
      <div className="import-header">
        <h2>Import Schedule</h2>
        <div className="import-actions">
          <button className="primary" onClick={onConfirm}>Confirm Import</button>
          <button onClick={onCancel}>Cancel</button>
        </div>
      </div>

      <div className="import-summary">
        <p><strong>{data.fellow_names.length}</strong> fellows, <strong>{data.num_weeks}</strong> weeks, <strong>{data.shifts_found.length}</strong> shifts found</p>
        <p className="hint">Fellows: {data.fellow_names.join(', ')}</p>
        <p className="hint">Shifts: {data.shifts_found.join(', ')}</p>
      </div>

      <div className="import-table-wrap">
        <table className="import-table">
          <thead>
            <tr>
              <th>Week</th>
              {data.fellow_names.map(f => <th key={f}>{f}</th>)}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: data.num_weeks }, (_, w) => (
              <tr key={w}>
                <td className="week-col">{w + 1}</td>
                {data.fellow_names.map(f => (
                  <td key={f}>{data.assignments[f]?.[w] || ''}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
