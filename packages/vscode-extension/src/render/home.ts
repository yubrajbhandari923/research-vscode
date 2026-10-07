/**
 * Research Home — the primary human landing page.
 *
 * Reading order is the product test: what are we trying to understand (focus) → what we believe and
 * what blocks us → what needs my judgment → where the plan stands → what we learned / what failed →
 * which results to look at. CRUD actions live in the header overflow menu, not on the page.
 */
import { ago, esc, truncate } from '../util';
import { blkHd, btn, confidence, ic, idLink, lineChart, md, overflow, ring, time, timeline } from './ui';

export interface HomeCtx {
  uri(path: string): string;
}

/** status → [codicon, css colour, human label] */
export const TASK_STATE: Record<string, [string, string, string]> = {
  done: ['check', 'var(--green)', 'done'],
  running: ['play', 'var(--accent)', 'in progress'],
  verify: ['eye', 'var(--blue)', 'awaiting verification'],
  blocked: ['circle-slash', 'var(--red)', 'blocked'],
  todo: ['circle-small-filled', 'var(--line-strong)', 'upcoming'],
};

/** Plan progress ring + legend, shared by Home, the sidebar and the plan page. */
export function planRing(tasks: any[], size = 44): { svg: string; legend: string } {
  const n = (s: string) => tasks.filter((t) => t.status === s).length;
  const counts: [string, number][] = [['done', n('done')], ['verify', n('verify')], ['running', n('running')], ['blocked', n('blocked')], ['todo', n('todo')]];
  const total = tasks.length;
  const svg = ring(counts.filter(([s]) => s !== 'todo').map(([s, v]) => [v, TASK_STATE[s][1]]), total, `${n('done')}/${total}`, size,
    `${n('done')} of ${total} tasks done`);
  const legend = `<div class="legend">${counts.filter(([, v]) => v).map(([s, v]) => `<span><i style="--c:${TASK_STATE[s][1]}"></i>${v} ${TASK_STATE[s][2]}</span>`).join('')}</div>`;
  return { svg, legend };
}

export function stepRow(t: any, o: { sub?: string; warn?: boolean; ready?: boolean; cls?: string } = {}): string {
  const [icon, color, label] = TASK_STATE[t.status] || TASK_STATE.todo;
  const cls = o.cls || (t.status === 'done' ? 'done' : t.status === 'todo' ? 'later' : 'now');
  return `<li class="step ${cls}" style="--c:${color}" data-open="${esc(t.id)}" tabindex="0" role="link" aria-label="${esc(t.title)} — ${esc(label)}">
    <span class="glyph" aria-hidden="true">${ic(icon)}</span>
    <span class="st ellipsis" title="${esc(t.title)}">${esc(t.title)}</span>
    <span class="tr">${o.ready ? `<span class="ready-tag">${ic('arrow-right')}next</span>` : ''}${idLink(t.id)}</span>
    ${o.sub ? `<span class="ss${o.warn ? ' warn' : ''}">${o.sub}</span>` : ''}
  </li>`;
}

function roleLabel(t: any): string {
  const who = t.status === 'running' && t.claimed_by ? String(t.claimed_by).split('/').pop() : t.assigned_role;
  return who ? esc(who) : '';
}

// ---------------------------------------------------------------- blocks

