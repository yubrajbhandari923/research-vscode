/** Detail views: experiment, finding, question, decision, checkpoint, run, artifact, plan, task. */
import { dur, esc, fmtVal, paramsSummary, truncate } from '../util';
import {
  ART_ICON, author, blkHd, btn, confidence, disclosure, empty, entityRow, field, fileLink, h2, ic, iconBtn, idLink, kindPill, kvTable, lineChart, md, menu,
  metricChart, overflow, pill, time, timeline,
} from './ui';
import { planRing, stepRow, TASK_STATE } from './home';

export interface RenderCtx {
  /** Convert an absolute path on the workspace host into a webview URI (or '' if not allowed). */
  uri(abs: string): string;
  /** Optional pre-read artifact content for previews. */
  preview?: { kind: string; text?: string; rows?: string[][]; truncated?: boolean; error?: string };
  logTail?: { stdout?: string; stderr?: string };
}

const IMG = /\.(png|jpe?g|gif|svg|webp|bmp)$/i;

function header(eyebrowHtml: string, title: string, idHtml: string, pills: string, metaHtml = '', toolbar = ''): string {
  return `<div class="hero"><div class="crumbs">${eyebrowHtml}<span class="crumb-id">${idHtml}</span></div>
    <h1>${esc(title)}</h1>
    ${pills ? `<div class="status-row">${pills}</div>` : ''}
    ${metaHtml ? `<div class="meta">${metaHtml}</div>` : ''}
    ${toolbar ? `<div class="toolbar">${toolbar}</div>` : ''}</div>`;
}

function gitMeta(o: any): string {
  if (!o.git_branch && !o.git_commit) return '';
  return `<span>${ic('git-branch')}<span class="mono">${esc(o.git_branch || '—')}</span> @ <span class="mono">${esc((o.git_commit || '').slice(0, 8))}</span>${o.git_dirty ? ' <span class="pill c-orange">dirty</span>' : ''}</span>`;
}

