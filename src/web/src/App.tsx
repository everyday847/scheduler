import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';

const API_BASE = 'http://127.0.0.1:5000';
const WEEK_COUNT = 52;
const HORIZON_START = new Date(2026, 5, 29); // June 29, 2026

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type AnnualConfig = {
  fellow_groups: Record<string, string[]>;
  shifts: string[];
  fellow_week_pairs: Record<string, number[]>;
  night_call: NightCallEntry[];
  weekend_call: WeekendCallEntry[];
  holiday_dates: string[];
  annual_rules?: { rules: AnnualRule[] };
  horizon_start?: string;
};

type NightCallEntry = { group: string; total_nights: number; friday_nights: number };
type WeekendCallEntry = { group: string; ncc_total: number; stroke_total: number };
type AnnualRule = { name: string; kind: string; active: boolean; [key: string]: any };

type FullSolution = {
  weekly_assignments: Record<string, string[]>;
  weekend_assignments: Record<string, string>[];
  night_assignments: Record<string, string>[];
  soft_penalty: number;
  elapsed: number;
};

type SolverStatus = 'idle' | 'building' | 'feasibility' | 'optimizing' | 'done' | 'error';
type AppMode = 'config' | 'schedule';

// ---------------------------------------------------------------------------
// Date helpers
// ---------------------------------------------------------------------------

function mondayOfWeek(weekIndex: number): Date {
  const d = new Date(HORIZON_START);
  d.setDate(d.getDate() + weekIndex * 7);
  return d;
}

