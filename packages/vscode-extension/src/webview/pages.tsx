import { useState } from 'react';
import { Badge, Breadcrumbs, Dialog, Empty, State, useAsync } from './common';
import type { Host } from './host';
import type { Attention, Page } from './types';

const KIND_LABEL: Record<string, string> = { synthesis: 'Synthesize', review: 'Review', blocked: 'Blocked', approval: 'Approve', workspace: 'Workspace', promotion: 'Promotion' };

export function HomePage({ host, go }: { host: Host; go: (p: Page, target?: string) => void }) {
  const a = useAsync<any>(() => host.call('control', { page: 'home' }), []);
  const open = (i: Attention) => (i.open.page === 'research' || i.open.page === 'work') && /^[A-Z]+-/.test(i.open.target) ? host.openEntity(i.open.target) : go(i.open.page, i.open.target);
  return (
    <div className="page-body">
      <Breadcrumbs trail={['Project', 'Home']} />
      <State a={a}>{(d) => (
        <>
          <header className="lead">
            <h1>{d.project.name}</h1>
            {d.project.goal ? <p className="muted">{d.project.goal}</p> : <p className="muted">No research goal yet. Add one under Settings → Project.</p>}
          </header>
          <section aria-labelledby="att"><h2 id="att">Needs your attention <span className="count">{d.attention.length}</span></h2>
            {d.attention.length === 0 ? <Empty title="Nothing is waiting on you.">New syntheses, reviews, blocked tasks and approvals appear here.</Empty> :
              <ul className="attention">{d.attention.map((i: Attention) => (
                <li key={i.kind + i.id} className={i.severity}>
                  <button type="button" onClick={() => open(i)}>
                    <Badge kind={i.severity === 'warn' ? 'warn' : ''}>{KIND_LABEL[i.kind] || i.kind}</Badge>
                    <span className="grow"><b>{i.title}</b>{i.detail && <span className="muted"> — {i.detail}</span>}</span>
                    <span aria-hidden>›</span></button></li>))}</ul>}
          </section>
          <div className="cols">
            <section aria-labelledby="nxt"><h2 id="nxt">What next</h2>
              {d.focus?.next_step || d.next_task ? <p>{d.next_task ? <><button className="link" onClick={() => host.openEntity(d.next_task.id)}>{d.next_task.id}</button> {d.next_task.title}</> : d.focus.next_step}</p>
                : <Empty title="No ready task.">Create a plan under Work, or add an experiment under Research.</Empty>}
              {d.focus?.blocker && <p className="help-warn">Blocker: {String(d.focus.blocker)}</p>}
            </section>
            <section aria-labelledby="run"><h2 id="run">Running now <span className="count">{d.running.length}</span></h2>
              {d.running.length === 0 ? <p className="muted">No runs in progress.</p> :
                <table><caption className="sr">Runs in progress</caption><thead><tr><th scope="col">Run</th><th scope="col">Experiment</th><th scope="col">Where</th><th scope="col">State</th></tr></thead>
                  <tbody>{d.running.map((r: any) => <tr key={r.id}><td><button className="link" onClick={() => host.openEntity(r.id)}>{r.id}</button></td><td>{r.experiment_id}</td><td>{r.backend}</td><td><Badge>{r.status}</Badge></td></tr>)}</tbody></table>}
            </section>
          </div>
          <section aria-labelledby="lrn"><h2 id="lrn">Recently learned</h2>
            {d.learned.length === 0 ? <p className="muted">No findings yet. A finding is an interpreted result backed by evidence.</p> :
              <ul className="plain">{d.learned.map((f: any) => <li key={f.id}><button className="link" onClick={() => host.openEntity(f.id)}>{f.id}</button> {f.title} <Badge kind={f.status === 'supported' ? 'ok' : f.status === 'contradicted' ? 'warn' : ''}>{f.status}</Badge></li>)}</ul>}
          </section>
        </>
      )}</State>
    </div>
  );
}

