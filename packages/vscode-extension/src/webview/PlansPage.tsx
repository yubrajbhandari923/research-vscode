import { useEffect, useState } from 'react';
import { Badge, Banner, Dialog, callPin, EmptyState, Icon, PageHeader, Progress, Section, State, useAsync } from './common';
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
      <Proposals host={host} act={act} tick={list.data} />
      <State a={list}>{(plans) => plans.length === 0 ? (
        <EmptyState icon="tasks" title="No YAML plans yet." actions={<><button type="button" className="primary" onClick={() => host.run('research.plan.yaml.new')}>Create from template</button>
          <button type="button" onClick={() => host.run('research.plan.yaml.import')}>Import a plan</button></>}>
          Ask a planning AI to write a plan in the Research OS YAML format, save it, and import it here. You can edit the file and apply it again at any time without losing results.</EmptyState>
      ) : <>
        <div className="cards">{plans.map((pl) => (
          <section key={pl.key} className={'card' + (sel.includes(pl.key) ? ' sel' : '')} aria-label={pl.title || pl.key} style={{ margin: '8px 0', padding: 12 }}>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <div><b>{pl.title || pl.key}</b> <span className="muted">{pl.id} · key <code>{pl.key}</code> · revision {pl.revision}</span></div>
              <Badge kind={pl.state === 'running' ? (pl.health?.state === 'halted' ? 'bad' : pl.health?.state === 'waiting_for_approval' ? 'warn' : 'info') : pl.state === 'completed' ? 'ok' : pl.state === 'paused' ? 'warn' : ''} live={pl.state === 'running' && pl.health?.state === 'progressing'}>{(pl.state === 'running' && pl.health?.label ? pl.health.label : pl.state).replace('_', ' ')}</Badge>
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
      <TelegramBox host={host} />
    </div>
  );
}

function PlanDetail({ host, plan, act }: { host: Host; plan: string; act: (fn: () => Promise<any>, ok: string) => Promise<void>; }) {
  const a = useAsync<any>(() => host.call('supervisor_status', { plan }), [plan]);
  const [detail, setDetail] = useState<string | null>(null);
  const [cont, setCont] = useState<string | null>(null);
  const [instr, setInstr] = useState<string | null>(null);
  const [now, setNow] = useState(0);
  useEffect(() => { const t = setInterval(() => { if (document.visibilityState !== 'hidden') { a.reload(); setNow((n) => n + 1); } }, 3000); return () => clearInterval(t); }, [plan]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <State a={a}>{(s) => <>
      {s.state === 'running' && ['halted', 'waiting_for_approval'].includes(s.health?.state) && <HaltPanel host={host} plan={plan} health={s.health} act={act} onChanged={a.reload} onInstruct={setInstr} />}
      <Section id="tasks" title={`Tasks of ${s.plan}`} count={s.tasks.length} hint={<>Phase is what Research OS observed, not what an agent claimed. Supervisor process: {s.daemon.running ? <b>running (pid {s.daemon.pid})</b> : <b>not running</b>}{' '}
        <button type="button" className="sm" onClick={() => act(() => host.call('supervisor_daemon', { action: s.daemon.running ? 'stop' : 'start' }), s.daemon.running ? 'Supervisor stopped' : 'Supervisor started')}>{s.daemon.running ? 'Stop supervisor' : 'Start supervisor'}</button>
        {s.daemon.running && <>{' '}<button type="button" className="sm" title="Validate the project settings and apply them now. Running agents, worktrees, budgets and promotions are not touched."
          onClick={() => act(async () => { const r = await host.call('supervisor_daemon', { action: 'reload' }); if (!r.valid) throw new Error('Settings invalid: ' + r.errors.join('; ')); return r; }, 'Settings reloaded; running agents untouched')}>Reload settings</button>{' '}
          <button type="button" className={`sm${s.daemon.restart_required ? ' primary' : ''}`} title={s.daemon.restart_reason || 'Replace the supervisor process with the installed Research OS. Running agents and jobs keep running and are reconciled, never relaunched.'}
            onClick={() => act(() => host.call('supervisor_daemon', { action: 'restart' }), 'Supervisor restarted; running agents untouched')}>Restart supervisor</button>
          {s.daemon.restart_required && <span className="sub"> Restart recommended: {s.daemon.restart_reason}</span>}</>}
        {s.concurrency && <span className="sub" title={s.concurrency.note}>Parallel agents: <b>{s.concurrency.effective}</b> from {s.concurrency.source} · {s.concurrency.running} running, {s.concurrency.ready_waiting} ready and waiting
          {s.concurrency.plan && s.concurrency.project && s.concurrency.plan !== s.concurrency.project ? ` (project setting ${s.concurrency.project} is overridden by the plan)` : ''}</span>}</>}>
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
                {['attention', 'blocked', 'repair_pending'].includes(t.phase) && t.attempts > 0 && <button type="button" className="sm" title="Hand this task to another agent, in the same worktree" onClick={() => setCont(t.id)}>Continue with another agent</button>}
                {!['verified', 'retired'].includes(t.phase) && <button type="button" className="sm" title="Tell the supervisor in words what to change: unblock, redirect, amend. Protected changes ask for your PIN once." onClick={() => setInstr(t.id)}>Instruct / Unblock</button>}
                {t.phase === 'blocked' && <button type="button" className="sm" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'unblock' }), `${t.id} unblocked`)}>Unblock</button>}
                {['blocked', 'attention'].includes(t.phase) && t.attempts > 0 && <button type="button" className="sm" title="Run the checks again on the work as it stands (no new agent)" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'completed', note: 're-verify requested' }), `${t.id}: verifying again`)}>Re-verify</button>}
                {['blocked', 'attention'].includes(t.phase) && t.attempts > 0 && <button type="button" className="sm" title="Use when the failure was Research OS's fault, not the agent's: the old attempts stay as evidence but stop counting" onClick={() => act(() => host.call('supervisor_task', { task: t.id, action: 'void', note: 'voided by the researcher: the failure was not the agent\'s' }), `${t.id}: attempts voided, queued again`)}>Retry fresh</button>}</td>
            </tr>); })}</tbody></table></div>
        <p className="muted small">Attempts {s.budgets.attempts || 0} of {s.budgets.max_total_attempts} · wall time {Math.round((s.budgets.wall_seconds || 0) / 60)} of {s.budgets.max_wall_minutes} min · limits are in Settings → Supervisor</p>
      </Section>
      <Promotion host={host} plan={plan} act={act} />
      {instr && <InstructDialog host={host} task={instr} running={['running', 'verifying', 'waiting_hpc'].includes((s.tasks.find((x: any) => x.id === instr) || {}).phase)} onClose={() => { setInstr(null); a.reload(); }} />}
      {cont && <ContinueDialog host={host} task={cont} onClose={() => { setCont(null); a.reload(); }} />}
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

