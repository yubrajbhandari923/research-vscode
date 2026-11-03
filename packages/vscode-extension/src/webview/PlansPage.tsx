import { useEffect, useState } from 'react';
import { Badge, Banner, Dialog, EmptyState, Icon, PageHeader, Progress, Section, State, useAsync } from './common';
import type { Host } from './host';

/** What each supervisor phase means for the researcher. "verifying" and "waiting_hpc" are real work in flight; "running" only appears
 *  while the recorded agent process exists. */
const PHASE: Record<string, { label: string; kind: string; hint: string }> = {
  waiting: { label: 'waiting', kind: '', hint: 'Waits for prerequisite tasks to be verified' },
  ready: { label: 'ready', kind: 'info', hint: 'Can start; waiting for a free agent slot or workspace' },
  awaiting_approval: { label: 'needs your approval', kind: 'warn', hint: 'The plan says a human must approve this task before it starts' },
  running: { label: 'agent running', kind: 'info', hint: 'An agent process is working in its workspace' },
  waiting_hpc: { label: 'waiting for HPC', kind: 'info', hint: 'The agent finished; its computational runs are still queued or running' },
  verifying: { label: 'verifying', kind: 'info', hint: 'Research OS is running the checks; the agent saying it is done does not count' },
  repair_pending: { label: 'repair queued', kind: 'warn', hint: 'Verification failed; a bounded repair attempt is next' },
  attention: { label: 'needs attention', kind: 'bad', hint: 'The supervisor stopped on purpose: read the reason' },
  blocked: { label: 'blocked', kind: 'bad', hint: 'Limits reached or a human decision is needed' },
  verified: { label: 'verified', kind: 'ok', hint: 'Checks passed; the task is complete' },
  retired: { label: 'retired', kind: '', hint: 'Removed from the plan; its history is kept' },
};
const live = (ph: string) => ['running', 'verifying', 'waiting_hpc'].includes(ph);

