/**
 * Research Home — the primary human landing page.
 *
 * Designed to answer in 10-20 seconds:
 * 1. What are we trying to understand?
 * 2. What do we currently believe?
 * 3. What changed recently?
 * 4. What is being worked on right now?
 * 5. What failed?
 * 6. Is anything blocked?
 * 7. What needs my attention?
 * 8. What happens next?
 * 9. Where are the important results?
 */
import { ago, esc, truncate } from '../util';
import { author, btn, confidence, ic, idLink, md, pill, time, timeline } from './ui';

const TASK_ICONS: Record<string, string> = {
  done: 'check',
  running: 'sync~spin',
  blocked: 'error',
  verify: 'eye',
  todo: 'circle-large-outline',
};

const TASK_COLORS: Record<string, string> = {
  done: 'var(--green)',
  running: 'var(--yellow)',
  blocked: 'var(--red)',
  verify: 'var(--blue)',
  todo: 'var(--muted)',
};

function taskIcon(status: string): string {
  const icon = TASK_ICONS[status] || 'circle-outline';
  const color = TASK_COLORS[status] || 'var(--muted)';
  return `<i class="codicon codicon-${icon}" style="color:${color}"></i>`;
}

function findingItem(f: any, kind: 'supported' | 'preliminary' | 'failed' | 'contradicted'): string {
  const icon = kind === 'supported' ? 'verified-filled' : kind === 'preliminary' ? 'lightbulb' : 'close';
  const color = kind === 'supported' ? 'var(--green)' : kind === 'preliminary' ? 'var(--yellow)' : 'var(--red)';
  const conf = f.confidence ? confidence(f.confidence, color) : '';
  const ev = f.evidence_count ? `<span class="finding-meta">${f.evidence_count} evidence</span>` : '';

  return `<div class="insight-item" data-open="${esc(f.id)}" tabindex="0">
    <i class="codicon codicon-${icon}" style="color:${color}"></i>
    <div class="insight-content">
      <div class="insight-title">${esc(f.title)}</div>
      ${f.statement ? `<div class="insight-statement">${esc(truncate(f.statement, 140))}</div>` : ''}
      <div class="insight-meta">${idLink(f.id)}${conf}${ev}</div>
    </div>
  </div>`;
}

function attentionItem(item: any): string {
  const icons: Record<string, string> = {
    task_blocked: 'error',
    verification_needed: 'eye',
    synthesis_required: 'warning',
    failed_run: 'close',
  };
  const colors: Record<string, string> = {
    task_blocked: 'var(--red)',
    verification_needed: 'var(--blue)',
    synthesis_required: 'var(--orange)',
    failed_run: 'var(--red)',
  };
  const icon = icons[item.kind] || 'warning';
  const color = colors[item.kind] || 'var(--orange)';

  return `<div class="attention-item" data-open="${esc(item.id)}" tabindex="0">
    <i class="codicon codicon-${icon}" style="color:${color}"></i>
    <div class="attention-content">
      <div class="attention-title">${esc(item.title)}</div>
      <div class="attention-message">${esc(item.message)}</div>
    </div>
    <span class="attention-id">${idLink(item.id)}</span>
  </div>`;
}

function outputThumb(output: any, uri: (p: string) => string): string {
  const isImg = output.type === 'image';
  const iconMap: Record<string, string> = { image: 'file-media', table: 'table' };

  return `<div class="output-thumb" data-open="${esc(output.id)}" title="${esc(output.path)}">
    <div class="output-preview">
      ${isImg && uri(output.path) ? `<img src="${uri(output.path)}" loading="lazy" alt="">` : `<i class="codicon codicon-${iconMap[output.type] || 'file'}"></i>`}
    </div>
    <div class="output-caption">
      <div class="output-name">${esc(truncate(output.name, 20))}</div>
      <div class="output-reason">${esc(output.reason)}</div>
    </div>
  </div>`;
}

export interface HomeCtx {
  uri(path: string): string;
}

