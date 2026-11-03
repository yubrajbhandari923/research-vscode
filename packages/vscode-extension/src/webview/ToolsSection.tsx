import { useState } from 'react';
import { Badge, Banner, Section, State, useAsync } from './common';
import type { Host } from './host';

const STAGE: Record<string, string> = { preflight: 'before a task is dispatched', post_implementation: 'after an agent’s change', pre_experiment: 'before a run is created', post_experiment: 'after runs finish' };

/** Skills, hooks and the MCP server: what each is for, what is loaded, and the one-click fixes. */
export function ToolsSection({ host }: { host: Host }) {
  const a = useAsync<any>(() => host.call('tools'), []);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const act = async (fn: () => Promise<any>, ok: (r: any) => string) => { try { const r = await fn(); setMsg({ ok: true, text: ok(r) }); } catch (e: any) { setMsg({ ok: false, text: e.message }); } a.reload(); };
  return (
    <Section id="tools" title="Skills, hooks and MCP"
      lead="Three ways Research OS shapes what an agent does. Skills tell it how to work, hooks are programs that check its work, and the MCP server lets it read and write the research records instead of guessing.">
      {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} role="status" onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
      <State a={a}>{(d) => {
        const linked = (k: string) => d.links[k]?.linked.length || 0;
        const missing = d.link_targets.some((k: string) => linked(k) < d.skills.length);
        const idle = Object.entries(d.starters).filter(([, s]: any) => !s.installed);
        const unapproved = d.hooks.filter((h: any) => !h.approved);
        return <>
          <h3>Skills <span className="count">{d.skills.length}</span></h3>
          <p className="muted small">Short written procedures (a <code>SKILL.md</code> each) such as <i>run-experiment</i> or <i>engineering-loop</i>. They live in <code>.research/skills</code> and are linked into <code>.claude/skills</code> and <code>.agents/skills</code>, which is how Claude Code and Codex find them. A skill marked <b>required</b> is injected into an agent’s brief and its hash recorded; the rest are discoverable.</p>
          <p>{d.link_targets.map((k: string) => <span key={k}><Badge kind={linked(k) >= d.skills.length ? 'ok' : 'warn'}>{k}: {linked(k)} of {d.skills.length} linked</Badge>{' '}</span>)}
            {missing && <button type="button" className="sm primary" onClick={() => act(() => host.call('skills_link'), () => 'Skills linked for the agent CLIs')}>Link skills now</button>}</p>
          <div className="tbl-wrap"><table><caption className="sr">Skills</caption><thead><tr><th scope="col">Skill</th><th scope="col">Level</th><th scope="col" className="hide-narrow">What it is for</th></tr></thead>
            <tbody>{d.skills.map((s: any) => <tr key={s.name}><td><b>{s.name}</b>{s.always && <span className="sub">always on</span>}</td><td><Badge kind={s.level === 'recommended' ? '' : 'info'}>{s.level}</Badge></td>
              <td className="hide-narrow">{String(s.description || '').slice(0, 150)}</td></tr>)}</tbody></table></div>
          <p className="muted small">Add your own with <code>research skills add &lt;folder or git url&gt;</code>; <code>research skills set NAME --level required</code> makes one binding.</p>

          <h3>Hooks <span className="count">{d.hooks.length}</span></h3>
          <p className="muted small">Commands <b>you</b> approve, run by Research OS at fixed points (<i>{Object.values(STAGE).join(' · ')}</i>). They are deterministic checks that replace tokens an agent would spend re-checking what a program can verify. An unapproved hook is reported as skipped, never as passed, and editing one revokes its approval.</p>
          {d.hooks.length === 0 ? <p className="muted">No hooks yet.</p> : <div className="tbl-wrap"><table><caption className="sr">Hooks</caption><thead><tr><th scope="col">Hook</th><th scope="col">Runs</th><th scope="col">State</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
            <tbody>{d.hooks.map((h: any) => <tr key={h.name}><td><b>{h.name}</b><span className="sub"><code>{h.command}</code></span></td><td>{STAGE[h.stage] || h.stage}{h.required ? '' : <span className="sub">never blocks</span>}</td>
              <td>{h.approved ? (h.enabled ? <Badge kind="ok">approved</Badge> : <Badge kind="">disabled</Badge>) : <Badge kind="warn">needs your approval</Badge>}{h.last && <span className="sub">last: {h.last.ok === false || h.last.passed === false ? 'failed' : 'passed'}</span>}</td>
              <td className="min">{!h.approved && <button type="button" className="sm primary" onClick={() => act(() => host.call('hook_approve', { names: [h.name] }), () => `${h.name} approved`)}>Approve</button>}
                {h.approved && <button type="button" className="sm" onClick={() => act(() => host.call('hook_toggle', { name: h.name, enabled: !h.enabled }), () => h.enabled ? `${h.name} disabled` : `${h.name} enabled`)}>{h.enabled ? 'Disable' : 'Enable'}</button>}
                {h.approved && h.enabled && <button type="button" className="sm" onClick={() => act(() => host.call('hook_run', { name: h.name }), (r) => `${h.name}: ${JSON.stringify(r.ran?.[0]?.ok ?? r.ran?.[0]?.passed ?? 'ran')}`)}>Run now</button>}</td></tr>)}</tbody></table></div>}
          {idle.length > 0 && <p><button type="button" className="sm" onClick={() => act(() => host.call('hook_install_starters', { approve: true }), (r) => `Added and approved: ${r.approved.join(', ')}`)}>Add starter hooks</button>{' '}
            <span className="muted small">{idle.map(([n, s]: any) => `${n} (${s.description})`).join(' · ')}</span></p>}
          {unapproved.length > 0 && <p className="help-warn">{unapproved.length} hook(s) are waiting for your approval and will not run until you approve them.</p>}

          <h3>MCP server</h3>
          <p className="muted small">The same records as the CLI, as tools an agent calls directly (<code>research_current</code>, <code>task_context</code>, <code>finding_review</code>, <code>guide_status</code>, <code>report</code>…), so it works from the brief and not from memory. Registered per agent above (<i>MCP registered</i>); the command it runs: <code>{d.mcp_command.join(' ')}</code></p>
        </>;
      }}</State>
    </Section>
  );
}