export function PlansPage({ host }: { host: Host }) {
  const list = useAsync<any[]>(() => host.call('managed_plans'), []);
  const [sel, setSel] = useState<string[]>([]);
  const toggleSel = (k: string) => setSel((v) => (v.includes(k) ? v.filter((x) => x !== k) : [...v, k]));
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { const t = setInterval(() => { if (document.visibilityState !== 'hidden') list.reload(); }, 4000); return () => clearInterval(t); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const act = async (fn: () => Promise<any>, ok: string) => { try { await fn(); setMsg({ ok: true, text: ok }); } catch (e: any) { setMsg({ ok: false, text: e.message }); } list.reload(); };
  return (
    <div className="page-body">
      <PageHeader title="Plans" sub="A plan is a YAML file that says what should be done. The supervisor runs it: it starts agents, verifies their work, repairs within limits, and stops for you when it must."
        actions={<><button type="button" onClick={() => host.run('research.plan.yaml.import')}><Icon name="plus" /> Import YAML</button>
          <button type="button" onClick={() => host.run('research.plan.yaml.new')}>New from template</button></>} />
      {msg && <Banner kind={msg.ok ? 'ok' : 'bad'} role="status" onDismiss={() => setMsg(null)}>{msg.text}</Banner>}
      <State a={list}>{(plans) => plans.length === 0 ? (
        <EmptyState icon="tasks" title="No YAML plans yet." actions={<><button type="button" className="primary" onClick={() => host.run('research.plan.yaml.new')}>Create from template</button>
          <button type="button" onClick={() => host.run('research.plan.yaml.import')}>Import a plan</button></>}>
          Ask a planning AI (Claude, ChatGPT) to write a plan in the Research OS YAML format, save it, and import it here. You can edit the file and apply it again at any time without losing results.</EmptyState>
      ) : <>
        <div className="cards">{plans.map((pl) => (
          <section key={pl.key} className={'card' + (sel.includes(pl.key) ? ' sel' : '')} aria-label={pl.title || pl.key} style={{ margin: '8px 0', padding: 12 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <div><b>{pl.title || pl.key}</b> <span className="muted">{pl.id} · key <code>{pl.key}</code> · revision {pl.revision}</span></div>
              <Badge kind={pl.state === 'running' ? 'info' : pl.state === 'completed' ? 'ok' : pl.state === 'paused' ? 'warn' : ''} live={pl.state === 'running'}>{pl.state.replace('_', ' ')}</Badge>
            </div>
            <Progress value={Number(pl.progress.split('/')[0])} max={Number(pl.progress.split('/')[1])} label={`${pl.progress} tasks verified`} ok={pl.state === 'completed'} />
            <div className="row">
              {['not_run', 'stopped'].includes(pl.state) && <button type="button" className="primary" onClick={() => act(() => host.call('plan_control', { plan: pl.key, action: 'run' }), `${pl.key} is running`)}><Icon name="play" /> Run plan</button>}
              {pl.state === 'running' && <button type="button" onClick={() => act(() => host.call('plan_control', { plan: pl.key, action: 'pause' }), `${pl.key} paused; running agents finish their attempt`)}>Pause</button>}
              {['paused', 'blocked', 'attention'].includes(pl.state) && <button type="button" className="primary" onClick={() => act(() => host.call('plan_control', { plan: pl.key, action: 'resume' }), `${pl.key} resumed`)}>Resume</button>}
              {['running', 'paused'].includes(pl.state) && <button type="button" onClick={() => act(() => host.call('plan_control', { plan: pl.key, action: 'stop' }), `${pl.key} stopped (history kept)`)}>Stop</button>}
              <button type="button" onClick={() => host.run('research.plan.yaml.open', pl.key)}>Open YAML</button>
              <button type="button" aria-expanded={sel.includes(pl.key)} onClick={() => toggleSel(pl.key)}>{sel.includes(pl.key) ? 'Hide tasks' : 'Show tasks'}</button>
              {pl.pending_proposals > 0 && <Badge kind="warn">{pl.pending_proposals} proposal(s) need approval</Badge>}
            </div>
          </section>))}
        </div>
        {sel.map((k) => <PlanDetail key={k} host={host} plan={k} act={act} />)}
      </>}</State>
    </div>
  );
}

function PlanDetail({ host, plan, act }: { host: Host; plan: string; act: (fn: () => Promise<any>, ok: string) => Promise<void>; }) {
  const a = useAsync<any>(() => host.call('supervisor_status', { plan }), [plan]);
  const [detail, setDetail] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  useEffect(() => { const t = setInterval(() => { if (document.visibilityState !== 'hidden') { a.reload(); setNow((n) => n + 1); } }, 3000); return () => clearInterval(t); }, [plan]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <State a={a}>{(s) => <>
      <Section id="tasks" title={`Tasks of ${s.plan}`} count={s.tasks.length} hint={<>Phase is what Research OS observed, not what an agent claimed. Supervisor process: {s.daemon.running ? <b>running (pid {s.daemon.pid})</b> : <b>not running</b>}{' '}
        <button type="button" className="sm" onClick={() => act(() => host.call('supervisor_daemon', { action: s.daemon.running ? 'stop' : 'start' }), s.daemon.running ? 'Supervisor stopped' : 'Supervisor started')}>{s.daemon.running ? 'Stop supervisor' : 'Start supervisor'}</button></>}>
        <div className="tbl-wrap"><table><caption className="sr">Tasks and their supervised state</caption>
          <thead><tr><th scope="col">Task</th><th scope="col">Phase</th><th scope="col" className="hide-narrow">Agent</th><th scope="col">Attempts</th><th scope="col" className="hide-narrow">Depends on</th><th scope="col"><span className="sr">Actions</span></th></tr></thead>
          <tbody>{s.tasks.map((t: any) => { const ph = PHASE[t.phase] || PHASE.waiting; return (
            <tr key={t.id}>
              <td><button type="button" className="link idlink" onClick={() => host.openEntity(t.id)}>{t.id}</button> <b>{t.title}</b>{t.experiment && <span className="sub">{t.experiment}</span>}
                {t.reason && <span className="sub" title={t.reason}>{t.reason.length > 170 ? t.reason.slice(0, 170) + '… (Details has the rest)' : t.reason}</span>}</td>
              <td title={ph.hint}><Badge kind={ph.kind} live={live(t.phase)}>{ph.label}</Badge>{t.execution?.state && live(t.phase) && <span className="sub">process {t.execution.state}{t.execution.alive ? '' : ' (not confirmed)'}</span>}
                {t.deferred && <span className="sub">spec change queued for next attempt</span>}</td>
              <td className="hide-narrow">{t.role}{t.profile ? <span className="sub">{t.profile}</span> : null}</td>
              <td>{t.attempts || '-'}{t.voided_attempts > 0 && <span className="sub" title="Kept as evidence, not counted against the budget">+{t.voided_attempts} voided</span>}{t.execution?.verification && !t.execution.verification.passed && <span className="sub">last check failed</span>}</td>
              <td className="hide-narrow">{t.depends_on.join(', ') || '-'}</td>
              <td className="min"><button type="button" className="sm" onClick={() => setDetail(t.id)}>Details</button>
                {t.phase === 'awaiting_approval' && <button type="button" className="sm primary" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'approve' }), `${t.id} approved`)}>Approve</button>}
                {t.phase === 'attention' && <button type="button" className="sm" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'retry' }), `${t.id} queued for another attempt`)}>Retry</button>}
                {t.phase === 'blocked' && <button type="button" className="sm" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'unblock' }), `${t.id} unblocked`)}>Unblock</button>}
                {['blocked', 'attention'].includes(t.phase) && t.attempts > 0 && <button type="button" className="sm" title="Run the checks again on the work as it stands (no new agent)" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'completed', note: 're-verify requested' }), `${t.id}: verifying again`)}>Re-verify</button>}
                {['blocked', 'attention'].includes(t.phase) && t.attempts > 0 && <button type="button" className="sm" title="Use when the failure was Research OS's fault, not the agent's: the old attempts stay as evidence but stop counting" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'void', note: 'voided by the researcher: the failure was not the agent\'s' }), `${t.id}: attempts voided, queued again`)}>Retry fresh</button>}</td>
            </tr>); })}</tbody></table></div>
        <p className="muted small">Attempts {s.budgets.attempts || 0} of {s.budgets.max_total_attempts} · wall time {Math.round((s.budgets.wall_seconds || 0) / 60)} of {s.budgets.max_wall_minutes} min · limits are in Settings → Supervisor</p>
      </Section>
      {s.proposals.length > 0 && <Section title="Proposed plan changes" count={s.proposals.length} hint="Agents propose; only additions of new tasks may apply automatically. Everything else waits for you.">
        {s.proposals.map((pr: any) => (
          <div key={pr.id} className="card" style={{ margin: '6px 0', padding: 10 }}>
            <div><b>{pr.id}</b> <Badge kind={pr.classification === 'scientific' ? 'warn' : 'info'}>{pr.classification}</Badge> <span className="muted">from {pr.task || 'an agent'}</span></div>
            <p>{pr.rationale}</p><pre className="diff" tabIndex={0}>{pr.patch}</pre>
            <div className="row"><button type="button" className="primary" onClick={() => act(() => host.call('proposal_decide', { id: pr.id, approve: true }), `${pr.id} applied through the plan engine`)}>Approve and apply</button>
              <button type="button" onClick={() => act(() => host.call('proposal_decide', { id: pr.id, approve: false }), `${pr.id} rejected`)}>Reject</button></div>
          </div>))}
      </Section>}
      <Promotion host={host} plan={plan} act={act} />
      {detail && <AttemptDialog host={host} task={detail} onClose={() => setDetail(null)} tick={now} />}
    </>}</State>
  );
}

