/** Detail views: experiment, finding, question, decision, checkpoint, run, artifact. */
import { dur, esc, fmtVal, paramsSummary, truncate } from '../util';
import {
  ART_ICON, author, btn, confidence, empty, entityRow, field, fileLink, h2, ic, iconBtn, idLink, kindPill, kvTable, md, menu,
  metricChart, pill, time, timeline,
} from './ui';

export interface RenderCtx {
  /** Convert an absolute path on the workspace host into a webview URI (or '' if not allowed). */
  uri(abs: string): string;
  /** Optional pre-read artifact content for previews. */
  preview?: { kind: string; text?: string; rows?: string[][]; truncated?: boolean; error?: string };
  logTail?: { stdout?: string; stderr?: string };
}

const IMG = /\.(png|jpe?g|gif|svg|webp|bmp)$/i;

function header(eyebrowHtml: string, title: string, idHtml: string, pills: string, metaHtml = '', toolbar = ''): string {
  return `<div class="hero"><div class="crumbs">${eyebrowHtml}</div>
    <h1>${idHtml} ${esc(title)}</h1>
    <div class="row wrap" style="gap:6px">${pills}</div>
    ${metaHtml ? `<div class="meta" style="margin-top:10px">${metaHtml}</div>` : ''}
    ${toolbar ? `<div class="toolbar">${toolbar}</div>` : ''}</div>`;
}

function gitMeta(o: any): string {
  if (!o.git_branch && !o.git_commit) return '';
  return `<span>${ic('git-branch')}<span class="mono">${esc(o.git_branch || '—')}</span> @ <span class="mono">${esc((o.git_commit || '').slice(0, 8))}</span>${o.git_dirty ? ' <span class="pill c-orange">dirty</span>' : ''}</span>`;
}

function artifactThumb(a: any, ctx: RenderCtx): string {
  const isImg = a.exists && (a.type === 'image' || IMG.test(a.path)) && !/\.(tiff?)$/i.test(a.path);
  const src = isImg ? ctx.uri(a.abspath) : '';
  return `<div class="thumb" data-open="${esc(a.id)}" title="${esc(a.path)}">
    <div class="img">${src ? `<img src="${src}" loading="lazy" alt="${esc(a.name)}">` : ic(a.exists ? ART_ICON[a.type] || 'file' : 'warning')}</div>
    <div class="cap"><div class="n ellipsis">${esc(a.name)}</div><div class="muted small ellipsis">${esc(a.id)} · ${esc(a.type)}${a.run_id ? ' · ' + esc(a.run_id) : ''}${a.exists ? '' : ' · missing'}</div></div></div>`;
}

export function gallery(arts: any[], ctx: RenderCtx): string {
  return `<div class="gallery">${arts.map((a) => artifactThumb(a, ctx)).join('')}</div>`;
}

// =============================================================================== experiment