function header(r: any): string {
  const p = r.project || {};
  const g = r.git || {};
  const la = r.last_activity;
  const cp = r.latest_checkpoint;
  const plan = r.active_plan;
  const more = overflow([
    { header: 'Record' },
    ['New question', 'research.question.create', [], 'question'],
    ['New experiment', 'research.experiment.create', [], 'beaker'],
    ['New finding', 'research.finding.create', [], 'lightbulb'],
    ['New decision', 'research.decision.create', [], 'law'],
    ['New note', 'research.note.create', [], 'note'],
    '-',
    { header: 'Plan' },
    ...(plan ? [['Add task to plan', 'research.task.create', [plan.id], 'add'] as [string, string, any[], string]] : [['New plan', 'research.plan.create', [], 'list-ordered'] as [string, string, any[], string]]),
    ['Plan objective with agent…', 'research.plan.withAgent', [], 'hubot'],
    '-',
    ['Export report…', 'research.report', [], 'output'],
    ['Set project goal', 'research.project.setGoal', [], 'target'],
    ['Open agent context (current.md)', 'research.openFile', ['.research/context/current.md'], 'symbol-structure'],
  ], 'More actions');
  return `<header class="home-hd">
    <div class="who">
      <h1 class="proj"><span class="ichip" style="--c:var(--accent)">${ic('beaker')}</span>${esc(p.name || 'Research')}</h1>
      ${p.goal ? `<p class="goal clamp2" title="${esc(p.goal)}">${esc(p.goal)}</p>` : `<p class="goal"><a data-cmd="research.project.setGoal" data-args="[]">Set a project goal</a></p>`}
    </div>
    <div class="acts">
      <button class="icon" data-cmd="research.search" data-args="[]" title="Search research" aria-label="Search research">${ic('search')}</button>
      ${btn('Checkpoint', 'research.checkpoint.create', [], { icon: 'bookmark', title: 'Freeze current understanding so you (or an agent) can resume quickly' })}
      ${more}
    </div>
  </header>
  <div class="meta">
    ${la ? `<span>${ic('history')}Updated <b>${esc(ago(la.ts))}</b>${la.author_type === 'agent' ? ` by ${esc(String(la.author_name || 'agent').split('/').pop())}` : ''}</span>` : ''}
    ${cp ? `<span>${ic('bookmark')}Checkpoint <a data-open="${esc(cp.id)}">${esc(truncate(cp.title || cp.id, 40))}</a> · ${esc(ago(cp.created_at))}</span>` : ''}
    ${g.branch ? `<span class="mono">${ic('git-branch')}${esc(g.branch)}${g.dirty ? ' · uncommitted' : ''}</span>` : ''}
  </div>`;
}

function sinceStrip(r: any): string {
  const cp = r.latest_checkpoint;
  const s = r.since_checkpoint || {};
  if (!cp) {
    return `<div class="since"><span class="lbl">${ic('bookmark')}No checkpoint yet</span><span>A checkpoint freezes what you understand now, so the next visit starts here.</span><span class="spacer"></span>${btn('Create first checkpoint', 'research.checkpoint.create', [], { cls: 'sm' })}</div>`;
  }
  const chg = (n: number, label: string, icon: string, color: string, ids: string[] = []) =>
    n ? `<span class="chg" style="--c:${color}" ${ids.length ? `title="${esc(ids.join(', '))}"` : ''}>${ic(icon)}<b>${n}</b> ${esc(label)}</span>` : '';
  const items = [
    chg((s.findings || []).length, (s.findings || []).length === 1 ? 'new finding' : 'new findings', 'lightbulb', 'var(--yellow)', s.findings),
    chg((s.tasks_done || []).length, (s.tasks_done || []).length === 1 ? 'task done' : 'tasks done', 'check', 'var(--green)', s.tasks_done),
    chg(s.runs || 0, s.runs === 1 ? 'run' : 'runs', 'play', 'var(--accent)'),
    chg((s.experiments || []).length, (s.experiments || []).length === 1 ? 'new experiment' : 'new experiments', 'beaker', 'var(--muted)', s.experiments),
    chg((s.decisions || []).length, (s.decisions || []).length === 1 ? 'decision' : 'decisions', 'law', 'var(--purple)', s.decisions),
  ].filter(Boolean);
  return `<div class="since"><span class="lbl">${ic('diff')}Since checkpoint</span>${items.length ? items.join('') : '<span>Nothing has changed.</span>'}</div>`;
}