function formatMonday(weekIndex: number): string {
  return mondayOfWeek(weekIndex).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function dateToWeekIndex(dateStr: string): number {
  const d = new Date(dateStr + 'T00:00:00');
  const diff = d.getTime() - HORIZON_START.getTime();
  return Math.max(0, Math.min(WEEK_COUNT - 1, Math.floor(diff / (7 * 86400000))));
}

function weekIndexToDate(weekIndex: number): string {
  return mondayOfWeek(weekIndex).toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

const emptyConfig: AnnualConfig = {
  fellow_groups: {}, shifts: [], fellow_week_pairs: {},
  night_call: [], weekend_call: [], holiday_dates: [],
};

function App() {
  const [configFiles, setConfigFiles] = useState<{ annual: string[]; standing: string[] }>({ annual: [], standing: [] });
  const [selectedFile, setSelectedFile] = useState<string>('');
  const [config, setConfig] = useState<AnnualConfig>(emptyConfig);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Vacation dates stored as date strings for the UI
  const [vacationDates, setVacationDates] = useState<Record<string, string[]>>({});

  // Solver state
  const [solverStatus, setSolverStatus] = useState<SolverStatus>('idle');
  const [solution, setSolution] = useState<FullSolution | null>(null);
  const [penalty, setPenalty] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [formulaInfo, setFormulaInfo] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  // UI mode
  const [mode, setMode] = useState<AppMode>('config');
  const [scheduleTab, setScheduleTab] = useState<'weekly' | 'weekend' | 'night'>('weekly');

  // Load config file list on mount
  useEffect(() => {
    fetch(`${API_BASE}/api/configs`)
      .then((r) => r.json())
      .then((data) => {
        setConfigFiles(data);
        if (data.annual.length > 0) {
          const first = data.annual[0];
          setSelectedFile(first);
          loadConfig(first);
        } else {
          setLoading(false);
        }
      })
      .catch((err) => { setError(err.message); setLoading(false); });
  }, []);

  const loadConfig = useCallback((filename: string) => {
    setLoading(true);
    setError(null);
    fetch(`${API_BASE}/api/config/annual/${filename}`)
      .then((r) => r.json())
      .then((data: AnnualConfig) => {
        setConfig({
          ...emptyConfig,
          ...data,
          night_call: data.night_call || [],
          weekend_call: data.weekend_call || [],
          holiday_dates: data.holiday_dates || [],
        });
        const dates: Record<string, string[]> = {};
        for (const [fellow, weeks] of Object.entries(data.fellow_week_pairs || {})) {
          dates[fellow] = (weeks || []).map(weekIndexToDate);
        }
        setVacationDates(dates);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const allFellows = useMemo(() => {
    return Object.entries(config.fellow_groups).flatMap(([, fellows]) => fellows);
  }, [config.fellow_groups]);

  // ---------------------------------------------------------------------------
  // Config mutators
  // ---------------------------------------------------------------------------

  const updateGroup = (groupName: string, value: string) => {
    const fellows = value.split('\n').map((s) => s.trim()).filter(Boolean);
    setConfig((c) => ({ ...c, fellow_groups: { ...c.fellow_groups, [groupName]: fellows } }));
  };

  const updateVacDate = (fellow: string, i: number, value: string) => {
    setVacationDates((cur) => {
      const d = [...(cur[fellow] || [])]; d[i] = value;
      return { ...cur, [fellow]: d };
    });
  };
  const addVacDate = (fellow: string) => {
    setVacationDates((cur) => ({ ...cur, [fellow]: [...(cur[fellow] || []), weekIndexToDate(0)] }));
  };
  const removeVacDate = (fellow: string, i: number) => {
    setVacationDates((cur) => { const d = [...(cur[fellow] || [])]; d.splice(i, 1); return { ...cur, [fellow]: d }; });
  };

  const updateNight = (i: number, field: keyof NightCallEntry, value: string) => {
    setConfig((c) => {
      const nc = [...c.night_call]; nc[i] = { ...nc[i], [field]: field === 'group' ? value : Number(value) };
      return { ...c, night_call: nc };
    });
  };

  const updateWeekend = (i: number, field: keyof WeekendCallEntry, value: string) => {
    setConfig((c) => {
      const wc = [...c.weekend_call]; wc[i] = { ...wc[i], [field]: field === 'group' ? value : Number(value) };
      return { ...c, weekend_call: wc };
    });
  };

  const updateHoliday = (i: number, value: string) => {
    setConfig((c) => { const h = [...c.holiday_dates]; h[i] = value; return { ...c, holiday_dates: h }; });
  };
  const addHoliday = () => {
    setConfig((c) => ({ ...c, holiday_dates: [...c.holiday_dates, '2026-07-04'] }));
  };
  const removeHoliday = (i: number) => {
    setConfig((c) => { const h = [...c.holiday_dates]; h.splice(i, 1); return { ...c, holiday_dates: h }; });
  };

  // ---------------------------------------------------------------------------
  // Solver
  // ---------------------------------------------------------------------------

  const buildRequest = useCallback((): AnnualConfig => {
    const weekPairs: Record<string, number[]> = {};
    for (const fellow of allFellows) {
      const dates = vacationDates[fellow];
      if (dates && dates.length > 0) weekPairs[fellow] = dates.map(dateToWeekIndex);
    }
    return { ...config, fellow_week_pairs: weekPairs };
  }, [config, allFellows, vacationDates]);

  const cancelSolve = useCallback(() => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    if (solverStatus !== 'done') setSolverStatus('idle');
  }, [solverStatus]);

  const generateSchedule = useCallback(async () => {
    cancelSolve();
    setSolverStatus('building'); setSolution(null); setPenalty(null); setElapsed(0);
    setError(null); setFormulaInfo(''); setMode('schedule');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/api/schedule/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildRequest()),
        signal: controller.signal,
      });
      if (!response.ok) { setError(await errorMessage(response)); setSolverStatus('error'); return; }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';
        for (const block of events) {
          const lines = block.split('\n');
          let eType = '', eData = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) eType = line.slice(7);
            else if (line.startsWith('data: ')) eData = line.slice(6);
          }
          if (eType && eData) handleEvent(eType, JSON.parse(eData));
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Connection lost.');
      setSolverStatus('error');
    }
  }, [buildRequest, cancelSolve]);

  const handleEvent = useCallback((type: string, data: any) => {
    if (type === 'status') {
      if (data.phase === 'building' || data.phase === 'built') {
        setSolverStatus('building');
        if (data.vars) setFormulaInfo(`${data.vars.toLocaleString()} vars, ${data.constraints.toLocaleString()} constraints`);
      } else if (data.phase === 'feasibility') setSolverStatus('feasibility');
      else setSolverStatus('optimizing');
    } else if (type === 'solution') {
      setSolution(data as FullSolution); setPenalty(data.soft_penalty); setElapsed(data.elapsed);
      setSolverStatus('optimizing');
    } else if (type === 'done') {
      setSolverStatus('done'); setPenalty(data.optimal_penalty); setElapsed(data.total_seconds);
    } else if (type === 'error') {
      setError(data.message); setSolverStatus('error');
    }
  }, []);

  const isRunning = solverStatus === 'building' || solverStatus === 'feasibility' || solverStatus === 'optimizing';

  // ---------------------------------------------------------------------------
  // CONFIG MODE
  // ---------------------------------------------------------------------------
  if (mode === 'config') {
    const groupEntries = Object.entries(config.fellow_groups);
    const groupNames = Object.keys(config.fellow_groups);

    return (
      <main className="app-shell single-pane">
        <div className="config-view">
          <div className="config-top-bar">
            <div>
              <p className="eyebrow">Stanford fellowship scheduling</p>
              <h1>Schedule Configuration</h1>
            </div>
            <div className="config-top-actions">
              <select value={selectedFile} onChange={(e) => { setSelectedFile(e.target.value); loadConfig(e.target.value); }}>
                {configFiles.annual.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
              <button className="primary generate-btn" onClick={generateSchedule} disabled={loading}>
                Generate Schedule
              </button>
            </div>
          </div>

          {error && <div className="error-banner" role="alert">{error}</div>}
          {loading && <p className="status-line">Loading config...</p>}

          <div className="config-sections">
            {/* Fellow Groups */}
            <section className="config-card">
              <h2>Fellow Groups</h2>
              <div className="input-stack">
                {groupEntries.map(([group, fellows]) => (
                  <label className="field-group" key={group}>
                    <span>{group}</span>
                    <textarea
                      value={fellows.join('\n')}
                      onChange={(e) => updateGroup(group, e.target.value)}
                      rows={Math.min(6, Math.max(2, fellows.length + 1))}
                      spellCheck={false}
                    />
                  </label>
                ))}
              </div>
            </section>

            {/* Vacation Requests */}
            <section className="config-card">
              <h2>Vacation Requests</h2>
              <p className="hint">First 3 dates per fellow are hard constraints; extras are soft.</p>
              <div className="request-grid">
                {allFellows.map((fellow) => {
                  const dates = vacationDates[fellow] || [];
                  return (
                    <div className="request-row" key={fellow}>
                      <strong>{fellow}</strong>
                      <div className="week-list">
                        {dates.map((d, i) => (
                          <span key={`${fellow}-${i}`} className="week-input">
                            <input type="date" value={d} onChange={(e) => updateVacDate(fellow, i, e.target.value)} />
                            <button type="button" onClick={() => removeVacDate(fellow, i)}>x</button>
                          </span>
                        ))}
                        <button type="button" className="add-btn" onClick={() => addVacDate(fellow)}>+ date</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* Night Call Targets */}
            <section className="config-card">
              <h2>Night Call Distribution</h2>
              <table className="config-table">
                <thead><tr><th>Group</th><th>Total Nights</th><th>Friday Nights</th></tr></thead>
                <tbody>
                  {config.night_call.map((entry, i) => (
                    <tr key={i}>
                      <td>
                        <select value={entry.group} onChange={(e) => updateNight(i, 'group', e.target.value)}>
                          {groupNames.map((g) => <option key={g} value={g}>{g}</option>)}
                        </select>
                      </td>
                      <td><input type="number" value={entry.total_nights} onChange={(e) => updateNight(i, 'total_nights', e.target.value)} /></td>
                      <td><input type="number" value={entry.friday_nights} onChange={(e) => updateNight(i, 'friday_nights', e.target.value)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {/* Weekend Call Targets */}
            <section className="config-card">
              <h2>Weekend Call Distribution</h2>
              <table className="config-table">
                <thead><tr><th>Group</th><th>NCC Total</th><th>Stroke Total</th></tr></thead>
                <tbody>
                  {config.weekend_call.map((entry, i) => (
                    <tr key={i}>
                      <td>
                        <select value={entry.group} onChange={(e) => updateWeekend(i, 'group', e.target.value)}>
                          {groupNames.map((g) => <option key={g} value={g}>{g}</option>)}
                        </select>
                      </td>
                      <td><input type="number" value={entry.ncc_total} onChange={(e) => updateWeekend(i, 'ncc_total', e.target.value)} /></td>
                      <td><input type="number" value={entry.stroke_total} onChange={(e) => updateWeekend(i, 'stroke_total', e.target.value)} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>

            {/* Holidays */}
            <section className="config-card">
              <h2>Holiday Dates</h2>
              <div className="week-list">
                {config.holiday_dates.map((d, i) => (
                  <span key={i} className="week-input">
                    <input type="date" value={d} onChange={(e) => updateHoliday(i, e.target.value)} />
                    <button type="button" onClick={() => removeHoliday(i)}>x</button>
                  </span>
                ))}
                <button type="button" className="add-btn" onClick={addHoliday}>+ date</button>
              </div>
            </section>
          </div>
        </div>
      </main>
    );
  }

  // ---------------------------------------------------------------------------
  // SCHEDULE MODE
  // ---------------------------------------------------------------------------
  return (
    <main className="app-shell single-pane">
      <div className="schedule-view">
        <div className="schedule-header">
          <button className="back-btn" onClick={() => { cancelSolve(); setMode('config'); }}>
            ← Configuration
          </button>
          <ProgressPanel status={solverStatus} penalty={penalty} elapsed={elapsed} formulaInfo={formulaInfo} />
          {isRunning && <button className="cancel-btn" onClick={cancelSolve}>Cancel</button>}
        </div>

        {error && <div className="error-banner" role="alert">{error}</div>}

        {solution ? (
          <>
            <div className="tab-bar">
              {(['weekly', 'weekend', 'night'] as const).map((tab) => (
                <button key={tab} className={scheduleTab === tab ? 'tab active' : 'tab'} onClick={() => setScheduleTab(tab)}>
                  {tab === 'weekly' ? 'Weekly Shifts' : tab === 'weekend' ? 'Weekend Call' : 'Night Call'}
                </button>
              ))}
            </div>
            {scheduleTab === 'weekly' && (
              <ScheduleTable
                columns={Object.keys(solution.weekly_assignments)}
                rows={solution.weekly_assignments}
              />
            )}
            {scheduleTab === 'weekend' && (
              <ScheduleTable
                columns={solution.weekend_assignments.length > 0 ? Object.keys(solution.weekend_assignments[0]) : []}
                rows={pivotWeeklyList(solution.weekend_assignments)}
              />
            )}
            {scheduleTab === 'night' && (
              <ScheduleTable
                columns={solution.night_assignments.length > 0 ? Object.keys(solution.night_assignments[0]) : []}
                rows={pivotWeeklyList(solution.night_assignments)}
              />
            )}
          </>
        ) : (
          <div className="empty-state">
            <h2>{isRunning ? 'Building schedule...' : 'No schedule yet'}</h2>
            <p>{isRunning ? 'The first result will appear within a few seconds.' : 'Go back to configuration and click Generate.'}</p>
          </div>
        )}
      </div>
    </main>
  );
}

// ---------------------------------------------------------------------------
// Components
// ---------------------------------------------------------------------------

function ProgressPanel({ status, penalty, elapsed, formulaInfo }: {
  status: SolverStatus; penalty: number | null; elapsed: number; formulaInfo: string;
}) {
  const label: Record<SolverStatus, string> = {
    building: 'Building formula...', feasibility: 'Checking feasibility...',
    optimizing: 'Optimizing...', done: 'Done', idle: '', error: 'Error',
  };
  const time = elapsed < 60 ? `${elapsed.toFixed(1)}s` : `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s`;
  return (
    <div className={`progress-panel ${status === 'done' ? 'done' : ''}`}>
      <span className="progress-indicator">{status === 'done' ? '✓' : '●'}</span>
      <span className="progress-status">{label[status]}</span>
      {penalty !== null && <span className="progress-penalty">Penalty: <strong>{penalty}</strong></span>}
      {elapsed > 0 && <span className="progress-elapsed">{time}</span>}
      {formulaInfo && status === 'building' && <span className="progress-formula">{formulaInfo}</span>}
    </div>
  );
}

function ScheduleTable({ columns, rows }: { columns: string[]; rows: Record<string, string[]> }) {
  const rowCount = Math.max(0, ...columns.map((col) => rows[col]?.length ?? 0));
  return (
    <div className="table-panel">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th className="week-col">Wk</th>
              <th className="date-col">Monday</th>
              {columns.map((col) => <th key={col}>{col}</th>)}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: rowCount }, (_, i) => (
              <tr key={i}>
                <th className="week-col">{i + 1}</th>
                <th className="date-col">{formatMonday(i)}</th>
                {columns.map((col) => {
                  const v = cellText(rows[col]?.[i]);
                  return <td key={`${col}-${i}`} className={classForValue(v)}>{v}</td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function pivotWeeklyList(list: Record<string, string>[]): Record<string, string[]> {
  if (!list.length) return {};
  const keys = Object.keys(list[0]);
  return Object.fromEntries(keys.map((k) => [k, list.map((w) => w[k] ?? '')]));
}

async function errorMessage(response: Response) {
  try { const d = await response.json(); return d.error ?? `Status ${response.status}`; }
  catch { return `Status ${response.status}`; }
}

function cellText(v: string | string[] | undefined) {
  return Array.isArray(v) ? v.join(', ') : v ?? '';
}

function classForValue(value: string) {
  const key = value.split(', ')[0].replace(/[^A-Za-z0-9]+/g, '-').toLowerCase();
  return key ? `schedule-cell value-${key}` : 'schedule-cell';
}

export default App;
