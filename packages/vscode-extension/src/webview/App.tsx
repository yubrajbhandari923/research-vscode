import { KeyboardEvent, useEffect, useRef, useState } from 'react';
import type { Host } from './host';
import { HomePage, ResearchPage, WorkPage } from './pages';
import { InitProject } from './InitProject';
import { ProvidersPage } from './Providers';
import { PlansPage } from './PlansPage';
import { GuidePage } from './GuidePage';
import { Banner, Icon, OpsContext, PinHost, useAsync, useHasOp } from './common';
import { SettingsPage } from './SettingsPage';
import type { Page } from './types';

const ALL_TABS: { id: Page; label: string; hint: string; op?: string }[] = [
  { id: 'home', label: 'Home', hint: 'What needs you, what is running, what you learned' },
  { id: 'work', label: 'Work', hint: 'Plans, tasks, workspaces, runs, execution' },
  { id: 'research', label: 'Research', hint: 'Questions, experiments, findings, decisions' },
  { id: 'plans', label: 'Plans', hint: 'YAML plans run by the supervisor', op: 'managed_plans' },
  { id: 'guide', label: 'Guide', hint: 'The scientific story: key results, citations, a website', op: 'guide_status' },
  { id: 'agents', label: 'Agents', hint: 'Providers, models, sessions', op: 'providers' },
  { id: 'settings', label: 'Settings', hint: 'Policies, agents, git, sandbox, HPC' },
];

export function App({ host, initial }: { host: Host; initial?: { page?: Page; target?: string } }) {
  const ping = useAsync<any>(() => host.call('ping'), []);
  if (ping.loading && !ping.data) return <div className="state" role="status"><span className="spinner" aria-hidden /> Starting…</div>;
  if (ping.error && /config\.ya?ml/.test(ping.error)) return (
    <main><Banner kind="bad"><b>The project configuration could not be read.</b>
      <pre className="diff" style={{ whiteSpace: 'pre-wrap' }}>{ping.error}</pre>
      <div className="row"><button type="button" className="primary" onClick={() => host.openFile('.research/config.yaml')}><Icon name="file" /> Open configuration file</button><button type="button" onClick={ping.reload}>Try again</button></div>
      <p className="meta" style={{ marginTop: 6 }}>Nothing was changed or rewritten. Fix the file, then try again; no migration or other step is needed.</p></Banner></main>);
  if (ping.error) return <div className="state error" role="alert"><b>Could not load.</b> {ping.error} <button className="link" onClick={ping.reload}>Try again</button></div>;
  if (ping.data?.incompatible) return <Incompatible message={ping.data.incompatible} />;
  if (ping.data && !ping.data.initialized) return <InitProject host={host} onDone={(r) => host.setState({ ...host.getState(), initialized: r })} />;
  return <OpsContext.Provider value={ping.data?.ops}><Shell host={host} initial={initial} name={ping.data?.name || 'Research'} /></OpsContext.Provider>;
}

function Incompatible({ message }: { message: string }) {
  return (
    <div className="page-body">
      <h1>This is not a Research OS 2 project</h1>
      <div className="banner bad" role="alert">{message}</div>
      <p>Nothing was changed, and your old records are untouched. To keep them and start fresh:</p>
      <ol><li>Rename the folder <code>.research</code> to <code>.research-v1-archive</code> (rename, don’t delete).</li><li>Reload this window and set up a new project.</li></ol>
    </div>
  );
}

function Shell({ host, initial, name }: { host: Host; initial?: { page?: Page; target?: string }; name: string }) {
  const [attention, setAttention] = useState(0);
  const [rev, setRev] = useState(0);
  const [stored, setPage] = useState<Page>(initial?.page || (host.getState().page as Page) || 'home');
  const [target, setTarget] = useState<string | undefined>(initial?.target);
  const has = useHasOp();
  const TABS = ALL_TABS.filter((t) => !t.op || has(t.op));
  const page: Page = TABS.some((t) => t.id === stored) ? stored : 'home';   // a remembered page this core cannot serve falls back to Home
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const go = (p: Page, t?: string) => { setPage(p); setTarget(t); host.setState({ ...host.getState(), page: p }); };
  useEffect(() => {
    const on = (e: MessageEvent) => { if (e.data?.type === 'navigate' && e.data.page) go(e.data.page, e.data.target); };
    window.addEventListener('message', on);
    host.ready?.();
    return () => window.removeEventListener('message', on);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const onKey = (e: KeyboardEvent, i: number) => {
    const n = e.key === 'ArrowRight' ? (i + 1) % TABS.length : e.key === 'ArrowLeft' ? (i + TABS.length - 1) % TABS.length : e.key === 'Home' ? 0 : e.key === 'End' ? TABS.length - 1 : -1;
    if (n >= 0) { e.preventDefault(); go(TABS[n].id); tabs.current[n]?.focus(); }
  };
  return (
    <div className="shell">
      <PinHost />
      <header className="topbar">
        <div className="brand" title="Research OS project"><span className="mark" aria-hidden /><span className="proj">{name}</span></div>
        <div role="tablist" aria-label="Research areas">
          {TABS.map((t, i) => (
            <button key={t.id} ref={(el) => { tabs.current[i] = el; }} role="tab" id={`tab-${t.id}`} aria-selected={page === t.id} aria-controls={`panel-${t.id}`}
              tabIndex={page === t.id ? 0 : -1} title={t.hint} data-count={t.id === 'home' ? attention : undefined} onClick={() => go(t.id)} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>))}
        </div>
        <div className="tools"><button type="button" className="ghost icon-btn" aria-label="Refresh" title="Reload from disk" onClick={() => setRev((r) => r + 1)}><Icon name="refresh" /></button></div>
      </header>
      <main role="tabpanel" id={`panel-${page}`} aria-labelledby={`tab-${page}`} tabIndex={-1}>
        {page === 'home' && <HomePage key={rev} host={host} go={go} onAttention={setAttention} />}
        {page === 'work' && <WorkPage key={rev} host={host} go={go} />}
        {page === 'research' && <ResearchPage key={rev} host={host} go={go} />}
        {page === 'plans' && <PlansPage key={rev} host={host} />}
        {page === 'guide' && <GuidePage key={rev} host={host} go={go} />}
        {page === 'agents' && <ProvidersPage key={rev} host={host} />}
        {page === 'settings' && <SettingsPage key={rev} host={host} initialCategory={target} />}
      </main>
    </div>
  );
}
