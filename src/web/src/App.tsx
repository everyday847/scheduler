import React, { useEffect, useMemo, useState } from 'react';
import './App.css';

const API_BASE = 'http://127.0.0.1:5000';
const WEEK_COUNT = 52;

type FellowGroupKey = 'jr_fellows' | 'sr_fellows' | 'stroke_fellows' | 'CCM_fellows' | 'NH_fellows' | 'lia';

type ScheduleRequest = Record<FellowGroupKey, string[]> & {
  shifts: string[];
  fellow_week_pairs: Record<string, number[]>;
};

type ScheduleResult = {
  request: ScheduleRequest;
  shifts_for_fellows: Record<string, string[]>;
  fellows_for_shifts: Record<string, string[]>;
};

const emptyRequest: ScheduleRequest = {
  jr_fellows: [],
  sr_fellows: [],
  stroke_fellows: [],
  CCM_fellows: [],
  NH_fellows: [],
  lia: [],
  shifts: [],
  fellow_week_pairs: {},
};

const groupLabels: Array<[FellowGroupKey, string]> = [
  ['jr_fellows', 'Junior NCC fellows'],
  ['sr_fellows', 'Senior NCC fellows'],
  ['stroke_fellows', 'Stroke fellows'],
  ['CCM_fellows', 'CCM fellows'],
  ['NH_fellows', 'NH fellows'],
  ['lia', 'Lia'],
];

const shiftOrder = ['NCC1', 'NCC2', 'Extra', 'Swing', 'Stroke', 'Telestroke/Clinic', 'Stroke_Supervisory'];

function App() {
  const [request, setRequest] = useState<ScheduleRequest>(emptyRequest);
  const [result, setResult] = useState<ScheduleResult | null>(null);
  const [loadingDefaults, setLoadingDefaults] = useState(true);
  const [solving, setSolving] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/api/default-schedule`)
      .then(readJson)
      .then((data: ScheduleRequest) => {
        if (!cancelled) {
          setRequest(data);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      })
      .finally(() => {
        if (!cancelled) setLoadingDefaults(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const requestFellows = useMemo(
    () => [...request.jr_fellows, ...request.sr_fellows, ...request.stroke_fellows],
    [request.jr_fellows, request.sr_fellows, request.stroke_fellows],
  );

  const visibleWeekPairs = useMemo(() => {
    return requestFellows.map((fellow) => ({
      fellow,
      weeks: request.fellow_week_pairs[fellow] ?? [],
    }));
  }, [request.fellow_week_pairs, requestFellows]);

  const updateGroup = (key: FellowGroupKey, value: string) => {
    const fellows = value
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
    setRequest((current) => ({ ...current, [key]: fellows }));
  };

  const updateWeek = (fellow: string, index: number, value: string) => {
    const nextWeek = clampWeek(Number(value));
    setRequest((current) => {
      const weeks = [...(current.fellow_week_pairs[fellow] ?? [])];
      weeks[index] = nextWeek;
      return {
        ...current,
        fellow_week_pairs: { ...current.fellow_week_pairs, [fellow]: weeks },
      };
    });
  };

  const addWeek = (fellow: string) => {
    setRequest((current) => ({
      ...current,
      fellow_week_pairs: {
        ...current.fellow_week_pairs,
        [fellow]: [...(current.fellow_week_pairs[fellow] ?? []), 0],
      },
    }));
  };

  const removeWeek = (fellow: string, index: number) => {
    setRequest((current) => {
      const weeks = [...(current.fellow_week_pairs[fellow] ?? [])];
      weeks.splice(index, 1);
      return {
        ...current,
        fellow_week_pairs: { ...current.fellow_week_pairs, [fellow]: weeks },
      };
    });
  };

  const buildRequest = (): ScheduleRequest => ({
    ...request,
    fellow_week_pairs: Object.fromEntries(
      requestFellows.map((fellow) => [fellow, request.fellow_week_pairs[fellow] ?? []]),
    ),
  });

  const generateSchedule = async () => {
    setSolving(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/schedule`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildRequest()),
      });
      const data = await readJson(response);
      setResult(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to generate schedule.');
    } finally {
      setSolving(false);
    }
  };

  const downloadWorkbook = async () => {
    setDownloading(true);
    setError(null);
    try {
      const response = await fetch(`${API_BASE}/api/schedule.xlsx`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildRequest()),
      });
      if (!response.ok) throw new Error(await errorMessage(response));
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'optimized_schedule.xlsx';
      link.click();
      window.URL.revokeObjectURL(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to download workbook.');
    } finally {
      setDownloading(false);
    }
  };

  return (
    <main className="app-shell">
      <section className="sidebar" aria-label="Schedule inputs">
        <div className="title-block">
          <p className="eyebrow">Stanford fellowship scheduling</p>
          <h1>Fellow Scheduler</h1>
        </div>

        {loadingDefaults ? <p className="status-line">Loading defaults...</p> : null}

        <div className="input-stack">
          {groupLabels.map(([key, label]) => (
            <label className="field-group" key={key}>
              <span>{label}</span>
              <textarea
                aria-label={label}
                value={request[key].join('\n')}
                onChange={(event) => updateGroup(key, event.target.value)}
                rows={key === 'CCM_fellows' ? 6 : 3}
                spellCheck={false}
              />
            </label>
          ))}
        </div>
      </section>

      <section className="workspace">
        <div className="toolbar">
          <div>
            <h2>Requests</h2>
            <p>Edit vacation/request weeks for NCC and Stroke fellows.</p>
          </div>
          <div className="actions">
            <button className="primary" onClick={generateSchedule} disabled={solving || loadingDefaults}>
              {solving ? 'Generating...' : 'Generate schedule'}
            </button>
            <button onClick={downloadWorkbook} disabled={downloading || loadingDefaults}>
              {downloading ? 'Preparing...' : 'Download Excel'}
            </button>
          </div>
        </div>

        {error ? <div className="error-banner" role="alert">{error}</div> : null}

        <section className="request-grid" aria-label="Vacation requests">
          {visibleWeekPairs.map(({ fellow, weeks }) => (
            <div className="request-row" key={fellow}>
              <strong>{fellow}</strong>
              <div className="week-list">
                {weeks.map((week, index) => (
                  <label key={`${fellow}-${index}`} className="week-input">
                    <span>Week</span>
                    <input
                      aria-label={`${fellow} request ${index + 1}`}
                      type="number"
                      min={0}
                      max={WEEK_COUNT - 1}
                      value={week}
                      onChange={(event) => updateWeek(fellow, index, event.target.value)}
                    />
                    <button type="button" onClick={() => removeWeek(fellow, index)} aria-label={`Remove ${fellow} request ${index + 1}`}>
                      Remove
                    </button>
                  </label>
                ))}
                <button type="button" onClick={() => addWeek(fellow)}>Add week</button>
              </div>
            </div>
          ))}
        </section>

        {result ? <ScheduleTables result={result} /> : <EmptyState />}
      </section>
    </main>
  );
}