function focusBlock(r: any): string {
  const f = r.current_focus;
  const cp = r.latest_checkpoint;
  const next = r.next_task;
  if (!f) {
    return `<section class="blk panel focus o1">${blkHd('target', 'Current focus')}
      <p class="muted" style="margin:6px 0 10px">No active plan or checkpoint yet. Start with the question you are trying to answer — agents can then propose a plan.</p>
      <div class="btnrow">${btn('New question', 'research.question.create', [], { icon: 'question', primary: true })}${btn('Plan with agent…', 'research.plan.withAgent', [], { icon: 'hubot' })}</div></section>`;
  }
  const blockedTask = (r.blocked_tasks || [])[0];
  const understanding = f.understanding || cp?.understanding;
  const rows: string[] = [];
  if (understanding) rows.push(`<div class="k"><span class="ichip" style="--c:var(--blue)">${ic('book')}</span>We believe</div><div class="v">${md(truncate(understanding, 520))}</div>`);
  if (f.blocker) {
    rows.push(`<div class="k"><span class="ichip" style="--c:var(--red)">${ic('circle-slash')}</span>Blocked</div><div class="v blocked">${esc(truncate(f.blocker, 220))}${blockedTask ? ` <a data-open="${esc(blockedTask.id)}">${esc(truncate(blockedTask.title, 48))}</a>` : ''}</div>`);
  }
  if (f.next_step) {
    const act = next && next.status === 'todo' ? btn('Dispatch', 'research.dispatch', [next.id], { icon: 'hubot', cls: 'sm', title: 'Hand this task to an agent' }) : '';
    rows.push(`<div class="k"><span class="ichip" style="--c:var(--green)">${ic('arrow-right')}</span>Next</div><div class="v"><span class="next">${next ? `<a data-open="${esc(next.id)}">${esc(f.next_step)}</a>` : esc(truncate(f.next_step, 220))}${act}</span></div>`);
  }
  if (!f.blocker) rows.push(`<div class="k"><span class="ichip" style="--c:var(--green)">${ic('pass')}</span>Blockers</div><div class="v muted">None recorded</div>`);
  return `<section class="blk panel focus o1" aria-label="Current focus">
    ${blkHd('target', 'Current focus', 'var(--accent)', f.source_id ? `<a class="aside" data-open="${esc(f.source_id)}">from ${f.source === 'plan' ? 'active plan' : 'checkpoint'}</a>` : '')}
    <div class="q">${esc(f.question || 'No current question')}</div>
    <div class="facts">${rows.join('')}</div>
  </section>`;
}

function planBlock(r: any): string {
  const plan = r.active_plan;
  if (!plan) {
    return `<section class="blk o3">${blkHd('list-ordered', 'Active plan')}
      <div class="empty-card">${ic('list-ordered')}<span class="grow">No active plan. A plan breaks the current objective into tasks that agents can claim and verify.</span>${btn('New plan', 'research.plan.create', [], { cls: 'sm' })}</div></section>`;
  }
  const tasks: any[] = r.tasks || [];
  const next = r.next_task;
  const { svg, legend } = planRing(tasks);
  const done = tasks.filter((t) => t.status === 'done');
  const now = tasks.filter((t) => ['running', 'verify', 'blocked'].includes(t.status));
  const todo = tasks.filter((t) => t.status === 'todo');
  todo.sort((a, b) => (next && a.id === next.id ? -1 : next && b.id === next.id ? 1 : 0));
  const upcoming = todo.slice(0, 3);
  let steps = '';
  if (done.length) {
    steps += `<details class="done-group" data-key="home-done"><summary class="step-sum">${ic('chevron-right')}${ic('check')}<span>${done.length} completed</span><span class="ellipsis">· ${esc(done.map((t) => t.title).join(' · '))}</span></summary>
      <ol class="steps">${done.map((t) => stepRow(t, { sub: t.result ? esc(truncate(t.result, 110)) : '' })).join('')}</ol></details>`;
  }
  steps += `<ol class="steps">`;
  for (const t of now) {
    const role = roleLabel(t);
    const sub = t.status === 'blocked' ? `${esc(truncate(t.blockers || 'Blocked', 130))}`
      : t.status === 'verify' ? `Done — awaiting verification${role ? ' · ' + role : ''}`
        : `In progress${role ? ' · ' + role : ''}${t.started_at ? ' · started ' + esc(ago(t.started_at)) : ''}`;
    steps += stepRow(t, { sub, warn: t.status === 'blocked' });
  }
  for (const t of upcoming) steps += stepRow(t, { ready: !!next && t.id === next.id, sub: next && t.id === next.id && t.assigned_role ? `Ready · ${esc(t.assigned_role)}` : '' });
  steps += `</ol>`;
  if (todo.length > upcoming.length) steps += `<div class="more-link">+ ${todo.length - upcoming.length} more later — <a data-open="${esc(plan.id)}">see full plan</a></div>`;
  return `<section class="blk o3" aria-label="Active plan">
    ${blkHd('list-ordered', 'Active plan', 'var(--accent)', `<button class="link sm" data-open="${esc(plan.id)}">Open plan ${ic('chevron-right')}</button>`)}
    <div class="panel">
      <div class="plan-top" data-open="${esc(plan.id)}" tabindex="0" role="link">${svg}<div class="grow"><div class="pt-title">${esc(plan.title)}</div>${legend}</div></div>
      ${steps}
    </div>
  </section>`;
}

