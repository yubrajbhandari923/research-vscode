import { useState } from 'react';
import { Badge, Dialog, EmptyState, IdLink, Icon, PageHeader, Progress, Section, Segmented, Stat, State, Status, ago, useAsync } from './common';
import { HealthBanner } from './Doctor';
import { NewExperiment } from './NewExperiment';
import type { Host } from './host';
import type { Attention, Page } from './types';

type Go = (p: Page, target?: string) => void;
const KIND_LABEL: Record<string, string> = { synthesis: 'Synthesize', review: 'Review', blocked: 'Blocked', approval: 'Approve', workspace: 'Workspace', promotion: 'Promotion' };
const ACTIVE_EXP = ['proposed', 'ready', 'running', 'needs_review', 'investigating'];
const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`;

function Rows({ children }: { children: React.ReactNode }) { return <ul className="rows">{children}</ul>; }

export function HomePage({ host, go, onAttention }: { host: Host; go: Go; onAttention?: (n: number) => void }) {
  const a = useAsync<any>(async () => { const d = await host.call('control', { page: 'home' }); onAttention?.(d.attention.length); return d; }, []);
  const [creating, setCreating] = useState(false);
  const [all, setAll] = useState(false);
  const open = (i: Attention) => (i.open.page === 'research' || i.open.page === 'work') && /^[A-Z]+-/.test(i.open.target) ? host.openEntity(i.open.target) : go(i.open.page, i.open.target);
  return (
    <div className="page-body">
      <State a={a}>{(d) => {
        const st = d.stats, warn = d.attention.filter((i: Attention) => i.severity === 'warn').length;
        const fresh = st.experiments === 0;
        const shown = all ? d.attention : d.attention.slice(0, 6);
        return (
          <>
            <PageHeader title={d.project.name} sub={d.project.goal || <>No research goal yet. Add one under Settings → Project so agents and collaborators share it.</>}
              actions={<button type="button" className="primary" onClick={() => setCreating(true)}><Icon name="plus" /> New experiment</button>} />
            <HealthBanner host={host} open={() => go('settings', 'health')} />
            <div className="kpis" role="list" aria-label="Project at a glance">
              <Stat value={st.experiments} sub={d.active_experiments.some((e: any) => ['running', 'ready'].includes(e.status)) ? `${d.active_experiments.filter((e: any) => ['running', 'ready'].includes(e.status)).length} active` : undefined} label="Experiments" />
              <Stat value={st.runs} sub={st.failed_runs ? `${st.failed_runs} failed` : undefined} label="Runs" />
              <Stat value={st.findings} sub={st.supported ? `${st.supported} supported` : undefined} label="Findings" />
              <Stat value={`${st.tasks_done}/${st.tasks}`} label="Tasks done" />
              <Stat value={d.attention.length} sub={warn ? `${warn} urgent` : undefined} label="Needs attention" warn={warn > 0} />
            </div>
            <div className="grid-main">
              <div className="col">
                <Section id="att" title="Needs your attention" count={d.attention.length} boxed>
                  {d.attention.length === 0
                    ? <EmptyState inline icon="check" title="Nothing is waiting on you.">Syntheses, reviews, blocked tasks, approvals and promotions appear here the moment they need a decision.</EmptyState>
                    : <><Rows>{shown.map((i: Attention) => (
                      <li key={i.kind + i.id} className={'sev-' + i.severity}>
                        <button type="button" className="rowbtn" onClick={() => open(i)}>
                          <span className={'kind k-' + (i.severity === 'warn' ? 'warn' : '')}>{KIND_LABEL[i.kind] || i.kind}</span>
                          <span className="row-main"><span className="t">{i.title}</span>{i.detail && <span className="d clamp1">{i.detail}</span>}</span>
                          <span className="chev"><Icon name="chevron" /></span></button></li>))}</Rows>
                      {d.attention.length > 6 && <button type="button" className="more" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${d.attention.length}`}</button>}</>}
                </Section>
                {fresh ? (
                  <Section id="start" title="Get started" boxed>
                    <ol className="checklist">
                      <li className="done"><span className="n"><Icon name="check" /></span><div><div className="t">Project created</div><div className="d">Research records live in <code>.research/</code> and are versioned with your code.</div></div></li>
                      <li><span className="n">2</span><div className="grow"><div className="t">State a hypothesis as an experiment</div><div className="d">Writing the expectation down before any run exists is what makes results interpretable.</div><p style={{ marginTop: 8 }}><button type="button" className="primary" onClick={() => setCreating(true)}>New experiment</button></p></div></li>
                      <li><span className="n">3</span><div><div className="t">Run it and log metrics</div><div className="d"><code>research run exec EXP-001 -- python train.py</code></div></div></li>
                      <li><span className="n">4</span><div><div className="t">Synthesize, then record a finding</div><div className="d">An interpreted result backed by evidence, independently reviewed before it counts as supported.</div></div></li>
                    </ol>
                  </Section>
                ) : (
                  <Section id="act" title="Active experiments" count={d.active_experiments.length} actions={<button type="button" className="link" onClick={() => go('research')}>All experiments</button>} boxed>
                    {d.active_experiments.length === 0 ? <EmptyState inline title="No active experiments.">Everything is completed or abandoned. Start the next one when you know what to ask.</EmptyState> :
                      <table><caption className="sr">Active experiments</caption><thead><tr><th scope="col">Experiment</th><th scope="col">Status</th><th scope="col" className="hide-narrow">Runs</th><th scope="col" className="num">To interpret</th></tr></thead>
                        <tbody>{d.active_experiments.map((e: any) => <tr key={e.id}>
                          <td><div className="title-cell"><span className="t clip"><IdLink id={e.id} host={host} /> {e.title}</span></div></td>
                          <td><Status value={e.status} /></td>
                          <td className="hide-narrow min"><Progress value={e.runs} max={e.planned} label={`${e.runs} of ${e.planned || '?'} runs`} />{e.failed > 0 && <span className="cell-sub">{e.failed} failed</span>}</td>
                          <td className="num">{e.unsynthesized ? <Badge kind="warn">{e.unsynthesized}</Badge> : <span className="dim">—</span>}</td></tr>)}</tbody></table>}
                  </Section>
                )}
              </div>
              <div className="col">
                <Section id="nxt" title="Current focus" boxed>
                  <div className="focus">
                    {d.next_task || d.focus?.next_step ? <>
                      <span className="label">Next action</span>
                      <div className="next">{d.next_task ? <><IdLink id={d.next_task.id} host={host} /> {d.next_task.title}</> : d.focus.next_step}</div>
                    </> : <>
                      <span className="label">Next action</span>
                      <div className="dim">No ready task. Create a plan under Work, or register the next experiment.</div>
                      <div className="row"><button type="button" onClick={() => go('work')}>Open Work</button></div></>}
                    {d.focus?.blocker && <div className="block"><Icon name="alert" /><span>{String(d.focus.blocker)}</span></div>}
                  </div>
                </Section>
                <Section id="run" title="Running now" count={d.running.length} boxed>
                  {d.running.length === 0 ? <EmptyState inline title="No jobs in flight.">Runs started with <code>research run</code> show up here with their backend.</EmptyState> :
                    <Rows>{d.running.map((r: any) => <li key={r.id}><div className="li"><i className="dot k-info live" aria-label="running" style={{ marginTop: 6 }} /><div className="grow idrow"><IdLink id={r.id} host={host} /><span className="dim t">{r.experiment_id}</span></div><span className="meta">{r.backend}</span></div></li>)}</Rows>}
                </Section>
                <Section id="lrn" title="Recently learned" count={d.learned.length || undefined} actions={<button type="button" className="link" onClick={() => go('research')}>All findings</button>} boxed>
                  {d.learned.length === 0 ? <EmptyState inline title="No findings yet.">A finding is an interpreted result backed by recorded evidence.</EmptyState> :
                    <Rows>{d.learned.slice(0, 4).map((f: any) => <li key={f.id}><div className="li"><div className="grow idrow"><IdLink id={f.id} host={host} /><span className="t">{f.title}</span></div><Status value={f.status} /></div></li>)}</Rows>}
                </Section>
                <Section id="log" title="Activity" boxed>
                  {d.activity.length === 0 ? <EmptyState inline title="No activity yet." /> :
                    <Rows>{d.activity.slice(0, 6).map((x: any, i: number) => <li key={i}><div className="li"><span className="when">{ago(x.ts)}</span><div className="grow clamp1">{x.summary}</div></div></li>)}</Rows>}
                </Section>
              </div>
            </div>
            {creating && <NewExperiment host={host} onClose={() => setCreating(false)} onCreated={() => a.reload()} />}
          </>
        );
      }}</State>
    </div>
  );
}

