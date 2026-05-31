import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';

const API_BASE = 'http://127.0.0.1:5000';
const WEEK_COUNT = 52;

type ScheduleRequest = {
  fellow_groups: Record<string, string[]>;
  shifts: string[];
  fellow_week_pairs: Record<string, number[]>;
};

type FullSolution = {
  weekly_assignments: Record<string, string[]>;
  weekend_assignments: Record<string, string>[];
  night_assignments: Record<string, string>[];
  soft_penalty: number;
  elapsed: number;
};

type SolverStatus = 'idle' | 'building' | 'feasibility' | 'optimizing' | 'done' | 'error';

const emptyRequest: ScheduleRequest = {
  fellow_groups: {},
  shifts: [],
  fellow_week_pairs: {},
};

const shiftOrder = ['NCC1', 'NCC2', 'Extra', 'Swing', 'Stroke', 'Telestroke/Clinic', 'Stroke_Supervisory'];

function App() {
  const [request, setRequest] = useState<ScheduleRequest>(emptyRequest);
  const [loadingDefaults, setLoadingDefaults] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Progressive solver state
  const [solverStatus, setSolverStatus] = useState<SolverStatus>('idle');
  const [solution, setSolution] = useState<FullSolution | null>(null);
  const [penalty, setPenalty] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState<number>(0);
  const [formulaInfo, setFormulaInfo] = useState<string>('');
  const abortRef = useRef<AbortController | null>(null);

  // Active tab for schedule views
  const [activeTab, setActiveTab] = useState<'weekly' | 'weekend' | 'night'>('weekly');

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
    return () => { cancelled = true; };
  }, []);

  const groupEntries = useMemo(() => Object.entries(request.fellow_groups), [request.fellow_groups]);

  const requestFellows = useMemo(() => {
    return groupEntries.flatMap(([, fellows]) => fellows);
  }, [groupEntries]);

  const visibleWeekPairs = useMemo(() => {
    return requestFellows.map((fellow) => ({
      fellow,
      weeks: request.fellow_week_pairs[fellow] ?? [],
    }));
  }, [request.fellow_week_pairs, requestFellows]);

  const updateGroup = (groupName: string, value: string) => {
    const fellows = value.split('\n').map((item) => item.trim()).filter(Boolean);
    setRequest((current) => ({
      ...current,
      fellow_groups: { ...current.fellow_groups, [groupName]: fellows },
    }));
  };

  const updateWeek = (fellow: string, index: number, value: string) => {
    const nextWeek = clampWeek(Number(value));
    setRequest((current) => {
      const weeks = [...(current.fellow_week_pairs[fellow] ?? [])];
      weeks[index] = nextWeek;
      return { ...current, fellow_week_pairs: { ...current.fellow_week_pairs, [fellow]: weeks } };
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
      return { ...current, fellow_week_pairs: { ...current.fellow_week_pairs, [fellow]: weeks } };
    });
  };

  const buildRequest = (): ScheduleRequest => ({
    ...request,
    fellow_week_pairs: Object.fromEntries(
      requestFellows.map((fellow) => [fellow, request.fellow_week_pairs[fellow] ?? []]),
    ),
  });

  const cancelSolve = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    if (solverStatus !== 'done') {
      setSolverStatus('idle');
    }
  }, [solverStatus]);

  const generateSchedule = useCallback(async () => {
    cancelSolve();
    setSolverStatus('building');
    setSolution(null);
    setPenalty(null);
    setElapsed(0);
    setError(null);
    setFormulaInfo('');

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${API_BASE}/api/schedule/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildRequest()),
        signal: controller.signal,
      });

      if (!response.ok) {
        const msg = await errorMessage(response);
        setError(msg);
        setSolverStatus('error');
        return;
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const events = buffer.split('\n\n');
        buffer = events.pop() || '';

        for (const eventBlock of events) {
          const lines = eventBlock.split('\n');
          let eventType = '';
          let eventData = '';
          for (const line of lines) {
            if (line.startsWith('event: ')) eventType = line.slice(7);
            else if (line.startsWith('data: ')) eventData = line.slice(6);
          }
          if (!eventType || !eventData) continue;

          const data = JSON.parse(eventData);
          handleSolverEvent(eventType, data);
        }
      }
    } catch (err) {
      if ((err as Error).name === 'AbortError') return;
      setError(err instanceof Error ? err.message : 'Connection lost.');
      setSolverStatus('error');
    }
  }, [request]);

  const handleSolverEvent = (type: string, data: any) => {
    switch (type) {
      case 'status':
        if (data.phase === 'building' || data.phase === 'built') {
          setSolverStatus('building');
          if (data.vars) {
            setFormulaInfo(`${data.vars.toLocaleString()} vars, ${data.constraints.toLocaleString()} constraints`);
          }
        } else if (data.phase === 'feasibility') {
          setSolverStatus('feasibility');
        } else if (data.phase === 'coarse_scan' || data.phase === 'fine_scan') {
          setSolverStatus('optimizing');
        }
        break;
      case 'solution':
        setSolution(data as FullSolution);
        setPenalty(data.soft_penalty);
        setElapsed(data.elapsed);
        if (solverStatus === 'feasibility' || solverStatus === 'building') {
          setSolverStatus('optimizing');
        }
        break;
      case 'done':
        setSolverStatus('done');
        setPenalty(data.optimal_penalty);
        setElapsed(data.total_seconds);
        break;
      case 'error':
        setError(data.message);
        setSolverStatus('error');
        break;
    }
  };

  const isRunning = solverStatus === 'building' || solverStatus === 'feasibility' || solverStatus === 'optimizing';

  return (
    <main className="app-shell">
      <section className="sidebar" aria-label="Schedule inputs">
        <div className="title-block">
          <p className="eyebrow">Stanford fellowship scheduling</p>
          <h1>Fellow Scheduler</h1>
        </div>

        {loadingDefaults ? <p className="status-line">Loading defaults...</p> : null}

        <div className="input-stack">
          {groupEntries.map(([groupName, fellows]) => {
            const label = `${groupName} fellows`;
            return (
              <label className="field-group" key={groupName}>
                <span>{label}</span>
                <textarea
                  aria-label={label}
                  value={fellows.join('\n')}
                  onChange={(event) => updateGroup(groupName, event.target.value)}
                  rows={Math.min(8, Math.max(3, fellows.length + 1))}
                  spellCheck={false}
                />
              </label>
            );
          })}
        </div>
      </section>

      <section className="workspace">
        <div className="toolbar">
          <div>
            <h2>Requests</h2>
            <p>Edit vacation/request weeks for NCC and Stroke fellows.</p>
          </div>
          <div className="actions">
            <button className="primary" onClick={generateSchedule} disabled={loadingDefaults}>
              {isRunning ? 'Solving...' : 'Generate schedule'}
            </button>
            {isRunning && (
              <button onClick={cancelSolve}>Cancel</button>
            )}
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

        {(isRunning || solverStatus === 'done') && (
          <ProgressPanel
            status={solverStatus}
            penalty={penalty}
            elapsed={elapsed}
            formulaInfo={formulaInfo}
          />
        )}

        {solution ? (
          <div className="results">
            <div className="tab-bar">
              <button className={activeTab === 'weekly' ? 'tab active' : 'tab'} onClick={() => setActiveTab('weekly')}>
                Weekly Shifts
              </button>
              <button className={activeTab === 'weekend' ? 'tab active' : 'tab'} onClick={() => setActiveTab('weekend')}>
                Weekend Call
              </button>
              <button className={activeTab === 'night' ? 'tab active' : 'tab'} onClick={() => setActiveTab('night')}>
                Night Call
              </button>
            </div>

            {activeTab === 'weekly' && (
              <ScheduleTable
                title="Per-fellow weekly schedule"
                columns={Object.keys(solution.weekly_assignments)}
                rows={solution.weekly_assignments}
              />
            )}
            {activeTab === 'weekend' && (
              <ScheduleTable
                title="Weekend call assignments"
                columns={solution.weekend_assignments.length > 0 ? Object.keys(solution.weekend_assignments[0]) : []}
                rows={pivotWeeklyList(solution.weekend_assignments)}
              />
            )}
            {activeTab === 'night' && (
              <ScheduleTable
                title="Night call assignments"
                columns={solution.night_assignments.length > 0 ? Object.keys(solution.night_assignments[0]) : []}
                rows={pivotWeeklyList(solution.night_assignments)}
              />
            )}
          </div>
        ) : (
          solverStatus === 'idle' && <EmptyState />
        )}
      </section>
    </main>
  );
}