export function WorkPage({ host, go }: { host: Host; go: (p: Page, target?: string) => void }) {
  const a = useAsync<any>(() => host.call('control', { page: 'work' }), []);
  const [creating, setCreating] = useState(false);
  const [impact, setImpact] = useState<any>(null);
  const [promo, setPromo] = useState<any>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  return (
    <div className="page-body">
      <Breadcrumbs trail={['Project', 'Work']} />
      <State a={a}>{(d) => (
        <>
          <section aria-labelledby="pl"><h2 id="pl">Plans and tasks</h2>
            {d.plans.length === 0 ? <Empty title="No plans yet.">A plan breaks an objective into tasks that agents or you can claim.</Empty> :
              d.plans.map((pl: any) => (
                <div key={pl.id} className="plan"><h3><button className="link" onClick={() => host.openEntity(pl.id)}>{pl.id}</button> {pl.title} <Badge>{pl.status}</Badge></h3>
                  <table><caption className="sr">Tasks in {pl.title}</caption><thead><tr><th scope="col">Task</th><th scope="col">Status</th><th scope="col">Role</th></tr></thead><tbody>
                    {pl.tasks.map((t: any) => <tr key={t.id}><td><button className="link" onClick={() => host.openEntity(t.id)}>{t.id}</button> {t.title}</td>
                      <td><Badge kind={t.status === 'blocked' ? 'warn' : t.status === 'done' ? 'ok' : ''}>{t.status}</Badge></td><td className="muted">{t.assigned_role || '—'}</td></tr>)}</tbody></table></div>))}
          </section>

          <section aria-labelledby="ws"><h2 id="ws">Workspaces <span className="count">{d.workspaces.length}</span></h2>
            {d.layout.mode === 'classic' ? <Empty title="Single-folder project.">Experiment branches and worktrees need a shared research ledger so every branch keeps one scientific history. Preview the change with <code>research ledger enable</code>; nothing moves until you add <code>--apply</code>.</Empty> : (
              <>
                <div className="row"><button type="button" className="primary" onClick={() => setCreating(true)}>New experiment workspace…</button>
                  <span className="muted small">Ledger: <code>{d.layout.ledger_root}</code></span></div>
                {msg && <div className={'banner ' + (msg.ok ? 'ok' : 'bad')} role="status">{msg.text}</div>}
                {d.workspaces.length === 0 ? <p className="muted">No workspaces yet.</p> :
                  <table><caption className="sr">Experiment workspaces</caption><thead><tr><th scope="col">Workspace</th><th scope="col">Branch</th><th scope="col">Experiment</th><th scope="col">State</th><th scope="col">Agent / tasks</th><th scope="col"><span className="sr">Actions</span></th></tr></thead><tbody>
                    {d.workspaces.map((w: any) => (
                      <tr key={w.id}><td>{w.id}</td><td><code>{w.branch}</code><div className="muted small">base {String(w.base_rev || '').slice(0, 8)}{w.commits_ahead != null ? ` · ${w.commits_ahead} ahead` : ''}</div></td>
                        <td>{w.experiment ? <button className="link" onClick={() => host.openEntity(w.experiment)}>{w.experiment}</button> : '—'}</td>
                        <td>{!w.present ? <Badge kind="warn">missing</Badge> : w.dirty || w.untracked ? <Badge kind="warn">uncommitted</Badge> : <Badge kind="ok">clean</Badge>}{w.active_runs.length > 0 && <Badge>{w.active_runs.length} running</Badge>}</td>
                        <td className="muted">{[w.agent, w.tasks.length ? w.tasks.join(', ') : null].filter(Boolean).join(' · ') || '—'}</td>
                        <td><button type="button" onClick={async () => setImpact(await host.call('workspace_archive', { id: w.id, dry_run: true }))}>Archive…</button></td></tr>))}</tbody></table>}
              </>)}
          </section>

          {d.layout.mode !== 'classic' && (
            <section aria-labelledby="pr"><h2 id="pr">Promotion candidates <span className="count">{d.promotions.length}</span></h2>
              <p className="muted">Move only the code worth keeping into <code>main</code>. Nothing merges without tests, an independent review and your approval.</p>
              {d.promotions.length === 0 ? <p className="muted">None. Prepare one from a workspace with <code>research promote prepare WS-001 --commit …</code>.</p> :
                <table><caption className="sr">Promotion candidates</caption><thead><tr><th scope="col">Candidate</th><th scope="col">State</th><th scope="col">Tests</th><th scope="col">Evidence</th><th scope="col">Review</th><th scope="col"><span className="sr">Actions</span></th></tr></thead><tbody>
                  {d.promotions.map((c: any) => <tr key={c.id}><td><b>{c.id}</b> {c.title}<div className="muted small"><code>{c.branch}</code></div></td><td><Badge kind={c.state === 'approved' ? 'ok' : ''}>{c.state}</Badge></td>
                    <td><Badge kind={c.gates.tests.state === 'passed' ? 'ok' : c.gates.tests.state === 'failed' ? 'warn' : ''}>{c.gates.tests.state}</Badge></td>
                    <td><Badge kind={c.gates.evidence.state === 'passed' ? 'ok' : c.gates.evidence.state === 'blocked' ? 'warn' : ''}>{c.gates.evidence.state}</Badge></td>
                    <td><Badge kind={c.gates.review.state === 'approved' ? 'ok' : ''}>{c.gates.review.state.replace('_', ' ')}</Badge></td>
                    <td><button type="button" onClick={() => setPromo(c)}>Open…</button></td></tr>)}</tbody></table>}
            </section>)}

          <section aria-labelledby="ex"><h2 id="ex">Execution profiles</h2>
            <p className="muted">Jobs run natively unless a profile applies. A restrictive profile is never silently replaced by native execution.</p>
            <table><caption className="sr">Sandbox profiles</caption><thead><tr><th scope="col">Profile</th><th scope="col">Runtime</th><th scope="col">Network</th><th scope="col">GPU</th><th scope="col">Source access</th></tr></thead><tbody>
              {[...d.execution.profiles].sort((a: any, b: any) => (a.runtime === 'native' ? -1 : 0) - (b.runtime === 'native' ? -1 : 0) || a.name.localeCompare(b.name)).map((p: any) => <tr key={p.name}><td><b>{p.name}</b>{d.execution.applies && d.execution.default === p.name && <Badge kind="ok">default</Badge>}</td><td>{p.runtime}</td><td>{p.network}</td><td>{p.gpu ? 'yes' : 'no'}</td><td>{p.runtime === 'native' ? '—' : p.source.replace('_', ' ')}</td></tr>)}</tbody></table>
            <p className="small muted">{d.execution.agent_boundary.note}</p>
            <button type="button" className="link" onClick={() => go('settings', 'sandbox')}>Configure execution and sandbox settings</button>
          </section>

          <section aria-labelledby="rn"><h2 id="rn">Recent runs</h2>
            {d.runs.length === 0 ? <p className="muted">No runs yet.</p> :
              <table><caption className="sr">Recent runs</caption><thead><tr><th scope="col">Run</th><th scope="col">Experiment</th><th scope="col">Backend</th><th scope="col">Status</th></tr></thead><tbody>
                {d.runs.slice(0, 12).map((r: any) => <tr key={r.id}><td><button className="link" onClick={() => host.openEntity(r.id)}>{r.id}</button></td><td>{r.experiment_id}</td><td>{r.backend}{r.slurm_job_id ? ` #${r.slurm_job_id}` : ''}</td><td><Badge kind={r.status === 'failed' ? 'warn' : r.status === 'completed' ? 'ok' : ''}>{r.status}</Badge></td></tr>)}</tbody></table>}
          </section>

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
      )}</State>
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

export function ResearchPage({ host }: { host: Host }) {
  const a = useAsync<any>(() => host.call('control', { page: 'research' }), []);
  return (
    <div className="page-body">
      <Breadcrumbs trail={['Project', 'Research']} />
      <State a={a}>{(d) => (
        <>
          <section aria-labelledby="ex2"><h2 id="ex2">Experiments <span className="count">{d.experiments.length}</span></h2>
            {d.experiments.length === 0 ? <Empty title="No experiments yet.">An experiment states a hypothesis before any run exists, so results can be interpreted honestly.</Empty> :
              <table><caption className="sr">Experiments</caption><thead><tr><th scope="col">Experiment</th><th scope="col">Status</th><th scope="col" className="num">Runs</th><th scope="col" className="num">Failed</th><th scope="col" className="num">Unsynthesized</th></tr></thead><tbody>
                {d.experiments.map((e: any) => <tr key={e.id}><td><button className="link" onClick={() => host.openEntity(e.id)}>{e.id}</button> {e.title}<div className="muted small clamp1">{e.hypothesis}</div></td>
                  <td><Badge>{e.status}</Badge></td><td className="num">{e.runs}</td><td className="num">{e.failed || ''}</td><td className="num">{e.unsynthesized ? <Badge kind="warn">{e.unsynthesized}</Badge> : ''}</td></tr>)}</tbody></table>}
          </section>
          <div className="cols">
            <section aria-labelledby="fi"><h2 id="fi">Findings <span className="count">{d.findings.length}</span></h2>
              {d.findings.length === 0 ? <p className="muted">None yet.</p> : <ul className="plain">{d.findings.map((f: any) => <li key={f.id}><button className="link" onClick={() => host.openEntity(f.id)}>{f.id}</button> {f.title} <Badge kind={f.status === 'supported' ? 'ok' : f.status === 'contradicted' ? 'warn' : ''}>{f.status}</Badge></li>)}</ul>}</section>
            <section aria-labelledby="qu"><h2 id="qu">Questions</h2>
              {d.questions.length === 0 ? <p className="muted">None yet.</p> : <ul className="plain">{d.questions.map((q: any) => <li key={q.id}><button className="link" onClick={() => host.openEntity(q.id)}>{q.id}</button> {q.title} <Badge>{q.status}</Badge></li>)}</ul>}
              <h2>Decisions</h2>{d.decisions.length === 0 ? <p className="muted">None yet.</p> : <ul className="plain">{d.decisions.map((x: any) => <li key={x.id}><button className="link" onClick={() => host.openEntity(x.id)}>{x.id}</button> {x.title}</li>)}</ul>}</section>
          </div>
        </>
      )}</State>
    </div>
  );
}

function PromotionDialog({ host, candidate, onClose }: { host: Host; candidate: any; onClose: () => void }) {
  const [c, setC] = useState<any>(candidate);
  const diff = useAsync<any>(() => host.call('promotion_diff', { id: c.id }), [c.gates.head]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [sure, setSure] = useState(false);
  const act = async (action: string, extra: any = {}) => {
    setBusy(true); setErr('');
    try { setC(await host.call('promotion_action', { id: c.id, action, ...extra })); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const g = c.gates;
  const row = (label: string, gate: any, children?: any) => <tr><th scope="row">{label}</th><td><Badge kind={['passed', 'approved'].includes(gate.state) ? 'ok' : ['failed', 'blocked', 'stale'].includes(gate.state) ? 'warn' : ''}>{gate.state.replace('_', ' ')}</Badge></td><td className="muted">{gate.detail || gate.note || ''}{children}</td></tr>;
  return (
    <Dialog title={`${c.id} · ${c.title}`} onClose={onClose} actions={<>
      <button type="button" onClick={onClose} data-autofocus>Close</button>
      {c.state !== 'promoted' && c.state !== 'abandoned' && <button type="button" disabled={busy} onClick={() => act('abandon')}>Abandon</button>}
      <button type="button" disabled={busy || c.state === 'promoted'} onClick={() => act('verify')}>Run tests</button>
      <button type="button" disabled={busy || g.review.state === 'approved' || g.tests.state !== 'passed'} onClick={() => act('review', { verdict: 'approve' })}>Approve code review</button>
      <button type="button" className="primary" disabled={busy || c.state !== 'reviewed'} onClick={() => act('approve')}>Approve promotion</button>
      <button type="button" className="primary" disabled={busy || c.state !== 'approved' || !sure} onClick={() => act('integrate', { confirm: true })}>Merge into main</button></>}>
      <p className="muted">Candidate <code>{c.branch}</code> at <code>{String(g.head).slice(0, 8)}</code>, built from <code>main</code> with only the selected changes. The experiment workspace is untouched.</p>
      <table><caption className="sr">Gates</caption><tbody>
        {row('Tests', g.tests)}{row('Scientific evidence', g.evidence)}{row('Implementation review', g.review)}{row('Human approval', g.approval)}</tbody></table>
      <p className="small muted">Scientifically successful: <b>{String(c.classification.scientifically_successful ?? 'not known')}</b> · technically correct: <b>{String(c.classification.technically_correct ?? 'not known')}</b> · integration-ready: <b>{String(c.classification.integration_ready)}</b> · promoted: <b>{String(c.classification.promoted)}</b></p>
      {err && <div className="field-error" role="alert">{err}</div>}
      <h3>Patch main would receive</h3>
      <State a={diff}>{(d) => <><p className="muted small">{d.stat}</p><pre className="diff" tabIndex={0} aria-label="Patch">{d.patch.split('\n').slice(0, 400).map((l: string, i: number) => <div key={i} className={l.startsWith('+') && !l.startsWith('+++') ? 'add' : l.startsWith('-') && !l.startsWith('---') ? 'del' : ''}>{l || ' '}</div>)}</pre></>}</State>
      {c.state === 'approved' && <label className="check"><input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} /> I have read the patch. Moving <code>main</code> is a fast-forward merge; no history is rewritten and no branch is deleted.</label>}
    </Dialog>
  );
}