const TASK_KIND: Record<string, 'ok' | 'bad' | 'info' | ''> = { done: 'ok', blocked: 'bad', running: 'info', in_progress: 'info' };

export function WorkPage({ host, go }: { host: Host; go: Go }) {
  const a = useAsync<any>(() => host.call('control', { page: 'work' }), []);
  const [creating, setCreating] = useState(false);
  const [impact, setImpact] = useState<any>(null);
  const [promo, setPromo] = useState<any>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <div className="page-body">
      <State a={a}>{(d) => {
        const tasks = d.plans.flatMap((p: any) => p.tasks);
        const by = (s: string) => tasks.filter((t: any) => t.status === s).length;
        const live = d.runs.filter((r: any) => ['running', 'queued'].includes(r.status));
        const pending = d.promotions.filter((c: any) => !['promoted', 'abandoned'].includes(c.state));
        return (
          <>
            <PageHeader title="Work" sub="Who is doing what, where it runs, and what is ready to move into main."
              actions={<><button type="button" onClick={() => host.run('research.plan.create')}><Icon name="tasks" /> New plan</button><button type="button" onClick={() => host.run('research.hpc.submit')}><Icon name="branch" /> Run on cluster</button>{d.layout.mode !== 'classic' && <button type="button" onClick={() => setCreating(true)}><Icon name="branch" /> New worktree</button>}</>} />
            <div className="kpis" role="list" aria-label="Operations at a glance">
              <Stat value={by('todo') + by('running') + by('in_progress')} sub={by('running') + by('in_progress') ? `${by('running') + by('in_progress')} in progress` : undefined} label="Open tasks" />
              <Stat value={by('blocked')} label="Blocked" warn={by('blocked') > 0} />
              <Stat value={d.workspaces.length} sub={d.workspaces.filter((w: any) => w.dirty || w.untracked).length ? `${d.workspaces.filter((w: any) => w.dirty || w.untracked).length} uncommitted` : undefined} label="Workspaces" />
              <Stat value={live.length} label="Jobs running" />
              <Stat value={pending.length} label="Promotions pending" />
            </div>
            {msg && <div className={'banner ' + (msg.ok ? 'ok' : 'bad')} role="status"><Icon name={msg.ok ? 'check' : 'alert'} /><div className="grow">{msg.text}</div></div>}
            <div className="stack">
              <Section id="pl" title="Plans and tasks" count={d.plans.length || undefined}>
                {d.plans.length === 0 ? <EmptyState icon="tasks" title="No plans yet.">A plan breaks an objective into tasks that agents or you can claim. Dependencies decide what is ready next.
                  <br /><button type="button" onClick={() => host.run('research.plan.create')}>New plan</button></EmptyState> :
                  <div className="grid-2">{d.plans.map((pl: any) => {
                    const done = pl.tasks.filter((t: any) => t.status === 'done').length;
                    return <div key={pl.id} className="card plan">
                      <div className="plan-head"><div className="grow"><span><IdLink id={pl.id} host={host} /> <b>{pl.title}</b></span></div><Progress value={done} max={pl.tasks.length} ok={done === pl.tasks.length && done > 0} label={`${done} of ${pl.tasks.length} tasks done`} /><button type="button" className="sm" onClick={() => host.run('research.task.create', pl.id)}>Add task</button></div>
                      {pl.tasks.length === 0 ? <EmptyState inline title="No tasks in this plan." /> : pl.tasks.map((t: any) => (
                        <div key={t.id} className={'task' + (t.status === 'done' ? ' done' : '')}>
                          <i className={'dot k-' + (TASK_KIND[t.status] || '')} aria-hidden /><IdLink id={t.id} host={host} /><span className="t">{t.title}</span>
                          {t.assigned_role && <span className="meta hide-narrow">{t.assigned_role}</span>}
                          <Status value={t.status} />{t.status !== 'done' && <button type="button" className="sm" aria-label={`Dispatch ${t.id} to an agent`} onClick={() => host.run('research.dispatch', t.id)}>Dispatch</button>}</div>))}
                    </div>;
                  })}</div>}
              </Section>

              <div className="grid-main">
                <div className="col">
                  <Section id="ws" title="Experiment worktrees" count={d.workspaces.length} boxed
                    lead={d.layout.mode === 'classic' ? undefined : <>Each experiment can live on its own git branch and folder while sharing one research history. Ledger <code>{d.layout.ledger_root}</code></>}>
                    {d.layout.mode === 'classic' ? <EmptyState icon="branch" title="Optional: try ideas on separate git branches.">Experiment worktrees and worktrees need a shared research ledger so every branch keeps one scientific history. Preview the change with <code>research ledger enable</code>; nothing moves until you add <code>--apply</code>.</EmptyState> :
                      d.workspaces.length === 0 ? <EmptyState inline icon="branch" title="No experiment worktrees yet." actions={<button type="button" onClick={() => setCreating(true)}>New worktree</button>}>Create one to try an idea without touching your current branch.</EmptyState> :
                        <div className="tbl-wrap"><table><caption className="sr">Experiment workspaces</caption><thead><tr><th scope="col">Workspace</th><th scope="col">Branch</th><th scope="col" className="hide-narrow">Experiment</th><th scope="col">State</th><th scope="col" className="hide-narrow">Agent / tasks</th><th scope="col"><span className="sr">Actions</span></th></tr></thead><tbody>
                          {d.workspaces.map((w: any) => (
                            <tr key={w.id}><td className="min"><b>{w.id}</b></td>
                              <td><code className="clip">{w.branch}</code><span className="sub">base {String(w.base_rev || '').slice(0, 8)}{w.commits_ahead != null ? ` · ${w.commits_ahead} ahead` : ''}</span></td>
                              <td className="hide-narrow min">{w.experiment ? <IdLink id={w.experiment} host={host} /> : '—'}</td>
                              <td className="min">{!w.present ? <Badge kind="bad">missing</Badge> : w.dirty || w.untracked ? <Badge kind="warn">uncommitted</Badge> : <Badge kind="ok">clean</Badge>}{w.active_runs.length > 0 && <Badge kind="info" live>{w.active_runs.length} running</Badge>}</td>
                              <td className="hide-narrow dim">{[w.agent, w.tasks.length ? w.tasks.join(', ') : null].filter(Boolean).join(' · ') || '—'}</td>
                              <td className="min"><button type="button" className="sm" onClick={() => host.run('research.workspace.open', w.path)}>Open</button> <button type="button" className="sm" onClick={async () => setImpact(await host.call('workspace_archive', { id: w.id, dry_run: true }))}>Archive…</button></td></tr>))}</tbody></table></div>}
                  </Section>

                  {d.layout.mode !== 'classic' && (
                    <Section id="pr" title="Promotion candidates" count={d.promotions.length} boxed lead={<>Move only the code worth keeping into <code>main</code>. Nothing merges without tests, an independent review and your approval.</>}>
                      {d.promotions.length === 0 ? <EmptyState inline icon="merge" title="Nothing to promote.">Prepare a candidate from a workspace: <code>research promote prepare WS-001 --commit …</code></EmptyState> :
                        <div className="tbl-wrap"><table><caption className="sr">Promotion candidates</caption><thead><tr><th scope="col">Candidate</th><th scope="col">State</th><th scope="col">Tests</th><th scope="col" className="hide-narrow">Evidence</th><th scope="col" className="hide-narrow">Review</th><th scope="col"><span className="sr">Actions</span></th></tr></thead><tbody>
                          {d.promotions.map((c: any) => <tr key={c.id}><td><div className="title-cell"><span className="t"><b>{c.id}</b> {c.title}</span><code className="cell-sub clip">{c.branch}</code></div></td>
                            <td className="min"><Status value={c.state} /></td>
                            <td className="min"><Status value={c.gates.tests.state} /></td>
                            <td className="hide-narrow min"><Status value={c.gates.evidence.state} /></td>
                            <td className="hide-narrow min"><Status value={c.gates.review.state} /></td>
                            <td className="min"><button type="button" className="sm" onClick={() => setPromo(c)}>Open…</button></td></tr>)}</tbody></table></div>}
                    </Section>)}
                </div>
                <div className="col">
                  <Section id="rn" title="Jobs" count={d.runs.length || undefined} boxed>
                    {d.runs.length === 0 ? <EmptyState inline icon="play" title="No runs yet.">Start one with <code>research run exec EXP-001 -- …</code>. Local and SLURM jobs appear here.</EmptyState> :
                      <Rows>{d.runs.slice(0, 8).map((r: any) => <li key={r.id}><div className="li"><div className="grow idrow"><IdLink id={r.id} host={host} /><span className="dim t">{r.experiment_id}</span></div><span className="meta">{r.backend}{r.slurm_job_id ? ` #${r.slurm_job_id}` : ''}</span><Status value={r.status} /></div></li>)}</Rows>}
                  </Section>
                  <details className="disclose" open={d.execution.applies}>
                    <summary><h2 id="ex">Job isolation <span className="count">optional</span></h2></summary>
                    <div className="card pad" style={{ marginTop: 8 }}>
                      <p className="dim">Jobs run natively unless a profile applies. A restrictive profile is never silently replaced by native execution.</p>
                      <table><caption className="sr">Sandbox profiles</caption><thead><tr><th scope="col">Profile</th><th scope="col">Runtime</th><th scope="col" className="hide-narrow">Network</th><th scope="col">GPU</th></tr></thead><tbody>
                        {[...d.execution.profiles].sort((a: any, b: any) => (a.runtime === 'native' ? -1 : 0) - (b.runtime === 'native' ? -1 : 0) || a.name.localeCompare(b.name)).map((p: any) => <tr key={p.name}><td><b>{p.name}</b>{d.execution.applies && d.execution.default === p.name && <Badge kind="ok">default</Badge>}</td><td>{p.runtime}</td><td className="hide-narrow">{p.network}</td><td>{p.gpu ? 'yes' : 'no'}</td></tr>)}</tbody></table>
                      <p className="meta" style={{ margin: '8px 0' }}>{d.execution.agent_boundary.note}</p>
                      <button type="button" className="link" onClick={() => go('settings', 'sandbox')}>Configure execution and sandbox settings</button>
                    </div>
                  </details>
                </div>
              </div>
            </div>
            {promo && <PromotionDialog host={host} candidate={promo} onClose={() => { setPromo(null); a.reload(); }} />}
            {creating && <CreateWorkspace host={host} onClose={() => setCreating(false)} onDone={(t) => { setCreating(false); setMsg({ ok: true, text: t }); a.reload(); }} />}
            {impact && (
              <Dialog title={`Archive ${impact.workspace}`} onClose={() => setImpact(null)} actions={<>
                <button type="button" onClick={() => setImpact(null)}>Cancel</button>
                <button type="button" className="primary" data-autofocus disabled={impact.blockers.length > 0}
                  onClick={async () => { try { await host.call('workspace_archive', { id: impact.workspace, dry_run: false }); setMsg({ ok: true, text: `${impact.workspace} archived. The branch and all results are kept.` }); } catch (e: any) { setMsg({ ok: false, text: e.message }); } setImpact(null); a.reload(); }}>Archive</button></>}>
                {impact.blockers.length > 0 ? <div className="field-error" role="alert"><b>Cannot archive yet:</b><ul>{impact.blockers.map((b: string) => <li key={b}>{b}</li>)}</ul></div> : <p>This is safe to do.</p>}
                {impact.unmerged_commits > 0 && <p className="help-warn">{impact.unmerged_commits} commit(s) are not in <code>main</code>. They stay on the branch.</p>}
                <p>Archiving will:</p><ul>{impact.would.map((w: string) => <li key={w}>{w}</li>)}</ul>
              </Dialog>)}
          </>
        );
      }}</State>
    </div>
  );
}

