import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './App.css';
import { TopBar } from './components/TopBar';
import { Sidebar, SidebarSection } from './components/Sidebar';
import { ImportSchedule } from './components/ImportSchedule';
import { CoverageTotalsTable } from './components/CoverageTotalsTable';
import { RuleCard } from './components/RuleCard';
import { RuleEditor } from './components/RuleEditor';
import { RulePalette } from './components/RulePalette';
import { CallRuleEditor, createDefaultCallRule } from './components/CallRuleEditor';
import { PaletteRule, ShiftTotalRule, Relation, RuleFeasibility, CallRule } from './types';
import { DiagnosePanel } from './components/DiagnosePanel';

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
// Night/Weekend rule format converters (YAML ↔ React component format)
// ---------------------------------------------------------------------------

function yamlNightRuleToReact(rule: any): PaletteRule {
  const base = { name: rule.name, groups: rule.groups || [], strength: rule.strength || 'hard', active: rule.active !== false, description: rule.description || '' };
  switch (rule.type) {
    case 'night_spacing':
      return { ...base, type: rule.type, params: { maxNights: rule.max_nights ?? 1, windowDays: rule.window_days ?? 3 } };
    case 'night_blocked_services':
      return { ...base, type: rule.type, params: { exactServices: rule.exact_services || [], substringServices: rule.substring_services || [] } };
    case 'night_holiday_eligibility':
      return { ...base, type: rule.type, params: { allowedServices: rule.allowed_services || [] } };
    case 'night_penalties':
      return { ...base, type: rule.type, params: { weights: rule.weights || {} } };
    case 'night_sunday_following':
      return { ...base, type: rule.type, params: { preferredServices: rule.preferred_services || [] } };
    default:
      return { ...base, type: rule.type, params: rule.params || {} } as any;
  }
}

function yamlWeekendRuleToReact(rule: any): PaletteRule {
  const base = { name: rule.name, groups: rule.groups || [], strength: rule.strength || 'hard', active: rule.active !== false, description: rule.description || '' };
  switch (rule.type) {
    case 'weekend_spacing':
      return { ...base, type: rule.type, params: { maxWeekends: rule.max_weekends ?? 1, windowWeeks: rule.window_weekends ?? 4 } };
    case 'weekend_blocked_services':
      return { ...base, type: rule.type, params: { exactServices: rule.exact_services || [], substringServices: rule.substring_services || [] } };
    case 'weekend_stroke_eligibility':
      return { ...base, type: rule.type, params: { eligibleServices: rule.eligible_services || [] } };
    case 'weekend_penalties':
      return { ...base, type: rule.type, params: { weights: rule.weights || {} } };
    default:
      return { ...base, type: rule.type, params: rule.params || {} } as any;
  }
}

function reactNightRuleToYaml(rule: PaletteRule): any {
  const base = { name: rule.name, type: rule.type, groups: (rule as any).groups, active: rule.active, description: (rule as any).description };
  const params = (rule as any).params || {};
  switch (rule.type) {
    case 'night_spacing':
      return { ...base, max_nights: params.maxNights, window_days: params.windowDays };
    case 'night_blocked_services':
      return { ...base, exact_services: params.exactServices, substring_services: params.substringServices };
    case 'night_holiday_eligibility':
      return { ...base, allowed_services: params.allowedServices };
    case 'night_penalties':
      return { ...base, weights: params.weights };
    case 'night_sunday_following':
      return { ...base, preferred_services: params.preferredServices };
    default:
      return { ...base, ...params };
  }
}