/** All proposals: agents' plan changes and experiment proposals. Approving needs a reason and (when set) the PIN; rejections and revision requests are kept. */
function Proposals({ host, act, tick }: { host: Host; act: (fn: () => Promise<any>, ok: string) => Promise<void>; tick: unknown }) {
  const a = useAsync<any[]>(() => host.call('proposals'), [tick]);
  const [note, setNote] = useState<Record<string, string>>({});
  const decide = (pr: any, approve: true | false | 'revise', ok: string) => act(async () => {
    const params = { id: pr.id, approve, note: note[pr.id] || undefined, rev: pr.rev };
    if (approve === true) await callPin(host, 'proposal_decide', params, `Approving ${pr.id} needs your PIN.`); else await host.call('proposal_decide', params);
    a.reload();
  }, ok);
  const all = a.data || [];
  const pending = all.filter((x) => x.status === 'pending');
  const history = all.filter((x) => x.status !== 'pending');
  if (!all.length) return null;
  return <>
    {pending.length > 0 && <Section title="Proposals" count={pending.length} hint="Agents propose; you decide. Experiments are never created by an agent directly, and only small operational task additions may apply on their own.">
      {pending.map((pr) => (
        <div key={pr.id} className="card" style={{ margin: '6px 0', padding: 10 }} aria-label={pr.id}>
          <div><b>{pr.id}</b> <Badge kind="info">{pr.kind === 'experiment' ? 'experiment' : 'plan change'}</Badge> <Badge kind={pr.classification === 'scientific' ? 'warn' : 'info'}>{pr.classification}</Badge> <span className="muted">from {pr.source || 'an agent'} · revision {pr.rev || 1}</span></div>
          <p>{pr.rationale}</p>
          {pr.kind === 'experiment' ? <dl>{[['Title', 'title'], ['Hypothesis', 'hypothesis'], ['Method', 'method'], ['Expected outputs', 'expected_outputs'], ['Relation to existing work', 'related']].map(([l, k]) => <div key={k}><dt>{l}</dt><dd>{pr.fields?.[k]}</dd></div>)}</dl>
            : <pre className="diff" tabIndex={0}>{pr.patch}</pre>}
          <label htmlFor={'why-' + pr.id}>Reason <span className="muted">(required to approve or request a revision; kept in the audit trail)</span></label>
          <textarea id={'why-' + pr.id} rows={2} value={note[pr.id] || ''} onChange={(e) => setNote({ ...note, [pr.id]: e.target.value })} />
          <div className="row">
            <button type="button" className="primary" disabled={!(note[pr.id] || '').trim()} onClick={() => decide(pr, true, `${pr.id} approved and applied`)}>Approve</button>
            <button type="button" onClick={() => decide(pr, false, `${pr.id} rejected`)}>Reject</button>
            <button type="button" disabled={!(note[pr.id] || '').trim()} onClick={() => decide(pr, 'revise', `${pr.id}: revision requested`)}>Request revision</button></div>
        </div>))}
    </Section>}
    {history.length > 0 && <details><summary>Audit history ({history.length})</summary>
      <ul>{history.map((pr) => <li key={pr.id}><b>{pr.id}</b> {pr.status.replace('_', ' ')} · {pr.kind === 'experiment' ? pr.fields?.title : pr.classification} · by {pr.decided_by || 'policy'}{pr.decided_at ? ` · ${pr.decided_at}` : ''}{pr.note ? ` · “${pr.note}”` : ''}</li>)}</ul></details>}
  </>;
}

