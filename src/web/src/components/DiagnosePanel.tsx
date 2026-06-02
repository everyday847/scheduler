import React, { useCallback, useEffect, useRef, useState } from 'react';

type MusCore = { name: string; type: string; groups?: string[] }[];

type Props = {
  apiBase: string;
  requestBody: any;
  onDisableRule: (ruleName: string) => void;
  onSoftenRule: (ruleName: string) => void;
  onClose: () => void;
};

export function DiagnosePanel({ apiBase, requestBody, onDisableRule, onSoftenRule, onClose }: Props) {
  const [cores, setCores] = useState<MusCore[]>([]);
  const [progress, setProgress] = useState<{ checked: number; total: number; phase: string } | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const runDiagnosis = useCallback(async () => {
    setCores([]);
    setDone(false);
    setError(null);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const response = await fetch(`${apiBase}/api/diagnose/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestBody),
        signal: controller.signal,
      });
      if (!response.ok) {
        setError('Failed to start diagnosis');
        return;
      }

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done: streamDone, value } = await reader.read();
        if (streamDone) break;
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
          if (!eType || !eData) continue;
          const data = JSON.parse(eData);

          if (eType === 'progress') {
            setProgress({ checked: data.checked, total: data.total, phase: data.phase });
          } else if (eType === 'core_found') {
            setCores(prev => [...prev, data.core]);
          } else if (eType === 'done') {
            setDone(true);
            setProgress(null);
          } else if (eType === 'error') {
            setError(data.message);
          }
        }
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setError('Connection lost');
      }
    }
  }, [apiBase, requestBody]);

  useEffect(() => {
    runDiagnosis();
    return () => { abortRef.current?.abort(); };
  }, [runDiagnosis]);

  return (
    <div className="diagnose-panel">
      <div className="diagnose-header">
        <h2>Diagnosing Rule Conflicts</h2>
        <button onClick={() => { abortRef.current?.abort(); onClose(); }}>Close</button>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {progress && !done && (
        <div className="diagnose-progress">
          <span className="progress-indicator">●</span>
          Analyzing rules... ({progress.checked} probes,{' '}
          {progress.phase === 'growing' ? 'adding rules' : 'narrowing conflict'})
        </div>
      )}

      {done && cores.length === 0 && (
        <p className="hint">No conflicts found. The schedule may be feasible.</p>
      )}

      {cores.length > 0 && (
        <div className="diagnose-cores">
          {cores.map((core, i) => (
            <div key={i} className="conflict-core">
              <h3>Conflict {i + 1}</h3>
              <p className="hint">These rules can't all be satisfied together. Disable or soften at least one.</p>
              <div className="conflict-rules">
                {core.map((rule: any) => (
                  <div key={rule.name} className="conflict-rule">
                    <span className="conflict-rule-name">{rule.name}</span>
                    <span className="conflict-rule-type">{rule.type}</span>
                    <div className="conflict-rule-actions">
                      <button className="danger-btn" onClick={() => onDisableRule(rule.name)}>Disable</button>
                      <button onClick={() => onSoftenRule(rule.name)}>Make Soft</button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {done && (
        <p className="diagnose-done">
          Analysis complete. {cores.length} conflict{cores.length !== 1 ? 's' : ''} found.
        </p>
      )}
    </div>
  );
}