function ScheduleTables({ result }: { result: ScheduleResult }) {
  return (
    <div className="results">
      <ScheduleTable
        title="Per-fellow schedule"
        columns={Object.keys(result.shifts_for_fellows)}
        rows={result.shifts_for_fellows}
      />
      <ScheduleTable
        title="Per-shift schedule"
        columns={shiftOrder.filter((shift) => result.fellows_for_shifts[shift])}
        rows={result.fellows_for_shifts}
      />
    </div>
  );
}

function ScheduleTable({ title, columns, rows }: { title: string; columns: string[]; rows: Record<string, string[]> }) {
  const rowCount = Math.max(0, ...columns.map((column) => rows[column]?.length ?? 0));
  return (
    <section className="table-panel">
      <h2>{title}</h2>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Week</th>
              {columns.map((column) => <th key={column}>{column}</th>)}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: rowCount }, (_, week) => (
              <tr key={week}>
                <th>{week}</th>
                {columns.map((column) => {
                  const value = cellText(rows[column]?.[week]);
                  return <td key={`${column}-${week}`} className={classForValue(value)}>{value}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function EmptyState() {
  return (
    <section className="empty-state">
      <h2>Ready to solve</h2>
      <p>Generate a schedule to review assignments in browser tables or download the Excel workbook.</p>
    </section>
  );
}

async function readJson(response: Response) {
  if (!response.ok) throw new Error(await errorMessage(response));
  return response.json();
}

async function errorMessage(response: Response) {
  try {
    const data = await response.json();
    return data.error ?? `Request failed with status ${response.status}`;
  } catch {
    return `Request failed with status ${response.status}`;
  }
}

function clampWeek(value: number) {
  if (Number.isNaN(value)) return 0;
  return Math.min(WEEK_COUNT - 1, Math.max(0, value));
}

function cellText(value: string | string[] | undefined) {
  if (Array.isArray(value)) return value.join(', ');
  return value ?? '';
}

function classForValue(value: string) {
  const key = value.split(', ')[0].replace(/[^A-Za-z0-9]+/g, '-').toLowerCase();
  return key ? `schedule-cell value-${key}` : 'schedule-cell';
}

export default App;
