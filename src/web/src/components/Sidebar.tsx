import React from 'react';

export type SidebarSection =
  | 'fellows' | 'shifts'
  | `rules-${string}`
  | 'rules-program'
  | 'night' | 'weekend' | 'vacations' | 'holidays'
  | 'night-rules' | 'weekend-rules' | 'call-rules' | 'solver-options';

type SidebarProps = {
  active: SidebarSection;
  onNavigate: (section: SidebarSection) => void;
  groups: string[];
  onGenerate: () => void;
  isRunning: boolean;
  lockedFellows?: string[];
  fellowGroups?: Record<string, string[]>;
};

export function Sidebar({ active, onNavigate, groups, onGenerate, isRunning, lockedFellows, fellowGroups }: SidebarProps) {
  return (
    <nav className="sidebar">
      <div className="sidebar-section">
        <div className="sidebar-heading">Config</div>
        <SidebarItem id="fellows" label="Fellows" active={active} onNavigate={onNavigate} />
      </div>

      <div className="sidebar-section">
        <div className="sidebar-heading">Rules</div>
        {groups.map(g => {
          const lockedInGroup = (lockedFellows || []).filter(f =>
            (fellowGroups || {})[g]?.includes(f)
          ).length;
          return (
            <SidebarItem
              key={g}
              id={`rules-${g}` as SidebarSection}
              label={g}
              active={active}
              onNavigate={onNavigate}
              badge={lockedInGroup > 0 ? `🔒${lockedInGroup}` : undefined}
            />
          );
        })}
        <SidebarItem id="rules-program" label="Program" active={active} onNavigate={onNavigate} />
      </div>

      <div className="sidebar-section">
        <div className="sidebar-heading">Annual</div>
        <SidebarItem id="night" label="Night Call" active={active} onNavigate={onNavigate} />
        <SidebarItem id="night-rules" label="Night Rules" active={active} onNavigate={onNavigate} />
        <SidebarItem id="weekend" label="Weekend Call" active={active} onNavigate={onNavigate} />
        <SidebarItem id="weekend-rules" label="Weekend Rules" active={active} onNavigate={onNavigate} />
        <SidebarItem id="call-rules" label="Call Rules" active={active} onNavigate={onNavigate} />
        <SidebarItem id="solver-options" label="Solver Options" active={active} onNavigate={onNavigate} />
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

function SidebarItem({ id, label, active, onNavigate, badge }: {
  id: SidebarSection; label: string; active: SidebarSection; onNavigate: (s: SidebarSection) => void; badge?: string;
}) {
  return (
    <button
      className={`sidebar-item ${active === id ? 'active' : ''}`}
      onClick={() => onNavigate(id)}
    >
      {label}
      {badge && <span className="lock-badge">{badge}</span>}
    </button>
  );
}