function ContinueDialog({ host, task, onClose }: { host: Host; task: string; onClose: () => void }) {
  const a = useAsync<any>(() => host.call('provider_status', { task }), [task]);
  const [pick, setPick] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const go = async () => {
    try { const r = await host.call('supervisor_task', { task, action: 'continue', reason: pick, note: 'continued with another agent from the Plans page' }); setMsg({ ok: true, text: `${task} will continue with ${r.profile}.${r.note ? ' ' + r.note : ''}` }); a.reload(); }
    catch (e: any) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <Dialog title={`${task}: continue with another agent`} onClose={onClose} actions={<><button type="button" onClick={onClose}>Close</button>
      <button type="button" className="primary" disabled={!pick} onClick={go}>Continue with {pick || '…'}</button></>}>
      <State a={a}>{(d) => <>
        <p>Current agent: <b>{d.current || 'none yet'}</b>. The next attempt runs in the same worktree with a written hand-over (not the old session).</p>
        <label htmlFor="cont-pick">Hand over to</label>
        <select id="cont-pick" data-autofocus value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">Choose a provider…</option>
          {d.providers.map((p: any) => <option key={p.profile} value={p.profile} disabled={!p.installed || p.profile === d.current}>{p.profile} — {p.installed ? 'installed' : 'not installed'}{p.profile === d.current ? ' (current)' : ''}</option>)}
        </select>
        {msg && <Banner kind={msg.ok ? 'ok' : 'bad'}>{msg.text}</Banner>}
        {d.handoffs?.length > 0 && <><h3>Handoff history</h3><ul>{d.handoffs.map((h: any, i: number) => <li key={i}>{h.from} → {h.to} · {h.by} · {h.ts} · {h.reason}</li>)}</ul></>}
      </>}</State>
    </Dialog>);
}


const IV_STEPS = ['requested', 'proposed', 'approved', 'implementing', 'verifying', 'resolved'];