function artifactThumb(a: any, ctx: RenderCtx): string {
  const isImg = a.exists && (a.type === 'image' || IMG.test(a.path)) && !/\.(tiff?)$/i.test(a.path);
  const src = isImg ? ctx.uri(a.abspath) : '';
  return `<div class="thumb" data-open="${esc(a.id)}" title="${esc(a.path)}" tabindex="0" role="link">
    <div class="img">${src ? `<img src="${src}" loading="lazy" width="320" height="200" alt="${esc(a.description || a.name)}">` : ic(a.exists ? ART_ICON[a.type] || 'file' : 'warning')}</div>
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
  const pills = [pill(e.status, { lg: true }), e.is_baseline ? `<span class="pill lg c-green">${ic('star-full')}baseline</span>` : '', author(e),
    ...(e.tags || []).map((t: string) => `<span class="tag">#${esc(t)}</span>`)].join('');
  const meta = [
    `<span>${ic('play')}${s.runs || 0} runs${s.completed ? ` · ${s.completed} ok` : ''}${s.failed ? ` · <span style="color:var(--red)">${s.failed} failed</span>` : ''}${e.planned_runs ? ` of ${e.planned_runs} planned` : ''}</span>`,
    `<span>${ic('calendar')}created ${time(e.created_at)}</span>`,
    e.completed_at ? `<span>${ic('pass')}closed ${time(e.completed_at)}</span>` : '',
  ].join('');
  const toolbar = [
    btn('Run…', 'research.experiment.run', [e.id], { icon: 'play', primary: !s.unsynthesized || !!s.active }),
    btn('Synthesize', 'research.experiment.synthesize', [e.id], { icon: 'sparkle', primary: !!s.unsynthesized && !s.active }),
    btn('Record finding', 'research.finding.create', [{ supports: [e.id] }], { icon: 'lightbulb' }),
    overflow([
      ['Attach existing run', 'research.run.attach', [e.id], 'link'],
      ['Create variant', 'research.experiment.variant', [e.id], 'git-compare'],
      ['Compare two runs…', 'research.run.compare', [{ experiment: e.id }], 'diff'],
      ['Analyze with agent…', 'research.dispatch', [e.id, 'analyst'], 'hubot'],
      '-',
      { header: 'Status' },
      ['Mark completed', 'research.setStatus', [e.id, 'completed'], 'pass'],
      ['Mark failed', 'research.setStatus', [e.id, 'failed'], 'error'],
      ['Mark ready', 'research.setStatus', [e.id, 'ready'], 'circle-large-outline'],
      ['Abandon', 'research.setStatus', [e.id, 'abandoned'], 'circle-slash'],
      [e.is_baseline ? 'Unset baseline' : 'Set as baseline', 'research.setBaseline', [e.is_baseline ? null : e.id], 'star-empty'],
      '-',
      ['Edit fields', 'research.edit', [e.id], 'edit'],
      ['Add metric', 'research.metric.add', [{ experiment: e.id }], 'graph'],
      ['Register artifact', 'research.artifact.register', [{ experiment: e.id }], 'file-add'],
      ['Open terminal here', 'research.terminal.here', [e.id], 'terminal'],
      ['Open Markdown file', 'research.openMirror', [e.id], 'markdown'],
      ['Export report', 'research.report', [e.id], 'output'],
      ['Copy ID', 'research.copyId', [e.id], 'copy'],
    ]),
  ].join('');
  let h = `<div class="page fade-in">${header(crumbs.join(' '), e.title, idLink(e.id), pills, meta, toolbar)}`;

  if (s.over_run_budget || s.over_failure_budget || (s.unsynthesized && !s.active)) {
    const msg = s.over_run_budget
      ? `<b>Run budget reached.</b> ${s.unsynthesized} runs have not been synthesized (limit ${s.max_runs_without_synthesis}). Agents are blocked from running more until this is synthesized.`
      : s.over_failure_budget ? `<b>${s.unreviewed_failed} failed runs need review</b> (limit ${s.max_failed_runs_without_review}).`
        : `<b>Needs synthesis.</b> ${s.unsynthesized} finished run${s.unsynthesized > 1 ? 's have' : ' has'} not been interpreted yet.`;
    h += `<div class="banner">${ic('warning')}<div class="grow">${msg}</div>${btn('Synthesize now', 'research.experiment.synthesize', [e.id], { primary: true, icon: 'sparkle' })}</div>`;
  }

  // ---------------------------------------------------------------- result first: what did we learn?
  const syn: any[] = e.syntheses || [];
  const last = syn[syn.length - 1];
  const findings: any[] = e.findings || [];
  const resultRows: string[] = [];
  if (last?.interpretation) resultRows.push(`<div class="k"><span class="ichip" style="--c:var(--blue)">${ic('book')}</span>Interpretation</div><div class="v">${md(last.interpretation)}</div>`);
  if (last?.what_worked) resultRows.push(`<div class="k"><span class="ichip" style="--c:var(--green)">${ic('pass')}</span>Worked</div><div class="v">${md(last.what_worked)}</div>`);
  if (last?.what_failed) resultRows.push(`<div class="k"><span class="ichip" style="--c:var(--red)">${ic('close')}</span>Didn’t</div><div class="v">${md(last.what_failed)}</div>`);
  if (last?.next_experiment) resultRows.push(`<div class="k"><span class="ichip" style="--c:var(--accent)">${ic('arrow-right')}</span>Next</div><div class="v">${md(last.next_experiment)}</div>`);
  if (!resultRows.length) resultRows.push(`<div class="k"><span class="ichip" style="--c:var(--yellow)">${ic('beaker')}</span>Hypothesis</div><div class="v">${e.hypothesis ? md(e.hypothesis) : '<span class="muted">No hypothesis recorded.</span>'}</div>`);
  h += `<section>${blkHd('lightbulb', last ? 'What we learned' : 'What we are testing', 'var(--yellow)', last ? `<span class="aside muted small">${author(last)} · ${time(last.created_at)}</span>` : '')}
    <div class="panel"><div class="facts">${resultRows.join('')}</div>
    ${findings.length ? `<div class="list" style="margin:10px -8px -6px;border-top:1px solid var(--line);padding-top:6px">${findings.map((f: any) => entityRow(f)).join('')}</div>` : ''}</div></section>`;

  // ---------------------------------------------------------------- charts + outputs
  const runs: any[] = e.runs || [];
  const pn: string[] = e.run_param_names || [];
  const mn: string[] = e.metric_names || [];
  const varying = pn.filter((k) => new Set(runs.map((x) => JSON.stringify(x.parameters?.[k]))).size > 1);
  const labelFor = (r: any) => r.label || (varying.length ? varying.map((k) => `${k}=${fmtVal(r.parameters?.[k])}`).join(' ') : r.id);
  const done = runs.filter((r) => r.status === 'completed');
  const charts = mn.map((k) => {
    const svg = lineChart(done.map((r) => ({ id: r.id, label: labelFor(r), value: typeof r.metrics?.[k]?.value === 'number' ? r.metrics[k].value : null })), { name: k, higherIsBetter: !/(err|loss|mae|mse|rmse|frac|time|runtime|latency|bias)/i.test(k) });
    return svg ? `<div class="chart-card"><div class="ct"><span class="mono">${esc(k)}</span></div><div class="cs">${done.length} completed runs · best highlighted</div>${svg}</div>` : '';
  }).filter(Boolean);
  const arts = [...(e.artifacts || [])];
  for (const r of runs) for (const a of r.artifacts || []) if (!arts.find((x) => x.id === a.id)) arts.push(a);
  if (charts.length || arts.length) {
    h += `<section>${blkHd('graph-line', 'Results', 'var(--purple)', btn('Register output', 'research.artifact.register', [{ experiment: e.id }], { icon: 'file-add', cls: 'sm ghost' }))}
      ${charts.length ? `<div class="grid2">${charts.join('')}</div>` : ''}
      ${arts.length ? `<div style="margin-top:${charts.length ? 12 : 0}px">${gallery(arts, ctx)}</div>` : ''}</section>`;
  }

  // ---------------------------------------------------------------- runs
  h += `<section>${blkHd('play-circle', 'Runs', 'var(--accent)', btn('Run…', 'research.experiment.run', [e.id], { icon: 'play', cls: 'sm ghost' }), runs.length)}`;
  if (runs.length) {
    const ranges: Record<string, [number, number]> = {};
    for (const k of mn) {
      const vs = runs.map((r) => r.metrics?.[k]?.value).filter((v) => typeof v === 'number') as number[];
      if (vs.length) ranges[k] = [Math.min(...vs), Math.max(...vs)];
    }
    h += `<div class="tablewrap"><table><thead><tr><th><span class="sr">Status</span></th><th>Run</th>${varying.map((k) => `<th class="param">${esc(k)}</th>`).join('')}${mn.map((k) => `<th class="metric" style="text-align:right">${esc(k)}</th>`).join('')}<th>Time</th><th><span class="sr">Actions</span></th></tr></thead><tbody>`;
    for (const r of runs) {
      const c = { completed: 'green', failed: 'red', running: 'accent', queued: 'gray', cancelled: 'gray', unknown: 'red' }[r.status as string] || 'gray';
      const active = r.status === 'running' || r.status === 'queued';
      h += `<tr data-open="${esc(r.id)}" tabindex="0"><td style="width:1%"><span class="dot${r.status === 'running' ? ' pulse' : ''}" style="--c:var(--${c})" title="${esc(r.status)}"></span><span class="sr">${esc(r.status)}</span></td>
        <td><div class="row" style="gap:8px"><span>${esc(truncate(r.label || r.id, 30))}</span>${r.status === 'failed' ? `<span class="pill c-red">exit ${esc(r.exit_code ?? '?')}</span>` : ''}${idLink(r.id)}</div></td>
        ${varying.map((k) => `<td class="num">${esc(fmtVal(r.parameters?.[k]))}</td>`).join('')}
        ${mn.map((k) => {
          const m = r.metrics?.[k];
          const v = m?.value;
          if (typeof v !== 'number') return `<td class="num muted">${esc(m?.text ?? '—')}</td>`;
          const [lo, hi] = ranges[k] || [v, v];
          const w = hi > lo ? 6 + (30 * (v - lo)) / (hi - lo) : 18;
          return `<td class="num${runs.length > 1 && v === lo ? ' best' : ''}" title="${esc(String(v))}${m.step != null ? ' @ step ' + m.step : ''}"><span class="bar" style="width:${w}px"></span>${esc(fmtVal(v))}${m.unit ? ' <span class="muted">' + esc(m.unit) + '</span>' : ''}</td>`;
        }).join('')}
        <td class="muted small">${active ? `<span style="color:var(--accent)">${esc(r.status)}</span>` : esc(dur(r.duration_s))}</td>
        <td class="acts">${iconBtn('output', 'research.run.logs', [r.id], 'Open stdout')}${active ? iconBtn('debug-stop', 'research.run.cancel', [r.id], 'Cancel run') : ''}</td></tr>`;
    }
    h += `</tbody></table></div>`;
  } else {
    h += empty('No runs yet. Runs are single executions of this experiment.', btn('Run…', 'research.experiment.run', [e.id], { icon: 'play', primary: true }));
  }
  h += `</section>`;

  // ---------------------------------------------------------------- deeper layers
  const design = [
    field('Hypothesis', e.hypothesis), field('Motivation', e.motivation), field('Method', e.method),
    field('Expected outcome', e.expected_outcome), field('Success criteria', e.success_criteria), field('Stop conditions', e.stop_conditions),
    field('Known limitations', e.limitations), field('Notes', e.notes),
  ].join('');
  const side = `<div class="stack">
    <div>${h2('Parameters')}${kvTable(e.parameters, e.param_delta)}</div>
    ${(e.metrics_requested || []).length ? `<div>${h2('Metrics to evaluate')}<div class="row wrap">${e.metrics_requested.map((m: string) => `<span class="tag mono">${esc(m)}</span>`).join('')}</div></div>` : ''}
    ${(e.expected_artifacts || []).length ? `<div>${h2('Expected artifacts')}<div class="row wrap">${e.expected_artifacts.map((m: string) => `<span class="tag">${esc(m)}</span>`).join('')}</div></div>` : ''}
    ${(e.children || []).length ? `<div>${h2('Variants', e.children.length)}<div class="list compact">${e.children.map((c: any) => entityRow(c)).join('')}</div></div>` : ''}
  </div>`;
  h += disclosure('Design &amp; parameters', `<section class="split"><div class="card">${design || '<div class="empty">No design recorded.</div>'}</div>${side}</section>`, `exp-design-${e.id}`);
  if (syn.length) {
    const synHtml = syn.slice().reverse().map((x) => {
      const rows: [string, string][] = [['What happened', x.what_happened], ['What worked', x.what_worked], ['What didn’t', x.what_failed],
        ['Interpretation', x.interpretation], ['Limitations', x.limitations], ['Unresolved', x.unresolved], ['Next experiment', x.next_experiment]];
      return `<div class="synth ${x.author_type === 'agent' ? 'agent' : ''}" style="margin-bottom:10px"><div class="hd">${ic('sparkle')}<b>${esc(x.id.split('/').pop())}</b>${author(x, true)}<span class="muted">${time(x.created_at)} · covers ${(x.runs_covered || []).length} runs</span></div>
        <div class="bd">${rows.filter(([, v]) => v).map(([k, v]) => `<div class="k">${esc(k)}</div><div>${md(v)}</div>`).join('')}</div></div>`;
    }).join('');
    h += disclosure(`Synthesis history <span class="muted">· ${syn.length}</span>`, synHtml, `exp-syn-${e.id}`);
  }
  const linked = [...(e.decisions || []).map((d: any) => entityRow(d)), ...(e.linked_notes || []).map((n: any) => `<div class="item" data-file="${esc(n.path)}" tabindex="0">${ic('note', 'lead')}<div class="grow"><div class="title">${esc(n.title)}</div><div class="sub">${esc(n.excerpt || '')}</div></div></div>`)].join('');
  if (linked) h += disclosure('Decisions &amp; notes', `<div class="card flush"><div class="list">${linked}</div></div>`, `exp-linked-${e.id}`);
  h += disclosure('Activity &amp; provenance', `<div class="meta" style="margin-bottom:10px">${gitMeta(e)}<span>${ic('calendar')}created ${time(e.created_at)}</span>${author(e, true)}</div><div class="card">${timeline(e.events, 15)}</div>`, `exp-act-${e.id}`);
  h += `</div>`;
  return h;
}

