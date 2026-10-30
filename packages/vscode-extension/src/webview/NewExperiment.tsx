import { useState } from 'react';
import { Dialog, useAsync } from './common';
import type { Host } from './host';

/** Guided "new experiment". The title and hypothesis are enough; worktree, task, sandbox and agent are opt-in and creating
 *  an experiment never runs anything, merges anything or deletes anything. */
export function NewExperiment({ host, onClose, onCreated }: { host: Host; onClose: () => void; onCreated: (r: any) => void }) {
  const ctx = useAsync<any>(async () => ({ research: await host.call('control', { page: 'research' }), work: await host.call('control', { page: 'work' }) }), []);
  const questions: any[] = ctx.data?.research.questions || [];
  const plans: any[] = ctx.data?.work.plans || [];
  const linked = ctx.data?.work.layout.mode !== 'classic';
  const profiles: any[] = (ctx.data?.work.execution.profiles || []).filter((p: any) => p.runtime !== 'native');

  const [title, setTitle] = useState('');
  const [hyp, setHyp] = useState('');
  const [qSel, setQSel] = useState('');           // '' = none, 'new' = type one, else an id
  const [qTitle, setQTitle] = useState('');
  const [more, setMore] = useState(false);
  const [worktree, setWorktree] = useState(false);
  const [task, setTask] = useState(false);
  const [planSel, setPlanSel] = useState('');
  const [profile, setProfile] = useState('');
  const [agent, setAgent] = useState(false);
  const [plan, setPlan] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const spec = () => ({
    title, hypothesis: hyp,
    question: qSel === 'new' ? { title: qTitle } : qSel ? { id: qSel } : undefined,
    worktree: worktree ? { create: true } : undefined,
    task: task || agent ? { create: true, plan: planSel || undefined } : undefined,
    execution: profile ? { profile } : undefined,
    agent: agent ? { dispatch: true, role: 'implementer' } : undefined,
  });
  const check = async () => { try { setPlan(await host.call('new_experiment_preview', { spec: spec() })); } catch (e: any) { setErr(e.message); } };
  const create = async () => {
    setBusy(true); setErr('');
    try { const r = await host.call('new_experiment', { spec: spec() }); setResult(r); onCreated(r); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  if (result) {
    return (
      <Dialog title={`${result.experiment} created`} onClose={onClose} actions={<><button type="button" onClick={() => host.openEntity(result.experiment)}>Open experiment</button><button type="button" className="primary" data-autofocus onClick={onClose}>Done</button></>}>
        <ul>
          {result.question && <li>Question <b>{result.question}</b></li>}
          <li>Experiment <b>{result.experiment}</b> (proposed)</li>
          {result.task && <li>Task <b>{result.task}</b> in plan {result.plan}</li>}
          {result.workspace && <li>Workspace <b>{result.workspace.id}</b> on <code>{result.workspace.branch}</code><div className="muted small">{result.workspace.path}</div></li>}
          {result.sandbox_profile && <li>Jobs will use the <b>{result.sandbox_profile}</b> sandbox profile</li>}
        </ul>
        {result.dispatch && <><p>Agent prepared, not started. Run this where the agent CLI is installed:</p><pre className="diff" tabIndex={0}>{result.dispatch.command}</pre>
          {!result.dispatch.available && <p className="help-warn">The agent CLI was not found on this machine.</p>}</>}
        <p className="muted">Next: run it with <code>{result.next[0]}</code>. Nothing has been run yet.</p>
      </Dialog>);
  }
  return (
    <Dialog title="New experiment" onClose={onClose} actions={<>
      <button type="button" onClick={onClose}>Cancel</button>
      {!plan && <button type="button" className="primary" disabled={!title.trim()} onClick={check}>Review</button>}
      {plan && <button type="button" onClick={() => setPlan(null)}>Edit</button>}
      {plan && <button type="button" className="primary" data-autofocus disabled={busy || !plan.ok} onClick={create}>{busy ? 'Creating…' : 'Create'}</button>}</>}>
      {!plan ? (
        <>
          <label htmlFor="ne-title">Title</label>
          <input id="ne-title" data-autofocus type="text" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Regularization sweep" />
          <label htmlFor="ne-hyp">Hypothesis <span className="muted">(what do you expect, and why?)</span></label>
          <textarea id="ne-hyp" rows={3} value={hyp} onChange={(e) => setHyp(e.target.value)} />
          <label htmlFor="ne-q">Question it answers</label>
          <select id="ne-q" value={qSel} onChange={(e) => setQSel(e.target.value)}>
            <option value="">None yet</option>{questions.map((q) => <option key={q.id} value={q.id}>{q.id} · {q.title}</option>)}<option value="new">A new question…</option></select>
          {qSel === 'new' && <input type="text" aria-label="New question" placeholder="The question this experiment helps answer" value={qTitle} onChange={(e) => setQTitle(e.target.value)} />}
          <details open={more} onToggle={(e) => setMore((e.target as HTMLDetailsElement).open)}>
            <summary>More options</summary>
            <label className="check"><input type="checkbox" checked={worktree} disabled={!linked} onChange={(e) => setWorktree(e.target.checked)} /> Work on its own git branch and folder</label>
            {!linked && <p className="muted small">Available after enabling the shared ledger (Settings → Git &amp; workspaces).</p>}
            <label className="check"><input type="checkbox" checked={task || agent} disabled={agent} onChange={(e) => setTask(e.target.checked)} /> Track the work as a task</label>
            {(task || agent) && <select aria-label="Plan" value={planSel} onChange={(e) => setPlanSel(e.target.value)}><option value="">In a new plan</option>{plans.map((p) => <option key={p.id} value={p.id}>{p.id} · {p.title}</option>)}</select>}
            <label className="check"><input type="checkbox" checked={agent} onChange={(e) => setAgent(e.target.checked)} /> Prepare an agent to implement it <span className="muted">(prepares the brief only)</span></label>
            {profiles.length > 0 && <><label htmlFor="ne-prof">Run its jobs in a restricted sandbox</label>
              <select id="ne-prof" value={profile} onChange={(e) => setProfile(e.target.value)}><option value="">No (run natively)</option>{profiles.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}</select></>}
          </details>
        </>
      ) : (plan.ok ? <><p>This will:</p><ul>{plan.actions.map((a: string) => <li key={a}>{a}</li>)}</ul><p className="muted small">It will not run anything, merge any code, or delete anything.</p></>
        : <div className="field-error" role="alert"><b>Fix this first:</b><ul>{plan.problems.map((p: string) => <li key={p}>{p}</li>)}</ul></div>)}
      {err && <div className="field-error" role="alert">{err}</div>}
    </Dialog>
  );
}