function CreateWorkspace({ host, onClose, onDone }: { host: Host; onClose: () => void; onDone: (t: string) => void }) {
  const exps = useAsync<any>(() => host.call('control', { page: 'research' }), []);
  const [exp, setExp] = useState('');
  const [name, setName] = useState('');
  const [err, setErr] = useState('');
  return (
    <Dialog title="New experiment workspace" onClose={onClose} actions={<>
      <button type="button" onClick={onClose}>Cancel</button>
      <button type="button" className="primary" disabled={!exp} onClick={async () => { try { const w = await host.call('workspace_create', { experiment: exp, name: name || undefined }); onDone(`Created ${w.id} on ${w.branch}`); } catch (e: any) { setErr(e.message); } }}>Create</button></>}>
      <p className="muted">Creates a new branch and worktree. Your current branch and files are not touched, and the experiment keeps using the same research history.</p>
      <label htmlFor="we">Experiment</label>
      <select id="we" data-autofocus value={exp} onChange={(e) => setExp(e.target.value)}><option value="">Choose…</option>
        {(exps.data?.experiments || []).map((e: any) => <option key={e.id} value={e.id}>{e.id} · {e.title}</option>)}</select>
      <label htmlFor="wn">Branch suffix <span className="muted">(optional)</span></label>
      <input id="wn" type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. cosine-schedule" />
      {err && <div className="field-error" role="alert">{err}</div>}
    </Dialog>
  );
}

