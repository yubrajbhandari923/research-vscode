import { useState } from 'react';
import { Banner, EmptyState, Section, State, Status, useAsync } from './common';
import type { Host } from './host';

/** Model strategy (who plans, implements, verifies) and per-task pins. Everything comes from the core's `agent_setup`; this only relays choices. */
export function StrategySection({ host, onChange }: { host: Host; onChange: () => void }) {
  const a = useAsync<any>(() => host.call('agent_setup'), []);
  const [pick, setPick] = useState('');
  const [ask, setAsk] = useState<{ name: string; warnings: string[] } | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const run = async (fn: () => Promise<any>, ok: string) => {
    try { await fn(); setMsg({ ok: true, text: ok }); setAsk(null); a.reload(); onChange(); } catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  const review = async (name: string) => {
    try { const pre = await host.call('strategy_use', { name, dry_run: true }); pre.previous === name ? setMsg({ ok: true, text: `${name} is already the active strategy.` }) : setAsk({ name, warnings: pre.warnings || [] }); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <Section title="Model strategy" id="strategy-h" lead="Decides which agent level plans, implements, verifies and writes up the science, and when work escalates. Switching changes future dispatches only; running sessions and all records stay as they are.">
      {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
      <State a={a}>{(d) => {
        const s = d.strategy;
        const list: any[] = d.strategies || [];
        const levels: Record<string, any> = s?.resolved_levels || {};
        const sel = pick || s?.name || '';
        return <>
          <div className="row">
            <label htmlFor="strategy-pick">Strategy</label>
            <select id="strategy-pick" value={sel} onChange={(e) => { setPick(e.target.value); setAsk(null); }}>
              {!s && <option value="" disabled>Choose a strategy…</option>}
              {list.map((x) => <option key={x.name} value={x.name} disabled={!x.ok}>{x.name}{x.active ? ' (active)' : ''}{x.ok ? '' : ' (invalid)'}</option>)}
            </select>
            <button type="button" className="primary" disabled={!sel || sel === s?.name} onClick={() => review(sel)}>Switch strategy</button>
          </div>
          {sel && <p className="muted small">{list.find((x) => x.name === sel)?.description || 'No description.'}</p>}
          {ask && <Banner kind="warn" role="status"><b>Switch to {ask.name}?</b> It applies to future dispatches only.
            {ask.warnings.map((w) => <p key={w} className="small">{w}</p>)}
            <div className="row"><button type="button" className="primary" autoFocus onClick={() => run(() => host.call('strategy_use', { name: ask.name }), `Strategy is now ${ask.name}.`)}>Switch to {ask.name}</button><button type="button" onClick={() => setAsk(null)}>Keep {s?.name || 'current'}</button></div></Banner>}
          {!s ? <EmptyState icon="info" title="No strategy chosen" inline>This project uses the original tier routing. Pick a strategy above to route planning, implementation and verification by level.</EmptyState> :
            <div className="tablewrap"><table><caption className="muted small">Active: {s.name} ({s.origin})</caption><thead><tr><th>Work</th><th>Level</th><th>Model</th></tr></thead><tbody>
              {Object.entries(s.definition.route as Record<string, string>).map(([work, lv]) => (
                <tr key={work}><td>{work}</td><td>{lv}</td><td><code>{levels[lv]?.model || 'CLI default'}</code> <span className="muted small">{levels[lv]?.profile}</span></td></tr>))}
            </tbody></table></div>}
          {s?.validation && [...s.validation.errors, ...s.validation.warnings].map((w: string) => <p key={w} className="help-warn">{w}</p>)}
          <h3>Pin a task to a level</h3>
          {!d.tasks.length ? <EmptyState icon="tasks" title="No open tasks" inline>Open tasks appear here so you can pin one to a level. A pin overrides the strategy for that task's implementer only.</EmptyState> :
            !s ? <p className="muted small">Choose a strategy first: a pin picks one of its levels.</p> :
            <table><thead><tr><th>Task</th><th>Status</th><th>Level</th></tr></thead><tbody>
              {d.tasks.map((t: any) => (
                <tr key={t.id}><td><button type="button" className="link" onClick={() => host.openEntity(t.id)}>{t.title}</button></td><td><Status value={t.status} /></td>
                  <td><select aria-label={`Level for ${t.title}`} value={t.override?.level || ''} onChange={(e) => run(() => host.call('task_pin', e.target.value ? { id: t.id, level: e.target.value } : { id: t.id, clear: true }),
                    e.target.value ? `${t.id} is pinned to ${e.target.value}.` : `${t.id} follows the strategy again.`)}>
                    <option value="">Strategy default{t.route ? ` (${t.route.level})` : ''}</option>
                    {Object.keys(levels).map((lv) => <option key={lv} value={lv}>{lv}</option>)}</select></td></tr>))}
            </tbody></table>}
        </>;
      }}</State>
    </Section>
  );
}
