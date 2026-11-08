/** Sidebar overview and the plan/step helpers shared with the entity pages. */
import { esc, truncate } from '../util';
import { btn, ic, idLink, ring } from './ui';

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

// ---------------------------------------------------------------- blocks

// Project-wide: independent of the selected focus, so a blocked task in any active plan stays discoverable.
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

