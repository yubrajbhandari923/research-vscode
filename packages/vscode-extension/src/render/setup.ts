/**
 * Agent Setup — who does which work, with what project guidance and tools.
 *
 * Reading order: model strategy (the decision with consequences) → project harness (what guides the work) →
 * skills → MCP servers → legacy orchestration detail, collapsed. Changes go through native quick-picks and
 * existing commands; the page itself only reads and links, so it stays calm.
 */
import { esc, truncate } from '../util';
import { blkHd, btn, disclosure, empty, ic, overflow, pill } from './ui';

const CLASS_LABEL: [string, string][] = [
  ['planning', 'Planning the work'], ['scientific', 'Scientific synthesis'], ['checked', 'Implementation with checks'],
  ['unchecked', 'Implementation without checks yet'], ['mechanical', 'Mechanical chores'], ['verification', 'Independent verification'],
];

const model = (x: any) => (x ? `<span class="mono">${esc(x.model || 'CLI default')}</span> <span class="muted small">${esc(x.profile)}</span>` : '<span class="muted">—</span>');

function strategySection(d: any): string {
  const s = d.strategy;
  const others = (d.strategies as any[]).filter((x) => !s || x.name !== s.name);
  const change = btn(s ? 'Change strategy…' : 'Choose a strategy…', 'research.strategy.use', [], { icon: 'arrow-swap', primary: !s });
  if (!s) {
    return `<section>${blkHd('organization', 'Model strategy', 'var(--accent)', change)}
      <div class="empty-card">${ic('info')}<span class="grow"><b>No strategy chosen.</b> This project uses the original Opus → Sonnet → Haiku tiers. A strategy decides which model
      plans, implements, verifies and synthesizes, and when work escalates. <code>sonnet_first</code> is the recommended starting point; you can switch at any time and
      running sessions keep what they started with.</span>${btn('Use sonnet_first', 'research.strategy.use', ['sonnet_first'], { primary: true })}</div>
      ${alternatives(others)}</section>`;
  }
  const dd = s.definition;
  const rows = CLASS_LABEL.map(([c, label]) => {
    const lv = dd.route[c];
    return `<tr><td>${esc(label)}</td><td class="muted small">${esc(lv)}</td><td>${model(s.resolved_levels[lv])}</td></tr>`;
  }).join('') + `<tr><td>Scientist Guide</td><td class="muted small">${esc(dd.guide)}</td><td>${model(s.resolved_levels[dd.guide])}</td></tr>`;
  const att = Object.keys(dd.levels).filter((lv) => dd.attempts[lv]).map((lv) => {
    const to = dd.escalate[lv];
    return `<span class="nowrap"><b>${esc(lv)}</b> ${dd.attempts[lv]} tries → ${to === 'human' ? 'asks you' : esc(to || '—')}</span>`;
  }).join(' · ');
  const problems = [...s.validation.errors.map((e: string) => `<div class="small" style="color:var(--red)">${ic('error')} ${esc(e)}</div>`),
    ...s.validation.warnings.map((w: string) => `<div class="small" style="color:var(--yellow)">${ic('warning')} ${esc(w)}</div>`)].join('');
  return `<section>${blkHd('organization', 'Model strategy', 'var(--accent)', change)}
    <div class="row" style="gap:8px;margin-bottom:2px"><b style="font-size:14px">${esc(s.name)}</b>${pill('active', { label: 'active · ' + s.origin })}
      <span class="muted small mono" title="Fingerprint of the resolved policy; recorded with every dispatch">@${esc(s.fingerprint)}</span></div>
    <div class="muted" style="margin-bottom:8px">${esc(dd.description || '')}</div>
    <div class="tablewrap"><table class="kv routes"><thead><tr><th>Work</th><th>Level</th><th>Model</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="small muted" style="margin-top:8px"><b>When work fails:</b> ${att || 'no limits set'} · max ${dd.max_escalations} escalations. Scientific-assumption problems → ${dd.on_scientific_assumption === 'human' ? 'you' : esc(dd.on_scientific_assumption)}.</div>
    ${problems}
    ${disclosure('Other strategies', alternatives(others), 'setup-others')}
    <div class="small muted" style="margin-top:6px">Switching affects future dispatches only. Edit levels, models or limits under <code>orchestration.strategies</code> in
      <a data-file=".research/config.yaml">config.yaml</a>.</div></section>`;
}

