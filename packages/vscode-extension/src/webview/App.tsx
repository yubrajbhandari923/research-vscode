import { KeyboardEvent, useEffect, useRef, useState } from 'react';
import type { Host } from './host';
import { HomePage, ResearchPage, WorkPage } from './pages';
import { SettingsPage } from './SettingsPage';
import type { Page } from './types';

const TABS: { id: Page; label: string; hint: string }[] = [
  { id: 'home', label: 'Home', hint: 'What needs you, what is running, what you learned' },
  { id: 'work', label: 'Work', hint: 'Plans, tasks, workspaces, runs, execution' },
  { id: 'research', label: 'Research', hint: 'Questions, experiments, findings, decisions' },
  { id: 'settings', label: 'Settings', hint: 'Policies, agents, git, sandbox, HPC' },
];

export function App({ host, initial }: { host: Host; initial?: { page?: Page; target?: string } }) {
  const [page, setPage] = useState<Page>(initial?.page || (host.getState().page as Page) || 'home');
  const [target, setTarget] = useState<string | undefined>(initial?.target);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const go = (p: Page, t?: string) => { setPage(p); setTarget(t); host.setState({ ...host.getState(), page: p }); };
  useEffect(() => {
    const on = (e: MessageEvent) => { if (e.data?.type === 'navigate' && e.data.page) go(e.data.page, e.data.target); };
    window.addEventListener('message', on);
    return () => window.removeEventListener('message', on);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const onKey = (e: KeyboardEvent, i: number) => {
    const n = e.key === 'ArrowRight' ? (i + 1) % TABS.length : e.key === 'ArrowLeft' ? (i + TABS.length - 1) % TABS.length : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1;
    if (n >= 0) { e.preventDefault(); go(TABS[n].id); tabs.current[n]?.focus(); }
  };
  return (
    <div className="shell">
      <header className="topbar">
        <div role="tablist" aria-label="Research areas">
          {TABS.map((t, i) => (
            <button key={t.id} ref={(el) => { tabs.current[i] = el; }} role="tab" id={`tab-${t.id}`} aria-selected={page === t.id} aria-controls={`panel-${t.id}`}
              tabIndex={page === t.id ? 0 : -1} title={t.hint} onClick={() => go(t.id)} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>))}
        </div>
      </header>
      <main role="tabpanel" id={`panel-${page}`} aria-labelledby={`tab-${page}`} tabIndex={-1}>
        {page === 'home' && <HomePage host={host} go={go} />}
        {page === 'work' && <WorkPage host={host} go={go} />}
        {page === 'research' && <ResearchPage host={host} />}
        {page === 'settings' && <SettingsPage host={host} initialCategory={target} />}
      </main>
    </div>
  );
}