// =============================================================================== finding

export function renderFinding(f: any, ctx: RenderCtx): string {
  const tone = f.kind === 'failure' ? 'var(--red)' : f.status === 'supported' ? 'var(--green)' : f.status === 'contradicted' ? 'var(--orange)' : 'var(--yellow)';
  const pills = [f.kind === 'failure' ? kindPill(f) : '', pill(f.status, { lg: true }), f.confidence ? confidence(f.confidence, tone) : '', author(f),
    ...(f.tags || []).map((t: string) => `<span class="tag">#${esc(t)}</span>`)].join('');
  const toolbar = [
    btn('Review…', 'research.finding.review', [f.id], { icon: 'comment-discussion', primary: true }),
    btn('Ask agent to review', 'research.dispatch', [f.id, 'verifier'], { icon: 'hubot' }),
    overflow([
      ['Record decision from this', 'research.decision.create', [{ findings: [f.id] }], 'law'],
      ['Add evidence / edit', 'research.edit', [f.id], 'edit'],
      '-',
      { header: 'Status' },
      ['Mark supported', 'research.setStatus', [f.id, 'supported'], 'verified'],
      ['Mark preliminary', 'research.setStatus', [f.id, 'preliminary'], 'lightbulb'],
      ['Mark contradicted', 'research.setStatus', [f.id, 'contradicted'], 'warning'],
      ['Supersede…', 'research.finding.supersede', [f.id], 'history'],
      '-',
      ['Open Markdown file', 'research.openMirror', [f.id], 'markdown'],
      ['Copy ID', 'research.copyId', [f.id], 'copy'],
    ]),
  ].join('');
  let h = `<div class="page narrow fade-in">${header(`${ic(f.kind === 'failure' ? 'close' : 'lightbulb')}Finding${(f.questions || []).length ? ' · addresses ' + f.questions.map((q: any) => idLink(q.id)).join(' ') : ''}`, f.title, idLink(f.id), pills,
    `<span>${ic('calendar')}created ${time(f.created_at)}</span>${f.updated_at !== f.created_at ? `<span>${ic('edit')}updated ${time(f.updated_at)}</span>` : ''}`, toolbar)}`;
  if (f.superseded_by_card) h += `<div class="banner" style="--c:var(--gray)">${ic('history')}<div class="grow">Superseded by ${idLink(f.superseded_by)} <b>${esc(f.superseded_by_card.title)}</b></div></div>`;
  const lastReview = (f.reviews || [])[0];
  if (f.awaiting_review) h += `<div class="banner" style="--c:var(--blue)">${ic('eye')}<div class="grow"><b>Awaiting an independent review.</b> Written by ${esc(f.author_name || 'an agent')}; it stays preliminary until a human or a different agent reviews it.</div>${btn('Review…', 'research.finding.review', [f.id], { primary: true })}${btn('Ask agent', 'research.dispatch', [f.id, 'verifier'], { icon: 'hubot' })}</div>`;
  else if (lastReview && lastReview.verdict !== 'supported' && f.status !== 'superseded') h += `<div class="banner" style="--c:var(--orange)">${ic('comment-discussion')}<div class="grow"><b>Reviewer ${esc(lastReview.author_name || lastReview.author_type)}: ${esc(lastReview.verdict.replace('_', ' '))}.</b> ${esc(lastReview.summary || '')}</div></div>`;
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
  h += `<section>${h2('Reviews', (f.reviews || []).length, btn('Review…', 'research.finding.review', [f.id], { icon: 'add', ghost: true }))}${reviewList(f.reviews)}</section>`;
  h += disclosure('History &amp; provenance', `<div class="meta" style="margin-bottom:10px">${author(f, true)}</div><div class="card">${timeline(f.events, 10)}</div>`, `f-hist-${f.id}`);
  return h + '</div>';
}

