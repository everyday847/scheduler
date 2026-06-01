import React from 'react';

export type SidebarSection =
  | 'fellows' | 'shifts'
  | 'rules-NCC_JR' | 'rules-NCC_SR' | 'rules-STROKE' | 'rules-NH' | 'rules-CCM' | 'rules-program'
  | 'night' | 'weekend' | 'vacations' | 'holidays';

type SidebarProps = {
  active: SidebarSection;
  onNavigate: (section: SidebarSection) => void;
  groups: string[];
  onGenerate: () => void;
  isRunning: boolean;
};

export function Sidebar({ active, onNavigate, groups, onGenerate, isRunning }: SidebarProps) {
  return (
    <nav className="sidebar">
      <div className="sidebar-section">
        <div className="sidebar-heading">Config</div>
        <SidebarItem id="fellows" label="Fellows" active={active} onNavigate={onNavigate} />
      </div>

      <div className="sidebar-section">
        <div className="sidebar-heading">Rules</div>
        {groups.map(g => (
          <SidebarItem key={g} id={`rules-${g}` as SidebarSection} label={g} active={active} onNavigate={onNavigate} />
        ))}
        <SidebarItem id="rules-program" label="Program" active={active} onNavigate={onNavigate} />
      </div>

      <div className="sidebar-section">
        <div className="sidebar-heading">Annual</div>
        <SidebarItem id="night" label="Night Call" active={active} onNavigate={onNavigate} />
        <SidebarItem id="weekend" label="Weekend Call" active={active} onNavigate={onNavigate} />
        <SidebarItem id="vacations" label="Vacations" active={active} onNavigate={onNavigate} />
        <SidebarItem id="holidays" label="Holidays" active={active} onNavigate={onNavigate} />
      </div>

      <div className="sidebar-footer">
        <button className="primary generate-btn" onClick={onGenerate} disabled={isRunning}>
          {isRunning ? 'Solving...' : 'Generate Schedule'}
        </button>
      </div>
    </nav>
  );
}

function SidebarItem({ id, label, active, onNavigate }: {
  id: SidebarSection; label: string; active: SidebarSection; onNavigate: (s: SidebarSection) => void;
}) {
  return (
    <button
      className={`sidebar-item ${active === id ? 'active' : ''}`}
      onClick={() => onNavigate(id)}
    >
      {label}
    </button>
  );
}