function alternatives(others: any[]): string {
  if (!others.length) return '';
  return `<div class="card flush"><div class="list">${others.map((o) => `<div class="item" style="cursor:default">
    ${ic(o.ok ? 'circle-large-outline' : 'warning', 'lead')}<div class="grow"><div class="hl"><span class="title">${esc(o.name)}</span><span class="muted small">${esc(o.origin)}</span></div>
    <div class="sub">${esc(truncate(o.description || '', 150))}</div></div>
    <div class="trail">${btn('Use', 'research.strategy.use', [o.name], { ghost: true, cls: 'sm' })}</div></div>`).join('')}</div></div>`;
}

function harnessSection(d: any): string {
  const h = d.harness;
  const edit = btn(h.configured ? 'Edit in config.yaml' : 'Set up in config.yaml', 'research.setup.openConfig', [], { icon: 'go-to-file', ghost: true });
  if (!h.configured) {
    return `<section>${blkHd('tools', 'Project harness', 'var(--purple)', edit)}
      ${empty('No harness yet. A harness points agents at what your project already has: its skills, conventions, an instructions file and verification commands. It is independent of the model strategy, so you can change either without touching the other.')}${hooksBlock(d)}</section>`;
  }
  const li = (icon: string, text: string, bad = false, why = 'not found') => `<li style="list-style:none;margin:2px 0">${ic(bad ? 'warning' : icon)} ${text}${bad ? ` <span class="small" style="color:var(--yellow)">${esc(why)}</span>` : ''}</li>`;
  const rows = [
    ...(h.instructions ? [li('book', `Instructions <a data-file="${esc(h.instructions)}">${esc(h.instructions)}</a>`, h.instructions_missing)] : []),
    ...h.skills_detail.map((s: any) => li('mortar-board', `Skill <b>${esc(s.name)}</b>`, s.missing, 'not installed')),
    ...h.context_detail.map((c: any) => li('symbol-structure', `Context <a data-file="${esc(c.path)}">${esc(c.path)}</a>`, c.missing)),
    ...h.checks.map((c: string) => li('beaker', `Verification <code>${esc(c)}</code>`)),
  ].join('');
  return `<section>${blkHd('tools', 'Project harness', 'var(--purple)', edit)}
    <div style="margin-bottom:4px"><b style="font-size:14px">${esc(h.name || 'unnamed harness')}</b> <span class="muted small mono">@${esc(h.fingerprint)}</span></div>
    <ul style="margin:0;padding:0">${rows}</ul>
    <div class="small muted" style="margin-top:6px">References are carried into every agent brief; Research OS does not interpret them. Only hooks below ever execute, and only after you approve them.</div>${hooksBlock(d)}</section>`;
}