function InstructDialog({ host, task, running, onClose }: { host: Host; task: string; running: boolean; onClose: () => void }) {
  const [text, setText] = useState('');
  const [interrupt, setInterrupt] = useState(false);
  const [note, setNote] = useState('');
  const [it, setIt] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  useEffect(() => {
    if (!it || ['resolved', 'rejected', 'failed', 'cancelled'].includes(it.state)) return;
    const t = setInterval(async () => { try { const all = await host.call('interventions', { task }); const m = all.find((x: any) => x.id === it.id); if (m) setIt(m); } catch { /* keep the last state */ } }, 3000);
    return () => clearInterval(t);
  }, [it?.id, it?.state]); // eslint-disable-line react-hooks/exhaustive-deps
  const send = async () => { setBusy(true); setErr(''); try { setIt(await host.call('intervention_request', { task, message: text, interrupt })); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  const decide = async (action: 'approve' | 'reject' | 'cancel' | 'plan') => {
    setBusy(true); setErr('');
    try {
      const params = { id: it.id, action, note: note || undefined };
      setIt(action === 'approve' && it.protected.length ? await callPin(host, 'intervention_action', params, `Approving ${it.id} (${it.protected.join(', ')}) needs your PIN.`) : await host.call('intervention_action', params));
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <Dialog title={`${task}: instruct the supervisor`} onClose={onClose} actions={<>
      <button type="button" onClick={onClose}>Close</button>
      {!it && <button type="button" className="primary" disabled={busy || !text.trim()} onClick={send}>{busy ? 'Planning…' : 'Plan this'}</button>}
      {it?.state === 'proposed' && <><button type="button" disabled={busy} onClick={() => decide('reject')}>Reject</button>
        <button type="button" className="primary" disabled={busy || (it.protected.length > 0 && !note.trim())} onClick={() => decide('approve')}>{it.protected.length ? 'Approve all (PIN)' : 'Approve'}</button></>}
      {it?.state === 'failed' && !it.applied && Object.keys(it.applied || {}).length === 0 && <button type="button" disabled={busy} onClick={() => decide('plan')}>Plan again</button>}
    </>}>
      {!it && <>
        <p className="muted small">Say what should change in plain words, for example "the output only has to exist, drop the content check", "retry with the second approach", or "add a task that writes the missing report". A planning agent turns it into concrete changes; you see them before anything runs.</p>
        <label htmlFor="iv-text">Instruction</label>
        <textarea id="iv-text" data-autofocus rows={5} value={text} onChange={(e) => setText(e.target.value)} />
        {running && <label className="row"><input type="checkbox" checked={interrupt} onChange={(e) => setInterrupt(e.target.checked)} /> Stop the running agent now (its attempt is kept as evidence). Otherwise this waits for the attempt to end.</label>}
      </>}
      {err && <Banner kind="bad">{err}</Banner>}
      {it && <>
        <p><b>{it.id}</b> · {it.state === 'failed' ? <Badge kind="bad">failed</Badge> : it.state === 'resolved' ? <Badge kind="ok">resolved</Badge> : it.state === 'rejected' ? <Badge kind="warn">rejected</Badge> : <Badge kind="info" live>{it.state}</Badge>}</p>
        <ol className="muted small" aria-label="Progress">{IV_STEPS.map((x) => <li key={x} style={{ fontWeight: x === it.state ? 700 : 400 }}>{x}{it.history?.some((h: any) => h.state === x) ? ' ✓' : ''}</li>)}</ol>
        {it.summary && <p>{it.summary}</p>}
        {it.preview?.length > 0 && <><h3>Proposed changes</h3><ul>{it.preview.map((x: string, i: number) => <li key={i}><code>{x}</code></li>)}</ul></>}
        {it.state === 'proposed' && it.protected.length > 0 && <Banner kind="warn">This changes {it.protected.join(', ')}. One approval with your PIN covers every change listed. Earlier attempts, failed checks and the original criteria are kept as evidence.</Banner>}
        {it.state === 'proposed' && it.protected.length > 0 && <><label htmlFor="iv-note">Why (recorded with the amendment)</label><input id="iv-note" value={note} onChange={(e) => setNote(e.target.value)} /></>}
        {['approved', 'implementing', 'verifying'].includes(it.state) && <p className="muted small">{it.queued ? 'Waiting for the running attempt to end, then ' : ''}The supervisor applies the changes, runs the task again under the updated contract and resumes the plan. It is resolved only when the task is verified.</p>}
        {it.state === 'failed' && <Banner kind="bad">{it.history?.[it.history.length - 1]?.note}</Banner>}
      </>}
    </Dialog>);
}

function HaltPanel({ host, plan, health, act, onChanged, onInstruct }: { host: Host; plan: string; health: any; act: (fn: () => Promise<any>, ok: string) => Promise<void>; onChanged: () => void; onInstruct: (t: string) => void }) {
  const [dbg, setDbg] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const first = health.blocking?.[0]?.task;
  const diagnose = async () => { setBusy(true); setErr(''); try { setDbg(await host.call('debug_run', { plan })); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  const choose = async (instruction: string) => { setErr(''); try { await host.call('intervention_request', { task: dbg.tasks[0], message: instruction }); onInstruct(dbg.tasks[0]); onChanged(); } catch (e: any) { setErr(e.message); } };
  return (
    <Banner kind={health.state === 'halted' ? 'bad' : 'warn'}>
      <b>{health.label}.</b> Nothing is running; waiting on {health.blocking.map((b: any) => `${b.task} (${b.phase})`).join(', ')}.
      <div className="row">
        {health.state === 'halted' && <button type="button" className="sm primary" disabled={busy} onClick={diagnose}>{busy ? 'Debug agent is reading…' : 'Diagnose with the debug agent'}</button>}
        {first && <button type="button" className="sm" onClick={() => onInstruct(first)}>Instruct {first}</button>}
      </div>
      {err && <p className="small">{err}</p>}
      {dbg && <div>{dbg.state === 'failed' ? <p>The debug agent failed: {dbg.error}</p> : <><p>{dbg.summary}</p>
        {dbg.options?.length > 0 && <ol>{dbg.options.map((o: any, i: number) => <li key={i}><b>{o.label}</b> <Badge kind={o.class === 'scientific' || o.class === 'resource' ? 'warn' : ''}>{o.class}</Badge>{dbg.recommended === i ? ' (recommended)' : ''}
          <div className="muted small">{o.instruction}</div><button type="button" className="sm" onClick={() => choose(o.instruction)}>Use this option</button></li>)}</ol>}
        <p className="muted small">Or write your own with Instruct. Model: {dbg.model}.</p></>}</div>}
    </Banner>);
}

export function TelegramBox({ host }: { host: Host }) {
  const a = useAsync<any>(() => host.call('telegram_status'), []);
  const [token, setToken] = useState('');
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<any>, ok: string) => { setBusy(true); try { await fn(); setMsg({ ok: true, text: ok }); setToken(''); a.reload(); } catch (e: any) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); } };
  return (
    <Section title="Telegram alerts" hint="Get a message when a plan halts, with a short diagnosis and options, and check on the supervisor from your phone. The bot token is stored on this machine only (never in the project or git). Protected changes still need your PIN here or in the terminal.">
      <State a={a}>{(d) => <>
        <p>{d.token_set ? <Badge kind="ok">token saved</Badge> : <Badge kind="warn">no token</Badge>} {d.chat_id_set ? <Badge kind="ok">chat linked</Badge> : <Badge kind="warn">no chat</Badge>} {d.enabled ? <Badge kind="ok">enabled</Badge> : <Badge>disabled</Badge>}</p>
        <label htmlFor="tg-token">Bot token (from @BotFather)</label>
        <input id="tg-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} placeholder="123456:ABC…" />
        <p className="muted small">Open your bot in Telegram and send it any message first, then press Connect: the first chat that messages it becomes the only chat Research OS obeys.</p>
        <div className="row">
          <button type="button" className="primary" disabled={busy || !token.trim()} onClick={() => run(() => host.call('telegram_set', { token, discover: true }), 'Connected. Turn on "Telegram notifications" in Settings → Supervisor to start alerts.')}>{busy ? 'Waiting for your message…' : 'Connect'}</button>
          <button type="button" disabled={busy || !d.chat_id_set} onClick={() => run(() => host.call('telegram_test'), 'Test message sent')}>Send test message</button>
          <button type="button" disabled={busy || !d.token_set} onClick={() => run(() => host.call('telegram_clear'), 'Credentials deleted')}>Remove</button>
        </div>
        {msg && <Banner kind={msg.ok ? 'ok' : 'bad'}>{msg.text}</Banner>}
      </>}</State>
    </Section>);
}