function ProgressPanel({ status, penalty, elapsed, formulaInfo }: {
  status: SolverStatus;
  penalty: number | null;
  elapsed: number;
  formulaInfo: string;
}) {
  const statusText = {
    building: 'Building formula...',
    feasibility: 'Checking feasibility...',
    optimizing: 'Optimizing...',
    done: 'Done',
    idle: '',
    error: 'Error',
  }[status];

  const elapsedStr = elapsed < 60
    ? `${elapsed.toFixed(1)}s`
    : `${Math.floor(elapsed / 60)}m ${Math.floor(elapsed % 60)}s`;

  return (
    <div className={`progress-panel ${status === 'done' ? 'done' : ''}`}>
      <span className="progress-indicator">
        {status === 'done' ? '✓' : '●'}
      </span>
      <span className="progress-status">{statusText}</span>
      {penalty !== null && (
        <span className="progress-penalty">Penalty: <strong>{penalty}</strong></span>
      )}
      {elapsed > 0 && (
        <span className="progress-elapsed">{elapsedStr}</span>
      )}
      {formulaInfo && status === 'building' && (
        <span className="progress-formula">{formulaInfo}</span>
      )}
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
      <p>Generate a schedule to see weekly shifts, weekend call, and night call — all optimized jointly.</p>
    </section>
  );
}

function pivotWeeklyList(weeklyList: Record<string, string>[]): Record<string, string[]> {
  if (weeklyList.length === 0) return {};
  const keys = Object.keys(weeklyList[0]);
  const result: Record<string, string[]> = {};
  for (const key of keys) {
    result[key] = weeklyList.map((week) => week[key] ?? '');
  }
  return result;
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