function hooksBlock(d: any): string {
  const hs = d.hooks as any[];
  const head = `<div class="row" style="margin:10px 0 2px"><b>Hooks</b><span class="small muted">commands that run at workflow stages; deterministic checks instead of model tokens</span><span class="spacer"></span>
    ${hs.some((h) => !h.approved && h.enabled) ? btn('Review and approve…', 'research.hook.approve', [], { icon: 'shield', primary: true, cls: 'sm' }) : ''}</div>`;
  if (!hs.length) return head + empty('No hooks yet. The orchestrator can propose one (research hook add …) when it finds a check worth repeating, such as a conservation test after each change. Nothing runs until you approve it.');
  const STAGE: Record<string, string> = { preflight: 'before a task starts', post_implementation: 'after a worker’s change', pre_experiment: 'before a run', post_experiment: 'after runs finish' };
  const row = (h: any) => {
    const l = h.last;
    const state = !h.enabled ? 'disabled' : !h.approved ? 'needs your approval' : l ? (l.ok ? `passed ${l.ts.slice(0, 16).replace('T', ' ')}` : `FAILED ${l.ts.slice(0, 16).replace('T', ' ')}${l.timed_out ? ' (timed out)' : ''}`) : 'approved, not run yet';
    const icon = !h.enabled ? 'circle-slash' : !h.approved ? 'shield' : l && !l.ok ? 'error' : l ? 'pass' : 'circle-large-outline';
    const color = !h.approved && h.enabled ? 'var(--yellow)' : l && !l.ok ? 'var(--red)' : 'var(--muted)';
    return `<div class="item" style="cursor:default;padding:4px 10px"><i class="codicon codicon-${icon} lead" style="color:${color}"></i>
      <div class="grow"><div class="hl"><span class="title">${esc(h.name)}</span><span class="small muted">${esc(STAGE[h.stage] || h.stage)}${h.required ? '' : ' · advisory'}</span></div>
      <div class="sub"><span class="mono">${esc(truncate(h.command, 90))}</span> · ${esc(state)} · ${h.timeout}s limit</div>
      ${l && !l.ok ? `<div class="sub mono" style="color:var(--red)">${esc(truncate((l.output || '').trim().split('\n').slice(-2).join(' ⏎ '), 160))}</div>` : ''}</div>
      <div class="trail">${overflow([...(h.approved && h.enabled ? [['Run now', 'research.hook.run', [h.name], 'play'] as any] : []),
        [h.enabled ? 'Disable' : 'Enable', 'research.hook.toggle', [h.name, !h.enabled], h.enabled ? 'circle-slash' : 'check']], 'Hook actions')}</div></div>`;
  };
  return head + `<div class="card flush"><div class="list">${hs.map(row).join('')}</div></div>`;
}

function tasksSection(d: any): string {
  const ts = d.tasks as any[];
  if (!ts.length) return '';
  const rows = ts.map((t) => {
    const c = t.contract || {};
    const cn = Object.keys(c).length;
    const model = t.override ? `pinned ${esc(t.override.level || t.override.profile || t.override.model)}` : t.route ? esc(t.route.level) : '';
    const verify = [t.checks ? `${t.checks} check${t.checks > 1 ? 's' : ''}` : 'no checks', t.frozen.length ? 'frozen tests' : '', c.allowed_files ? `files: ${c.allowed_files.length}` : ''].filter(Boolean).join(' · ');
    const a = [t.id];
    return `<div class="item" style="cursor:default;padding:4px 10px"><i class="codicon codicon-${t.override ? 'pin' : 'tasklist'} lead"></i>
      <div class="grow"><div class="hl"><a class="title" data-open="${esc(t.id)}">${esc(t.title)}</a><span class="small muted">${esc(t.status)}</span></div>
        <div class="sub">${model ? `${model}${t.route ? ' · <span class="mono">' + esc(t.route.model || 'CLI default') + '</span>' : ''} · ` : ''}${esc(verify)}${cn ? ` · contract (${cn})` : ' · no contract'}${t.attempts ? ` · ${t.attempts} attempt${t.attempts > 1 ? 's' : ''}` : ''}</div></div>
      <div class="trail">${overflow([['Pin a model…', 'research.task.pin', a, 'pin'], ...(t.override ? [['Use strategy default', 'research.task.unpin', a, 'close'] as any] : [])], 'Task model')}</div></div>`;
  }).join('');
  return `<section>${blkHd('tasklist', 'Work in flight', 'var(--accent)', '', ts.length)}
    <div class="muted small" style="margin-bottom:6px">Which model each open task goes to, and what must verify it. A pin overrides the strategy for that task's implementer only.</div>
    <div class="card flush"><div class="list">${rows}</div></div></section>`;
}

