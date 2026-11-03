import { useState } from 'react';
import { Badge, State, Status, useAsync } from './common';
import type { Host } from './host';
import { ToolsSection } from './ToolsSection';

const LABEL: Record<string, string> = { claude: 'Claude Code', codex: 'OpenAI Codex', agy: 'Google Antigravity' };
const yes = (v: any) => (v === true ? 'yes' : v ? String(v) : 'no');

/** Agents & Providers: what is installed, which is default, whether the Research MCP server is reachable, and what has run. */
export function ProvidersPage({ host }: { host: Host }) {
  const a = useAsync<any>(() => host.call('providers'), []);
  const [msg, setMsg] = useState('');
  const [log, setLog] = useState<{ id: string; text: string } | null>(null);
  const act = async (fn: () => Promise<any>, ok: string) => { try { await fn(); setMsg(ok); a.reload(); } catch (e: any) { setMsg('✗ ' + e.message); } };
  return (
    <div className="page-body">
      <h1>Agents &amp; Providers</h1>
      <p className="muted">Research OS keeps the plan, tasks and records. These are the agents that can do the work. Changes here affect <b>future</b> dispatches only.</p>
      {msg && <div className="banner info" role="status">{msg}</div>}
      <State a={a}>{(d) => <>
        <p>Allocation strategy: <b>{d.strategy || 'none (project profiles)'}</b> · default agent: <b>{LABEL[d.default] || d.default}</b>
          <span className="muted"> · also editable in <button type="button" className="link" onClick={() => host.openFile('.research/config.yaml')}>config.yaml</button> (<code>agents.default_profile</code>)</span></p>
        <div className="cards">
          {d.providers.map((p: any) => (
            <section key={p.name} className="card" aria-label={LABEL[p.name] || p.name} style={{ margin: '8px 0', padding: 12 }}>
              <h2 style={{ margin: 0 }}>{LABEL[p.name] || p.name} {d.default === p.name && <Badge kind="ok">default</Badge>}</h2>
              <p>{p.installed ? <Badge kind="ok">installed {p.version || ''}</Badge> : <Badge kind="warn">not installed</Badge>}{' '}
                <Badge kind={p.mcp_registered ? 'ok' : p.mcp_registered === false ? 'warn' : ''}>MCP {p.mcp_registered ? 'registered' : p.mcp_registered === false ? 'not registered' : 'unknown'}</Badge></p>
              {p.problem && <p className="help-warn">{p.problem}</p>}
              <dl>
                <dt>Modes</dt><dd>interactive: {yes(p.capabilities.interactive)} · headless: {yes(p.capabilities.headless)}</dd>
                <dt>Model / effort</dt><dd>model switch: {yes(p.capabilities.model)} · reasoning effort: {yes(p.capabilities.effort)}</dd>
                <dt>Output</dt><dd>{p.capabilities.structured_output || 'text'} · usage reporting: {yes(p.capabilities.usage)} · resume: {yes(p.capabilities.resume)}</dd>
                <dt>Instructions</dt><dd>{p.instructions} (generated from one AGENTS.md)</dd>
                <dt>Models</dt><dd>{p.models.models.length ? p.models.models.map((m: any) => m.id).join(', ') : 'CLI default'} <span className="muted">({p.models.source === 'cli' ? 'reported by the CLI' : 'from your profiles'})</span></dd>
                <dt>Sign-in</dt><dd className="muted">{p.auth}</dd>
              </dl>
              <div className="row">
                {d.default !== p.name && <button type="button" onClick={() => act(() => host.call('provider_default', { name: p.name }), `Default agent is now ${LABEL[p.name] || p.name}`)}>Make default</button>}
                <button type="button" onClick={() => act(async () => { const r = await host.call('provider_mcp', { name: p.name, apply: true }); if (!r.verify.ok) throw new Error(r.verify.problem); }, `Research MCP registered for ${LABEL[p.name] || p.name} and verified from the experiment directory`)}>{p.mcp_registered ? 'Repair MCP' : 'Install MCP'}</button>
                <button type="button" onClick={() => act(async () => { const r = await host.call('provider_mcp', { name: p.name }); if (!r.verify.ok) throw new Error(r.verify.problem); }, 'MCP server reachable: tools listed from the worktree')}>Verify MCP</button>
              </div>
            </section>))}
        </div>
        <ToolsSection host={host} />
        <h2>Recent sessions</h2>
        <p className="muted small">Completed = the agent process exited successfully. It does not mean the task passed: run verification.</p>
        {d.recent.length === 0 ? <p className="muted">Nothing dispatched yet. Open a task and choose Dispatch.</p> :
          <table><thead><tr><th>Task</th><th>State</th><th>Requested</th><th>Mode</th><th>Working in</th><th /></tr></thead><tbody>
            {d.recent.map((r: any) => (
              <tr key={r.id}><td>{r.task}</td><td><Status value={r.state} /></td><td>{r.profile}{r.model ? ` / ${r.model}` : ''}</td><td>{r.mode}</td><td><code>{String(r.cwd || '').split('/').slice(-2).join('/')}</code></td>
                <td>{r.state === 'running' && <button type="button" onClick={() => act(() => host.call('execution_stop', { id: r.id }), 'Stop requested')}>Stop</button>}
                  {['failed', 'cancelled', 'completed'].includes(r.state) && <button type="button" onClick={() => act(() => host.call('execution_retry', { id: r.id }), 'Prepared a new session; start it from the task')}>Retry</button>}
                  {r.mode === 'headless' && <button type="button" className="link" onClick={async () => { const l = await host.call('execution_logs', { id: r.id }); setLog({ id: r.id, text: (l.out || '') + (l.err ? '\n[stderr]\n' + l.err : '') }); }}>Logs</button>}</td></tr>))}
          </tbody></table>}
        {log && <pre className="diff" aria-label={`Log ${log.id}`}>{log.text || '(empty)'}</pre>}
      </>}</State>
    </div>
  );
}