function insightsBlock(r: any): string {
  const fb = r.findings_by_status || {};
  const disputed = new Set((r.attention_items || []).filter((a: any) => a.kind === 'reviewer_disagrees').map((a: any) => a.id));
  const pending = new Set((r.attention_items || []).filter((a: any) => a.kind === 'awaiting_review').map((a: any) => a.id));
  const item = (f: any, kind: 'supported' | 'preliminary') => {
    const color = kind === 'supported' ? 'var(--green)' : 'var(--yellow)';
    const flag = disputed.has(f.id) ? `<span class="flag">${ic('comment-discussion')}reviewer disagrees</span>` : pending.has(f.id) ? `<span>${ic('eye')} awaiting review</span>` : '';
    return `<div class="ins" style="--c:${color}" data-open="${esc(f.id)}" tabindex="0" role="link">
      ${ic(kind === 'supported' ? 'verified-filled' : 'lightbulb')}
      <div><div class="t">${esc(f.title)}</div>
        ${f.statement && f.statement !== f.title ? `<div class="s clamp2">${esc(f.statement)}</div>` : ''}
        <div class="m"><span>${kind}</span>${f.confidence ? confidence(f.confidence, color) : ''}${f.evidence_count ? `<span>${f.evidence_count} evidence</span>` : `<span class="flag">no evidence linked</span>`}${flag}${idLink(f.id)}</div></div>
    </div>`;
  };
  const supported = fb.supported || [];
  const prelim = fb.preliminary || [];
  const failed = [...(fb.failed || []), ...(fb.contradicted || [])];
  if (!supported.length && !prelim.length && !failed.length) {
    return `<section class="blk o4">${blkHd('lightbulb', 'What we have learned', 'var(--yellow)')}
      <div class="empty-card">${ic('lightbulb')}<span class="grow">No findings yet. Findings are reusable conclusions backed by experiments; agents record preliminary ones after analysis, and they stay preliminary until independently reviewed.</span></div></section>`;
  }
  return `<section class="blk o4" aria-label="What we have learned">
    ${blkHd('lightbulb', 'What we have learned', 'var(--yellow)', `<button class="link sm" data-cmd="research.focus" data-args='["findings"]'>All findings ${ic('chevron-right')}</button>`)}
    <div class="panel flush">
      ${supported.slice(0, 3).map((f: any) => item(f, 'supported')).join('')}
      ${prelim.slice(0, 3).map((f: any) => item(f, 'preliminary')).join('')}
      ${failed.length ? `<div class="subhd" style="--c:var(--red)">${ic('circle-slash')}Failed directions — don’t repeat</div>
        ${failed.slice(0, 3).map((f: any) => `<div class="ins failed" style="--c:var(--red)" data-open="${esc(f.id)}" tabindex="0" role="link">${ic('close')}<div><div class="t">${esc(f.title)}</div>${f.statement && f.statement !== f.title ? `<div class="s clamp1">${esc(f.statement)}</div>` : ''}</div></div>`).join('')}` : ''}
    </div>
  </section>`;
}

const ATT: Record<string, [string, string, string, boolean]> = {
  // kind: [icon, colour, why-label, needs human judgment]
  task_blocked: ['circle-slash', 'var(--red)', 'Blocked task', true],
  reviewer_disagrees: ['comment-discussion', 'var(--orange)', 'Reviewer disagrees', true],
  verification_failed: ['error', 'var(--red)', 'Checks failing', true],
  synthesis_required: ['sparkle', 'var(--orange)', 'Run budget reached — agents paused', true],
  stale_task: ['watch', 'var(--orange)', 'Stalled', true],
  verification_needed: ['eye', 'var(--blue)', 'awaiting verification', false],
  awaiting_review: ['eye', 'var(--blue)', 'awaiting review', false],
  failed_run: ['close', 'var(--red)', 'failed run', false],
};

function attentionAction(a: any): string {
  if (a.kind === 'synthesis_required') return btn('Synthesize', 'research.experiment.synthesize', [a.id], { icon: 'sparkle', cls: 'sm' });
  if (a.kind === 'reviewer_disagrees') return btn('Review…', 'research.finding.review', [a.id], { icon: 'comment-discussion', cls: 'sm' });
  if (a.kind === 'verification_failed') return btn('Re-run checks', 'research.task.verify', [a.id], { icon: 'checklist', cls: 'sm' });
  if (a.kind === 'stale_task') return btn('Release', 'research.task.release', [a.id], { cls: 'sm' });
  return '';
}

