import { useMemo, useState } from 'react';
import { Badge, Banner, Section, Segmented, State, useAsync } from './common';
import type { Host } from './host';

const STAGE: Record<string, string> = { preflight: 'before a task is dispatched', post_implementation: 'after an agent’s change', pre_experiment: 'before a run is created', post_experiment: 'after runs finish' };
const KIND: Record<string, { label: string; hint: string }> = {
  'first-party': { label: 'Research OS', hint: 'Written for Research OS; ships with every project' },
  bundled: { label: 'bundled', hint: 'A curated, attributed copy of a third-party skill (works offline)' },
  external: { label: 'from source', hint: 'Installed from its GitHub source at a pinned commit when you ask (needs network)' },
};
type Tab = 'skills' | 'packs' | 'hooks' | 'files';

/** Skills, hooks and the MCP server: what each is for, what is installed and active, and the one-click fixes. Files stay the source of truth. */
export function ToolsSection({ host }: { host: Host }) {
  const a = useAsync<any>(() => host.call('tools'), []);
  const [tab, setTab] = useState<Tab>('skills');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState('');
  const act = async (key: string, fn: () => Promise<any>, ok: (r: any) => string) => {
    setBusy(key);
    try { const r = await fn(); setMsg({ ok: true, text: ok(r) }); } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(''); }
    a.reload();
  };
  return (
    <Section id="tools" title="Skills, hooks and MCP"
      lead="Three ways Research OS shapes what an agent does. Skills tell it how to work, hooks are programs that check its work, and the MCP server lets it read and write the research records instead of guessing. A skill reaches an agent’s brief only when the task type or role matches it.">
      {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} role="status" onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
      <State a={a}>{(d) => <>
        <Segmented label="Skills, hooks and MCP views" value={tab} onChange={setTab}
          options={[{ id: 'skills', label: 'Skills', n: d.enabled_skill_count }, { id: 'packs', label: 'Packs', n: d.enabled_packs.length }, { id: 'hooks', label: 'Hooks', n: d.hooks.length }, { id: 'files', label: 'MCP & files' }]} />
        {tab === 'skills' && <Skills host={host} d={d} act={act} busy={busy} />}
        {tab === 'packs' && <Packs d={d} act={act} busy={busy} host={host} />}
        {tab === 'hooks' && <Hooks host={host} d={d} act={act} busy={busy} />}
        {tab === 'files' && <Files host={host} d={d} />}
      </>}</State>
    </Section>
  );
}