function Promotion({ host, plan, act }: { host: Host; plan: string; act: (fn: () => Promise<any>, ok: string) => Promise<void> }) {
  const a = useAsync<any>(() => host.call('promotion_hint', { plan }), [plan]);
  return <State a={a}>{(d) => d.workspaces.length === 0 ? null : (
    <Section title="Code worktrees" hint="Validated code reaches main only through the existing promotion review; nothing merges automatically.">
      {d.workspaces.map((w: any) => <p key={w.workspace}><b>{w.experiment}</b> on <code>{w.branch}</code>{w.ahead ? ` · ${w.ahead} commit(s) ahead` : ''}{w.uncommitted ? ' · uncommitted changes' : ''}{' '}
        <button type="button" className="link" onClick={() => host.run('research.workspace.open', w.path || '')}>open</button></p>)}
      <p className="muted small">Verified tasks are committed on their experiment branch. Prepare candidates from those commits, then review them in the Work tab (tests, review, approval, then merge).</p>
      <button type="button" onClick={() => act(async () => { return host.call('plan_promote', { plan }); }, 'Promotion candidate(s) prepared: continue in the Work tab')}>Prepare promotion from verified work</button>
    </Section>)}</State>;
}

function AttemptDialog({ host, task, onClose, tick }: { host: Host; task: string; onClose: () => void; tick: number }) {
  const [vmsg, setVmsg] = useState('');
  const [n, setN] = useState(0);
  const a = useAsync<any>(() => host.call('attempt_detail', { task, attempt: n }), [task, n, tick]);
  return (
    <Dialog title={`${task}: attempt history and context`} onClose={onClose} actions={<button type="button" className="primary" data-autofocus onClick={onClose}>Close</button>}>
      <State a={a}>{(d) => !d.attempt ? <p className="muted">No attempt has been made yet.</p> : <>
        <div className="row">{Array.from({ length: d.attempts }, (_, i) => i + 1).map((i) => <button key={i} type="button" className={'sm' + ((n || d.attempts) === i ? ' primary' : '')} onClick={() => setN(i)}>attempt {i}</button>)}</div>
        <dl>
          <dt>Kind</dt><dd>{d.attempt.kind}{d.attempt.session_mode ? ` · session: ${d.attempt.session_mode}` : ''}{d.attempt.resumed_session ? ' (native resume)' : ''}</dd>
          <dt>Agent</dt><dd>{d.attempt.profile} · {d.attempt.model || 'default model'} · runs in <code>{d.attempt.cwd}</code></dd>
          <dt>Outcome</dt><dd>{d.attempt.outcome || 'in progress'}{d.attempt.exit_code != null ? ` (exit ${d.attempt.exit_code})` : ''}</dd>
          {d.attempt.verification && <><dt>Verification</dt><dd>{d.attempt.verification.passed ? <Badge kind="ok">passed</Badge> : <Badge kind="bad">failed</Badge>} {d.attempt.verification.checks}
            {d.attempt.verification.diagnostics?.length > 0 && <ul>{d.attempt.verification.diagnostics.map((x: string, i: number) => <li key={i}><code>{x.slice(0, 400)}</code></li>)}</ul>}</dd></>}
          {d.attempt.category && <><dt>Problem class</dt><dd>{d.attempt.category}</dd></>}
          {d.attempt.voided && <><dt>Not counted</dt><dd>{d.attempt.voided}</dd></>}
        </dl>
        {!d.attempt.voided && d.attempt.state !== 'verified' && d.attempt.ended && <p><button type="button" className="sm" title="The failure was not the agent's work (provider limit, environment, Research OS defect). The attempt stays as evidence but stops counting." onClick={async () => { try { await host.call('supervisor_task', { task, action: 'void', attempts: [d.attempt.n], note: 'voided by the researcher: not the agent\'s fault' }); setVmsg('Voided; the task is queued again.'); } catch (e: any) { setVmsg(e.message); } }}>Void this attempt (not the agent's fault)</button> {vmsg}</p>}
        <h3>Prompt context (exact text, with sources)</h3>
        {d.attempt.context_sources && <p className="muted small">{d.attempt.context_sources.map((c: any) => c.section).join(' · ')}</p>}
        <pre className="diff" tabIndex={0} style={{ maxHeight: 220, overflow: 'auto' }}>{d.prompt || '(not recorded)'}</pre>
        {d.reports.length > 0 && <><h3>Reports from the agent</h3><ul>{d.reports.map((r: any, i: number) => <li key={i}><b>{r.kind}</b> {r.message || r.summary || r.path || r.run || ''}</li>)}</ul></>}
        {d.logs && <><h3>Agent output</h3><pre className="diff" tabIndex={0} style={{ maxHeight: 180, overflow: 'auto' }}>{(d.logs.err || '') + (d.logs.out || '') || '(none yet)'}</pre></>}
      </>}</State>
    </Dialog>);
}