function experimentsSection(d: any): string {
  const es = d.experiments as any[];
  if (!es.length) return '';
  const row = (e: any) => {
    const st = e.state;
    const bits = [`${st.runs} run${st.runs === 1 ? '' : 's'}`, st.active ? `${st.active} active` : '', st.failed ? `${st.failed} failed` : '',
      st.needs_synthesis || st.over_run_budget ? 'synthesis due' : st.unsynthesized ? `${st.unsynthesized} unsynthesized` : ''].filter(Boolean).join(' · ');
    const due = st.needs_synthesis || st.over_run_budget;
    return `<div class="item" style="cursor:default;padding:4px 10px"><i class="codicon codicon-${due ? 'sparkle' : 'beaker'} lead" style="color:${due ? 'var(--yellow)' : 'var(--muted)'}"></i>
      <div class="grow"><div class="hl"><a class="title" data-open="${esc(e.id)}">${esc(e.title)}</a><span class="small muted">${esc(e.status)}</span></div>
      <div class="sub">${esc(bits)} · ${e.validation ? 'checked: ' + esc(truncate(e.validation.summary, 90)) : 'not checked yet'}</div></div>
      <div class="trail">${btn('Check', 'research.experiment.check', [e.id], { ghost: true, cls: 'sm', title: 'Run deterministic validation (no model involved)' })}</div></div>`;
  };
  return `<section>${blkHd('beaker', 'Experiment validation', 'var(--green)', '', es.length)}
    <div class="muted small" style="margin-bottom:6px"><b>Check</b> verifies with programs only: runs finished, metrics logged, artifacts intact, approved hooks pass. What it cannot decide (unreviewed failures, a due synthesis) is listed for you.</div>
    <div class="card flush"><div class="list">${es.map(row).join('')}</div></div></section>`;
}

function usageSection(d: any): string {
  const g = d.usage.groups as Record<string, any>;
  const keys = Object.keys(g).sort();
  if (!keys.length) return `<section>${blkHd('graph', 'Model usage', 'var(--gray)')}${empty('No usage recorded yet. Run an agent with `research orchestrate dispatch --headless` and Research OS records the tokens and cost the provider reports. Interactive sessions do not expose usage, so nothing is guessed for them.')}</section>`;
  const cell = (x: any, rec: number, money = false) => x.total === null ? '<span class="muted">—</span>' : `${money ? '$' : ''}${Number(x.total).toLocaleString(undefined, { maximumFractionDigits: money ? 3 : 0 })}<span class="muted small"> ${x.reported}/${rec}</span>`;
  const rows = keys.map((k) => `<tr><td>${esc(k)}</td><td>${g[k].tasks}</td><td>${cell(g[k].input_tokens, g[k].records)}</td><td>${cell(g[k].output_tokens, g[k].records)}</td><td>${cell(g[k].cache_read_tokens, g[k].records)}</td><td>${cell(g[k].cost_usd, g[k].records, true)}</td></tr>`).join('');
  return `<section>${blkHd('graph', 'Model usage by strategy', 'var(--gray)')}
    <div class="tablewrap"><table class="kv routes"><thead><tr><th>Strategy</th><th>Tasks</th><th>Input</th><th>Output</th><th>Cache read</th><th>Cost</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="small muted" style="margin-top:6px">n/m = records that reported the value; a missing value is never counted as zero. Cost is what the provider reported.</div></section>`;
}

function skillsSection(d: any): string {
  const sk = d.skills as any[];
  const head = `<span class="btnrow">${btn('Import…', 'research.skill.import', [], { icon: 'cloud-download', ghost: true })}${btn('New skill', 'research.skill.create', [], { icon: 'add', ghost: true })}</span>`;
  if (!sk.length) return `<section>${blkHd('mortar-board', 'Skills', 'var(--purple)', head)}${empty('No skills yet. Skills are short how-to guides agents load for a kind of work (a numerical convention, an evaluation recipe). Import some, or write one for your project.')}</section>`;
  const row = (s: any) => {
    const a = [{ key: 'ag/sk/' + (s.dir || s.name) }];
    return `<div class="item" style="cursor:default;padding:4px 10px">${ic(s.always ? 'star-full' : 'mortar-board', 'lead')}
      <div class="grow"><div class="hl"><a class="title" data-file="${esc(s.path)}">${esc(s.name)}</a>
        ${s.always ? '<span class="small muted">always on</span>' : ''}${s.in_harness ? '<span class="small muted">in harness</span>' : ''}</div>
        <div class="sub ellipsis" title="${esc(s.description || '')}">${esc(s.description || '')}</div></div>
      <div class="trail">${overflow([[s.always ? 'Stop loading in every session' : 'Load in every session', 'research.skill.toggleAlways', a, 'star'],
        ...(s.source ? [['Update from source', 'research.skill.update', a, 'sync'] as any] : []), '-', ['Remove', 'research.skill.remove', a, 'trash']], 'Skill actions')}</div></div>`;
  };
  return `<section>${blkHd('mortar-board', 'Skills', 'var(--purple)', head, sk.length)}<div class="card flush"><div class="list">${sk.slice(0, 6).map(row).join('')}</div></div>
    ${sk.length > 6 ? disclosure(`${sk.length - 6} more skills`, `<div class="card flush"><div class="list">${sk.slice(6).map(row).join('')}</div></div>`, 'setup-skills') : ''}</section>`;
}