function Skills({ host, d, act, busy }: any) {
  const [q, setQ] = useState('');
  const [pack, setPack] = useState('');
  const [show, setShow] = useState('all');
  const linked = (k: string) => d.links[k]?.linked.length || 0;
  const missing = d.link_targets.some((k: string) => linked(k) < d.enabled_skill_count);
  const rows = useMemo(() => d.catalog.filter((r: any) => {
    const hay = (r.name + ' ' + (r.description || '') + ' ' + (r.packs || []).join(' ') + ' ' + (r.applies_to || []).join(' ')).toLowerCase();
    const hit = q.toLowerCase().split(/\s+/).filter(Boolean).every((w) => hay.includes(w) || hay.includes(w.length > 6 ? w.slice(0, -3) : w.length > 4 ? w.slice(0, -1) : w));   // statistics ~ statistical
    return hit && (!pack || (r.packs || []).includes(pack))
      && (show === 'all' || (show === 'installed' && r.installed) || (show === 'available' && !r.installed) || (show === 'disabled' && r.disabled) || (show === 'updates' && (r.update_available || r.modified)));
  }), [d.catalog, q, pack, show]);
  const updates = d.catalog.filter((r: any) => r.update_available).length;
  return <>
    <p className="muted small">Short written procedures (a <code>SKILL.md</code> each) such as <i>run-experiment</i> or <i>hypothesis-design</i>, kept in <code>.research/skills</code> and linked into each agent’s skills folder (see Links below) so the agents find them. Settings live in <code>.research/skills.yaml</code> and <code>skills.packs</code> / <code>skills.disabled</code> in the config; edit those files and this view follows.</p>
    <p>{d.link_targets.map((k: string) => <span key={k}><Badge kind={linked(k) >= d.enabled_skill_count ? 'ok' : 'warn'}>{k}: {linked(k)} of {d.enabled_skill_count} linked</Badge>{' '}</span>)}
      {missing && <button type="button" className="sm primary" onClick={() => act('link', () => host.call('skills_link'), () => 'Skills linked for the agent CLIs')}>Link skills now</button>}{' '}
      <button type="button" className="sm" disabled={busy === 'upgrade'} title="Bring bundled and first-party skills to this release; skills you changed are never replaced" onClick={() => act('upgrade', () => host.call('skills_upgrade'), (r) => (r.updated.length ? `Updated: ${r.updated.join(', ')}` : 'Bundled skills are current') + (r.kept_yours.length ? `. Left alone because you changed them: ${r.kept_yours.join(', ')}` : ''))}>Update bundled skills</button>
      {updates > 0 && <Badge kind="info">{updates} update(s) available</Badge>}</p>
    <div className="row" role="search">
      <label htmlFor="sk-q" className="sr">Search the skills catalog</label>
      <input id="sk-q" type="search" placeholder="Search skills (e.g. statistics, debugging, qiskit)" value={q} onChange={(e) => setQ(e.target.value)} size={34} />
      <label htmlFor="sk-pack" className="sr">Pack</label>
      <select id="sk-pack" value={pack} onChange={(e) => setPack(e.target.value)}><option value="">All packs</option>{d.packs.map((x: any) => <option key={x.id} value={x.id}>{x.title}</option>)}</select>
      <label htmlFor="sk-show" className="sr">Show</label>
      <select id="sk-show" value={show} onChange={(e) => setShow(e.target.value)}><option value="all">Everything</option><option value="installed">Installed</option><option value="available">Not installed</option><option value="disabled">Disabled</option><option value="updates">Updates / changed by you</option></select>
    </div>
    <p className="muted small" aria-live="polite">{rows.length} of {d.catalog.length} skills</p>
    <div className="tbl-wrap"><table><caption className="sr">Skills catalog</caption>
      <thead><tr><th scope="col">Skill</th><th scope="col" className="hide-narrow">Source</th><th scope="col">State</th><th scope="col" className="hide-narrow">Activates for</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
      <tbody>{rows.map((r: any) => (
        <tr key={r.name}>
          <td><b>{r.name}</b><span className="sub" title={r.description}>{String(r.description || '').slice(0, 110)}{String(r.description || '').length > 110 ? '…' : ''}</span></td>
          <td className="hide-narrow"><span title={KIND[r.kind]?.hint}><Badge kind={r.kind === 'external' ? 'warn' : ''}>{KIND[r.kind]?.label || r.kind}</Badge></span><span className="sub">{r.license || 'license not stated'}{r.commit ? ` · ${r.commit.slice(0, 7)}` : ''}</span></td>
          <td>{r.installed ? (r.disabled ? <Badge kind="">disabled</Badge> : <Badge kind="ok">on</Badge>) : <Badge kind="">not installed</Badge>}
            {r.modified && <span className="sub" title="You changed this skill; updates never replace it">yours (edited)</span>}
            {r.update_available && <span className="sub">update available</span>}</td>
          <td className="hide-narrow"><ActivationEdit host={host} r={r} act={act} /></td>
          <td className="min">
            {!r.installed && <button type="button" className="sm primary" disabled={busy === 'i' + r.name} onClick={() => act('i' + r.name, () => host.call('skill_install', { names: [r.name] }), (x) => `${r.name}: ${x.results[0].state}${x.results[0].why ? ' (' + x.results[0].why + ')' : ''}`)}>{r.network ? 'Install from source' : 'Install'}</button>}
            {r.installed && <button type="button" className="sm" onClick={() => act('t' + r.name, () => host.call('skill_toggle', { name: r.name, enabled: r.disabled }), () => `${r.name} ${r.disabled ? 'enabled' : 'disabled'}`)}>{r.disabled ? 'Enable' : 'Disable'}</button>}
          </td>
        </tr>))}</tbody></table></div>
    {d.own.length > 0 && <p className="muted small">Your own skills (not in the catalog, never touched): {d.own.map((s: any) => s.name).join(', ')}.</p>}
    <p className="muted small">External skills come from the repository named in the catalog at a pinned commit, under that repository’s license (shown above); read it before relying on one. <code>research skills add &lt;folder or git url&gt;</code> imports any other.</p>
  </>;
}

/** applies_to is the activation rule: task types, roles or tags. Edited here, stored in .research/skills.yaml. */
function ActivationEdit({ host, r, act }: any) {
  const [edit, setEdit] = useState(false);
  const [v, setV] = useState((r.applies_to || []).join(', '));
  if (!r.installed) return <span className="muted small">{(r.applies_to || []).join(', ') || 'on request'}</span>;
  if (!edit) return <span><span className="small">{r.always ? 'always on' : (r.applies_to || []).join(', ') || 'only when assigned'}</span>{' '}<button type="button" className="link small" onClick={() => setEdit(true)} aria-label={`Edit activation of ${r.name}`}>edit</button></span>;
  return (
    <form className="row" onSubmit={(e) => { e.preventDefault(); act('a' + r.name, () => host.call('skills_set', { name: r.name, applies_to: v.split(',').map((x: string) => x.trim()).filter(Boolean) }), () => `${r.name} now activates for: ${v || 'nothing (only when assigned)'}`); setEdit(false); }}>
      <label className="sr" htmlFor={'ap-' + r.name}>Task types or roles for {r.name}</label>
      <input id={'ap-' + r.name} type="text" value={v} onChange={(e) => setV(e.target.value)} size={22} placeholder="analysis, verifier, debug" data-autofocus />
      <button type="submit" className="sm primary">Save</button><button type="button" className="sm" onClick={() => setEdit(false)}>Cancel</button>
    </form>);
}

