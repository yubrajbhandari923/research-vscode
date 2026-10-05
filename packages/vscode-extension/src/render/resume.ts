/** The Resume view — optimized for recovering project context in under two minutes. */
import { ago, esc, paramsSummary, truncate } from '../util';
import { author, btn, confidence, empty, entityRow, fileLink, h2, ic, idLink, md, pill, statLink, time, timeline } from './ui';

function findingItem(f: any, failure = false): string {
  const ev = (f.evidence || []).slice(0, 4).map((id: string) => idLink(id)).join(' ');
  const badges = failure ? (f.author_type === 'agent' ? author(f) : '')
    : [pill(f.status), confidence(f.confidence, f.status === 'supported' ? 'var(--green)' : 'var(--yellow)'), f.author_type === 'agent' ? author(f) : ''].join('');
  const sub = [badges ? `<div class="row wrap" style="gap:6px;margin:3px 0 2px">${badges}</div>` : '',
    f.statement && f.statement !== f.title ? `<div>${esc(truncate(f.statement, 180))}</div>` : '',
    ev ? `<div class="row wrap" style="gap:4px;margin-top:3px">${ic('link', 'muted')}${ev}</div>` : ''].join('');
  const lead = failure ? `<i class="codicon codicon-close lead" style="color:var(--red)"></i>` : f.status === 'supported'
    ? `<i class="codicon codicon-verified-filled lead" style="color:var(--green)"></i>` : `<i class="codicon codicon-lightbulb lead" style="color:var(--yellow)"></i>`;
  return `<div class="item" data-open="${esc(f.id)}" tabindex="0">${lead}
    <div class="grow"><div class="hl">${idLink(f.id)}<span class="title">${esc(f.title)}</span></div>${sub ? `<div class="sub">${sub}</div>` : ''}</div></div>`;
}

function expItem(e: any): string {
  const s = e.state || {};
  const bits = [`${s.runs || 0} run${s.runs === 1 ? '' : 's'}`];
  if (s.active) bits.push(`<span style="color:var(--yellow)">${s.active} active</span>`);
  if (s.failed) bits.push(`<span style="color:var(--red)">${s.failed} failed</span>`);
  if (e.parameters && Object.keys(e.parameters).length) bits.push(`<span class="mono">${esc(paramsSummary(e.parameters, 3))}</span>`);
  const warn = s.unsynthesized ? `<span class="pill c-orange">${ic('warning')}${s.unsynthesized} to synthesize</span>` : '';
  return entityRow({ id: e.id, type: 'experiment', title: e.title, status: e.status }, bits.join(' · ') + (e.question_id ? ` · ${idLink(e.question_id)}` : ''), warn + (e.is_baseline ? `<span class="pill c-green">${ic('star-full')}baseline</span>` : ''));
}