const VERDICT: Record<string, [string, string]> = {
  supported: ['verified-filled', 'green'], contradicted: ['close', 'red'], needs_work: ['comment-discussion', 'orange'],
  pass: ['pass-filled', 'green'], fail: ['error', 'red'],
};

function reviewList(rows: any[] | undefined, emptyText = 'No reviews yet.'): string {
  if (!rows || !rows.length) return empty(emptyText);
  return `<div class="card flush"><div class="list" style="padding:4px">${rows.map((r: any) => {
    const [icon, c] = VERDICT[r.verdict] || ['circle-outline', 'muted'];
    return `<div class="item"><i class="codicon codicon-${icon} lead" style="color:var(--${c})"></i>
      <div class="grow"><div class="hl"><span class="mono small">${esc(r.id)}</span><span class="title">${esc((r.verdict || '').replace('_', ' '))}</span>${author(r)}<span class="muted small">${time(r.created_at)}</span></div>
      ${r.summary ? `<div class="sub">${md(r.summary)}</div>` : ''}</div></div>`;
  }).join('')}</div></div>`;
}

// =============================================================================== question

export function renderQuestion(q: any): string {
  const toolbar = [
    btn('New experiment', 'research.experiment.create', [{ question: q.id }], { icon: 'beaker', primary: true }),
    btn('Sub-question', 'research.question.create', [{ parent: q.id }], { icon: 'add' }),
    btn('Edit', 'research.edit', [q.id], { icon: 'edit' }),
    btn('Report', 'research.report', [q.id], { icon: 'output' }),
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
    btn('Compare with…', 'research.run.compare', [r.id], { icon: 'diff' }),
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
  if (!p) return empty(`No built-in preview for "${a.type}". Open it with an extension that understands the format.`, btn('Open', 'research.openFile', [a.path], { ghost: true }));
  if (p.error) return empty(p.error);
  if (p.kind === 'table' && p.rows) {
    const [head, ...body] = p.rows;
    return `<div class="tablewrap" style="max-height:480px;overflow:auto"><table><thead><tr>${(head || []).map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${body.map((r) => `<tr style="cursor:default">${r.map((c) => `<td class="${/^-?[\d.eE+-]+$/.test(c) ? 'num' : ''}">${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${p.truncated ? '<div class="muted small" style="margin-top:6px">Showing first rows only.</div>' : ''}`;
  }
  if (p.kind === 'markdown') return `<div class="card">${md(p.text)}</div>`;
  return `<pre class="text">${esc(p.text || '')}</pre>${p.truncated ? '<div class="muted small" style="margin-top:6px">Truncated preview.</div>' : ''}`;
}

// =============================================================================== plan

const TASK_STATUS_ICON: Record<string, [string, string]> = {
  done: ['check', 'green'],
  running: ['play', 'accent'],
  blocked: ['circle-slash', 'red'],
  verify: ['eye', 'blue'],
  todo: ['circle-large-outline', 'muted'],
};

export function renderPlan(p: any): string {
  const tasks: any[] = p.tasks || [];
  const total = p.task_count || tasks.length;
  const next = p.next_task;
  const toolbar = [
    btn('Add task', 'research.task.create', [p.id], { icon: 'add', primary: true }),
    next ? btn(`Dispatch next task`, 'research.dispatch', [next.id], { icon: 'hubot', title: `Hand “${next.title}” to an agent` }) : '',
    overflow([
      ['Refine plan with planner agent…', 'research.dispatch', [p.id, 'planner'], 'hubot'],
      ['Export report', 'research.report', [p.id], 'output'],
      ['Edit plan', 'research.edit', [p.id], 'edit'],
      '-',
      { header: 'Status' },
      ['Mark completed', 'research.setStatus', [p.id, 'completed'], 'pass'],
      ['Mark blocked', 'research.setStatus', [p.id, 'blocked'], 'circle-slash'],
      ['Abandon', 'research.setStatus', [p.id, 'abandoned'], 'circle-slash'],
      '-',
      ['Open Markdown file', 'research.openMirror', [p.id], 'markdown'],
      ['Copy ID', 'research.copyId', [p.id], 'copy'],
    ]),
  ].join('');
  const pills = [pill(p.status, { lg: true }), author(p)].join('');
  const meta = `<span>${ic('calendar')}created ${time(p.created_at)}</span>${p.completed_at ? `<span>${ic('pass')}completed ${time(p.completed_at)}</span>` : ''}`;
  let h = `<div class="page narrow fade-in">${header(`${ic('list-ordered')}Plan${p.root_question_id ? ` · answers ${idLink(p.root_question_id)}` : ''}`, p.title, idLink(p.id), pills, meta, toolbar)}`;

  const { svg, legend } = planRing(tasks, 56);
  h += `<div class="plan-summary">${svg}<div class="grow">
      <div style="font-weight:600">${p.tasks_done || 0} of ${total} tasks done</div>${legend}</div></div>`;

  const facts: string[] = [];
  if (p.objective) facts.push(`<div class="k"><span class="ichip" style="--c:var(--accent)">${ic('target')}</span>Objective</div><div class="v">${md(p.objective)}</div>`);
  if (p.success_criteria) facts.push(`<div class="k"><span class="ichip" style="--c:var(--green)">${ic('pass')}</span>Done when</div><div class="v">${md(p.success_criteria)}</div>`);
  if (facts.length) h += `<section><div class="panel"><div class="facts">${facts.join('')}</div></div></section>`;

  // tasks, grouped by what they mean for the researcher
  const byId: Record<string, any> = Object.fromEntries(tasks.map((t) => [t.id, t]));
  const ready = (t: any) => (t.depends_on || []).every((d: string) => byId[d]?.status === 'done');
  const sub = (t: any) => {
    const bits: string[] = [];
    if (t.status === 'blocked') return esc(truncate(t.blockers || 'Blocked', 160));
    if (t.status === 'verify') bits.push('Done — awaiting verification');
    if (t.status === 'running') bits.push(`In progress${t.claimed_by ? ' · ' + esc(String(t.claimed_by).split('/').pop()) : ''}`);
    if (t.status === 'done' && t.result) return esc(truncate(t.result, 140));
    if (t.status === 'todo') {
      const waiting = (t.depends_on || []).filter((d: string) => byId[d]?.status !== 'done');
      bits.push(waiting.length ? `waits for ${waiting.map((d: string) => esc(truncate(byId[d]?.title || d, 32))).join(', ')}` : 'ready');
    }
    if (t.assigned_role && t.status !== 'running') bits.push(esc(t.assigned_role));
    if (t.task_type) bits.push(esc(t.task_type));
    return bits.join(' · ');
  };
  const groups: [string, any[], string][] = [
    ['Now', tasks.filter((t) => ['running', 'verify', 'blocked'].includes(t.status)), 'now'],
    ['Up next', tasks.filter((t) => t.status === 'todo' && ready(t)), 'later'],
    ['Later', tasks.filter((t) => t.status === 'todo' && !ready(t)), 'later'],
  ];
  const done = tasks.filter((t) => t.status === 'done');
  h += `<section>${blkHd('tasklist', 'Tasks', 'var(--accent)', btn('Add', 'research.task.create', [p.id], { icon: 'add', cls: 'sm ghost' }), total)}<div class="panel">`;
  if (tasks.length) {
    for (const [title, ts, cls] of groups) {
      if (!ts.length) continue;
      h += `<div class="group-hd">${esc(title)} <span class="muted" style="font-weight:400">${ts.length}</span></div><ol class="steps">${ts.map((t) => stepRow(t, { sub: sub(t), warn: t.status === 'blocked', ready: !!next && next.id === t.id, cls })).join('')}</ol>`;
    }
    if (done.length) h += `<details class="done-group" data-key="plan-done-${esc(p.id)}"><summary class="step-sum">${ic('chevron-right')}${ic('check')}<span>${done.length} completed</span></summary><ol class="steps">${done.map((t) => stepRow(t, { sub: sub(t) })).join('')}</ol></details>`;
  } else {
    h += empty('No tasks yet. Tasks break the plan into concrete units of work that agents can claim, and checks gate their completion.', btn('Add task', 'research.task.create', [p.id], { primary: true }));
  }
  h += `</div></section>`;
  if (p.context) h += disclosure('Context', `<div class="card">${md(p.context)}</div>`, `plan-ctx-${p.id}`);
  h += disclosure('Activity', `<div class="card">${timeline(p.events, 15)}</div>`, `plan-act-${p.id}`);
  return h + `</div>`;
}

// =============================================================================== task

export function renderTask(t: any): string {
  const deps: any[] = t.dependencies || [];
  const isReady = t.is_ready;
  const checks: string[] = t.checks_desc || [];
  const lastCheck = (t.verifications || [])[0];
  // one obvious next action for the current state; everything else in the overflow menu
  const primary = t.status === 'todo' && isReady ? btn('Dispatch to agent…', 'research.dispatch', [t.id], { icon: 'hubot', primary: true }) + btn('Start myself', 'research.task.start', [t.id], { icon: 'play' })
    : t.status === 'verify' ? (checks.length ? btn('Run checks', 'research.task.verify', [t.id], { icon: 'checklist', primary: true }) : '') + btn('Verify with agent…', 'research.dispatch', [t.id, 'verifier'], { icon: 'hubot' }) + btn('Complete', 'research.task.complete', [t.id], { icon: 'check' })
      : t.status === 'running' ? btn('Complete', 'research.task.complete', [t.id], { icon: 'check', primary: true }) + btn('Add note', 'research.task.note', [t.id], { icon: 'comment' })
        : t.status === 'blocked' ? btn('Add note', 'research.task.note', [t.id], { icon: 'comment', primary: true }) + btn('Unblock', 'research.setStatus', [t.id, 'todo'], { icon: 'debug-continue' })
          : btn('Add note', 'research.task.note', [t.id], { icon: 'comment' });
  const toolbar = primary + overflow([
    ['Add progress note…', 'research.task.note', [t.id], 'comment'],
    ...(checks.length ? [['Run checks', 'research.task.verify', [t.id], 'checklist'] as [string, string, any[], string]] : []),
    ...(t.status === 'running' ? [['Block…', 'research.task.block', [t.id], 'circle-slash'] as [string, string, any[], string], ['Release (hand back)', 'research.task.release', [t.id], 'debug-step-back'] as [string, string, any[], string]] : []),
    ['Edit task', 'research.edit', [t.id], 'edit'],
    '-',
    { header: 'Status' },
    ['Mark done', 'research.setStatus', [t.id, 'done'], 'check'],
    ['Mark in progress', 'research.setStatus', [t.id, 'running'], 'play'],
    ['Mark blocked', 'research.setStatus', [t.id, 'blocked'], 'circle-slash'],
    ['Mark awaiting verification', 'research.setStatus', [t.id, 'verify'], 'eye'],
    ['Reset to upcoming', 'research.setStatus', [t.id, 'todo'], 'circle-large-outline'],
    '-',
    ['Show agent brief', 'research.task.brief', [t.id], 'hubot'],
    ['Open Markdown file', 'research.openMirror', [t.id], 'markdown'],
    ['Copy ID', 'research.copyId', [t.id], 'copy'],
  ]);
  const [, color, label] = TASK_STATE[t.status] || TASK_STATE.todo;
  const pills = [`<span class="pill lg" style="--c:${color}">${ic((TASK_STATE[t.status] || TASK_STATE.todo)[0])}${esc(label)}</span>`,
    isReady && t.status === 'todo' ? `<span class="pill lg c-green">${ic('arrow-right')}ready</span>` : '',
    t.task_type ? `<span class="tag">${esc(t.task_type)}</span>` : ''].join('');
  const who = t.claimed_by && t.status === 'running' ? `<span>${ic('person')}${esc(t.claimed_by)} · claimed ${time(t.claimed_at)}</span>` : t.assigned_role ? `<span>${ic('person')}${esc(t.assigned_role)}</span>` : '';
  const meta = [who, t.started_at ? `<span>${ic('play')}started ${time(t.started_at)}</span>` : '', t.completed_at ? `<span>${ic('check')}completed ${time(t.completed_at)}</span>` : ''].filter(Boolean).join('');
  let h = `<div class="page narrow fade-in">${header(`${ic('list-ordered')}<a data-open="${esc(t.plan_id)}">${esc(t.plan?.title ? truncate(t.plan.title, 60) : t.plan_id)}</a> ${ic('chevron-right')} Task`, t.title, idLink(t.id), pills, meta, toolbar)}`;

  // state banners: what happened, why it matters, what to do
  if (t.status === 'blocked') h += `<div class="banner" style="--c:var(--red)">${ic('circle-slash')}<div class="grow"><b>Blocked.</b> ${esc(t.blockers || 'No reason recorded.')}</div></div>`;
  if (t.stale_hours) h += `<div class="banner" style="--c:var(--orange)">${ic('watch')}<div class="grow"><b>No activity for ${esc(t.stale_hours)}h</b>${t.claimed_by ? ` (claimed by ${esc(t.claimed_by)})` : ''}. Check on it, or release it so another agent can continue.</div>${btn('Release', 'research.task.release', [t.id])}</div>`;
  if (lastCheck && lastCheck.verdict === 'fail' && t.status !== 'done') h += `<div class="banner" style="--c:var(--red)">${ic('error')}<div class="grow"><b>Checks failing:</b> ${esc(lastCheck.summary)}</div>${btn('Re-run checks', 'research.task.verify', [t.id])}</div>`;
  if (t.status === 'verify' && !(lastCheck && lastCheck.verdict === 'fail')) h += `<div class="banner" style="--c:var(--blue)">${ic('eye')}<div class="grow"><b>Work is done and awaiting verification.</b> ${checks.length ? 'Run the checks, or ask an independent verifier agent.' : 'No automatic checks — review the outputs, then complete it.'}</div></div>`;

  // level 1: goal + result
  const facts: string[] = [];
  facts.push(`<div class="k"><span class="ichip" style="--c:var(--accent)">${ic('target')}</span>Goal</div><div class="v">${t.goal ? md(t.goal) : '<span class="muted">No goal written.</span>'}</div>`);
  if (t.result) facts.push(`<div class="k"><span class="ichip" style="--c:var(--green)">${ic('pass')}</span>Result</div><div class="v">${md(t.result)}</div>`);
  if (t.acceptance_criteria) facts.push(`<div class="k"><span class="ichip" style="--c:var(--green)">${ic('checklist')}</span>Done when</div><div class="v">${md(t.acceptance_criteria)}</div>`);
  if (deps.length) facts.push(`<div class="k"><span class="ichip" style="--c:var(--muted)">${ic('git-merge')}</span>After</div><div class="v"><div class="dep-chips">${deps.map((d: any) => {
    const [di, dc] = TASK_STATUS_ICON[d.status] || ['circle-outline', 'muted'];
    return `<span class="dep-chip" style="--c:var(--${dc})" data-open="${esc(d.id)}" tabindex="0" role="link" title="${esc(d.status)}">${ic(di)}${esc(truncate(d.title, 48))}</span>`;
  }).join('')}</div></div>`);
  h += `<section><div class="panel"><div class="facts">${facts.join('')}</div></div></section>`;

  // level 2: progress + checks
  const progress: any[] = t.progress || [];
  h += `<section>${blkHd('comment-discussion', 'Progress', 'var(--accent)', btn('Add note', 'research.task.note', [t.id], { icon: 'add', cls: 'sm ghost' }), progress.length || undefined)}
    ${progress.length ? `<div class="panel notes">${progress.map((n: any) => `<div class="note">${ic(n.author_type === 'agent' ? 'hubot' : 'person')}<div><div class="nh">${esc(String(n.author_name || n.author_type || 'you').split('/').pop())} · ${time(n.ts)}</div>${md(n.text)}</div></div>`).join('')}</div>`
      : empty('No notes yet. Agents leave notes as they work, so whoever continues knows where things stand.')}</section>`;
  const vcolor = lastCheck ? (lastCheck.verdict === 'pass' ? 'var(--green)' : 'var(--red)') : 'var(--muted)';
  if (checks.length) {
    h += `<section>${blkHd('checklist', 'Checks', 'var(--green)', lastCheck ? `<span class="aside muted small">last run ${time(lastCheck.created_at)} · <b style="color:${vcolor}">${esc(lastCheck.verdict)}</b></span>` : '<span class="aside muted small">not run yet</span>')}
      <div class="panel checks">${checks.map((c) => `<div class="ck" style="--c:${vcolor}">${ic(lastCheck ? (lastCheck.verdict === 'pass' ? 'pass' : 'circle-large-outline') : 'circle-large-outline')}<span class="mono small">${esc(c)}</span></div>`).join('')}</div></section>`;
  }

  // level 3: definition, skills, related, history
  const def = [field('Inputs', t.inputs), field('Expected outputs', t.expected_outputs), field('Verification', t.verification), field('Notes', t.notes)].join('');
  const missing = [!t.inputs && 'inputs', !t.expected_outputs && 'expected outputs', !t.acceptance_criteria && 'acceptance criteria'].filter(Boolean);
  const skills: any[] = t.skills_info || [];
  const related = [t.plan && entityRow(t.plan), t.question && entityRow(t.question), t.experiment && entityRow(t.experiment)].filter(Boolean).join('');
  const arts = t.artifact_cards || [];
  let deeper = '';
  if (def || missing.length) deeper += `<section style="margin-top:6px">${def ? `<div class="card">${def}</div>` : ''}${missing.length ? `<div class="def-missing" style="margin-top:${def ? 8 : 0}px">${ic('info')}Not specified: ${esc(missing.join(', '))}.${btn('Edit task', 'research.edit', [t.id], { cls: 'sm link' })}</div>` : ''}</section>`;
  if (related || arts.length) deeper += `<section>${h2('Related')}<div class="card flush"><div class="list">${related}${arts.map((a: any) => entityRow(a)).join('')}</div></div></section>`;
  if (skills.length) deeper += `<section>${h2('Skills agents load', skills.length)}<div class="card flush"><div class="list">${skills.map((k: any) => `<div class="item"><i class="codicon codicon-mortar-board lead" style="color:var(--${k.missing ? 'red' : 'purple'})"></i><div class="grow"><div class="hl"><span class="title">${esc(k.name)}</span><span class="muted small">${esc(k.why)}</span></div>${k.description ? `<div class="sub">${esc(truncate(k.description, 140))}</div>` : ''}${k.missing ? '<div class="sub" style="color:var(--red)">not installed</div>' : ''}</div></div>`).join('')}</div></div></section>`;
  if ((t.verifications || []).length) deeper += `<section>${h2('Verification history', t.verifications.length)}${reviewList(t.verifications)}</section>`;
  h += disclosure('Definition, related items &amp; skills', deeper, `task-def-${t.id}`);
  h += disclosure('Activity &amp; provenance', `<div class="meta" style="margin-bottom:10px"><span>${ic('calendar')}created ${time(t.created_at)}</span>${author(t, true)}</div><div class="card">${timeline(t.events, 10)}</div>`, `task-act-${t.id}`);
  return h + `</div>`;
}

// =============================================================================== run comparison

function cell(v: any): string {
  if (v === null || v === undefined || v === '') return '<span class="muted">—</span>';
  return esc(typeof v === 'number' ? fmtVal(v) : String(v));
}

export function renderCompare(c: any, ctx: RenderCtx): string {
  const A = c.a.id, B = c.b.id;
  const changed = (c.changed_parameters || []).length;
  let h = `<div class="page fade-in">${header(`${ic('diff')}Compare runs`, `${A} vs ${B}`, `${idLink(A)} ${idLink(B)}`,
    [c.same_commit ? '<span class="pill lg c-green">same commit</span>' : '<span class="pill lg c-orange">different commits</span>',
      `<span class="pill lg c-gray">${changed} parameter${changed === 1 ? '' : 's'} changed</span>`].join(''),
    `<span>${ic('beaker')}${idLink(c.a.experiment_id)} · ${idLink(c.b.experiment_id)}</span>`,
    btn('Swap', 'research.run.compare', [B, A], { icon: 'arrow-swap', ghost: true }))}`;
  const table = (title: string, rows: any[], withDelta = false) => rows.length ? `<section>${h2(title, rows.length)}<div class="tablewrap"><table><thead><tr><th></th><th>${idLink(A)}</th><th>${idLink(B)}</th>${withDelta ? '<th>Δ</th>' : ''}</tr></thead><tbody>${
    rows.map((r: any) => {
      const diff = withDelta ? r.delta != null && r.delta !== 0 : r.same === false;
      const d = withDelta && r.delta != null ? `<td class="mono" style="color:var(--${r.delta < 0 ? 'green' : r.delta > 0 ? 'orange' : 'muted'})">${r.delta > 0 ? '+' : ''}${esc(fmtVal(r.delta))}${r.rel != null ? ` <span class="muted small">(${(r.rel * 100).toFixed(1)}%)</span>` : ''}</td>` : withDelta ? '<td></td>' : '';
      return `<tr${diff ? ' class="hl-row"' : ''}><td><b>${esc(r.name)}</b>${r.unit ? ` <span class="muted small">${esc(r.unit)}</span>` : ''}</td><td class="mono">${cell(r.a)}</td><td class="mono">${cell(r.b)}</td>${d}</tr>`;
    }).join('')}</tbody></table></div></section>` : '';
  h += table('Metrics', c.metrics || [], true);
  h += table('Parameters', c.parameters || []);
  h += table('Run details', (c.meta || []).filter((m: any) => !m.same || ['status', 'commit'].includes(m.name)));
  if (c.code_diff) h += `<section>${h2('Code changes between the commits')}<pre class="log">${esc(c.code_diff.stat || '')}</pre>${c.code_diff.patch ? `<details><summary class="muted small">full patch</summary><pre class="log">${esc(c.code_diff.patch)}</pre></details>` : ''}</section>`;
  for (const side of ['a', 'b']) {
    if (c.uncommitted?.[side]) h += `<section>${h2(`${c[side].id} ran with uncommitted changes`)}<details><summary class="muted small">git.diff</summary><pre class="log">${esc(c.uncommitted[side])}</pre></details></section>`;
  }
  const arts: any[] = c.artifacts || [];
  if (arts.length) {
    h += `<section>${h2('Outputs side by side', arts.length)}`;
    for (const x of arts) {
      const thumb = (a: any) => !a ? `<div class="empty-card">${ic('circle-slash')}<span class="grow">not produced</span></div>`
        : a.exists && (a.type === 'image' || IMG.test(a.path || '')) ? `<a data-open="${esc(a.id)}"><img src="${ctx.uri(a.abspath)}" style="max-width:100%;max-height:320px;border-radius:6px" alt=""></a>`
          : entityRow({ id: a.id, type: 'artifact', title: a.name, path: a.path });
      h += `<div class="card"><div class="row" style="gap:8px;margin-bottom:6px"><b>${esc(x.name)}</b><span class="muted small">${esc(x.type)}</span>${x.same_hash ? '<span class="pill c-gray">identical</span>' : ''}</div>
        <div class="grid2">${thumb(x.a)}${thumb(x.b)}</div></div>`;
    }
    h += `</section>`;
  }
  return h + '</div>';
}