function Packs({ d, act, busy, host }: any) {
  const [opt, setOpt] = useState<Record<string, boolean>>({});
  return <>
    <p className="muted small">A pack is a set of skills for a kind of work. Core Research is always on. Enabling a pack installs its skills (any you already have or changed are left alone); disabling one switches its skills off without deleting anything. The same choice lives in <code>skills.packs</code>: put it in the machine file for the default of every new project.</p>
    <div className="cards">{d.packs.map((x: any) => (
      <section key={x.id} className={'card' + (x.enabled ? ' sel' : '')} aria-label={x.title} style={{ margin: '8px 0', padding: 12 }}>
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <div><b>{x.title}</b> {x.default && <Badge kind="ok">always on</Badge>} <span className="muted small">{x.installed} of {x.total} skills installed</span></div>
          {x.enabled ? <Badge kind="ok">on</Badge> : <Badge kind="">off</Badge>}
        </div>
        <p className="muted small">{x.description}</p>
        <p className="small">{x.skills.slice(0, 8).join(', ')}{x.skills.length > 8 ? `, +${x.skills.length - 8} more` : ''}</p>
        {x.optional.length > 0 && <label className="check small"><input type="checkbox" checked={!!opt[x.id]} onChange={(e) => setOpt({ ...opt, [x.id]: e.target.checked })} /> Also install from source (network): {x.optional.join(', ')}</label>}
        <div className="row">
          {!x.enabled && <button type="button" className="sm primary" disabled={busy === 'p' + x.id} onClick={() => act('p' + x.id, () => host.call('pack_set', { id: x.id, enabled: true, optional: !!opt[x.id] }), (r) => `${x.title}: ` + r.results.map((y: any) => `${y.name} ${y.state}`).join(', '))}>Enable pack</button>}
          {x.enabled && !x.default && <button type="button" className="sm" onClick={() => act('p' + x.id, () => host.call('pack_set', { id: x.id, enabled: false }), () => `${x.title} off; its skills are disabled (files kept)`)}>Disable pack</button>}
          {x.enabled && x.installed < x.total && <button type="button" className="sm" onClick={() => act('p' + x.id, () => host.call('pack_set', { id: x.id, enabled: true, optional: !!opt[x.id] }), () => `${x.title} completed`)}>Install missing</button>}
        </div>
      </section>))}</div>
    <p className="muted small"><button type="button" className="link" onClick={() => host.openDoc('docs/AGENT_TOOLS.md')}>How packs, sources and licenses work</button></p>
  </>;
}