function attentionBlock(r: any): string {
  const items: any[] = r.attention_items || [];
  const major = items.filter((a) => (ATT[a.kind] || ['', '', '', true])[3]);
  const routine = items.filter((a) => !(ATT[a.kind] || ['', '', '', true])[3]);
  const body = major.length ? major.map((a) => {
    const [icon, color, why] = ATT[a.kind] || ['warning', 'var(--orange)', a.kind.replace(/_/g, ' ')];
    const msg = a.kind === 'synthesis_required' ? `${a.message}. Interpret the results before more runs.` : a.message;
    return `<div class="att" style="--c:${color}" data-open="${esc(a.id)}" tabindex="0" role="link">
      <span class="ichip" style="--c:${color}">${ic(icon)}</span>
      <div><div class="why">${esc(why)}</div><div class="t">${esc(a.title)}</div>${msg && msg !== a.title ? `<div class="s clamp2">${esc(msg)}</div>` : ''}
      ${attentionAction(a) ? `<div class="a">${attentionAction(a)}</div>` : ''}</div>
    </div>`;
  }).join('') : `<div class="calm">${ic('pass-filled')}<span>Nothing needs your judgment. Agents can continue with the plan.</span></div>`;
  const kinds = Array.from(new Set(routine.map((a) => (ATT[a.kind] || ['', '', a.kind])[2])));
  const rt = routine.length ? `<details class="routine" data-key="home-routine"><summary>${ic('chevron-right')}${routine.length} routine item${routine.length > 1 ? 's' : ''} · ${esc(kinds.join(', '))}</summary>
    ${routine.map((a) => { const [icon, color, why] = ATT[a.kind] || ['circle-outline', 'var(--muted)', a.kind]; return `<div class="r" style="--c:${color}" data-open="${esc(a.id)}" tabindex="0" role="link">${ic(icon)}<span class="ellipsis" title="${esc(a.title)}">${esc(a.title)}</span><span class="muted small">${esc(why)}</span></div>`; }).join('')}</details>` : '';
  return `<section class="blk o2" aria-label="Needs your judgment">
    ${blkHd('bell', 'Needs your judgment', major.length ? 'var(--orange)' : 'var(--green)', '', major.length || undefined)}
    <div class="panel flush">${body}${rt}</div>
  </section>`;
}

function inFlight(r: any): string {
  const runs: any[] = r.active_runs || [];
  if (!runs.length) return '';
  return `<div class="subhd" style="margin:0 10px 2px">${ic('pulse')}Running now</div><div>${runs.slice(0, 3).map((x) => `<div class="flight" data-open="${esc(x.id)}" tabindex="0" role="link"><span class="dot pulse" style="--c:var(--accent)"></span><span class="grow ellipsis">${esc(x.label || x.id)} <span class="muted">· ${esc(x.experiment_id)}${x.slurm_job_id ? ' · job ' + esc(x.slurm_job_id) : ''}</span></span>${idLink(x.id)}</div>`).join('')}</div>`;
}

const LOWER_BETTER = /(err|loss|mae|mse|rmse|frac|time|runtime|latency|nll|ppl|dist|bias)/i;

function resultsBlock(r: any, ctx: HomeCtx): string {
  const charts: any[] = r.result_charts || [];
  const outs: any[] = (r.key_outputs || []).filter((o: any) => o.exists !== false);
  if (!charts.length && !outs.length) return '';
  const cards = charts.map((c) => {
    const pair = c.metrics.map((m: string, k: number) => {
      const higher = !LOWER_BETTER.test(m);
      const svg = lineChart(c.runs.map((x: any) => ({ id: x.id, label: x.label, value: x.values[k] })), { higherIsBetter: higher, name: m });
      return svg ? `<div><div class="mname"><span class="mono">${esc(m)}</span><span class="dir">${higher ? 'higher is better' : 'lower is better'}</span></div>${svg}</div>` : '';
    }).join('');
    return `<div class="chart-card"><div class="ct"><a data-open="${esc(c.experiment_id)}">${esc(c.title)}</a></div><div class="cs">${c.runs.length} runs · best highlighted</div><div class="chart-pair">${pair}</div></div>`;
  }).join('');
  const gallery = outs.length ? `<div class="outs">${outs.map((o) => {
    const img = o.type === 'image' && o.abspath ? ctx.uri(o.abspath) : '';
    return `<div class="out" data-open="${esc(o.id)}" tabindex="0" role="link" title="${esc(o.path)}">
      <div class="pv${img ? '' : ' noimg'}">${img ? `<img src="${img}" loading="lazy" width="320" height="200" alt="${esc(o.name)}">` : ic(o.type === 'table' ? 'table' : 'file')}</div>
      <div class="cap"><div class="n ellipsis">${esc(o.name)}</div><div class="r ellipsis">${esc(o.reason)}</div></div></div>`;
  }).join('')}</div>` : '';
  return `<section class="blk o5" aria-label="Key results">
    ${blkHd('graph-line', 'Key results', 'var(--purple)')}
    <div class="grid2">${cards}</div>${gallery}
  </section>`;
}