const EXP_FILTERS = [{ id: 'all', label: 'All' }, { id: 'active', label: 'Active' }, { id: 'review', label: 'Needs review' }, { id: 'done', label: 'Completed' }] as const;
type ExpFilter = (typeof EXP_FILTERS)[number]['id'];

export function ResearchPage({ host }: { host: Host }) {
  const a = useAsync<any>(() => host.call('control', { page: 'research' }), []);
  const [creating, setCreating] = useState(false);
  const [filter, setFilter] = useState<ExpFilter>('all');
  const match = (e: any, f: ExpFilter) => f === 'all' || (f === 'active' ? ACTIVE_EXP.includes(e.status) && e.status !== 'needs_review' : f === 'review' ? e.status === 'needs_review' || e.unsynthesized > 0 : e.status === 'completed');
  return (
    <div className="page-body">
      <State a={a}>{(d) => {
        const exps = d.experiments.filter((e: any) => match(e, filter));
        const supported = d.findings.filter((f: any) => f.status === 'supported').length;
        return (
          <>
            <PageHeader title="Research" sub={<>{plural(d.questions.length, 'question')} · {plural(d.experiments.length, 'experiment')} · {plural(d.findings.length, 'finding')} ({supported} supported) · {plural(d.decisions.length, 'decision')}</>}
              actions={<button type="button" className="primary" onClick={() => setCreating(true)}><Icon name="plus" /> New experiment</button>} />
            {creating && <NewExperiment host={host} onClose={() => setCreating(false)} onCreated={() => a.reload()} />}
            <div className="stack">
              <Section id="ex2" title="Experiments" count={d.experiments.length}
                actions={d.experiments.length > 0 && <Segmented label="Filter experiments" value={filter} onChange={setFilter} options={EXP_FILTERS.map((f) => ({ id: f.id, label: f.label, n: d.experiments.filter((e: any) => match(e, f.id)).length }))} />} boxed>
                {d.experiments.length === 0 ? <EmptyState icon="flask" title="No experiments yet." actions={<button type="button" className="primary" onClick={() => setCreating(true)}>New experiment</button>}>An experiment states a hypothesis before any run exists, so results can be interpreted honestly.</EmptyState> :
                  exps.length === 0 ? <EmptyState inline title="No experiments match this filter." /> :
                    <div className="tbl-wrap"><table><caption className="sr">Experiments</caption><thead><tr><th scope="col">Experiment</th><th scope="col">Status</th><th scope="col" className="hide-narrow">Runs</th><th scope="col" className="hide-narrow">Result</th><th scope="col" className="num">Failed</th><th scope="col" className="num">To interpret</th></tr></thead><tbody>
                      {exps.map((e: any) => <tr key={e.id}>
                        <td><div className="title-cell"><span className="t"><IdLink id={e.id} host={host} /> {e.title}</span>{e.hypothesis && <span className="cell-sub clamp1">{e.hypothesis}</span>}</div></td>
                        <td className="min"><Status value={e.status} /></td>
                        <td className="hide-narrow min"><Progress value={e.runs} max={e.planned} label={`${e.runs} runs`} /></td>
                        <td className="hide-narrow min">{e.metric ? <span className="mono"><span className="dim">{e.metric.name} </span>{fmtNum(e.metric.last)}{e.metric.n > 1 && <span className="cell-sub">{fmtNum(e.metric.min)} – {fmtNum(e.metric.max)} over {e.metric.n}</span>}</span> : <span className="dim">—</span>}</td>
                        <td className="num">{e.failed ? <Badge kind="bad">{e.failed}</Badge> : <span className="dim">—</span>}</td>
                        <td className="num">{e.unsynthesized ? <Badge kind="warn">{e.unsynthesized}</Badge> : <span className="dim">—</span>}</td></tr>)}</tbody></table></div>}
              </Section>
              <div className="grid-main">
                <Section id="fi" title="Findings" count={d.findings.length} boxed>
                  {d.findings.length === 0 ? <EmptyState inline icon="bulb" title="No findings yet.">Record one when an experiment supports or contradicts a claim: <code>research finding add</code></EmptyState> :
                    <ul className="rows">{d.findings.map((f: any) => <li key={f.id}><div className="li"><div className="grow"><div className="idrow"><IdLink id={f.id} host={host} /><span className="t">{f.title}</span></div>{f.confidence && <span className="cell-sub">{f.kind || 'result'} · {f.confidence} confidence</span>}</div><Status value={f.status} /></div></li>)}</ul>}
                </Section>
                <div className="col">
                  <Section id="qu" title="Questions" count={d.questions.length} boxed>
                    {d.questions.length === 0 ? <EmptyState inline title="No questions yet." /> : <ul className="rows">{d.questions.map((q: any) => <li key={q.id}><div className="li"><div className="grow idrow"><IdLink id={q.id} host={host} /><span className="t">{q.title}</span></div><Status value={q.status} /></div></li>)}</ul>}
                  </Section>
                  <Section id="de" title="Decisions" count={d.decisions.length} boxed>
                    {d.decisions.length === 0 ? <EmptyState inline title="No decisions yet." /> : <ul className="rows">{d.decisions.map((x: any) => <li key={x.id}><div className="li"><div className="grow idrow"><IdLink id={x.id} host={host} /><span className="t">{x.title}</span></div></div></li>)}</ul>}
                  </Section>
                </div>
              </div>
            </div>
          </>
        );
      }}</State>
    </div>
  );
}