function Hooks({ host, d, act, busy }: any) {
  const unapproved = d.hooks.filter((h: any) => !h.approved);
  const offer = d.recommended_hooks.filter((h: any) => !h.installed);
  const note = (h: any) => { const o = String(h.last?.output || ''); return o.startsWith('SKIPPED') ? 'skipped: ' + o.slice(9, 90) : h.last ? (h.last.ok ? 'passed' : 'FAILED') : ''; };
  return <>
    <p className="muted small">Commands <b>you</b> approve, run by Research OS at fixed points (<i>{Object.values(STAGE).join(' · ')}</i>). They are deterministic checks that replace tokens an agent would spend re-checking what a program can verify. Nothing runs until you approve that exact command; editing one revokes its approval. Cheap checks are on by default; the ones that run your code are never automatic. Detected here: <b>{d.stacks.join(', ') || 'no code yet'}</b>.</p>
    {d.hooks.length === 0 ? <p className="muted">No hooks yet.</p> : <div className="tbl-wrap"><table><caption className="sr">Installed hooks</caption><thead><tr><th scope="col">Hook</th><th scope="col">Runs</th><th scope="col">State</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
      <tbody>{d.hooks.map((h: any) => <tr key={h.name}><td><b>{h.name}</b><span className="sub"><code>{h.command}</code></span>{h.description && <span className="sub">{h.description}</span>}</td><td>{STAGE[h.stage] || h.stage}{h.required ? <span className="sub">blocks on failure</span> : <span className="sub">never blocks</span>}</td>
        <td>{h.approved ? (h.enabled ? <Badge kind="ok">approved</Badge> : <Badge kind="">disabled</Badge>) : <Badge kind="warn">needs your approval</Badge>}{h.last && <span className="sub">last test: {note(h)}</span>}</td>
        <td className="min">{!h.approved && <button type="button" className="sm primary" onClick={() => act('ha' + h.name, () => host.call('hook_approve', { names: [h.name] }), () => `${h.name} approved`)}>Approve</button>}
          {h.approved && <button type="button" className="sm" onClick={() => act('ht' + h.name, () => host.call('hook_toggle', { name: h.name, enabled: !h.enabled }), () => h.enabled ? `${h.name} disabled` : `${h.name} enabled`)}>{h.enabled ? 'Disable' : 'Enable'}</button>}
          {h.approved && h.enabled && <button type="button" className="sm" disabled={busy === 'hr' + h.name} onClick={() => act('hr' + h.name, () => host.call('hook_run', { name: h.name }), (r) => { const x = (r.ran || [])[0]; return x ? `${h.name}: ${x.ok ? 'passed' : 'FAILED'} · ${String(x.output || '').split('\n')[0].slice(0, 120)}` : `${h.name}: ${(r.skipped || [])[0]?.reason || 'did not run'}`; })}>Test now</button>}</td></tr>)}</tbody></table></div>}
    {unapproved.length > 0 && <p className="help-warn">{unapproved.length} hook(s) wait for your approval and will not run until you approve them.
      <button type="button" className="sm" onClick={() => act('haall', () => host.call('hook_approve', { names: unapproved.map((h: any) => h.name) }), () => 'All approved')}>Approve all</button></p>}
    <h3>Recommended for this project <span className="count">{offer.length}</span></h3>
    {offer.length === 0 ? <p className="muted">Everything that applies is installed.</p> : <div className="tbl-wrap"><table><caption className="sr">Recommended hooks</caption><thead><tr><th scope="col">Hook</th><th scope="col">Cost</th><th scope="col">Fits</th><th scope="col"><span className="sr">Install</span></th></tr></thead>
      <tbody>{offer.map((h: any) => <tr key={h.name}><td><b>{h.name}</b><span className="sub">{h.description}</span></td>
        <td><Badge kind={h.tier === 'costly' ? 'warn' : ''}>{h.tier === 'costly' ? 'runs your code' : 'cheap'}</Badge></td>
        <td>{h.applies ? <Badge kind="ok">{h.stacks ? h.stacks.filter((s: string) => d.stacks.includes(s)).join(', ') : 'any project'}</Badge> : <span className="muted small">needs {(h.stacks || []).join(' / ')}</span>}</td>
        <td className="min"><button type="button" className="sm" onClick={() => act('hi' + h.name, () => host.call('hook_install', { names: [h.name], approve: false }), () => `${h.name} added; approve it to let it run`)}>Add</button>
          <button type="button" className="sm primary" onClick={() => act('hi' + h.name, () => host.call('hook_install', { names: [h.name], approve: true }), () => `${h.name} added and approved`)}>Add and approve</button></td></tr>)}</tbody></table></div>}
    <p className="muted small">Add your own: <code>research hook add NAME --stage post_implementation --command "…" [--advisory]</code>. Details: <button type="button" className="link" onClick={() => host.openDoc('docs/AGENT_TOOLS.md')}>docs/AGENT_TOOLS.md</button>.</p>
  </>;
}

function Files({ host, d }: any) {
  return <>
    <h3>MCP server</h3>
    <p className="muted small">The same records as the CLI, as tools an agent calls directly (<code>research_current</code>, <code>task_context</code>, <code>finding_review</code>, <code>guide_status</code>, <code>report</code>…), so it works from the brief and not from memory. Registration per agent is on the cards above (<i>MCP registered</i>). The command it runs: <code>{d.mcp_command.join(' ')}</code></p>
    <h3>Where the settings live</h3>
    <ul className="small">
      <li>Skill packs and switches: <code>skills.packs</code>, <code>skills.disabled</code> in <button type="button" className="link" onClick={() => host.openFile(d.config_files.project)}>{d.config_files.project}</button>{d.config_files.machine && <> or the machine file <button type="button" className="link" onClick={() => host.openFile(d.config_files.machine)}>{d.config_files.machine}</button> (the default for new projects; the project value wins)</>}.</li>
      <li>Per-skill activation, <code>always</code> and <code>level</code>: <code>.research/skills.yaml</code>. Hooks: <code>harness.hooks</code> in the config; approvals in <code>harness.hooks_approved</code>.</li>
      <li>Agent links: {Object.entries(d.links).map(([k, v]: any) => <span key={k}><code>{v.dir}</code> ({v.linked.length}) </span>)}</li>
    </ul>
  </>;
}
