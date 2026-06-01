import React from 'react';

type TopBarProps = {
  selectedFile: string;
  configFiles: string[];
  numWeeks: number;
  hasDraft: boolean;
  onFileChange: (file: string) => void;
  onWeeksChange: (weeks: number) => void;
  onPublish: () => void;
  onDiscard: () => void;
  onImportFile: (file: File) => void;
};

export function TopBar({ selectedFile, configFiles, numWeeks, hasDraft, onFileChange, onWeeksChange, onPublish, onDiscard, onImportFile }: TopBarProps) {
  return (
    <header className="top-bar">
      <div className="top-bar-left">
        <span className="top-bar-title">Stanford Fellowship Scheduling</span>
      </div>
      <div className="top-bar-right">
        <label className="inline-field">
          <span>Year</span>
          <select value={selectedFile} onChange={(e) => onFileChange(e.target.value)}>
            {configFiles.map((f) => <option key={f} value={f}>{f.replace('.yaml', '')}</option>)}
          </select>
        </label>
        <label className="inline-field">
          <span>Weeks</span>
          <input type="number" value={numWeeks} min={1} max={53}
            onChange={(e) => onWeeksChange(Number(e.target.value) || 52)}
            style={{ width: '4rem' }} />
        </label>
        <label className="import-btn-label">
          Import
          <input type="file" accept=".csv,.xlsx" style={{ display: 'none' }}
            onChange={(e) => { if (e.target.files?.[0]) onImportFile(e.target.files[0]); e.target.value = ''; }} />
        </label>
        {hasDraft && (
          <div className="draft-status">
            <span className="draft-badge">Draft</span>
            <button className="primary" onClick={onPublish}>Publish</button>
            <button onClick={onDiscard}>Discard</button>
          </div>
        )}
      </div>
    </header>
  );
}
