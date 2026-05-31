import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';

const API_BASE = 'http://127.0.0.1:5000';
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
  annual_rules?: { rules: Rule[] };
  horizon_start?: string;
  num_weeks?: number;
};

type NightCallEntry = { group: string; total_nights: number; friday_nights: number };
type WeekendCallEntry = { group: string; ncc_total: number; stroke_total: number };
type Rule = { name: string; kind: string; active: boolean; strength?: string; [key: string]: any };

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
  return Math.max(0, Math.min(51, Math.floor(diff / (7 * 86400000))));
}

function weekIndexToDate(weekIndex: number): string {
  return mondayOfWeek(weekIndex).toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

const emptyConfig: AnnualConfig = {
  fellow_groups: {}, shifts: [], fellow_week_pairs: {},
  night_call: [], weekend_call: [], holiday_dates: [], num_weeks: 52,
};

function App() {
  const [configFiles, setConfigFiles] = useState<{ annual: string[]; standing: string[] }>({ annual: [], standing: [] });
  const [selectedFile, setSelectedFile] = useState('');
  const [config, setConfig] = useState<AnnualConfig>(emptyConfig);
  const [standingRules, setStandingRules] = useState<Rule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [vacationDates, setVacationDates] = useState<Record<string, string[]>>({});

  const [solverStatus, setSolverStatus] = useState<SolverStatus>('idle');
  const [solution, setSolution] = useState<FullSolution | null>(null);
  const [penalty, setPenalty] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [formulaInfo, setFormulaInfo] = useState('');
  const abortRef = useRef<AbortController | null>(null);

  const [mode, setMode] = useState<AppMode>('config');
  const [scheduleTab, setScheduleTab] = useState<'weekly' | 'weekend' | 'night'>('weekly');

  const W = config.num_weeks || 52;

  // Load config file list
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadConfig = useCallback((filename: string) => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetch(`${API_BASE}/api/config/annual/${filename}`).then((r) => r.json()),
      fetch(`${API_BASE}/api/config/standing/stanford-fellowship.yaml`).then((r) => r.json()),
    ])
      .then(([annual, standing]) => {
        setConfig({
          ...emptyConfig, ...annual,
          night_call: annual.night_call || [],
          weekend_call: annual.weekend_call || [],
          holiday_dates: annual.holiday_dates || [],
          num_weeks: annual.num_weeks || 52,
        });
        const dates: Record<string, string[]> = {};
        for (const [fellow, weeks] of Object.entries(annual.fellow_week_pairs || {})) {
          dates[fellow] = ((weeks as number[]) || []).map(weekIndexToDate);
        }
        setVacationDates(dates);
        setStandingRules((standing.rules || []) as Rule[]);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const allFellows = useMemo(() =>
    Object.entries(config.fellow_groups).flatMap(([, f]) => f),
    [config.fellow_groups],
  );

  // Budget calculations
  const nightBudget = useMemo(() => {
    const required = W * 7;
    const configured = config.night_call.reduce((s, e) => {
      const groupSize = (config.fellow_groups[e.group] || []).length;
      return s + e.total_nights * groupSize;
    }, 0);
    const fridayRequired = W;
    const fridayConfigured = config.night_call.reduce((s, e) => {
      const groupSize = (config.fellow_groups[e.group] || []).length;
      return s + e.friday_nights * groupSize;
    }, 0);
    return { required, configured, fridayRequired, fridayConfigured };
  }, [config.night_call, config.fellow_groups, W]);

  const weekendBudget = useMemo(() => {
    const nccRequired = W * 2;
    const nccConfigured = config.weekend_call.reduce((s, e) => {
      const groupSize = (config.fellow_groups[e.group] || []).length;
      return s + e.ncc_total * groupSize;
    }, 0);
    const strokeRequired = W;
    const strokeConfigured = config.weekend_call.reduce((s, e) => {
      const groupSize = (config.fellow_groups[e.group] || []).length;
      return s + e.stroke_total * groupSize;
    }, 0);
    return { nccRequired, nccConfigured, strokeRequired, strokeConfigured };
  }, [config.weekend_call, config.fellow_groups, W]);

  // Config mutators
  const updateGroup = (g: string, v: string) => {
    setConfig((c) => ({ ...c, fellow_groups: { ...c.fellow_groups, [g]: v.split('\n').map((s) => s.trim()).filter(Boolean) } }));
  };
  const updateVacDate = (f: string, i: number, v: string) => {
    setVacationDates((cur) => { const d = [...(cur[f] || [])]; d[i] = v; return { ...cur, [f]: d }; });
  };
  const addVacDate = (f: string) => {
    setVacationDates((cur) => ({ ...cur, [f]: [...(cur[f] || []), weekIndexToDate(0)] }));
  };
  const removeVacDate = (f: string, i: number) => {
    setVacationDates((cur) => { const d = [...(cur[f] || [])]; d.splice(i, 1); return { ...cur, [f]: d }; });
  };
  const updateNight = (i: number, field: string, v: string) => {
    setConfig((c) => { const nc = [...c.night_call]; nc[i] = { ...nc[i], [field]: field === 'group' ? v : Number(v) }; return { ...c, night_call: nc }; });
  };
  const updateWeekend = (i: number, field: string, v: string) => {
    setConfig((c) => { const wc = [...c.weekend_call]; wc[i] = { ...wc[i], [field]: field === 'group' ? v : Number(v) }; return { ...c, weekend_call: wc }; });
  };
  const updateHoliday = (i: number, v: string) => {
    setConfig((c) => { const h = [...c.holiday_dates]; h[i] = v; return { ...c, holiday_dates: h }; });
  };
  const addHoliday = () => setConfig((c) => ({ ...c, holiday_dates: [...c.holiday_dates, '2026-07-04'] }));
  const removeHoliday = (i: number) => setConfig((c) => { const h = [...c.holiday_dates]; h.splice(i, 1); return { ...c, holiday_dates: h }; });

  const toggleStandingRule = (i: number, field: 'active' | 'strength') => {
    setStandingRules((rules) => {
      const next = [...rules];
      if (field === 'active') next[i] = { ...next[i], active: !next[i].active };
      else next[i] = { ...next[i], strength: next[i].strength === 'hard' ? 'soft' : 'hard' };
      return next;
    });
  };

  const toggleAnnualRule = (i: number, field: 'active' | 'strength') => {
    setConfig((c) => {
      const rules = [...(c.annual_rules?.rules || [])];
      if (field === 'active') rules[i] = { ...rules[i], active: !rules[i].active };
      else rules[i] = { ...rules[i], strength: rules[i].strength === 'hard' ? 'soft' : 'hard' };
      return { ...c, annual_rules: { rules } };
    });
  };

  // Solver
  const buildRequest = useCallback((): any => {
    const weekPairs: Record<string, number[]> = {};
    for (const f of allFellows) {
      const dates = vacationDates[f];
      if (dates && dates.length > 0) weekPairs[f] = dates.map(dateToWeekIndex);
    }
    return {
      ...config,
      fellow_week_pairs: weekPairs,
      standing_rules: standingRules.filter((r) => r.active),
    };
  }, [config, allFellows, vacationDates, standingRules]);

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buildRequest, cancelSolve]);

  const handleEvent = useCallback((type: string, data: any) => {
    if (type === 'status') {
      if (data.phase === 'building' || data.phase === 'built') {
        setSolverStatus('building');
        if (data.vars) setFormulaInfo(`${data.vars.toLocaleString()} vars, ${data.constraints.toLocaleString()} constraints`);
      } else if (data.phase === 'feasibility') setSolverStatus('feasibility');
      else setSolverStatus('optimizing');
    } else if (type === 'solution') {
      setSolution(data); setPenalty(data.soft_penalty); setElapsed(data.elapsed);
      setSolverStatus('optimizing');
    } else if (type === 'done') {
      setSolverStatus('done'); setPenalty(data.optimal_penalty); setElapsed(data.total_seconds);
    } else if (type === 'error') {
      setError(data.message); setSolverStatus('error');
    }
  }, []);

  const isRunning = solverStatus === 'building' || solverStatus === 'feasibility' || solverStatus === 'optimizing';
  const groupNames = Object.keys(config.fellow_groups);

  // =========================================================================
  // CONFIG MODE
  // =========================================================================
  if (mode === 'config') {
    return (
      <main className="app-shell single-pane">
        <div className="config-view">
          <div className="config-top-bar">
            <div>
              <p className="eyebrow">Stanford fellowship scheduling</p>
              <h1>Schedule Configuration</h1>
            </div>
            <div className="config-top-actions">
              <label className="inline-field">
                <span>Year</span>
                <select value={selectedFile} onChange={(e) => { setSelectedFile(e.target.value); loadConfig(e.target.value); }}>
                  {configFiles.annual.map((f) => <option key={f} value={f}>{f.replace('.yaml', '')}</option>)}
                </select>
              </label>
              <label className="inline-field">
                <span>Weeks</span>
                <input type="number" value={W} min={1} max={53}
                  onChange={(e) => setConfig((c) => ({ ...c, num_weeks: Number(e.target.value) || 52 }))}
                  style={{ width: '4rem' }} />
              </label>
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
              <div className="groups-grid">
                {Object.entries(config.fellow_groups).map(([group, fellows]) => (
                  <label className="field-group" key={group}>
                    <span>{group} <em className="count">({fellows.length})</em></span>
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

            {/* Night Call */}
            <section className="config-card">
              <h2>Night Call Distribution</h2>
              <table className="config-table">
                <thead><tr><th>Group</th><th>Total Nights (per fellow)</th><th>Friday Nights (per fellow)</th></tr></thead>
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
              <BudgetBar label="Total nights" configured={nightBudget.configured} required={nightBudget.required} unit={`${W} wks × 7`} />
              <BudgetBar label="Friday nights" configured={nightBudget.fridayConfigured} required={nightBudget.fridayRequired} unit={`${W} wks × 1`} />
            </section>

            {/* Weekend Call */}
            <section className="config-card">
              <h2>Weekend Call Distribution</h2>
              <table className="config-table">
                <thead><tr><th>Group</th><th>NCC Total (per fellow)</th><th>Stroke Total (per fellow)</th></tr></thead>
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
              <BudgetBar label="Weekend NCC" configured={weekendBudget.nccConfigured} required={weekendBudget.nccRequired} unit={`${W} wks × 2`} />
              <BudgetBar label="Weekend Stroke" configured={weekendBudget.strokeConfigured} required={weekendBudget.strokeRequired} unit={`${W} wks × 1`} />
            </section>

            {/* Vacation Requests */}
            <section className="config-card">
              <h2>Vacation Requests</h2>
              <p className="hint">First 3 per fellow are hard; extras are soft.</p>
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
                            <button type="button" onClick={() => removeVacDate(fellow, i)}>×</button>
                          </span>
                        ))}
                        <button type="button" className="add-btn" onClick={() => addVacDate(fellow)}>+ date</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* Holidays */}
            <section className="config-card">
              <h2>Holiday Dates</h2>
              <div className="week-list">
                {config.holiday_dates.map((d, i) => (
                  <span key={i} className="week-input">
                    <input type="date" value={d} onChange={(e) => updateHoliday(i, e.target.value)} />
                    <button type="button" onClick={() => removeHoliday(i)}>×</button>
                  </span>
                ))}
                <button type="button" className="add-btn" onClick={addHoliday}>+ date</button>
              </div>
            </section>

            {/* Standing Rules */}
            <section className="config-card">
              <h2>Standing Rules</h2>
              <p className="hint">From stanford-fellowship.yaml. Toggle active/strength for this session.</p>
              <RulesTable rules={standingRules} onToggle={toggleStandingRule} />
            </section>

            {/* Annual Rules */}
            {(config.annual_rules?.rules?.length ?? 0) > 0 && (
              <section className="config-card">
                <h2>Annual Rules</h2>
                <RulesTable rules={config.annual_rules!.rules} onToggle={toggleAnnualRule} />
              </section>
            )}
          </div>
        </div>
      </main>
    );
  }

  // =========================================================================
  // SCHEDULE MODE
  // =========================================================================
  return (
    <main className="app-shell single-pane">
      <div className="schedule-view">
        <div className="schedule-header">
          <button className="back-btn" onClick={() => { cancelSolve(); setMode('config'); }}>← Configuration</button>
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
            {scheduleTab === 'weekly' && <ScheduleTable columns={Object.keys(solution.weekly_assignments)} rows={solution.weekly_assignments} />}
            {scheduleTab === 'weekend' && <ScheduleTable columns={solution.weekend_assignments.length > 0 ? Object.keys(solution.weekend_assignments[0]) : []} rows={pivotWeeklyList(solution.weekend_assignments)} />}
            {scheduleTab === 'night' && <ScheduleTable columns={solution.night_assignments.length > 0 ? Object.keys(solution.night_assignments[0]) : []} rows={pivotWeeklyList(solution.night_assignments)} />}
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

function BudgetBar({ label, configured, required, unit }: { label: string; configured: number; required: number; unit: string }) {
  const match = configured === required;
  const cls = match ? 'budget-ok' : configured > required ? 'budget-over' : 'budget-under';
  return (
    <div className={`budget-bar ${cls}`}>
      <span className="budget-label">{label}:</span>
      <strong>{configured}</strong>
      <span className="budget-sep">/</span>
      <span>{required} needed ({unit})</span>
      <span className="budget-status">{match ? '✓' : configured > required ? `+${configured - required} over` : `${required - configured} short`}</span>
    </div>
  );
}

function RulesTable({ rules, onToggle }: { rules: Rule[]; onToggle: (i: number, field: 'active' | 'strength') => void }) {
  if (!rules.length) return <p className="hint">No rules loaded.</p>;
  return (
    <table className="config-table rules-table">
      <thead><tr><th>Active</th><th>Name</th><th>Kind</th><th>Strength</th><th>Groups</th></tr></thead>
      <tbody>
        {rules.map((r, i) => (
          <tr key={i} className={r.active ? '' : 'rule-inactive'}>
            <td><input type="checkbox" checked={r.active} onChange={() => onToggle(i, 'active')} /></td>
            <td>{r.name}</td>
            <td className="rule-kind">{r.kind}</td>
            <td>
              {r.strength && (
                <button className={`strength-toggle ${r.strength}`} onClick={() => onToggle(i, 'strength')}>
                  {r.strength}
                </button>
              )}
            </td>
            <td className="rule-groups">{(r.fellow_groups || []).join(', ')}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

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