const fmtNum = (v: any) => (typeof v === 'number' ? (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(3)) : String(v ?? ''));

function PromotionDialog({ host, candidate, onClose }: { host: Host; candidate: any; onClose: () => void }) {
  const [c, setC] = useState<any>(candidate);
  const diff = useAsync<any>(() => host.call('promotion_diff', { id: c.id }), [c.gates.head]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sure, setSure] = useState(false);
  const [cmd, setCmd] = useState('');
  const needCmd = /No test command configured/.test(err);
  const act = async (action: string, extra: any = {}) => {
    setBusy(true); setErr('');
    try { const r = await host.call('promotion_action', { id: c.id, action, ...extra }); const all = await host.call('promotions', {}).catch(() => []); setC(all.find((x: any) => x.id === c.id) || r); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const g = c.gates;
  const row = (label: string, gate: any, children?: any) => <tr><th scope="row">{label}</th><td><Badge kind={['passed', 'approved'].includes(gate.state) ? 'ok' : ['failed', 'blocked', 'stale'].includes(gate.state) ? 'warn' : ''}>{gate.state.replace('_', ' ')}</Badge></td><td className="muted">{gate.detail || gate.note || ''}{children}</td></tr>;
  return (
    <Dialog title={`${c.id} · ${c.title}`} onClose={onClose} actions={<>
      <button type="button" onClick={onClose} data-autofocus>Close</button>
      {c.state !== 'promoted' && c.state !== 'abandoned' && <button type="button" disabled={busy} onClick={() => act('abandon')}>Abandon</button>}
      <button type="button" disabled={busy || c.state === 'promoted'} onClick={() => act('verify', cmd.trim() ? { command: cmd.trim() } : {})}>Run tests</button>
      <button type="button" disabled={busy || g.review.state === 'approved' || g.tests.state !== 'passed'} onClick={() => act('review', { verdict: 'approve' })}>Approve code review</button>
      <button type="button" className="primary" disabled={busy || c.state !== 'reviewed'} onClick={() => act('approve')}>Approve promotion</button>
      <button type="button" className="primary" disabled={busy || c.state !== 'approved' || !sure} onClick={() => act('integrate', { confirm: true })}>Merge into main</button></>}>
      <p className="muted">Candidate <code>{c.branch}</code> at <code>{String(g.head).slice(0, 8)}</code>, built from <code>main</code> with only the selected changes. The experiment workspace is untouched.</p>
      <table><caption className="sr">Gates</caption><tbody>
        {row('Tests', g.tests)}{row('Scientific evidence', g.evidence)}{row('Implementation review', g.review)}{row('Human approval', g.approval)}</tbody></table>
      <p className="small muted">Scientifically successful: <b>{String(c.classification.scientifically_successful ?? 'not known')}</b> · technically correct: <b>{String(c.classification.technically_correct ?? 'not known')}</b> · integration-ready: <b>{String(c.classification.integration_ready)}</b> · promoted: <b>{String(c.classification.promoted)}</b></p>
      {err && <div className="field-error" role="alert">{err}</div>}
      {needCmd && <div className="stack"><label htmlFor="pc-cmd">Test command to run in the candidate <span className="muted">(set promotion.test_command in Settings → Git &amp; workspaces to keep it)</span></label><input id="pc-cmd" type="text" value={cmd} placeholder="python test_diffuse.py" onChange={(e) => setCmd(e.target.value)} /></div>}
      <h3>Patch main would receive</h3>
      <State a={diff}>{(d) => <><p className="muted small">{d.stat}</p><pre className="diff" tabIndex={0} aria-label="Patch">{d.patch.split('\n').slice(0, 400).map((l: string, i: number) => <div key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : ''}>{l || ' '}</div>)}</pre></>}</State>
      {c.state === 'approved' && <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I have read the patch. Moving <code>main</code> is a fast-forward merge; no history is rewritten and no branch is deleted.</label>}
    </Dialog>
  );
}