function activityBlock(r: any): string {
  return `<section class="blk o6" aria-label="Recent activity">
    ${blkHd('pulse', 'Recent activity', 'var(--muted)')}
    <div class="panel">${timeline(r.recent_events, 7)}</div>
  </section>`;
}

export function renderHome(r: any, ctx: HomeCtx): string {
  return `<div class="home fade-in">
    ${header(r)}
    ${sinceStrip(r)}
    <div class="cols">
      <div class="col-main">${focusBlock(r)}${planBlock(r)}${insightsBlock(r)}${resultsBlock(r, ctx)}</div>
      <div class="col-side">${attentionBlock(r)}<div class="o2">${inFlight(r)}</div>${activityBlock(r)}</div>
    </div>
  </div>`;
}

/** Compact sidebar overview: the focus, the plan at a glance, and whether anything needs you. */
export function renderOverviewHome(r: any, error?: string): string {
  if (error) return `<div class="page"><div class="banner" style="--c:var(--red)">${ic('error')}<div class="grow">${esc(error)}</div></div><div style="margin-top:8px">${btn('Show log', 'research.showOutput', [], { ghost: true })}</div></div>`;
  if (!r) return `<div class="page"><div class="row muted"><span class="spinner"></span> Loading…</div></div>`;
  const f = r.current_focus;
  const plan = r.active_plan;
  const major = (r.attention_items || []).filter((a: any) => (ATT[a.kind] || ['', '', '', true])[3]);
  let h = `<div class="page fade-in">`;
  h += `<div class="ov-q clamp3">${f?.question ? esc(truncate(f.question, 180)) : r.project?.goal ? esc(truncate(r.project.goal, 180)) : `<a data-cmd="research.project.setGoal" data-args="[]">${ic('target')} Set a project goal</a>`}</div>`;
  if (f?.blocker) h += `<div class="ov-line" style="--c:var(--red)">${ic('circle-slash')}<span>${esc(truncate(f.blocker, 120))}</span></div>`;
  if (f?.next_step) h += `<div class="ov-line" style="--c:var(--green)">${ic('arrow-right')}<span>${r.next_task ? `<a data-open="${esc(r.next_task.id)}">${esc(truncate(f.next_step, 100))}</a>` : esc(truncate(f.next_step, 120))}</span></div>`;
  if (plan) {
    const { svg, legend } = planRing(r.tasks || [], 36);
    h += `<div class="ov-plan" data-open="${esc(plan.id)}" tabindex="0" role="link">${svg}<div class="grow"><div class="small" style="font-weight:600">${esc(truncate(plan.title, 70))}</div>${legend}</div></div>`;
  }
  h += major.length
    ? `<div class="ov-att" data-cmd="research.resume" data-args="[]" tabindex="0" role="button">${ic('bell')}<span class="grow"><b>${major.length}</b> item${major.length > 1 ? 's' : ''} need${major.length > 1 ? '' : 's'} your judgment</span>${ic('chevron-right')}</div>`
    : `<div class="ov-att ok">${ic('pass-filled')}<span>Nothing needs your judgment</span></div>`;
  h += `<div style="margin-top:12px">${btn('Open Research Home', 'research.resume', [], { icon: 'home', primary: true, cls: 'wide' })}</div>`;
  const recent = (r.findings_by_status?.supported || []).concat(r.findings_by_status?.preliminary || []).slice(0, 3);
  if (recent.length) {
    h += `<section><h2>Latest findings</h2><div class="list">${recent.map((x: any) => `<div class="item" data-open="${esc(x.id)}" tabindex="0" role="link"><i class="codicon codicon-${r.findings_by_status.supported.includes(x) ? 'verified-filled' : 'lightbulb'} lead" style="color:var(--${r.findings_by_status.supported.includes(x) ? 'green' : 'yellow'})"></i><div class="grow small clamp2">${esc(x.title)}</div></div>`).join('')}</div></section>`;
  }
  return h + `</div>`;
}