function mcpSection(d: any): string {
  const m = d.mcp;
  const reg = m.clients.filter((c: any) => c.registered).length;
  const rows = m.clients.map((c: any) => `<div class="item" style="cursor:default;padding:4px 10px">${ic(c.registered ? 'pass' : 'circle-large-outline', 'lead')}
      <div class="grow"><div class="hl"><span class="title">${esc(c.client)}</span></div>
      <div class="sub">${c.error ? `<span style="color:var(--red)">${esc(c.error)}</span>` : c.registered ? 'research server registered' : c.exists ? 'file exists; research server not registered' : 'not set up'} · <span class="mono">${esc(c.file)}</span></div></div></div>`).join('');
  const others = m.others.length ? `<div class="small muted" style="margin-top:8px"><b>Other servers your agents can use</b> (read from the same files, managed by you)</div>
    <div class="card flush"><div class="list">${m.others.map((o: any) => `<div class="item" style="cursor:default">${ic('plug', 'lead')}<div class="grow"><div class="hl"><span class="title">${esc(o.name)}</span><span class="muted small">${esc(o.client)}</span></div><div class="sub mono">${esc(truncate(o.command, 100))}</div></div></div>`).join('')}</div></div>` : '';
  return `<section>${blkHd('plug', 'MCP servers', 'var(--blue)', btn(reg ? 'Register for more agents…' : 'Register research server…', 'research.mcp.install', [], { icon: 'add', ghost: true }))}
    <div class="muted" style="margin-bottom:6px">The research server lets agents read and record project state directly. Codex is configured ${esc(m.codex)}.
    Research OS never installs third-party servers; add those yourself.</div>
    <div class="card flush"><div class="list">${rows}</div></div>${others}</section>`;
}

function orchestrationSection(d: any): string {
  const o = d.orchestration;
  const files = o.agent_files.map((f: any) => `<div class="item" style="cursor:default">${ic('hubot', 'lead')}<div class="grow"><div class="hl"><a class="title" data-file="${esc(f.path)}">${esc(f.path.split('/').pop())}</a>
    <span class="small muted">${esc({ create: 'will be created', update: 'out of date', unchanged: 'up to date', 'skip-user-owned': 'yours, left untouched' }[f.state as string] || f.state)}</span></div>
    <div class="sub mono">${esc(f.model)}</div></div></div>`).join('');
  const lim = Object.entries(o.limits || {}).filter(([, v]) => v !== null).map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`).join(' · ');
  const body = `<div class="small muted" style="margin-bottom:6px">Claude Code sub-agent files that Research OS manages (<code>research-*.md</code>). Files without the managed marker are never overwritten, and your own agents are not touched.</div>
    <div class="card flush"><div class="list">${files}</div></div>
    ${lim ? `<div class="small muted" style="margin-top:6px"><b>Legacy tier limits:</b> ${esc(lim)}</div>` : ''}
    <div class="btnrow" style="margin-top:8px">${btn(o.enabled ? 'Update agent files…' : 'Set up agents…', 'research.setup.agents', [], { icon: 'sparkle', ghost: true })}</div>`;
  return `<section>${blkHd('hubot', 'Agent files & orchestration', 'var(--gray)', `<span class="small muted">${o.enabled ? 'enabled' : 'not set up'}</span>`)}${body}</section>`;
}

export function renderSetup(d: any): string {
  return `<div class="page narrow setup stack">
    <div class="row"><div class="grow"><h1 style="margin:0;font-size:18px">Agent setup</h1>
      <div class="muted">Which models do which work, what guides them, and the tools they can reach.</div></div></div>
    ${strategySection(d)}${tasksSection(d)}${harnessSection(d)}${experimentsSection(d)}${usageSection(d)}${skillsSection(d)}${mcpSection(d)}${orchestrationSection(d)}</div>`;
}