export function renderResume(r: any): string {
  const p = r.project || {};
  const g = r.git || {};
  const cp = r.latest_checkpoint;
  const since = r.since_checkpoint || {};
  const la = r.last_activity;
  const age = r.last_activity_age_days;
  const awayLong = age !== null && age !== undefined && age >= 7;

  // -------------------------------------------------------------------- hero
  let html = `<div class="page fade-in"><div class="hero">
    <div class="eyebrow">${ic('beaker')} ${esc(p.name)}</div>
    <div class="goal">${p.goal ? esc(p.goal) : `<span class="muted">No project goal set.</span> <a data-cmd="research.project.setGoal" data-args="[]">Set goal</a>`}</div>
    <div class="meta">
      ${la ? `<span>${ic('history')}Last activity <b>${esc(ago(la.ts))}</b>${la.author_type === 'agent' ? ` by ${esc(la.author_name || 'an agent')}` : ''} — ${esc(truncate(la.summary || '', 90))}</span>` : ''}
      ${g.branch ? `<span>${ic('git-branch')}<span class="mono">${esc(g.branch)}</span> @ <span class="mono">${esc((g.commit || '').slice(0, 8))}</span>${g.dirty ? ' <span class="pill c-orange">uncommitted</span>' : ''}</span>` : ''}
    </div>
    <div class="toolbar">
      ${btn('Create Checkpoint', 'research.checkpoint.create', [], { icon: 'bookmark', primary: true })}
      ${btn('New Question', 'research.question.create', [], { icon: 'question' })}
      ${btn('New Experiment', 'research.experiment.create', [], { icon: 'beaker' })}
      ${btn('New Finding', 'research.finding.create', [], { icon: 'lightbulb' })}
      ${btn('Open current.md', 'research.openFile', ['.research/context/current.md'], { icon: 'symbol-structure', ghost: true, title: 'Agent-readable summary' })}
    </div>
  </div>`;

  // -------------------------------------------------------------------- banners
  if (!cp) {
    html += `<div class="banner" style="--c:var(--blue)">${ic('bookmark')}<div class="grow"><b>No checkpoint yet.</b> A checkpoint freezes what you currently understand so that future-you (or an agent) can resume quickly.</div>${btn('Create first checkpoint', 'research.checkpoint.create', [], { primary: true })}</div>`;
  } else if (cp.stale || (since.events || 0) > 25) {
    html += `<div class="banner">${ic('clock')}<div class="grow"><b>Checkpoint is ${esc(ago(cp.created_at))} old</b> and ${since.events || 0} changes have happened since. Consider writing a fresh one.</div>${btn('Update checkpoint', 'research.checkpoint.create', [], { primary: true })}</div>`;
  }
  const needs = r.needs_synthesis || [];
  if (needs.length) {
    const n = needs.reduce((a: number, e: any) => a + (e.state?.unsynthesized || 0), 0);
    html += `<div class="banner">${ic('warning')}<div class="grow"><b>${needs.length} experiment${needs.length > 1 ? 's' : ''} need${needs.length > 1 ? '' : 's'} synthesis</b> — ${n} run${n === 1 ? '' : 's'} not yet interpreted: ${needs.map((e: any) => idLink(e.id)).join(' ')}</div>${btn('Synthesize ' + needs[0].id, 'research.experiment.synthesize', [needs[0].id], { primary: true, icon: 'sparkle' })}</div>`;
  }
  if ((r.active_runs || []).length) {
    html += `<div class="banner" style="--c:var(--yellow)"><span class="dot pulse" style="--c:var(--yellow)"></span><div class="grow"><b>${r.active_runs.length} run${r.active_runs.length > 1 ? 's' : ''} in flight:</b> ${r.active_runs
      .map((x: any) => `${idLink(x.id)} <span class="muted small">(${esc(x.experiment_id)}${x.slurm_job_id ? ', job ' + esc(x.slurm_job_id) : ''})</span>`).join(' ')}</div></div>`;
  }

  // -------------------------------------------------------------------- where we left off
  html += `<section>${h2('Where we left off', undefined, cp ? `<span class="muted small" style="text-transform:none;letter-spacing:0;font-weight:400">${idLink(cp.id)} · ${time(cp.created_at)} · ${author(cp)}</span>` : '')}`;
  if (cp) {
    const sinceBits: string[] = [];
    if (since.runs) sinceBits.push(`${since.runs} run${since.runs > 1 ? 's' : ''}`);
    if ((since.experiments || []).length) sinceBits.push(`${since.experiments.length} new experiment${since.experiments.length > 1 ? 's' : ''} ${since.experiments.map(idLink).join(' ')}`);
    if ((since.findings || []).length) sinceBits.push(`${since.findings.length} new finding${since.findings.length > 1 ? 's' : ''} ${since.findings.map(idLink).join(' ')}`);
    if ((since.decisions || []).length) sinceBits.push(`${since.decisions.map(idLink).join(' ')}`);
    html += `<div class="grid2">
      <div class="card tone" style="--c:var(--blue)"><h3>${ic('book')}Current understanding</h3><div class="body">${md(cp.understanding) || '<div class="empty">—</div>'}</div>
        ${cp.baseline ? `<div class="field" style="margin-top:12px"><div class="field-label">Baseline</div>${md(cp.baseline)}</div>` : ''}</div>
      <div class="stack">
        <div class="card tint" style="--c:var(--red)"><h3>${ic('flame')}Current problem</h3><div class="body">${md(cp.current_problem) || '<div class="empty">Nothing recorded as broken.</div>'}</div></div>
        <div class="card tint" style="--c:var(--green)"><h3>${ic('arrow-right')}Next step</h3><div class="body">${md(cp.next_experiment) || '<div class="empty">Not specified.</div>'}</div></div>
      </div>
    </div>
    ${sinceBits.length ? `<div class="meta" style="margin-top:10px">${ic('diff-added')}<span>Since this checkpoint: ${sinceBits.join(' · ')}</span></div>` : `<div class="meta" style="margin-top:10px">${ic('check')}<span>Nothing has changed since this checkpoint.</span></div>`}`;
  } else html += empty('No checkpoint yet.');
  html += `</section>`;

  // -------------------------------------------------------------------- beliefs vs failures
  const est = r.established_findings || [];
  const fail = r.failed_directions || [];
  html += `<section class="grid2">
    <div>${h2('What we believe', est.length, btn('New', 'research.finding.create', [], { icon: 'add', ghost: true }))}
      <div class="card flush"><div class="list" style="padding:4px">${est.length ? est.slice(0, 8).map((f: any) => findingItem(f)).join('') : '<div class="empty" style="padding:10px 12px">No findings yet. Findings are the reusable conclusions of experiments.</div>'}</div>
      ${est.length > 8 ? `<div class="more muted" style="padding:0 0 10px">+ ${est.length - 8} more in the Findings view</div>` : ''}</div></div>
    <div>${h2('What failed — don’t repeat', fail.length)}
      <div class="card flush"><div class="list" style="padding:4px">${fail.length ? fail.slice(0, 8).map((f: any) => findingItem(f, true)).join('') : '<div class="empty" style="padding:10px 12px">No failed directions recorded. Record them as findings with kind “failure”.</div>'}</div></div></div>
  </section>`;

  // -------------------------------------------------------------------- baseline + questions
  const b = r.baseline;
  const qs = [...(r.active_questions || []), ...(r.open_questions || []), ...(r.blocked_questions || [])];
  html += `<section class="grid2">
    <div>${h2('Current baseline')}
      ${b ? `<div class="card tone" style="--c:var(--green)">${entityRow({ ...b, type: 'experiment' }, b.parameters && Object.keys(b.parameters).length ? `<span class="mono">${esc(paramsSummary(b.parameters, 6))}</span>` : '')}</div>`
        : empty('No baseline experiment set.', '')}
      <div style="margin-top:18px">${h2('Decisions in force', (r.decisions || []).length)}
      ${(r.decisions || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${r.decisions.slice(0, 6).map((d: any) =>
        entityRow({ id: d.id, type: 'decision', title: d.statement }, [d.date, ...(d.findings || []).map(idLink)].filter(Boolean).join(' · '), d.author_type === 'agent' ? author(d) : '')).join('')}</div></div>` : empty('No decisions recorded.')}</div>
    </div>
    <div>${h2('Open questions', qs.length, btn('New', 'research.question.create', [], { icon: 'add', ghost: true }))}
      ${qs.length ? `<div class="card flush"><div class="list" style="padding:4px">${qs.map((q: any) => entityRow({ id: q.id, type: 'question', title: q.title, status: q.status }, q.experiments ? `${q.experiments} experiment${q.experiments > 1 ? 's' : ''}` : 'no experiments yet')).join('')}</div></div>` : empty('No open questions.')}
    </div>
  </section>`;

  // -------------------------------------------------------------------- experiments + blockers
  const cur = r.current_experiments || [];
  const blockers = (r.blockers || []).filter((x: any) => x.kind !== 'checkpoint');
  html += `<section class="grid2">
    <div>${h2('Experiments in progress', cur.length, btn('New', 'research.experiment.create', [], { icon: 'add', ghost: true }))}
      ${cur.length ? `<div class="card flush"><div class="list" style="padding:4px">${cur.map(expItem).join('')}</div></div>` : empty('No experiments in progress.')}</div>
    <div>${h2('Needs attention', blockers.length)}
      ${blockers.length ? `<div class="card flush"><div class="list" style="padding:4px">${blockers.slice(0, 8).map((x: any) =>
        `<div class="item" ${x.id ? `data-open="${esc(x.id)}"` : ''} tabindex="0"><i class="codicon codicon-${x.kind === 'question' ? 'error' : x.kind === 'budget' ? 'warning' : 'close'} lead" style="color:var(--${x.kind === 'budget' ? 'orange' : 'red'})"></i><div class="grow"><div class="title">${esc(x.text)}</div><div class="sub">${esc(x.kind)} ${idLink(x.id)}</div></div></div>`).join('')}</div></div>` : `<div class="empty-card">${ic('pass', '')}<span>Nothing blocked.</span></div>`}</div>
  </section>`;

  // -------------------------------------------------------------------- files + activity
  const files = r.suggested_files || [];
  html += `<section class="grid2">
    <div>${h2('Look at these first', files.length)}
      ${files.length ? `<div class="card flush"><div class="list" style="padding:4px">${files.map((f: any) =>
        `<div class="item" data-file="${esc(f.path)}" tabindex="0"><i class="codicon codicon-${f.kind === 'artifact' ? 'file-media' : f.kind === 'note' ? 'pinned' : f.kind === 'checkpoint' ? 'bookmark' : f.kind === 'experiment' ? 'beaker' : 'file'} lead"></i>
          <div class="grow"><div class="title mono ellipsis" style="font-size:12px">${esc(f.path)}</div><div class="sub">${esc(f.reason)}${f.ref ? ' · ' + idLink(f.ref) : ''}${f.exists ? '' : ' · <span style="color:var(--red)">missing</span>'}</div></div></div>`).join('')}</div></div>` : empty('Nothing suggested yet.')}</div>
    <div>${h2('Recent activity')}
      <div class="card">${timeline(r.recent_events, 10)}</div></div>
  </section>`;

  const c = r.counts || {};
  html += `<section><div class="stats">
    ${statLink(c.questions ?? 0, 'questions', 'research.focus', ['questions'])}
    ${statLink(c.experiments ?? 0, 'experiments', 'research.focus', ['experiments'])}
    ${statLink(c.runs ?? 0, 'runs', 'research.focus', ['experiments'])}
    ${statLink(c.findings ?? 0, 'findings', 'research.focus', ['findings'])}
    ${statLink(c.decisions ?? 0, 'decisions', 'research.focus', ['decisions'])}
    ${statLink(c.checkpoints ?? 0, 'checkpoints', 'research.focus', ['checkpoints'])}
    ${statLink(c.artifacts ?? 0, 'artifacts', 'research.focus', ['artifacts'])}
  </div></section>`;
  html += `</div>`;
  return html;
}

/** Compact sidebar overview. */
export function renderOverview(r: any, error?: string): string {
  if (error) return `<div class="page"><div class="banner" style="--c:var(--red)">${ic('error')}<div class="grow">${esc(error)}</div></div>${btn('Show log', 'research.showOutput', [], { ghost: true })}</div>`;
  if (!r) return `<div class="page"><div class="row muted"><span class="spinner"></span> Loading…</div></div>`;
  const p = r.project || {};
  const cp = r.latest_checkpoint;
  const needs = r.needs_synthesis || [];
  const c = r.counts || {};
  const est = r.established_findings || [];
  const cur = r.current_experiments || [];
  const qs = [...(r.active_questions || []), ...(r.open_questions || [])];
  let h = `<div class="page fade-in">`;
  h += `<div class="goal" title="Project goal">${p.goal ? esc(p.goal) : `<a data-cmd="research.project.setGoal" data-args="[]">${ic('target')} Set a project goal</a>`}</div>`;
  h += `<button class="primary wide" data-cmd="research.resume" data-args="[]">${ic('history')}Resume Project</button>`;
  h += `<div class="stats" style="margin-top:10px">${statLink(c.questions ?? 0, 'questions', 'research.focus', ['questions'])}${statLink(c.experiments ?? 0, 'exps', 'research.focus', ['experiments'])}${statLink(c.findings ?? 0, 'findings', 'research.focus', ['findings'])}</div>`;
  if (needs.length) {
    h += `<div class="banner" style="margin-top:10px;padding:8px 10px">${ic('warning')}<div class="grow small"><b>Needs synthesis</b><br>${needs.map((e: any) => `${idLink(e.id)} <span class="muted">${e.state.unsynthesized} runs</span>`).join('<br>')}</div></div>`;
  }
  if ((r.active_runs || []).length) {
    h += `<div class="banner" style="--c:var(--yellow);margin-top:8px;padding:8px 10px"><span class="dot pulse" style="--c:var(--yellow)"></span><div class="grow small"><b>${r.active_runs.length} run${r.active_runs.length > 1 ? 's' : ''} active</b> ${r.active_runs.map((x: any) => idLink(x.id)).join(' ')}</div></div>`;
  }
  // checkpoint
  h += `<section>${h2('Latest checkpoint')}`;
  if (cp) {
    h += `<div class="card" data-open="${esc(cp.id)}" style="cursor:pointer"><div class="row small muted">${ic('bookmark')}${esc(cp.id)} · ${time(cp.created_at)}${cp.stale ? ' · <span style="color:var(--orange)">stale</span>' : ''}</div>
      ${cp.current_problem ? `<div class="small" style="margin-top:6px"><span style="color:var(--red)">${ic('flame')}</span> ${esc(truncate(cp.current_problem.replace(/^- /gm, ''), 140))}</div>` : ''}
      ${cp.next_experiment ? `<div class="small" style="margin-top:4px"><span style="color:var(--green)">${ic('arrow-right')}</span> ${esc(truncate(cp.next_experiment, 140))}</div>` : ''}</div>`;
  } else h += `<div class="empty-card small">${ic('bookmark')}<span class="grow">No checkpoint yet</span></div>`;
  h += `<div style="margin-top:6px">${btn('Create Checkpoint', 'research.checkpoint.create', [], { icon: 'bookmark', cls: 'wide' })}</div></section>`;
  // questions
  if (qs.length) h += `<section>${h2('Active questions', qs.length)}<div class="list">${qs.slice(0, 5).map((q: any) => entityRow({ id: q.id, type: 'question', title: q.title, status: q.status })).join('')}</div></section>`;
  if (cur.length) h += `<section>${h2('Current experiments', cur.length)}<div class="list">${cur.slice(0, 6).map((e: any) =>
    entityRow({ id: e.id, type: 'experiment', title: e.title, status: e.status }, `${e.state.runs} runs${e.state.unsynthesized ? ` · <span style="color:var(--orange)">${e.state.unsynthesized} to synthesize</span>` : ''}`)).join('')}</div></section>`;
  if (est.length) h += `<section>${h2('Latest findings', est.length)}<div class="list">${est.slice(0, 4).map((f: any) => entityRow({ id: f.id, type: 'finding', title: f.title, status: f.status, kind: f.kind })).join('')}</div></section>`;
  if ((r.decisions || []).length) h += `<section>${h2('Current decisions', r.decisions.length)}<div class="list">${r.decisions.slice(0, 3).map((d: any) => entityRow({ id: d.id, type: 'decision', title: d.statement })).join('')}</div></section>`;
  if (!qs.length && !cur.length && !est.length) {
    h += `<section><div class="empty-card small" style="flex-direction:column;align-items:flex-start">${ic('rocket')}<div>Start with a question — what are you trying to understand?</div>${btn('New Question', 'research.question.create', [], { icon: 'add', primary: true })}</div></section>`;
  }
  h += `</div>`;
  return h;
}
