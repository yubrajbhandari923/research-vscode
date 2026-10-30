import { useState } from 'react';
import { State, useAsync } from './common';
import type { Host } from './host';

interface Env {
  root: string; folder_name: string; existing_project: boolean;
  git: { available: boolean; repo: boolean; has_commit: boolean };
  agents: Record<string, boolean>; strategies: string[];
  runtimes: { runtime: string; available: boolean; notes: string[] }[];
}

const STEPS = ['Project', 'How you work', 'Agents', 'Execution', 'Review'];

/** First-run setup. Starts with two fields; everything else is optional and revealed only when you ask for it. */
export function Setup({ host, onDone }: { host: Host; onDone: () => void }) {
  const env = useAsync<Env>(() => host.call('setup_environment'), []);
  return (
    <div className="page-body setup">
      <State a={env}>{(e) => <Wizard host={host} env={e} onDone={onDone} />}</State>
    </div>
  );
}

function Wizard({ host, env, onDone }: { host: Host; env: Env; onDone: () => void }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState(env.folder_name);
  const [goal, setGoal] = useState('');
  const [gitInit, setGitInit] = useState(false);
  const [ledger, setLedger] = useState(false);
  const [provider, setProvider] = useState('');
  const [strategy, setStrategy] = useState('');
  const [isolate, setIsolate] = useState(false);
  const [datasets, setDatasets] = useState('');
  const [review, setReview] = useState(true);
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const spec = () => ({
    name, goal, git_init: gitInit, shared_ledger: ledger,
    agent: provider ? { provider, strategy: provider === 'claude' && strategy ? strategy : undefined } : undefined,
    isolation: isolate ? { enabled: true, profile: 'restricted', datasets: datasets.split('\n').map((s) => s.trim()).filter(Boolean) } : undefined,
    verification: { independent_review: review },
  });
  const preview = async () => { try { setPlan(await host.call('setup_preview', { spec: spec() })); setErr(''); } catch (e: any) { setErr(e.message); } };
  const go = async (n: number) => { setStep(n); if (n === 4) await preview(); };
  const create = async () => {
    setBusy(true); setErr('');
    try { await host.call('setup_apply', { spec: spec() }); onDone(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const runtimeOk = env.runtimes.some((r) => r.available);
  const canNext = step === 0 ? name.trim().length > 0 : true;

  return (
    <div className="wizard">
      <h1>Set up Research OS</h1>
      <p className="muted">Two answers are enough to start. Everything else can be changed later in Settings.</p>
      <ol className="steps" aria-label="Setup steps">
        {STEPS.map((s, i) => <li key={s} aria-current={i === step ? 'step' : undefined} className={i === step ? 'on' : i < step ? 'done' : ''}>{s}</li>)}
      </ol>

      {step === 0 && (
        <section aria-label="Project">
          <label htmlFor="su-name">Project name</label>
          <input id="su-name" type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          <label htmlFor="su-goal">What are you trying to find out? <span className="muted">(optional; agents read this first)</span></label>
          <textarea id="su-goal" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. Find the regularization strength that suppresses streak artifacts without blurring edges." />
          <p className="muted small">Location: <code>{env.root}</code></p>
        </section>)}

      {step === 1 && (
        <section aria-label="How you work">
          <p>By default Research OS keeps its records in <code>.research/</code> next to your code. That is all most projects need.</p>
          <details open={ledger || gitInit}>
            <summary>Try ideas on separate git branches (optional)</summary>
            <p className="muted">Each experiment can get its own branch and folder, while all of them share one research history. Good for trying different implementations without touching <code>main</code>.</p>
            {!env.git.available && <p className="help-warn">Git was not found on this machine.</p>}
            {env.git.available && !env.git.repo && <label className="check"><input type="checkbox" checked={gitInit} onChange={(e) => { setGitInit(e.target.checked); if (e.target.checked) setLedger(false); }} /> Create a git repository in this folder</label>}
            {env.git.repo && !env.git.has_commit && <p className="help-warn">This repository has no commits yet. Make a first commit, then enable this in Settings.</p>}
            <label className="check"><input type="checkbox" disabled={!env.git.repo || !env.git.has_commit} checked={ledger} onChange={(e) => setLedger(e.target.checked)} />
              Use a shared research ledger so experiments can have their own worktrees</label>
            {!env.git.repo && <p className="muted small">Needs an existing git repository with at least one commit.</p>}
            {ledger && <p className="muted small">Creates a separate ledger folder next to this one; your current <code>.research/</code> is kept as <code>.research.pre-ledger</code>.</p>}
          </details>
        </section>)}

      {step === 2 && (
        <section aria-label="Agents">
          <p>Do you use an AI coding agent on this project? Research OS can hand it tasks and give it the project’s rules. You can skip this.</p>
          <label htmlFor="su-prov">Agent</label>
          <select id="su-prov" value={provider} onChange={(e) => { setProvider(e.target.value); if (e.target.value !== 'claude') setStrategy(''); }}>
            <option value="">I’ll decide later</option>
            {Object.entries(env.agents).map(([k, ok]) => <option key={k} value={k}>{k}{ok ? '' : ' (not installed on this machine)'}</option>)}
          </select>
          {provider === 'claude' && (
            <details>
              <summary>Model allocation (optional)</summary>
              <p className="muted">Decides which model level plans, implements and interprets. “sonnet_first” is a sensible default.</p>
              <label htmlFor="su-strat">Strategy</label>
              <select id="su-strat" value={strategy} onChange={(e) => setStrategy(e.target.value)}><option value="">Don’t use model routing</option>{env.strategies.map((s) => <option key={s} value={s}>{s}</option>)}</select>
            </details>)}
          <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> Findings need an independent review before they count as “supported” (recommended)</label>
        </section>)}

      {step === 3 && (
        <section aria-label="Execution">
          <p>Experiments run on this computer unless you set up a cluster later (Settings → HPC). Nothing to configure for that now.</p>
          <details open={isolate}>
            <summary>Restrict what jobs can read and write (optional)</summary>
            <p className="muted">Jobs get a read-only copy of your code, only the datasets you list, and one output folder. This is for accident prevention, not a hardened security boundary.</p>
            {!runtimeOk
              ? <p className="help-warn">No isolation runtime is available on this machine, so jobs would be refused. Leave this off.</p>
              : <label className="check"><input type="checkbox" checked={isolate} onChange={(e) => setIsolate(e.target.checked)} /> Run jobs in a restricted sandbox by default</label>}
            {isolate && <><label htmlFor="su-ds">Datasets jobs may read <span className="muted">(one absolute path per line)</span></label>
              <textarea id="su-ds" rows={3} value={datasets} onChange={(e) => setDatasets(e.target.value)} /></>}
          </details>
        </section>)}

      {step === 4 && (
        <section aria-label="Review">
          {plan ? (plan.ok ? <><p>Research OS will:</p><ul>{plan.actions.map((a: string) => <li key={a}>{a}</li>)}</ul></>
            : <div className="field-error" role="alert"><b>Fix this first:</b><ul>{plan.problems.map((p: string) => <li key={p}>{p}</li>)}</ul></div>) : <p className="muted">Checking…</p>}
        </section>)}

      {err && <div className="field-error" role="alert">{err}</div>}
      <div className="wizard-actions">
        {step > 0 && <button type="button" onClick={() => go(step - 1)}>Back</button>}
        <span className="spacer" />
        {step < 4 && <button type="button" onClick={() => go(4)} className="link">Skip to review</button>}
        {step < 4 ? <button type="button" className="primary" disabled={!canNext} onClick={() => go(step + 1)}>Next</button>
          : <button type="button" className="primary" disabled={busy || !plan?.ok} onClick={create}>{busy ? 'Creating…' : 'Create project'}</button>}
      </div>
    </div>
  );
}

export function Incompatible({ message, host }: { message: string; host: Host }) {
  return (
    <div className="page-body">
      <h1>This is not a Research OS 2 project</h1>
      <div className="banner bad" role="alert">{message}</div>
      <p>Nothing was changed, and your old records are untouched. To keep them and start fresh:</p>
      <ol><li>Rename the folder <code>.research</code> to <code>.research-v1-archive</code> (rename, don’t delete).</li><li>Reload this window and set up a new project.</li></ol>
      <button type="button" onClick={() => host.openDoc('docs/GUIDE_V2_START_FRESH.md')}>Read the guide</button>
    </div>
  );
}