export function renderExperiment(e: any, ctx: RenderCtx): string {
  const s = e.state || {};
  const q = e.question;
  const crumbs = [q ? `${ic('question')}${idLink(q.id)} <span class="ellipsis" style="max-width:520px">${esc(q.title)}</span>` : `${ic('beaker')}Experiment`];
  if (e.parent) crumbs.push(`${ic('chevron-right')}${ic('git-compare')} derived from ${idLink(e.parent.id)}`);
  const pills = [pill(e.status, { lg: true }), e.is_baseline ? `<span class="pill lg c-green">${ic('star-full')}baseline</span>` : '', author(e, true),
    ...(e.tags || []).map((t: string) => `<span class="tag">#${esc(t)}</span>`)].join('');
  const meta = [
    `<span>${ic('play')}${s.runs || 0} runs${s.completed ? ` · ${s.completed} ok` : ''}${s.failed ? ` · <span style="color:var(--red)">${s.failed} failed</span>` : ''}${e.planned_runs ? ` of ${e.planned_runs} planned` : ''}</span>`,
    `<span>${ic('calendar')}created ${time(e.created_at)}</span>`,
    e.completed_at ? `<span>${ic('pass')}closed ${time(e.completed_at)}</span>` : '',
    gitMeta(e),
  ].join('');
  const toolbar = [
    btn('Run…', 'research.experiment.run', [e.id], { icon: 'play', primary: true }),
    btn('Attach run', 'research.run.attach', [e.id], { icon: 'link' }),
    btn('Synthesize', 'research.experiment.synthesize', [e.id], { icon: 'sparkle', primary: !!s.unsynthesized && !s.active }),
    btn('Finding', 'research.finding.create', [{ supports: [e.id] }], { icon: 'lightbulb' }),
    btn('Variant', 'research.experiment.variant', [e.id], { icon: 'git-compare' }),
    menu('Status', [
      ['Mark completed', 'research.setStatus', [e.id, 'completed'], 'pass'],
      ['Mark failed', 'research.setStatus', [e.id, 'failed'], 'error'],
      ['Mark ready', 'research.setStatus', [e.id, 'ready'], 'circle-large-outline'],
      ['Abandon', 'research.setStatus', [e.id, 'abandoned'], 'circle-slash'],
      [e.is_baseline ? 'Unset baseline' : 'Set as baseline', 'research.setBaseline', [e.is_baseline ? null : e.id], 'star-empty'],
    ]),
    menu('More', [
      ['Edit fields', 'research.edit', [e.id], 'edit'],
      ['Open Markdown file', 'research.openMirror', [e.id], 'markdown'],
      ['Open terminal here', 'research.terminal.here', [e.id], 'terminal'],
      ['Add metric', 'research.metric.add', [{ experiment: e.id }], 'graph'],
      ['Register artifact', 'research.artifact.register', [{ experiment: e.id }], 'file-add'],
      ['Copy ID', 'research.copyId', [e.id], 'copy'],
    ], 'ellipsis'),
  ].join('');
  let h = `<div class="page fade-in">${header(crumbs.join(' '), e.title, idLink(e.id), pills, meta, toolbar)}`;

  if (s.over_run_budget || s.over_failure_budget || (s.unsynthesized && !s.active)) {
    const msg = s.over_run_budget
      ? `<b>Run budget reached.</b> ${s.unsynthesized} runs have not been synthesized (limit ${s.max_runs_without_synthesis}). Agents are blocked from running more until this is synthesized.`
      : s.over_failure_budget ? `<b>${s.unreviewed_failed} failed runs need review</b> (limit ${s.max_failed_runs_without_review}).`
        : `<b>Needs synthesis.</b> ${s.unsynthesized} finished run${s.unsynthesized > 1 ? 's have' : ' has'} not been interpreted yet.`;
    h += `<div class="banner">${ic('warning')}<div class="grow">${msg}</div>${btn('Synthesize now', 'research.experiment.synthesize', [e.id], { primary: true, icon: 'sparkle' })}</div>`;
  }

  // ---------------------------------------------------------------- design
  const design = [
    field('Hypothesis', e.hypothesis, 'No hypothesis recorded'), field('Motivation', e.motivation), field('Method', e.method),
    field('Expected outcome', e.expected_outcome), field('Success criteria', e.success_criteria), field('Stop conditions', e.stop_conditions),
  ].join('');
  const side = `<div class="stack">
    <div>${h2('Parameters')}${kvTable(e.parameters, e.param_delta)}</div>
    ${e.param_delta && Object.keys(e.param_delta).length ? `<div class="card tint" style="--c:var(--orange)"><h3>${ic('git-compare')}Delta vs ${idLink(e.parent_id)}</h3>${Object.entries(e.param_delta).map(([k, d]: any) => `<div class="delta">${esc(k)}: <s>${esc(fmtVal(d.from))}</s> → ${esc(fmtVal(d.to))}</div>`).join('')}</div>` : ''}
    ${(e.metrics_requested || []).length ? `<div>${h2('Metrics to evaluate')}<div class="row wrap">${e.metrics_requested.map((m: string) => `<span class="pill c-purple">${esc(m)}</span>`).join('')}</div></div>` : ''}
    ${(e.expected_artifacts || []).length ? `<div>${h2('Expected artifacts')}<div class="row wrap">${e.expected_artifacts.map((m: string) => `<span class="tag">${esc(m)}</span>`).join('')}</div></div>` : ''}
    ${(e.children || []).length ? `<div>${h2('Variants', e.children.length)}<div class="list compact">${e.children.map((c: any) => entityRow(c)).join('')}</div></div>` : ''}
  </div>`;
  h += `<section class="split"><div class="card">${design || '<div class="empty">No design recorded.</div>'}</div>${side}</section>`;

  // ---------------------------------------------------------------- runs
  const runs: any[] = e.runs || [];
  h += `<section>${h2('Runs', runs.length, btn('Run…', 'research.experiment.run', [e.id], { icon: 'play', ghost: true }) + btn('Attach', 'research.run.attach', [e.id], { icon: 'link', ghost: true }))}`;
  if (runs.length) {
    const pn: string[] = e.run_param_names || [];
    const mn: string[] = e.metric_names || [];
    const colVals = (k: string) => runs.map((r) => r.metrics?.[k]?.value).filter((v) => typeof v === 'number') as number[];
    const ranges: Record<string, [number, number]> = {};
    for (const k of mn) {
      const vs = colVals(k);
      if (vs.length) ranges[k] = [Math.min(...vs), Math.max(...vs)];
    }
    h += `<div class="tablewrap"><table><thead><tr><th></th><th>Run</th>${pn.map((k) => `<th class="param">${esc(k)}</th>`).join('')}${mn.map((k) => `<th class="metric" style="text-align:right">${esc(k)}</th>`).join('')}<th>Time</th><th>Where</th><th></th></tr></thead><tbody>`;
    for (const r of runs) {
      const c = { completed: 'green', failed: 'red', running: 'yellow', queued: 'gray', cancelled: 'gray', unknown: 'red' }[r.status as string] || 'gray';
      const active = r.status === 'running' || r.status === 'queued';
      h += `<tr data-open="${esc(r.id)}"><td style="width:1%"><span class="dot${r.status === 'running' ? ' pulse' : ''}" style="--c:var(--${c})" title="${esc(r.status)}"></span></td>
        <td><div class="row" style="gap:6px">${idLink(r.id)}${r.label ? `<span class="small">${esc(truncate(r.label, 28))}</span>` : ''}${r.status === 'failed' ? `<span class="pill c-red">exit ${esc(r.exit_code ?? '?')}</span>` : ''}${r.git_dirty ? `<span title="uncommitted changes (git.diff saved)" style="color:var(--orange)">${ic('diff')}</span>` : ''}${r.author_type === 'agent' ? `<span title="${esc(r.author_name || 'agent')}" style="color:var(--purple)">${ic('hubot')}</span>` : ''}</div></td>
        ${pn.map((k) => `<td class="num">${esc(fmtVal(r.parameters?.[k]))}</td>`).join('')}
        ${mn.map((k) => {
          const m = r.metrics?.[k];
          const v = m?.value;
          if (typeof v !== 'number') return `<td class="num muted">${esc(m?.text ?? '—')}</td>`;
          const [lo, hi] = ranges[k] || [v, v];
          const w = hi > lo ? 6 + (34 * (v - lo)) / (hi - lo) : 20;
          return `<td class="num${runs.length > 1 && v === lo ? ' best' : ''}" title="${esc(String(v))}${m.step != null ? ' @ step ' + m.step : ''}"><span class="bar" style="width:${w}px"></span>${esc(fmtVal(v))}${m.unit ? ' <span class="muted">' + esc(m.unit) + '</span>' : ''}</td>`;
        }).join('')}
        <td class="muted small">${active ? `<span style="color:var(--yellow)">${esc(r.status)}</span>` : esc(dur(r.duration_s))}</td>
        <td class="muted small">${esc(r.backend)}${r.slurm_job_id ? ` · <span class="mono">${esc(r.slurm_job_id)}</span>` : ''}</td>
        <td class="acts">${iconBtn('output', 'research.run.logs', [r.id], 'stdout')}${iconBtn('warning', 'research.run.logs', [r.id, 'stderr'], 'stderr')}${active ? iconBtn('debug-stop', 'research.run.cancel', [r.id], 'Cancel') : ''}</td></tr>`;
    }
    h += `</tbody></table></div>`;
    // charts
    const labelFor = (r: any) => {
      const varying = pn.filter((k) => new Set(runs.map((x) => JSON.stringify(x.parameters?.[k]))).size > 1);
      const ps = varying.length ? varying.map((k) => `${k}=${fmtVal(r.parameters?.[k])}`).join(' ') : '';
      return ps || r.label || r.id;
    };
    const charts = mn.map((k) => metricChart(k, runs.map((r) => ({ id: r.id, label: labelFor(r), value: typeof r.metrics?.[k]?.value === 'number' ? r.metrics[k].value : null })), runs.find((r) => r.metrics?.[k]?.unit)?.metrics[k].unit)).filter(Boolean);
    if (charts.length) h += `<div class="grid2" style="margin-top:12px">${charts.join('')}</div>`;
  } else {
    h += empty('No runs yet. Runs are single executions of this experiment.', btn('Run…', 'research.experiment.run', [e.id], { icon: 'play', primary: true }));
  }
  h += `</section>`;

  // ---------------------------------------------------------------- artifacts
  const arts = [...(e.artifacts || [])];
  for (const r of runs) for (const a of r.artifacts || []) if (!arts.find((x) => x.id === a.id)) arts.push(a);
  h += `<section>${h2('Artifacts', arts.length, btn('Register', 'research.artifact.register', [{ experiment: e.id }], { icon: 'file-add', ghost: true }))}${arts.length ? gallery(arts, ctx) : empty('No artifacts registered. Artifacts are referenced by path — nothing is copied.')}</section>`;

  // ---------------------------------------------------------------- synthesis
  const syn: any[] = e.syntheses || [];
  h += `<section>${h2('Synthesis', syn.length, btn('Synthesize', 'research.experiment.synthesize', [e.id], { icon: 'sparkle', ghost: true }))}`;
  if (syn.length) {
    for (const x of syn.slice().reverse()) {
      const rows: [string, string][] = [['What happened', x.what_happened], ['What worked', x.what_worked], ['What didn’t', x.what_failed],
        ['Interpretation', x.interpretation], ['Limitations', x.limitations], ['Unresolved', x.unresolved], ['Next experiment', x.next_experiment]];
      h += `<div class="synth ${x.author_type === 'agent' ? 'agent' : ''}" style="margin-bottom:10px"><div class="hd">${ic('sparkle')}<b>${esc(x.id.split('/').pop())}</b>${author(x, true)}<span class="muted">${time(x.created_at)} · covers ${(x.runs_covered || []).length} runs</span>
        ${x.author_type === 'agent' ? '<span class="muted small" style="margin-left:auto">agent-generated synthesis</span>' : ''}</div>
        <div class="bd">${rows.filter(([, v]) => v).map(([k, v]) => `<div class="k">${esc(k)}</div><div>${md(v)}</div>`).join('')}</div></div>`;
    }
  } else h += empty('Not synthesized yet. A synthesis interprets the runs: what happened, what worked, what it means.');
  h += `</section>`;

  // ---------------------------------------------------------------- findings / decisions / notes
  h += `<section class="grid2">
    <div>${h2('Findings produced', (e.findings || []).length, btn('New', 'research.finding.create', [{ supports: [e.id] }], { icon: 'add', ghost: true }))}${(e.findings || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${e.findings.map((f: any) => entityRow(f)).join('')}</div></div>` : empty('No findings yet.')}</div>
    <div>${h2('Decisions & notes')}${[...(e.decisions || []).map((d: any) => entityRow(d)), ...(e.linked_notes || []).map((n: any) => `<div class="item" data-file="${esc(n.path)}">${ic('note', 'lead')}<div class="grow"><div class="title">${esc(n.title)}</div><div class="sub">${esc(n.excerpt || '')}</div></div></div>`)].join('') || empty('None linked.')}</div>
  </section>`;
  if (e.limitations || e.notes) h += `<section class="grid2">${e.limitations ? `<div class="card">${field('Known limitations', e.limitations)}</div>` : ''}${e.notes ? `<div class="card">${field('Notes', e.notes)}</div>` : ''}</section>`;
  h += `<section>${h2('Activity')}<div class="card">${timeline(e.events, 15)}</div></section></div>`;
  return h;
}

// =============================================================================== finding

export function renderFinding(f: any, ctx: RenderCtx): string {
  const tone = f.kind === 'failure' ? 'var(--red)' : f.status === 'supported' ? 'var(--green)' : f.status === 'contradicted' ? 'var(--orange)' : 'var(--yellow)';
  const pills = [kindPill(f), pill(f.status, { lg: true }), f.confidence ? `<span class="pill lg c-gray">${confidence(f.confidence, tone)}</span>` : '', author(f, true),
    ...(f.tags || []).map((t: string) => `<span class="tag">#${esc(t)}</span>`)].join('');
  const toolbar = [
    btn('Edit', 'research.edit', [f.id], { icon: 'edit' }),
    btn('Decision from this', 'research.decision.create', [{ findings: [f.id] }], { icon: 'law' }),
    menu('Status', [
      ['Mark supported', 'research.setStatus', [f.id, 'supported'], 'verified'],
      ['Mark preliminary', 'research.setStatus', [f.id, 'preliminary'], 'lightbulb'],
      ['Mark contradicted', 'research.setStatus', [f.id, 'contradicted'], 'warning'],
      ['Supersede…', 'research.finding.supersede', [f.id], 'history'],
    ]),
    menu('More', [['Open Markdown file', 'research.openMirror', [f.id], 'markdown'], ['Copy ID', 'research.copyId', [f.id], 'copy']], 'ellipsis'),
  ].join('');
  let h = `<div class="page narrow fade-in">${header(`${ic(f.kind === 'failure' ? 'close' : 'lightbulb')}Finding${(f.questions || []).length ? ' · addresses ' + f.questions.map((q: any) => idLink(q.id)).join(' ') : ''}`, f.title, idLink(f.id), pills,
    `<span>${ic('calendar')}created ${time(f.created_at)}</span>${f.updated_at !== f.created_at ? `<span>${ic('edit')}updated ${time(f.updated_at)}</span>` : ''}`, toolbar)}`;
  if (f.superseded_by_card) h += `<div class="banner" style="--c:var(--gray)">${ic('history')}<div class="grow">Superseded by ${idLink(f.superseded_by)} <b>${esc(f.superseded_by_card.title)}</b></div></div>`;
  h += `<section><div class="quote" style="--c:${tone}">${md(f.statement)}</div></section>`;
  const ev = f.evidence || {};
  h += `<section>${h2('Supporting evidence', (ev.supports || []).length, btn('Add evidence', 'research.edit', [f.id], { icon: 'add', ghost: true }))}
    ${(ev.supports || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${ev.supports.map((b: any) => entityRow(b, b.type === 'artifact' ? `<span class="mono">${esc(b.path || '')}</span>` : b.type === 'run' ? `run of ${idLink(b.experiment_id)}` : '')).join('')}</div></div>`
      : `<div class="banner" style="--c:var(--orange)">${ic('warning')}<div class="grow"><b>No evidence linked.</b> A finding without evidence is an opinion. Link experiments, runs or artifacts.</div></div>`}</section>`;
  const imgs = (f.artifacts || []).filter((a: any) => a.exists && (a.type === 'image' || IMG.test(a.path || '')));
  if (imgs.length) h += `<section>${h2('Evidence at a glance')}${gallery(imgs, ctx)}</section>`;
  if ((ev.contradicts || []).length || f.contradicting_evidence)
    h += `<section>${h2('Contradicting evidence', (ev.contradicts || []).length)}<div class="card tint" style="--c:var(--red)">${(ev.contradicts || []).map((b: any) => entityRow(b)).join('')}${md(f.contradicting_evidence)}</div></section>`;
  h += `<section class="grid2"><div>${h2('Limitations')}<div class="card">${md(f.limitations) || '<div class="empty">None stated — every finding has some. What is the scope?</div>'}</div></div>
    <div>${h2('Experiments')}${(f.experiments || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${f.experiments.map((b: any) => entityRow(b)).join('')}</div></div>` : empty('No experiments linked.')}</div></section>`;
  const rel = [...(ev.related || []), ...(f.referenced_by || []), ...(f.supersedes || [])];
  h += `<section class="grid2"><div>${h2('Decisions based on this', (f.decisions || []).length)}${(f.decisions || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${f.decisions.map((d: any) => entityRow(d)).join('')}</div></div>` : empty('No decisions yet.', btn('Create', 'research.decision.create', [{ findings: [f.id] }], { ghost: true }))}</div>
    <div>${h2('Related findings', rel.length)}${rel.length ? `<div class="card flush"><div class="list" style="padding:4px">${rel.map((b: any) => entityRow(b)).join('')}</div></div>` : empty('None.')}</div></section>`;
  h += `<section>${h2('History')}<div class="card">${timeline(f.events, 10)}</div></section></div>`;
  return h;
}

// =============================================================================== question

export function renderQuestion(q: any): string {
  const toolbar = [
    btn('New experiment', 'research.experiment.create', [{ question: q.id }], { icon: 'beaker', primary: true }),
    btn('Sub-question', 'research.question.create', [{ parent: q.id }], { icon: 'add' }),
    btn('Edit', 'research.edit', [q.id], { icon: 'edit' }),
    menu('Status', ['open', 'investigating', 'answered', 'blocked', 'abandoned'].map((s) => [`Mark ${s}`, 'research.setStatus', [q.id, s], 'tag'] as [string, string, any[], string])),
  ].join('');
  let h = `<div class="page narrow fade-in">${header(`${ic('question')}Question${q.parent ? ` · part of ${idLink(q.parent.id)} ${esc(q.parent.title)}` : ''}`, q.title, idLink(q.id), [pill(q.status, { lg: true }), author(q)].join(''),
    `<span>${ic('calendar')}created ${time(q.created_at)}</span>`, toolbar)}`;
  h += `<section><div class="card">${md(q.description) || '<div class="empty">No description.</div>'}</div></section>`;
  if ((q.children || []).length) h += `<section>${h2('Sub-questions', q.children.length)}<div class="card flush"><div class="list" style="padding:4px">${q.children.map((c: any) => entityRow(c)).join('')}</div></div></section>`;
  h += `<section>${h2('Experiments', (q.experiments || []).length)}${(q.experiments || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${q.experiments.map((e: any) =>
    entityRow(e, `${e.state.runs} runs${e.state.unsynthesized ? ` · <span style="color:var(--orange)">${e.state.unsynthesized} to synthesize</span>` : ''}`)).join('')}</div></div>` : empty('No experiments address this question yet.', btn('New experiment', 'research.experiment.create', [{ question: q.id }], { primary: true }))}</section>`;
  h += `<section>${h2('What we learned', (q.findings || []).length)}${(q.findings || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${q.findings.map((f: any) => entityRow(f)).join('')}</div></div>` : empty('No findings yet.')}</section>`;
  if ((q.linked_notes || []).length) h += `<section>${h2('Notes')}${q.linked_notes.map((n: any) => `<div class="item" data-file="${esc(n.path)}">${ic('note', 'lead')}<div class="grow"><div class="title">${esc(n.title)}</div></div></div>`).join('')}</section>`;
  h += `<section>${h2('History')}<div class="card">${timeline(q.events, 10)}</div></section></div>`;
  return h;
}

// =============================================================================== decision

export function renderDecision(d: any): string {
  const toolbar = [
    btn('Edit', 'research.edit', [d.id], { icon: 'edit' }),
    menu('Status', [['Mark active', 'research.setStatus', [d.id, 'active'], 'pass'], ['Reverse', 'research.setStatus', [d.id, 'reversed'], 'discard'], ['Supersede with new decision…', 'research.decision.create', [{ supersedes: d.id, findings: (d.findings || []).map((x: any) => x.id) }], 'history']]),
  ].join('');
  let h = `<div class="page narrow fade-in">${header(`${ic('law')}Decision · ${esc(d.date || '')}`, d.title || d.statement, idLink(d.id), [pill(d.status, { lg: true }), author(d, true)].join(''), '', toolbar)}`;
  if (d.superseded_by_card) h += `<div class="banner" style="--c:var(--gray)">${ic('history')}<div class="grow">Superseded by ${idLink(d.superseded_by)} ${esc(d.superseded_by_card.title)}</div></div>`;
  h += `<section><div class="quote" style="--c:var(--green)">${md(d.statement)}</div></section>`;
  h += `<section>${h2('Why')}<div class="card">${md(d.reason) || '<div class="empty">No reason recorded.</div>'}</div></section>`;
  h += `<section class="grid2"><div>${h2('Based on findings', (d.findings || []).length)}${(d.findings || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${d.findings.map((f: any) => entityRow(f)).join('')}</div></div>` : empty('Not linked to any finding.')}</div>
    <div>${h2('Experiments', (d.experiments || []).length)}${(d.experiments || []).length ? `<div class="card flush"><div class="list" style="padding:4px">${d.experiments.map((f: any) => entityRow(f)).join('')}</div></div>` : empty('None.')}</div></section>`;
  h += `<section>${h2('History')}<div class="card">${timeline(d.events, 10)}</div></section></div>`;
  return h;
}

// =============================================================================== checkpoint

export function renderCheckpoint(c: any): string {
  const cards = c.cards || {};
  const list = (title: string, items: any[], icon: string) => `<div>${h2(title, items.length)}${items.length ? `<div class="card flush"><div class="list" style="padding:4px">${items.map((b: any) => entityRow(b)).join('')}</div></div>` : empty('None.')}</div>`;
  let h = `<div class="page fade-in">${header(`${ic('bookmark')}Checkpoint`, c.title, idLink(c.id), author(c, true),
    `<span>${ic('calendar')}${time(c.created_at)} · ${esc((c.created_at || '').slice(0, 16).replace('T', ' '))}</span>${gitMeta(c)}`,
    [btn('Open Markdown', 'research.openMirror', [c.id], { icon: 'markdown' }), btn('New checkpoint', 'research.checkpoint.create', [], { icon: 'bookmark' })].join(''))}`;
  h += `<section class="grid2"><div class="card tone" style="--c:var(--blue)">${field('Current goal', c.goal, '—')}${field('Current understanding', c.understanding, '—')}${c.baseline ? field('Baseline', c.baseline) : ''}</div>
    <div class="stack"><div class="card tint" style="--c:var(--red)"><h3>${ic('flame')}Current problems / blockers</h3>${md(c.current_problem) || '<div class="empty">None</div>'}</div>
    <div class="card tint" style="--c:var(--green)"><h3>${ic('arrow-right')}Next step</h3>${md(c.next_experiment) || '<div class="empty">—</div>'}</div></div></section>`;
  if (c.notes) h += `<section><div class="card">${field('Notes', c.notes)}</div></section>`;
  h += `<section class="grid2">${list('Important findings', cards.findings || [], 'lightbulb')}${list('Known failures', cards.failures || [], 'close')}</section>`;
  h += `<section class="grid2">${list('Open questions', cards.questions || [], 'question')}${list('Active experiments', [...(cards.baseline ? [{ ...cards.baseline, title: '★ ' + cards.baseline.title }] : []), ...(cards.experiments || [])], 'beaker')}</section></div>`;
  return h;
}

// =============================================================================== run

export function renderRun(r: any, ctx: RenderCtx): string {
  const active = r.status === 'running' || r.status === 'queued';
  const toolbar = [
    btn('stdout', 'research.run.logs', [r.id], { icon: 'output' }),
    btn('stderr', 'research.run.logs', [r.id, 'stderr'], { icon: 'warning' }),
    btn('Run dir', 'research.revealFile', [r.run_dir], { icon: 'folder' }),
    btn('Terminal here', 'research.terminal.here', [r.id], { icon: 'terminal' }),
    btn('Add metric', 'research.metric.add', [{ run: r.id }], { icon: 'graph' }),
    btn('Register artifact', 'research.artifact.register', [{ run: r.id }], { icon: 'file-add' }),
    active ? btn('Cancel', 'research.run.cancel', [r.id], { icon: 'debug-stop', cls: 'danger' }) : '',
    btn('Refresh', 'research.run.refresh', [r.id], { icon: 'sync', ghost: true }),
  ].join('');
  const meta = [`<span>${ic('clock')}${r.started_at ? 'started ' + time(r.started_at) : 'not started'}${r.duration_s != null ? ' · took ' + esc(dur(r.duration_s)) : ''}</span>`,
    `<span>${ic('server')}${esc(r.backend)}${r.hostname ? ' · ' + esc(r.hostname) : ''}${r.slurm_job_id ? ' · job <span class="mono">' + esc(r.slurm_job_id) + '</span>' : ''}</span>`, gitMeta(r)].join('');
  let h = `<div class="page fade-in">${header(`${ic('beaker')}${idLink(r.experiment_id)} ${esc(r.experiment?.title || '')} ${ic('chevron-right')} Run`, r.label || r.id, idLink(r.id),
    [pill(r.status, { lg: true }), r.exit_code != null ? `<span class="pill lg ${r.exit_code === 0 ? 'c-green' : 'c-red'}">exit ${esc(r.exit_code)}</span>` : '', author(r, true), r.reviewed ? '<span class="pill c-gray">reviewed</span>' : ''].join(''), meta, toolbar)}`;
  h += `<section>${h2('Command')}<div class="card"><div class="row top"><code class="grow" style="white-space:pre-wrap;word-break:break-all;background:none;border:0;padding:0">${esc(r.command || '—')}</code>${iconBtn('copy', 'research.copyText', [r.command || ''], 'Copy command')}</div>
    <div class="muted small" style="margin-top:6px">${ic('folder')} in <span class="mono">${esc(r.working_dir || '.')}</span>${r.git_dirty ? ` · <a data-file="${esc(r.run_dir + '/git.diff')}">${ic('diff')} git.diff</a> (uncommitted changes at launch)` : ''}</div></div></section>`;
  h += `<section class="grid2"><div>${h2('Parameters')}${kvTable(r.parameters)}</div><div>${h2('Metrics (latest)')}${kvTable(Object.fromEntries(Object.entries(r.metrics || {}).map(([k, v]: any) => [k, v.value ?? v.text])))}</div></section>`;
  if (r.slurm) h += `<section>${h2('SLURM')}${kvTable({ ...(r.slurm.requested || {}), state: r.slurm.state, nodes: r.slurm.nodelist, script: r.slurm.script })}</section>`;
  if ((r.artifacts || []).length) h += `<section>${h2('Artifacts', r.artifacts.length)}${gallery(r.artifacts, ctx)}</section>`;
  const lt = ctx.logTail || {};
  h += `<section class="grid2"><div>${h2('stdout (tail)', undefined, btn('Open', 'research.run.logs', [r.id], { ghost: true, icon: 'go-to-file' }))}<pre class="log">${esc(lt.stdout || '(empty)')}</pre></div>
    <div>${h2('stderr (tail)', undefined, btn('Open', 'research.run.logs', [r.id, 'stderr'], { ghost: true, icon: 'go-to-file' }))}<pre class="log">${esc(lt.stderr || '(empty)')}</pre></div></section>`;
  if ((r.findings || []).length) h += `<section>${h2('Cited by findings')}${r.findings.map((f: any) => entityRow(f)).join('')}</section>`;
  if (r.notes) h += `<section><div class="card">${field('Notes', r.notes)}</div></section>`;
  return h + '</div>';
}

// =============================================================================== artifact

export function renderArtifact(a: any, ctx: RenderCtx): string {
  const toolbar = [
    btn('Open', 'research.openFile', [a.path], { icon: 'go-to-file', primary: true }),
    btn('Reveal', 'research.revealFile', [a.path], { icon: 'file-symlink-directory' }),
    btn('Copy path', 'research.copyText', [a.abspath], { icon: 'copy' }),
    btn('Cite in finding', 'research.finding.create', [{ supports: [a.id] }], { icon: 'lightbulb' }),
  ].join('');
  const owner = [a.experiment ? `${idLink(a.experiment.id)} ${esc(a.experiment.title)}` : '', a.run ? `${ic('chevron-right')}${idLink(a.run.id)}` : ''].join(' ');
  let h = `<div class="page fade-in">${header(`${ic(ART_ICON[a.type] || 'file')}Artifact ${owner ? '· ' + owner : ''}`, a.name, idLink(a.id),
    [`<span class="pill lg c-blue">${esc(a.type)}</span>`, a.exists ? '' : `<span class="pill lg c-red">${ic('warning')}missing on disk</span>`, a.external ? '<span class="pill c-gray">external path</span>' : '', author(a)].join(''),
    `<span class="mono">${esc(a.path)}</span>`, toolbar)}`;
  if (a.description) h += `<section><div class="card">${md(a.description)}</div></section>`;
  h += `<section>${h2('Preview')}${renderPreview(a, ctx)}</section>`;
  h += `<section>${h2('Metadata')}${kvTable({ path: a.path, 'absolute path': a.abspath, size: a.size != null ? `${a.size.toLocaleString()} bytes` : null, modified: a.mtime, sha256: a.hash ? a.hash.slice(0, 16) + '…' : 'not hashed', registered: a.created_at, ...(a.preview || {}) })}</section>`;
  if ((a.findings || []).length) h += `<section>${h2('Cited by findings')}${a.findings.map((f: any) => entityRow(f)).join('')}</section>`;
  return h + '</div>';
}

/** Preview adapters: image, text/markdown/log, json, table. Everything else → metadata + open. */
function renderPreview(a: any, ctx: RenderCtx): string {
  if (!a.exists) return empty('File not found at this path. It may have been moved, or lives on another machine.');
  if (a.type === 'image' || IMG.test(a.path)) {
    const src = ctx.uri(a.abspath);
    return src ? `<img class="preview-img" src="${src}" alt="${esc(a.name)}">` : empty('Image outside the allowed roots.');
  }
  const p = ctx.preview;
  if (!p) return empty(`No built-in preview for “${a.type}”. Open it with an extension that understands the format.`, btn('Open', 'research.openFile', [a.path], { ghost: true }));
  if (p.error) return empty(p.error);
  if (p.kind === 'table' && p.rows) {
    const [head, ...body] = p.rows;
    return `<div class="tablewrap" style="max-height:480px;overflow:auto"><table><thead><tr>${(head || []).map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${body.map((r) => `<tr style="cursor:default">${r.map((c) => `<td class="${/^-?[\d.eE+-]+$/.test(c) ? 'num' : ''}">${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${p.truncated ? '<div class="muted small" style="margin-top:6px">Showing first rows only.</div>' : ''}`;
  }
  if (p.kind === 'markdown') return `<div class="card">${md(p.text)}</div>`;
  return `<pre class="text">${esc(p.text || '')}</pre>${p.truncated ? '<div class="muted small" style="margin-top:6px">Truncated preview.</div>' : ''}`;
}