function reactWeekendRuleToYaml(rule: PaletteRule): any {
  const base = { name: rule.name, type: rule.type, groups: (rule as any).groups, active: rule.active, description: (rule as any).description };
  const params = (rule as any).params || {};
  switch (rule.type) {
    case 'weekend_spacing':
      return { ...base, max_weekends: params.maxWeekends, window_weekends: params.windowWeeks };
    case 'weekend_blocked_services':
      return { ...base, exact_services: params.exactServices, substring_services: params.substringServices };
    case 'weekend_stroke_eligibility':
      return { ...base, eligible_services: params.eligibleServices };
    case 'weekend_penalties':
      return { ...base, weights: params.weights };
    default:
      return { ...base, ...params };
  }
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
  const [paletteRules, setPaletteRules] = useState<PaletteRule[]>([]);
  const [nightRules, setNightRules] = useState<PaletteRule[]>([]);
  const [weekendRules, setWeekendRules] = useState<PaletteRule[]>([]);
  const [callRules, setCallRules] = useState<CallRule[]>([]);
  const [editingCallRuleIdx, setEditingCallRuleIdx] = useState<number | null>(null);
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
  const [showDiagnose, setShowDiagnose] = useState(false);

  const [activeSection, setActiveSection] = useState<SidebarSection>('fellows');
  const [hasDraft, setHasDraft] = useState(false);
  const [lockedAssignments, setLockedAssignments] = useState<Record<string, string[]>>({});
  const [importData, setImportData] = useState<any | null>(null);
  const [editingRuleIdx, setEditingRuleIdx] = useState<number | null>(null);
  const [showPalette, setShowPalette] = useState(false);
  const [feasibility, setFeasibility] = useState<Record<string, RuleFeasibility>>({});
  const draftTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [showNightPalette, setShowNightPalette] = useState(false);
  const [showWeekendPalette, setShowWeekendPalette] = useState(false);
  const [editingNightRuleIdx, setEditingNightRuleIdx] = useState<number | null>(null);
  const [editingWeekendRuleIdx, setEditingWeekendRuleIdx] = useState<number | null>(null);

  const W = config.num_weeks || 52;

  // Compute actual num_days and Friday count from horizon_start (matching solver logic)
  const { numDays, numFridays } = useMemo(() => {
    const horizonStr = config.horizon_start || '2026-07-01';
    const parts = horizonStr.split('-');
    const startYear = parseInt(parts[0]), startMonth = parseInt(parts[1]) - 1, startDay = parseInt(parts[2]);
    const start = new Date(startYear, startMonth, startDay);
    const end = new Date(startYear + 1, startMonth, startDay);
    end.setDate(end.getDate() - 1);
    const totalDays = Math.round((end.getTime() - start.getTime()) / 86400000) + 1;
    const startDow = start.getDay() === 0 ? 6 : start.getDay() - 1; // JS Sun=0 → Mon=0..Sun=6
    let fridays = 0;
    for (let d = 0; d < totalDays; d++) {
      if ((startDow + d) % 7 === 4) fridays++;
    }
    return { numDays: totalDays, numFridays: fridays };
  }, [config.horizon_start]);

  // Load config file list
  useEffect(() => {
    fetch(`${API_BASE}/api/configs`)
      .then((r) => r.json())
      .then((data) => {
        setConfigFiles(data);
        if (data.annual.length > 0) {
          const v2 = data.annual.find((f: string) => f.includes('-v2'));
          const first = v2 || data.annual[0];
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
    // Determine standing config name based on whether annual config is v2
    const standingName = filename.includes('-v2')
      ? 'stanford-fellowship-v2.yaml'
      : 'stanford-fellowship-v2.yaml';
    Promise.all([
      fetch(`${API_BASE}/api/config/annual/${filename}/draft`).then((r) => r.json()),
      fetch(`${API_BASE}/api/config/standing/${standingName}`).then((r) => r.json()),
    ])
      .then(([annual, standing]) => {
        setHasDraft(annual._has_draft || false);
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
        const annualRules = annual.rules || [];
        if (annualRules.length > 0 && annualRules[0].type) {
          setPaletteRules(annualRules as PaletteRule[]);
        } else {
          setPaletteRules([]);
        }
        setNightRules((annual.night_rules?.length ? annual.night_rules : (standing.night_rules || [])).map(yamlNightRuleToReact));
        setWeekendRules((annual.weekend_rules?.length ? annual.weekend_rules : (standing.weekend_rules || [])).map(yamlWeekendRuleToReact));
        setCallRules(annual.call_rules || []);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  const allFellows = useMemo(() =>
    Object.entries(config.fellow_groups).flatMap(([, f]) => f.map((s) => s.trim()).filter(Boolean)),
    [config.fellow_groups],
  );

  // Budget calculations (values are group totals, sum directly)
  const nightBudget = useMemo(() => {
    const required = numDays;
    const configured = config.night_call.reduce((s, e) => s + (e.total_nights || 0), 0);
    const fridayRequired = numFridays;
    const fridayConfigured = config.night_call.reduce((s, e) => s + (e.friday_nights || 0), 0);
    return { required, configured, fridayRequired, fridayConfigured };
  }, [config.night_call, numDays, numFridays]);

  const weekendBudget = useMemo(() => {
    const nccRequired = W * 2;
    const nccConfigured = config.weekend_call.reduce((s, e) => s + (e.ncc_total || 0), 0);
    const strokeRequired = W;
    const strokeConfigured = config.weekend_call.reduce((s, e) => s + (e.stroke_total || 0), 0);
    return { nccRequired, nccConfigured, strokeRequired, strokeConfigured };
  }, [config.weekend_call, W]);

  // Config mutators
  const updateGroup = (g: string, v: string) => {
    setConfig((c) => ({ ...c, fellow_groups: { ...c.fellow_groups, [g]: v.split('\n') } }));
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

  const togglePaletteRuleActive = (ruleName: string) => {
    setPaletteRules(prev => prev.map(r =>
      r.name === ruleName ? { ...r, active: !r.active } : r
    ));
  };

  const togglePaletteRuleStrength = (ruleName: string) => {
    setPaletteRules(prev => prev.map(r =>
      r.name === ruleName ? { ...r, strength: r.strength === 'hard' ? 'soft' : 'hard' } : r
    ));
  };

  const changePaletteRuleCount = (ruleName: string, count: number) => {
    setPaletteRules(prev => prev.map(r =>
      r.name === ruleName && r.type === 'shift_total' ? { ...r, count } : r
    ));
  };

  const changePaletteRuleRelation = (ruleName: string, relation: Relation) => {
    setPaletteRules(prev => prev.map(r =>
      r.name === ruleName && r.type === 'shift_total' ? { ...r, relation } : r
    ));
  };

  const savePaletteRule = (updated: PaletteRule) => {
    if (editingRuleIdx !== null) {
      setPaletteRules(prev => prev.map((r, i) => i === editingRuleIdx ? updated : r));
    }
    setEditingRuleIdx(null);
  };

  const removePaletteRule = (idx: number) => {
    setPaletteRules(prev => prev.filter((_, i) => i !== idx));
    setEditingRuleIdx(null);
  };

  const addPaletteRule = (rule: PaletteRule) => {
    setPaletteRules(prev => {
      setEditingRuleIdx(prev.length);
      return [...prev, rule];
    });
    setShowPalette(false);
  };

  const addNightRule = (rule: PaletteRule) => {
    setNightRules(prev => {
      setEditingNightRuleIdx(prev.length);
      return [...prev, rule];
    });
    setShowNightPalette(false);
  };

  const addWeekendRule = (rule: PaletteRule) => {
    setWeekendRules(prev => {
      setEditingWeekendRuleIdx(prev.length);
      return [...prev, rule];
    });
    setShowWeekendPalette(false);
  };

  const saveNightRule = (updated: PaletteRule) => {
    if (editingNightRuleIdx !== null) {
      setNightRules(prev => prev.map((r, i) => i === editingNightRuleIdx ? updated : r));
    }
    setEditingNightRuleIdx(null);
  };

  const saveWeekendRule = (updated: PaletteRule) => {
    if (editingWeekendRuleIdx !== null) {
      setWeekendRules(prev => prev.map((r, i) => i === editingWeekendRuleIdx ? updated : r));
    }
    setEditingWeekendRuleIdx(null);
  };

  const removeNightRule = (idx: number) => {
    setNightRules(prev => prev.filter((_, i) => i !== idx));
    setEditingNightRuleIdx(null);
  };

  const removeWeekendRule = (idx: number) => {
    setWeekendRules(prev => prev.filter((_, i) => i !== idx));
    setEditingWeekendRuleIdx(null);
  };

  // Feasibility checking
  const checkFeasibility = useCallback(async () => {
    if (paletteRules.length === 0) return;

    const activeRules = paletteRules.filter(r => r.active);
    if (activeRules.length === 0) return;

    // Mark all as checking
    const checking: Record<string, RuleFeasibility> = {};
    for (const r of activeRules) {
      checking[r.name] = { solo: 'checking', pairwise: 'unchecked' };
    }
    setFeasibility(checking);

    const configPayload = {
      fellow_groups: config.fellow_groups,
      shifts: config.shifts,
      num_weeks: config.num_weeks || 52,
      rules: paletteRules,
    };

    // Shift totals for a group are tested collectively (they define what's allowed).
    // Non-shift-total rules are tested individually.
    const shiftTotals = activeRules.filter(r => r.type === 'shift_total');
    const constraintRules = activeRules.filter(r => r.type !== 'shift_total');

    // Group shift_totals by group for collective testing
    const totalsByGroup: Record<string, PaletteRule[]> = {};
    for (const r of shiftTotals) {
      for (const g of (r.groups || [])) {
        if (!totalsByGroup[g]) totalsByGroup[g] = [];
        totalsByGroup[g].push(r);
      }
    }

    const soloResults: Record<string, boolean> = {};
    const concurrency = 3;

    // Test each group's shift_totals collectively (one probe per group)
    const groupProbes = Object.entries(totalsByGroup);
    for (let i = 0; i < groupProbes.length; i += concurrency) {
      const batch = groupProbes.slice(i, i + concurrency);
      await Promise.all(batch.map(async ([group, rules]) => {
        try {
          const resp = await fetch(`${API_BASE}/api/feasibility/check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              config: configPayload,
              standing_rules: standingRules,
              rules: rules,
            }),
          });
          const data = await resp.json();
          const status = data.satisfiable ? 'pass' as const : 'fail' as const;
          for (const r of rules) {
            soloResults[r.name] = data.satisfiable;
          }
          setFeasibility(prev => {
            const next = { ...prev };
            for (const r of rules) { next[r.name] = { ...next[r.name], solo: status }; }
            return next;
          });
        } catch {
          for (const r of rules) { soloResults[r.name] = false; }
          setFeasibility(prev => {
            const next = { ...prev };
            for (const r of rules) { next[r.name] = { ...next[r.name], solo: 'fail' }; }
            return next;
          });
        }
      }));
    }

    // Test constraint rules individually
    for (let i = 0; i < constraintRules.length; i += concurrency) {
      const batch = constraintRules.slice(i, i + concurrency);
      await Promise.all(batch.map(async (rule) => {
        try {
          const resp = await fetch(`${API_BASE}/api/feasibility/check`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              config: configPayload,
              standing_rules: standingRules,
              rule,
            }),
          });
          const data = await resp.json();
          soloResults[rule.name] = data.satisfiable;
          setFeasibility(prev => ({
            ...prev,
            [rule.name]: { ...prev[rule.name], solo: data.satisfiable ? 'pass' : 'fail' },
          }));
        } catch {
          soloResults[rule.name] = false;
          setFeasibility(prev => ({
            ...prev,
            [rule.name]: { ...prev[rule.name], solo: 'fail' },
          }));
        }
      }));
    }

    // Pairwise checks: only for rules that passed solo
    const passedRules = activeRules.filter(r => soloResults[r.name]);

    // Mark pairwise as checking for passed rules
    setFeasibility(prev => {
      const next = { ...prev };
      for (const r of passedRules) {
        next[r.name] = { ...next[r.name], pairwise: 'checking' };
      }
      return next;
    });

    for (const rule of passedRules) {
      let allPairsPass = true;
      const others = passedRules.filter(r => r.name !== rule.name);

      for (let i = 0; i < others.length; i += concurrency) {
        const batch = others.slice(i, i + concurrency);
        const results = await Promise.all(batch.map(async (other) => {
          try {
            const resp = await fetch(`${API_BASE}/api/feasibility/check`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                config: configPayload,
                standing_rules: standingRules,
                rule,
                pair_with: other,
              }),
            });
            const data = await resp.json();
            return data.satisfiable;
          } catch {
            return false;
          }
        }));
        if (results.some(r => !r)) {
          allPairsPass = false;
          break;
        }
      }

      setFeasibility(prev => ({
        ...prev,
        [rule.name]: { ...prev[rule.name], pairwise: allPairsPass ? 'pass' : 'fail' },
      }));
    }
  }, [paletteRules, config, standingRules]);

  // Draft auto-save: debounce 3s after any palette rule change
  const saveDraft = useCallback(() => {
    if (!selectedFile || paletteRules.length === 0) return;
    const body = {
      ...config,
      rules: [...paletteRules, ...callRules],
      night_rules: nightRules,
      weekend_rules: weekendRules,
      fellow_week_pairs: (() => {
        const wp: Record<string, number[]> = {};
        for (const f of Object.values(config.fellow_groups).flat()) {
          const dates = vacationDates[f];
          if (dates && dates.length > 0) wp[f] = dates.map(dateToWeekIndex);
          else wp[f] = [];
        }
        return wp;
      })(),
    };
    fetch(`${API_BASE}/api/config/annual/${selectedFile}/draft`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(() => setHasDraft(true)).catch(() => {});
  }, [config, paletteRules, vacationDates, selectedFile, nightRules, weekendRules, callRules]);

  // Auto-save on palette rule edits
  useEffect(() => {
    if (paletteRules.length === 0) return;
    if (draftTimerRef.current) clearTimeout(draftTimerRef.current);
    draftTimerRef.current = setTimeout(saveDraft, 3000);
    return () => { if (draftTimerRef.current) clearTimeout(draftTimerRef.current); };
  }, [paletteRules, saveDraft]);

  const publishDraft = useCallback(() => {
    if (!selectedFile) return;
    fetch(`${API_BASE}/api/config/annual/${selectedFile}/publish`, { method: 'POST' })
      .then((r) => { if (r.ok) setHasDraft(false); })
      .catch(() => {});
  }, [selectedFile]);

  const discardDraft = useCallback(() => {
    if (!selectedFile) return;
    fetch(`${API_BASE}/api/config/annual/${selectedFile}/draft`, { method: 'DELETE' })
      .then((r) => { if (r.ok) { setHasDraft(false); loadConfig(selectedFile); } })
      .catch(() => {});
  }, [selectedFile, loadConfig]);

  // Import handlers
  const handleImportFile = useCallback(async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const resp = await fetch(`${API_BASE}/api/schedule/import`, {
        method: 'POST',
        body: formData,
      });
      if (!resp.ok) {
        const err = await resp.json();
        setError(err.error || 'Import failed');
        return;
      }
      const data = await resp.json();
      setImportData(data);
      setActiveSection('fellows'); // switch to main panel to show preview
    } catch (err) {
      setError('Failed to upload file');
    }
  }, []);

  const confirmImport = useCallback(() => {
    if (!importData) return;
    setLockedAssignments(importData.assignments);
    setConfig(prev => {
      return {
        ...prev,
        num_weeks: importData.num_weeks,
        shifts: Array.from(new Set([...prev.shifts, ...importData.shifts_found])),
      };
    });
    setImportData(null);
  }, [importData]);

  const cancelImport = useCallback(() => {
    setImportData(null);
  }, []);

  // Solver
  const buildRequest = useCallback((): any => {
    const weekPairs: Record<string, number[]> = {};
    for (const f of allFellows) {
      const dates = vacationDates[f];
      if (dates && dates.length > 0) weekPairs[f] = dates.map(dateToWeekIndex);
    }
    // Clean fellow_groups: trim and remove empty entries before sending to solver
    const cleanGroups: Record<string, string[]> = {};
    for (const [group, fellows] of Object.entries(config.fellow_groups)) {
      cleanGroups[group] = fellows.map((s) => s.trim()).filter(Boolean);
    }

    const req: any = {
      ...config,
      fellow_groups: cleanGroups,
      fellow_week_pairs: weekPairs,
      night_rules: nightRules.map(reactNightRuleToYaml),
      weekend_rules: weekendRules.map(reactWeekendRuleToYaml),
    };

    if (paletteRules.length > 0) {
      req.rules = [...paletteRules, ...(callRules as any[])];
      req.standing_rules = standingRules.filter((r) => r.active);
    } else {
      req.rules = [...callRules];
      req.standing_rules = standingRules.filter((r) => r.active);
    }

    if (Object.keys(lockedAssignments).length > 0) {
      req.locked_assignments = lockedAssignments;
    }

    return req;
  }, [config, allFellows, vacationDates, standingRules, paletteRules, lockedAssignments, nightRules, weekendRules, callRules]);

  const cancelSolve = useCallback(() => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null; }
    setSolverStatus('idle');
  }, []);

  const generateSchedule = useCallback(async () => {
    cancelSolve();
    setShowDiagnose(false);
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
  // Main panel content renderer
  // =========================================================================
  function renderMainContent() {
    if (importData) {
      return <ImportSchedule data={importData} onConfirm={confirmImport} onCancel={cancelImport} />;
    }

    if (mode === 'schedule') {
      return (
        <div className="schedule-view">
          <div className="schedule-header">
            <button className="back-btn" onClick={() => { cancelSolve(); setMode('config'); }}>← Configuration</button>
            <ProgressPanel status={solverStatus} penalty={penalty} elapsed={elapsed} formulaInfo={formulaInfo} />
            {isRunning && <button className="cancel-btn" onClick={cancelSolve}>Cancel</button>}
          </div>

          {error && <div className="error-banner" role="alert">{error}</div>}

          {error && error.toLowerCase().includes('infeasible') && !showDiagnose && (
            <button className="primary" onClick={() => setShowDiagnose(true)} style={{ marginTop: '0.5rem' }}>
              Diagnose Conflicts
            </button>
          )}

          {showDiagnose && (
            <DiagnosePanel
              apiBase={API_BASE}
              requestBody={buildRequest()}
              onDisableRule={(name) => {
                setPaletteRules(prev => prev.map(r => r.name === name ? { ...r, active: false } : r));
              }}
              onSoftenRule={(name) => {
                setPaletteRules(prev => prev.map(r => r.name === name ? { ...r, strength: 'soft' } : r));
              }}
              onClose={() => setShowDiagnose(false)}
            />
          )}

          {!showDiagnose && solution ? (
            <>
              <div className="tab-bar">
                {(['weekly', 'weekend', 'night'] as const).map((tab) => (
                  <button key={tab} className={scheduleTab === tab ? 'tab active' : 'tab'} onClick={() => setScheduleTab(tab)}>
                    {tab === 'weekly' ? 'Weekly Shifts' : tab === 'weekend' ? 'Weekend Call' : 'Night Call'}
                  </button>
                ))}
                <button className="export-btn" onClick={async () => {
                  try {
                    const resp = await fetch(`${API_BASE}/api/schedule/export`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(solution),
                    });
                    if (!resp.ok) return;
                    const blob = await resp.blob();
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = 'schedule.xlsx';
                    a.click();
                    URL.revokeObjectURL(url);
                  } catch {}
                }}>Export Excel</button>
              </div>
              {scheduleTab === 'weekly' && <ScheduleTable columns={Object.keys(solution.weekly_assignments)} rows={solution.weekly_assignments} />}
              {scheduleTab === 'weekend' && <ScheduleTable columns={solution.weekend_assignments.length > 0 ? Object.keys(solution.weekend_assignments[0]) : []} rows={pivotWeeklyList(solution.weekend_assignments)} />}
              {scheduleTab === 'night' && <ScheduleTable columns={solution.night_assignments.length > 0 ? Object.keys(solution.night_assignments[0]) : []} rows={pivotWeeklyList(solution.night_assignments)} />}
            </>
          ) : (
            !showDiagnose && (
              <div className="empty-state">
                <h2>{isRunning ? 'Building schedule...' : 'No schedule yet'}</h2>
                <p>{isRunning ? 'The first result will appear within a few seconds.' : 'Go back to configuration and click Generate.'}</p>
              </div>
            )
          )}
        </div>
      );
    }

    // Config mode: section-based rendering
    return (
      <div className="config-view">
        {error && <div className="error-banner" role="alert">{error}</div>}
        {loading && <p className="status-line">Loading config...</p>}

        {activeSection === 'fellows' && (
          <section className="config-card">
            <h2>Fellow Groups</h2>
            <div className="groups-grid">
              {Object.entries(config.fellow_groups).map(([group, fellows]) => {
                const count = fellows.filter((s) => s.trim()).length;
                return (
                  <label className="field-group" key={group}>
                    <span>{group} <em className="count">({count})</em></span>
                    <textarea
                      value={fellows.join('\n')}
                      onChange={(e) => updateGroup(group, e.target.value)}
                      rows={Math.min(6, Math.max(2, fellows.length + 1))}
                      spellCheck={false}
                    />
                  </label>
                );
              })}
            </div>
          </section>
        )}

        {activeSection === 'night' && (
          <section className="config-card">
            <h2>Night Call Distribution</h2>
            <table className="config-table">
              <thead><tr><th>Group</th><th>Total Nights</th><th>Per Fellow</th><th>Friday Nights</th><th>Per Fellow</th></tr></thead>
              <tbody>
                {config.night_call.map((entry, i) => {
                  const gs = (config.fellow_groups[entry.group] || []).length;
                  return (
                    <tr key={i}>
                      <td>
                        <select value={entry.group} onChange={(e) => updateNight(i, 'group', e.target.value)}>
                          {groupNames.map((g) => <option key={g} value={g}>{g} ({(config.fellow_groups[g] || []).length})</option>)}
                        </select>
                      </td>
                      <td><input type="number" value={entry.total_nights} onChange={(e) => updateNight(i, 'total_nights', e.target.value)} /></td>
                      <td className="breakdown">{gs > 0 ? perFellowText(entry.total_nights, gs) : '—'}</td>
                      <td><input type="number" value={entry.friday_nights} onChange={(e) => updateNight(i, 'friday_nights', e.target.value)} /></td>
                      <td className="breakdown">{gs > 0 ? perFellowText(entry.friday_nights, gs) : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <BudgetBar label="Total nights" configured={nightBudget.configured} required={nightBudget.required} unit={`${W} wks × 7`} />
            <BudgetBar label="Friday nights" configured={nightBudget.fridayConfigured} required={nightBudget.fridayRequired} unit={`${W} wks × 1`} />
          </section>
        )}

        {activeSection === 'weekend' && (
          <section className="config-card">
            <h2>Weekend Call Distribution</h2>
            <table className="config-table">
              <thead><tr><th>Group</th><th>NCC Total</th><th>Per Fellow</th><th>Stroke Total</th><th>Per Fellow</th></tr></thead>
              <tbody>
                {config.weekend_call.map((entry, i) => {
                  const gs = (config.fellow_groups[entry.group] || []).length;
                  return (
                    <tr key={i}>
                      <td>
                        <select value={entry.group} onChange={(e) => updateWeekend(i, 'group', e.target.value)}>
                          {groupNames.map((g) => <option key={g} value={g}>{g} ({(config.fellow_groups[g] || []).length})</option>)}
                        </select>
                      </td>
                      <td><input type="number" value={entry.ncc_total} onChange={(e) => updateWeekend(i, 'ncc_total', e.target.value)} /></td>
                      <td className="breakdown">{gs > 0 ? perFellowText(entry.ncc_total, gs) : '—'}</td>
                      <td><input type="number" value={entry.stroke_total} onChange={(e) => updateWeekend(i, 'stroke_total', e.target.value)} /></td>
                      <td className="breakdown">{gs > 0 ? perFellowText(entry.stroke_total, gs) : '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <BudgetBar label="Weekend NCC" configured={weekendBudget.nccConfigured} required={weekendBudget.nccRequired} unit={`${W} wks × 2`} />
            <BudgetBar label="Weekend Stroke" configured={weekendBudget.strokeConfigured} required={weekendBudget.strokeRequired} unit={`${W} wks × 1`} />
          </section>
        )}

        {activeSection === 'vacations' && (
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
        )}

        {activeSection === 'holidays' && (
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
        )}

        {activeSection === 'night-rules' && (
          <section className="config-card">
            <h2>Night Call Rules</h2>
            <p className="hint">Configure spacing, blocked services, holiday eligibility, penalties, and Sunday-following preferences for night call.</p>

            {editingNightRuleIdx !== null && nightRules[editingNightRuleIdx] ? (
              <RuleEditor
                rule={nightRules[editingNightRuleIdx]}
                allShifts={config.shifts}
                allGroups={groupNames}
                onSave={saveNightRule}
                onCancel={() => setEditingNightRuleIdx(null)}
                onRemove={() => removeNightRule(editingNightRuleIdx)}
              />
            ) : showNightPalette ? (
              <RulePalette
                group="Night"
                category="night"
                onAdd={addNightRule}
                onCancel={() => setShowNightPalette(false)}
              />
            ) : (
              <>
                {nightRules.length > 0 ? (
                  <div className="rule-cards">
                    {nightRules.map((r, i) => (
                      <RuleCard
                        key={r.name || i}
                        rule={r}
                        onToggleActive={(name) => setNightRules(prev => prev.map(rr => rr.name === name ? { ...rr, active: !rr.active } : rr))}
                        onToggleStrength={(name) => setNightRules(prev => prev.map(rr => rr.name === name ? { ...rr, strength: rr.strength === 'hard' ? 'soft' : 'hard' } : rr))}
                        onEdit={() => setEditingNightRuleIdx(i)}
                      />
                    ))}
                  </div>
                ) : (
                  <p className="hint">No night rules configured.</p>
                )}
                <button className="add-btn" style={{ marginTop: '1rem' }}
                  onClick={() => setShowNightPalette(true)}>+ Add Night Rule</button>
              </>
            )}
          </section>
        )}

        {activeSection === 'weekend-rules' && (
          <section className="config-card">
            <h2>Weekend Call Rules</h2>
            <p className="hint">Configure spacing, blocked services, stroke eligibility, and penalties for weekend call.</p>

            {editingWeekendRuleIdx !== null && weekendRules[editingWeekendRuleIdx] ? (
              <RuleEditor
                rule={weekendRules[editingWeekendRuleIdx]}
                allShifts={config.shifts}
                allGroups={groupNames}
                onSave={saveWeekendRule}
                onCancel={() => setEditingWeekendRuleIdx(null)}
                onRemove={() => removeWeekendRule(editingWeekendRuleIdx)}
              />
            ) : showWeekendPalette ? (
              <RulePalette
                group="Weekend"
                category="weekend"
                onAdd={addWeekendRule}
                onCancel={() => setShowWeekendPalette(false)}
              />
            ) : (
              <>
                {weekendRules.length > 0 ? (
                  <div className="rule-cards">
                    {weekendRules.map((r, i) => (
                      <RuleCard
                        key={r.name || i}
                        rule={r}
                        onToggleActive={(name) => setWeekendRules(prev => prev.map(rr => rr.name === name ? { ...rr, active: !rr.active } : rr))}
                        onToggleStrength={(name) => setWeekendRules(prev => prev.map(rr => rr.name === name ? { ...rr, strength: rr.strength === 'hard' ? 'soft' : 'hard' } : rr))}
                        onEdit={() => setEditingWeekendRuleIdx(i)}
                      />
                    ))}
                  </div>
                ) : (
                  <p className="hint">No weekend rules configured.</p>
                )}
                <button className="add-btn" style={{ marginTop: '1rem' }}
                  onClick={() => setShowWeekendPalette(true)}>+ Add Weekend Rule</button>
              </>
            )}
          </section>
        )}

        {activeSection === 'call-rules' && (
          <section className="config-card">
            <h2>Call Rules</h2>
            <p className="hint">Per-fellow shift targets, night/weekend pins, prerequisites, and other annual call rules.</p>

            {editingCallRuleIdx !== null && callRules[editingCallRuleIdx] ? (
              <CallRuleEditor
                rule={callRules[editingCallRuleIdx]}
                allShifts={config.shifts}
                allFellows={allFellows}
                allGroups={groupNames}
                onSave={(updated) => {
                  setCallRules(prev => prev.map((r, i) => i === editingCallRuleIdx ? updated : r));
                  setEditingCallRuleIdx(null);
                }}
                onCancel={() => setEditingCallRuleIdx(null)}
                onRemove={() => {
                  setCallRules(prev => prev.filter((_, i) => i !== editingCallRuleIdx));
                  setEditingCallRuleIdx(null);
                }}
              />
            ) : (
              <>
                {callRules.length > 0 ? (
                  <div className="rule-cards">
                    {callRules.map((r, i) => (
                      <div key={r.name || i} className={`rule-card ${!r.active ? 'inactive' : ''}`}>
                        <div className="rule-card-header">
                          <span className="rule-name">{r.name}</span>
                          <span className="rule-type-badge">{r.type.replace(/_/g, ' ')}</span>
                        </div>
                        <div className="rule-card-meta">
                          {r.fellow && <span>Fellow: {r.fellow}</span>}
                          {r.shifts && r.shifts.length > 0 && <span>Shifts: {r.shifts.join(', ')}</span>}
                          {r.relation && <span>{r.relation} {r.count}</span>}
                          {r.strength && <span className={`strength-badge ${r.strength}`}>{r.strength}</span>}
                        </div>
                        <div className="rule-card-actions">
                          <button onClick={() => setCallRules(prev => prev.map((rr, j) => j === i ? { ...rr, active: !rr.active } : rr))}>
                            {r.active ? 'Disable' : 'Enable'}
                          </button>
                          <button onClick={() => setEditingCallRuleIdx(i)}>Edit</button>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="hint">No call rules configured.</p>
                )}
                <button className="add-btn" style={{ marginTop: '1rem' }}
                  onClick={() => {
                    setCallRules(prev => [...prev, createDefaultCallRule(allFellows)]);
                    setEditingCallRuleIdx(callRules.length);
                  }}>+ Add Call Rule</button>
              </>
            )}
          </section>
        )}

        {activeSection === 'rules-program' && (
          <>
            <section className="config-card">
              <h2>Standing Rules</h2>
              <p className="hint">From stanford-fellowship.yaml. Toggle active/strength for this session.</p>
              <RulesTable rules={standingRules} onToggle={toggleStandingRule} />
            </section>

            {(config.annual_rules?.rules?.length ?? 0) > 0 && (
              <section className="config-card" style={{ marginTop: '1rem' }}>
                <h2>Annual Rules</h2>
                <RulesTable rules={config.annual_rules!.rules} onToggle={toggleAnnualRule} />
              </section>
            )}
          </>
        )}

        {activeSection.startsWith('rules-') && activeSection !== 'rules-program' && (() => {
          const group = activeSection.replace('rules-', '');
          const groupRules = paletteRules.filter(r => r.groups?.includes(group));
          const shiftTotals = groupRules.filter((r): r is ShiftTotalRule => r.type === 'shift_total');
          const otherRules = groupRules.filter(r => r.type !== 'shift_total');

          return (
            <section className="config-card">
              <h2>{group}</h2>

              {editingRuleIdx !== null && paletteRules[editingRuleIdx] && groupRules.includes(paletteRules[editingRuleIdx]) ? (
                <RuleEditor
                  rule={paletteRules[editingRuleIdx]}
                  allShifts={config.shifts}
                  allGroups={groupNames}
                  onSave={savePaletteRule}
                  onCancel={() => setEditingRuleIdx(null)}
                  onRemove={() => removePaletteRule(editingRuleIdx)}
                />
              ) : showPalette ? (
                <RulePalette
                  group={group}
                  category="weekly"
                  onAdd={addPaletteRule}
                  onCancel={() => setShowPalette(false)}
                />
              ) : (
                <>
                  <CoverageTotalsTable
                    rules={shiftTotals}
                    feasibility={feasibility}
                    allShifts={config.shifts}
                    group={group}
                    onToggleActive={togglePaletteRuleActive}
                    onToggleStrength={togglePaletteRuleStrength}
                    onChangeCount={changePaletteRuleCount}
                    onChangeRelation={changePaletteRuleRelation}
                    onUpdateRule={(name, updated) => {
                      setPaletteRules(prev => prev.map(r => r.name === name ? updated as ShiftTotalRule : r));
                    }}
                    onAddRule={addPaletteRule}
                    onRemoveRule={(name) => {
                      setPaletteRules(prev => prev.filter(r => r.name !== name));
                    }}
                  />
                  {otherRules.length > 0 && (
                    <div className="rule-cards">
                      <h3>Constraints</h3>
                      {otherRules.map(r => {
                        const globalIdx = paletteRules.indexOf(r);
                        return (
                          <RuleCard
                            key={r.name || globalIdx}
                            rule={r}
                            onToggleActive={togglePaletteRuleActive}
                            onToggleStrength={togglePaletteRuleStrength}
                            onEdit={() => setEditingRuleIdx(globalIdx)}
                            feasibility={feasibility[r.name]}
                          />
                        );
                      })}
                    </div>
                  )}
                  <button className="add-btn" style={{ marginTop: '1rem' }}
                    onClick={() => setShowPalette(true)}>+ Add Rule</button>
                  <button onClick={checkFeasibility} style={{ marginLeft: '0.5rem', marginTop: '1rem' }}>
                    Check Feasibility
                  </button>
                </>
              )}

              {groupRules.length === 0 && !showPalette && (
                <p className="hint">No rules configured for {group}.</p>
              )}
            </section>
          );
        })()}
      </div>
    );
  }

  // =========================================================================
  // Render: unified shell
  // =========================================================================
  return (
    <div className="app-shell">
      <TopBar
        selectedFile={selectedFile}
        configFiles={configFiles.annual}
        numWeeks={W}
        hasDraft={hasDraft}
        onFileChange={(f) => { setSelectedFile(f); loadConfig(f); }}
        onWeeksChange={(w) => setConfig((c) => ({ ...c, num_weeks: w }))}
        onPublish={publishDraft}
        onDiscard={discardDraft}
        onImportFile={handleImportFile}
      />
      <Sidebar
        active={activeSection}
        onNavigate={(s) => { setActiveSection(s); if (mode === 'schedule') { cancelSolve(); } setMode('config'); }}
        groups={groupNames}
        onGenerate={generateSchedule}
        isRunning={isRunning}
        lockedFellows={Object.keys(lockedAssignments)}
        fellowGroups={config.fellow_groups}
      />
      <main className="main-panel">
        {renderMainContent()}
      </main>
    </div>
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
    <div className="standing-rules-list">
      {rules.map((r, i) => (
        <div key={i} className={`standing-rule-item ${r.active ? '' : 'rule-inactive'}`}>
          <div className="standing-rule-header">
            <input type="checkbox" checked={r.active} onChange={() => onToggle(i, 'active')} />
            <span className="standing-rule-name">{r.name}</span>
            <span className="rule-type-label">{r.type || r.kind}</span>
            {r.strength && (
              <button className={`strength-toggle ${r.strength}`} onClick={() => onToggle(i, 'strength')}>
                {r.strength}
              </button>
            )}
            <span className="rule-groups">{(r.groups || r.fellow_groups || []).join(', ')}</span>
          </div>
          {r.description && (
            <p className="standing-rule-desc">{r.description}</p>
          )}
        </div>
      ))}
    </div>
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

function perFellowText(total: number, groupSize: number): string {
  if (groupSize === 0) return '—';
  const base = Math.floor(total / groupSize);
  const remainder = total % groupSize;
  if (remainder === 0) return `${base} each`;
  return `${base}–${base + 1} each`;
}

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