export function renderHome(r: any, ctx: HomeCtx): string {
  const p = r.project || {};
  const g = r.git || {};
  const la = r.last_activity;
  const plan = r.active_plan;
  const focus = r.current_focus;
  const tasks = r.tasks || [];
  const attention = r.attention_items || [];
  const findings = r.findings_by_status || {};
  const outputs = r.key_outputs || [];
  const events = r.recent_events || [];

  // ---------------------------------------------------------------- hero
  let html = `<div class="home fade-in">
    <header class="home-header">
      <div class="home-project">${ic('beaker')} ${esc(p.name || 'Research')}</div>
      <h1 class="home-goal">${p.goal ? esc(p.goal) : `<span class="muted">No project goal set.</span> <a data-cmd="research.project.setGoal" data-args="[]">Set goal</a>`}</h1>
      <div class="home-meta">
        ${la ? `<span>${ic('clock')} Updated <b>${esc(ago(la.ts))}</b>${la.author_type === 'agent' ? ` by ${esc(la.author_name || 'agent')}` : ''}</span>` : ''}
        ${g.branch ? `<span class="mono">${ic('git-branch')} ${esc(g.branch)} @ ${esc((g.commit || '').slice(0, 7))}${g.dirty ? ' <span class="uncommitted">uncommitted</span>' : ''}</span>` : ''}
      </div>
    </header>`;

  // ---------------------------------------------------------------- current focus
  if (focus) {
    html += `<section class="focus-section">
      <h2 class="section-label">Current Focus</h2>
      <div class="focus-card">
        <div class="focus-question">${esc(focus.question || 'No current question')}</div>
        ${focus.understanding ? `<div class="focus-field"><span class="focus-label">Current understanding</span><div class="focus-value">${md(truncate(focus.understanding, 400))}</div></div>` : ''}
        ${focus.next_step ? `<div class="focus-field"><span class="focus-label">Next</span><div class="focus-value">${esc(focus.next_step)}</div></div>` : ''}
        ${focus.blocker ? `<div class="focus-field focus-blocker"><span class="focus-label">${ic('error')} Blocker</span><div class="focus-value">${esc(truncate(focus.blocker, 200))}</div></div>` : `<div class="focus-no-blocker">${ic('pass')} No blockers</div>`}
      </div>
    </section>`;
  }

  // ---------------------------------------------------------------- active plan
  if (plan) {
    const done = plan.tasks_done || 0;
    const total = plan.task_count || 0;
    const pct = total > 0 ? Math.round((done / total) * 100) : 0;

    html += `<section class="plan-section">
      <div class="plan-header">
        <h2 class="section-label">Active Plan</h2>
        <span class="plan-progress">${done} / ${total}</span>
      </div>
      <div class="plan-card" data-open="${esc(plan.id)}">
        <div class="plan-title">${esc(plan.title)}</div>
        <div class="plan-bar"><div class="plan-bar-fill" style="width:${pct}%"></div></div>
        <div class="plan-tasks">`;

    for (const t of tasks.slice(0, 10)) {
      const isActive = t.status === 'running';
      const isDone = t.status === 'done';
      html += `<div class="plan-task ${isDone ? 'done' : ''} ${isActive ? 'active' : ''}" data-open="${esc(t.id)}" title="${esc(t.title)}">
        ${taskIcon(t.status)}
        <span class="task-title">${esc(truncate(t.title, 40))}</span>
        ${t.assigned_role ? `<span class="task-role">${esc(t.assigned_role)}</span>` : ''}
      </div>`;
    }

    if (tasks.length > 10) {
      html += `<div class="plan-task muted">+ ${tasks.length - 10} more</div>`;
    }

    html += `</div>
        <div class="plan-actions">
          <a class="plan-link" data-open="${esc(plan.id)}">${ic('link-external')} Open plan</a>
        </div>
      </div>
    </section>`;
  } else {
    html += `<section class="plan-section">
      <h2 class="section-label">Active Plan</h2>
      <div class="empty-plan">
        <p>No active plan. Plans help you structure research objectives into concrete tasks.</p>
        ${btn('Create Plan', 'research.plan.create', [], { icon: 'add', ghost: true })}
      </div>
    </section>`;
  }

  // ---------------------------------------------------------------- two-column layout
  html += `<div class="home-grid">`;

  // ---------------------------------------------------------------- latest insights
  html += `<section class="insights-section">
    <h2 class="section-label">Latest Insights</h2>`;

  const supported = findings.supported || [];
  const preliminary = findings.preliminary || [];
  const failed = findings.failed || [];
  const contradicted = findings.contradicted || [];

  if (supported.length || preliminary.length || failed.length || contradicted.length) {
    if (supported.length) {
      html += `<div class="insight-group">
        <div class="insight-group-label">${ic('verified-filled')} Supported</div>
        ${supported.slice(0, 3).map((f: any) => findingItem(f, 'supported')).join('')}
      </div>`;
    }
    if (preliminary.length) {
      html += `<div class="insight-group">
        <div class="insight-group-label">${ic('lightbulb')} Preliminary</div>
        ${preliminary.slice(0, 2).map((f: any) => findingItem(f, 'preliminary')).join('')}
      </div>`;
    }
    if (failed.length) {
      html += `<div class="insight-group insight-group-failed">
        <div class="insight-group-label">${ic('close')} Failed Direction</div>
        ${failed.slice(0, 2).map((f: any) => findingItem(f, 'failed')).join('')}
      </div>`;
    }
  } else {
    html += `<div class="empty-insights">No findings yet. Findings capture the reusable conclusions from experiments.</div>`;
  }
  html += `</section>`;

  // ---------------------------------------------------------------- needs attention
  html += `<section class="attention-section">
    <h2 class="section-label">Needs Your Attention <span class="attention-count">${attention.length}</span></h2>`;

  if (attention.length) {
    html += `<div class="attention-list">${attention.slice(0, 5).map(attentionItem).join('')}</div>`;
  } else {
    html += `<div class="no-attention">${ic('pass')} No intervention needed. Agents can continue with the current plan.</div>`;
  }
  html += `</section>`;

  html += `</div>`; // close home-grid

  // ---------------------------------------------------------------- key outputs
  if (outputs.length) {
    html += `<section class="outputs-section">
      <h2 class="section-label">Key Outputs</h2>
      <div class="outputs-grid">${outputs.map((o: any) => outputThumb(o, ctx.uri)).join('')}</div>
    </section>`;
  }

  // ---------------------------------------------------------------- recent activity
  html += `<section class="activity-section">
    <h2 class="section-label">Recent Activity</h2>
    <div class="activity-compact">${timeline(events, 8)}</div>
  </section>`;

  // ---------------------------------------------------------------- quick actions
  html += `<div class="home-actions">
    ${btn('Create Checkpoint', 'research.checkpoint.create', [], { icon: 'bookmark', primary: true })}
    ${btn('New Question', 'research.question.create', [], { icon: 'question' })}
    ${btn('New Experiment', 'research.experiment.create', [], { icon: 'beaker' })}
    ${plan ? btn('Add Task', 'research.task.create', [plan.id], { icon: 'add' }) : ''}
  </div>`;

  html += `</div>`; // close .home
  return html;
}
