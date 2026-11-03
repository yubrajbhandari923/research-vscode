import { useState } from 'react';
import { State, useAsync } from './common';
import type { Host } from './host';

interface Prov { name: string; provider: string; installed: boolean; version: string | null; capabilities: any; models: { source: string; models: { id: string; label: string }[] }; problem?: string }
interface Env {
  uv?: { path?: string | null; version?: string | null };
  root: string; folder_name: string; entries: string[]; empty: boolean; existing_project: boolean;
  git: { available: boolean; version: string | null; user_configured: boolean };
  providers: Prov[]; strategies: string[]; ssh_hosts: string[]; layout: Record<string, string>;
}

const STEPS = ['Learn', 'Project', 'Folders', 'Agents', 'Execution', 'Review'];
const BLURB: Record<string, string> = {
  claude: 'Anthropic’s Claude Code. Default. Interactive and headless, per-task model choice, reports token usage.',
  codex: 'OpenAI Codex CLI. Interactive and headless (`codex exec`), model and reasoning effort, runs in a sandbox.',
  agy: 'Google Antigravity CLI (`agy`). Interactive and headless, model and effort, JSON output. Not the same tool as Gemini CLI.',
};
const LABEL: Record<string, string> = { claude: 'Claude Code', codex: 'OpenAI Codex', agy: 'Google Antigravity' };

/** A short "what is this and why" box. Every step has one so the wizard teaches as it asks. */
const Learn = ({ title, children }: { title: string; children: any }) => (
  <aside className="banner info learn" aria-label={`About: ${title}`}><b>{title}</b><div>{children}</div></aside>
);

/** Initialize Project: from an empty folder to a working shared-ledger workspace. Nothing is written until the last step. */
export function InitProject({ host, onDone, onClassic }: { host: Host; onDone: (r: any) => void; onClassic: () => void }) {
  const env = useAsync<Env>(() => host.call('init_environment'), []);
  return <div className="page-body setup"><State a={env}>{(e) => <Wizard host={host} env={e} onDone={onDone} onClassic={onClassic} />}</State></div>;
}

function Wizard({ host, env, onDone, onClassic }: { host: Host; env: Env; onDone: (r: any) => void; onClassic: () => void }) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState(env.folder_name);
  const [goal, setGoal] = useState('');
  const [lay, setLay] = useState({ main_dir: 'main', ledger_dir: 'ledger', experiments_dir: 'experiments' });
  const [wsFile, setWsFile] = useState(true);
  const [remote, setRemote] = useState('');
  const [enabled, setEnabled] = useState<string[]>(['claude']);
  const [dflt, setDflt] = useState('claude');
  const [strategy, setStrategy] = useState('sonnet_first');
  const [review, setReview] = useState(true);
  const [hpc, setHpc] = useState(false);
  const [h, setH] = useState({ ssh_host: '', workspace_root: '', shared_data: '', scratch: '', partition: '', setup: '', python: '', required_packages: '' });
  const [py, setPy] = useState({ mode: env.uv?.path ? 'create' : 'none', path: '', packages: 'numpy, matplotlib' });
  const [pyProbe, setPyProbe] = useState<any>(null);
  const [compute, setCompute] = useState(false);
  const [probe, setProbe] = useState<any>(null);
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const [result, setResult] = useState<any>(null);

  const spec = () => ({
    name, goal, layout: lay, create_workspace: wsFile, git: remote ? { remote_url: remote } : {},
    agents: { enabled, default: dflt, strategy: dflt === 'claude' || enabled.includes('claude') ? strategy : undefined },
    verification: { independent_review: review }, hpc: hpc ? { enabled: true, ...h } : undefined,
    python: py.mode === 'none' ? undefined : py,
  });
  const go = async (n: number) => {
    setStep(n); setErr('');
    if (n === 5) { try { setPlan(await host.call('init_preview', { spec: spec() })); } catch (e: any) { setErr(e.message); } }
  };
  const toggle = (k: string) => {
    const on = enabled.includes(k) ? enabled.filter((x) => x !== k) : [...enabled, k];
    if (!on.length) return;
    setEnabled(on); if (!on.includes(dflt)) setDflt(on[0]);
  };
  const create = async () => {
    setBusy(true); setErr('');
    try { const r = await host.call('init_apply', { spec: spec() }); setResult(r); onDone(r); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };
  const testPy = async () => {
    setPyProbe({ busy: true });
    try { setPyProbe(await host.call('env_probe', { python: py.path, packages: py.packages.split(',').map((x) => x.trim()).filter(Boolean) })); } catch (e: any) { setPyProbe({ error: e.message }); }
  };
  const test = async () => {
    setProbe({ busy: true });
    try { setProbe(await host.call('hpc_test', { ssh_host: h.ssh_host, workspace_root: h.workspace_root, python: h.python, required_packages: h.required_packages, setup: h.setup, partition: h.partition, compute })); } catch (e: any) { setProbe({ error: e.message }); }
  };

  if (result) return <Done r={result} host={host} />;
  const canNext = step === 1 ? name.trim().length > 0 : true;
  const set = (k: string) => (e: any) => setLay({ ...lay, [k]: e.target.value });

  return (
    <div className="wizard">
      <h1>Initialize Research Project</h1>
      <p className="muted">About 2 minutes. You will review every change before anything is written.</p>
      <ol className="steps" aria-label="Initialize steps">
        {STEPS.map((s, i) => <li key={s} aria-current={i === step ? 'step' : undefined} className={i === step ? 'on' : i < step ? 'done' : ''}>{s}</li>)}
      </ol>
      {!env.empty && step === 0 && <div className="banner warn" role="status">This folder already has {env.entries.length} item(s). They will be left alone and never committed; Research OS only adds <code>main/</code>, <code>ledger/</code>, <code>experiments/</code> and a workspace file.</div>}
      {!env.git.available && <div className="banner bad" role="alert">Git was not found. Install git first: every experiment is a git worktree.</div>}

      {step === 0 && (
        <section aria-label="Learn">
          <Learn title="How Research OS works, in one minute">
            <ol>
              <li><b>Ask</b> a research <em>question</em>, then register an <em>experiment</em> (hypothesis, method, parameters, success criteria).</li>
              <li><b>Plan</b>: break the work into small <em>tasks</em> with acceptance checks (a command that must pass, a file that must exist, a metric threshold).</li>
              <li><b>Dispatch</b> a task to an AI agent (Claude, Codex or Antigravity). It works in that experiment’s own git worktree, so experiments never collide.</li>
              <li><b>Run</b> everything through <code>research run exec</code>: each run records code version, parameters, metrics and artifacts.</li>
              <li><b>Verify</b>: finishing the agent’s session is not the same as the task passing. The task’s checks must pass, and findings need an independent review before they count as “supported”.</li>
              <li><b>Switch agents any time.</b> All records live in the shared ledger, not in any agent, so another agent continues with the same history.</li>
            </ol>
          </Learn>
          <Learn title="What this wizard builds">
            <pre className="diff" aria-label="Folder layout">{`${env.folder_name}/
├── main/          code: checkout of branch main (your GitHub root)
├── ledger/        research records (.research/)
├── experiments/   one git worktree per experiment
└── ${env.folder_name}.code-workspace   opens main/ and ledger/ together`}</pre>
            <p className="muted small">All three folders belong to one git repository, so branches, history and GitHub work normally.</p>
          </Learn>
        </section>)}

      {step === 1 && (
        <section aria-label="Project">
          <label htmlFor="ip-name">Project name</label>
          <input id="ip-name" type="text" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          <label htmlFor="ip-goal">What are you trying to find out? <span className="muted">(optional; every agent reads this first)</span></label>
          <textarea id="ip-goal" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. Find the regularization strength that suppresses streak artifacts without blurring edges." />
          <Learn title="Tip">A one-sentence goal is the single most useful thing you can give an agent. You can edit it later in <code>ledger/.research/config.yaml</code> or Settings.</Learn>
        </section>)}

      {step === 2 && (
        <section aria-label="Folders">
          <Learn title="Why three folders?">
            <b>main/</b> holds your code. <b>ledger/</b> holds the research record on its own branch (<code>research/ledger</code>), so records are shared by every experiment and never mixed into your code history.
            <b> experiments/</b> gets a separate checkout per experiment, so two agents can work on two experiments at once. When you push, the files inside <code>main/</code> appear at the <i>top level</i> of GitHub’s <code>main</code> branch, with no extra <code>main/</code> folder.
          </Learn>
          <label htmlFor="ip-main">Code checkout folder</label><input id="ip-main" type="text" value={lay.main_dir} onChange={set('main_dir')} />
          <label htmlFor="ip-led">Ledger folder</label><input id="ip-led" type="text" value={lay.ledger_dir} onChange={set('ledger_dir')} />
          <label htmlFor="ip-exp">Experiment worktrees folder</label><input id="ip-exp" type="text" value={lay.experiments_dir} onChange={set('experiments_dir')} />
          <label className="check"><input type="checkbox" checked={wsFile} onChange={(e) => setWsFile(e.target.checked)} /> Create <code>{name || 'project'}.code-workspace</code> and open it when done (recommended)</label>
          <details><summary>GitHub remote (optional)</summary>
            <p className="muted">Adds <code>origin</code> only. <b>Nothing is pushed</b>; you push yourself when ready.</p>
            <label htmlFor="ip-remote">Remote URL</label><input id="ip-remote" type="text" value={remote} onChange={(e) => setRemote(e.target.value)} placeholder="git@github.com:you/sparse-ct.git" />
          </details>
        </section>)}

      {step === 3 && (
        <section aria-label="Agents">
          <Learn title="Agents are interchangeable workers">
            Research OS owns the plan, tasks, ledger and verification. An agent only does the work. Enable the ones you have; pick a default. You can choose a different one for any task later, and changing the default never touches a session that is already running.
          </Learn>
          {env.providers.map((p) => (
            <div key={p.name} className="card" style={{ margin: '8px 0', padding: 10 }}>
              <label className="check"><input type="checkbox" checked={enabled.includes(p.name)} onChange={() => toggle(p.name)} aria-label={`Enable ${LABEL[p.name]}`} />
                <b> {LABEL[p.name]}</b> {p.installed ? <span className="badge k-ok">installed {p.version || ''}</span> : <span className="badge k-warn">not installed: configurable, cannot run yet</span>}</label>
              <p className="muted small">{BLURB[p.name]}</p>
              {enabled.includes(p.name) && <label className="check"><input type="radio" name="dflt" checked={dflt === p.name} onChange={() => setDflt(p.name)} /> Use as the default agent</label>}
            </div>))}
          <label htmlFor="ip-strat">Model allocation (Claude)</label>
          <select id="ip-strat" value={strategy} onChange={(e) => setStrategy(e.target.value)}>{env.strategies.map((s) => <option key={s} value={s}>{s}{s === 'sonnet_first' ? ' (recommended)' : ''}</option>)}</select>
          <p className="muted small">“sonnet_first” plans and implements with Sonnet, uses a faster model for simple steps and escalates to Opus for hard scientific synthesis. Codex and Antigravity use the model you pick per dispatch or in their profile.</p>
          <label className="check"><input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} /> A finding needs an independent review before it counts as “supported” (recommended)</label>
        </section>)}

      {step === 4 && (
        <section aria-label="Execution">
          <p>Experiments run on this computer. <b>Local execution is on</b>.</p>
          <Learn title="Python environment for this computer (recommended)">
            Agents, task checks and <code>{'{python}'}</code> in checks all use <b>one</b> Python. Without it they use whatever <code>python3</code> is on your PATH, which often lacks the packages your code needs.
            Research OS can create the environment with <b>uv</b> (fast, nothing is installed globally), or you can point it at one you already have.
          </Learn>
          <div className="stack" role="radiogroup" aria-label="Python environment">
            <label className="check"><input type="radio" name="pymode" checked={py.mode === 'create'} disabled={!env.uv?.path} onChange={() => setPy({ ...py, mode: 'create' })} /> Create one with uv {env.uv?.path ? <span className="muted">({String(env.uv.version || 'uv').replace(/^uv\s+/, 'uv ')} found)</span> : <span className="help-warn">uv not found: install from docs.astral.sh/uv</span>}</label>
            <label className="check"><input type="radio" name="pymode" checked={py.mode === 'existing'} onChange={() => setPy({ ...py, mode: 'existing' })} /> Use an existing environment</label>
            <label className="check"><input type="radio" name="pymode" checked={py.mode === 'none'} onChange={() => setPy({ ...py, mode: 'none' })} /> Decide later</label>
          </div>
          {py.mode === 'existing' && <><label htmlFor="py-path">Venv folder or interpreter</label><input id="py-path" type="text" value={py.path} onChange={(e) => setPy({ ...py, path: e.target.value })} placeholder="/path/to/venv" /></>}
          {py.mode !== 'none' && <><label htmlFor="py-pk">Packages <span className="muted">(comma separated; created environments install them with uv)</span></label>
            <input id="py-pk" type="text" value={py.packages} onChange={(e) => setPy({ ...py, packages: e.target.value })} />
            {py.mode === 'create' && <p className="muted small">Created in <code>{env.folder_name}/.venv</code>, next to main/.</p>}
            {py.mode === 'existing' && <div className="row"><button type="button" disabled={!py.path || pyProbe?.busy} onClick={testPy}>Test this environment</button></div>}
            {pyProbe?.busy && <p className="muted">Checking…</p>}
            {pyProbe?.error && <div className="field-error" role="alert">{pyProbe.error}</div>}
            {pyProbe?.python && <p>{pyProbe.ok ? '✓' : '✗'} {pyProbe.python} {pyProbe.version ? `(Python ${pyProbe.version})` : ''}{pyProbe.problems?.length ? ` · ${pyProbe.problems.join('; ')}` : ''}</p>}</>}
          <Learn title="Optional: run on an HPC cluster">
            If you have a Slurm cluster, Research OS can ship only the <b>source code for one experiment</b> to it and bring results back. It does <b>not</b> copy your ledger or large datasets: the ledger stays here, and datasets stay where they are on the cluster.
            Connecting is read-only until you press <i>Test connection</i>, and nothing is created remotely during setup.
          </Learn>
          <label className="check"><input type="checkbox" checked={hpc} onChange={(e) => setHpc(e.target.checked)} /> Set up an HPC cluster</label>
          {hpc && <>
            <label htmlFor="hp-host">SSH host <span className="muted">(aliases from your ~/.ssh/config)</span></label>
            {env.ssh_hosts.length > 0
              ? <select id="hp-host" value={h.ssh_host} onChange={(e) => setH({ ...h, ssh_host: e.target.value })}><option value="">Choose…</option>{env.ssh_hosts.map((x) => <option key={x}>{x}</option>)}</select>
              : <input id="hp-host" type="text" value={h.ssh_host} onChange={(e) => setH({ ...h, ssh_host: e.target.value })} placeholder="my-cluster-login" />}
            <p className="muted small">Authentication stays with OpenSSH (keys, agent). Research OS stores no passwords or private keys. Use a key or agent: it cannot answer password or MFA prompts.</p>
            <label htmlFor="hp-ws">Remote workspace directory</label><input id="hp-ws" type="text" value={h.workspace_root} onChange={(e) => setH({ ...h, workspace_root: e.target.value })} placeholder="/scratch/you/research-os" />
            <label htmlFor="hp-data">Shared datasets directory <span className="muted">(read-only reuse, optional)</span></label><input id="hp-data" type="text" value={h.shared_data} onChange={(e) => setH({ ...h, shared_data: e.target.value })} />
            <label htmlFor="hp-scr">Scratch directory <span className="muted">(optional)</span></label><input id="hp-scr" type="text" value={h.scratch} onChange={(e) => setH({ ...h, scratch: e.target.value })} />
            <label htmlFor="hp-part">Slurm partition <span className="muted">(optional)</span></label><input id="hp-part" type="text" value={h.partition} onChange={(e) => setH({ ...h, partition: e.target.value })} />
            <label htmlFor="hp-setup">Job environment <span className="muted">(optional; one shell line per row, run inside every job)</span></label>
            <textarea id="hp-setup" rows={2} placeholder={'module load python/3.11\nsource ~/venvs/research/bin/activate'} value={h.setup} onChange={(e) => setH({ ...h, setup: e.target.value })} />
            <p className="muted small">Compute nodes often lack packages (for example NumPy) that the login node has. Without this, the first job can fail with ModuleNotFoundError.</p>
            <label htmlFor="hp-py">Python on the cluster <span className="muted">(optional; interpreter or venv path visible from compute nodes)</span></label><input id="hp-py" type="text" value={h.python} onChange={(e) => setH({ ...h, python: e.target.value })} placeholder="/home/you/venvs/research/bin/python" />
            <label htmlFor="hp-req">Packages the jobs need <span className="muted">(comma separated; checked by the test)</span></label><input id="hp-req" type="text" value={h.required_packages} onChange={(e) => setH({ ...h, required_packages: e.target.value })} placeholder="numpy" />
            <label className="check"><input type="checkbox" checked={compute} onChange={(e) => setCompute(e.target.checked)} /> Also test on a compute node <span className="muted">(runs a one-minute, one-CPU srun)</span></label>
            <div className="row"><button type="button" disabled={!h.ssh_host || probe?.busy} onClick={test}>Test connection (read-only)</button></div>
            {probe?.busy && <p className="muted">Connecting…</p>}
            {probe?.error && <div className="field-error" role="alert">{probe.error}</div>}
            {probe?.checks && <ul>{probe.checks.map((c: any) => <li key={c.check}>{c.ok ? '✓' : c.ok === false ? '✗' : '–'} {c.check}: {c.detail}{c.ok === false && c.fix ? ` (${c.fix})` : ''}</li>)}</ul>}
          </>}
        </section>)}

      {step === 5 && (
        <section aria-label="Review">
          {plan ? (plan.ok ? <>
            <p>Research OS will create:</p>
            <pre className="diff">{plan.tree.join('\n')}</pre>
            <p>and do exactly this:</p>
            <ul>{plan.actions.map((a: string) => <li key={a}>{a}</li>)}</ul>
            {plan.notes.map((n: string) => <p key={n} className="help-warn">{n}</p>)}
            <p className="muted small">Nothing is pushed anywhere. Existing files in this folder are not committed.</p>
          </> : <div className="field-error" role="alert"><b>Fix this first:</b><ul>{plan.problems.map((p: string) => <li key={p}>{p}</li>)}</ul></div>) : <p className="muted">Checking…</p>}
        </section>)}

      {err && <div className="field-error" role="alert">{err}</div>}
      <div className="wizard-actions">
        {step > 0 && <button type="button" onClick={() => go(step - 1)}>Back</button>}
        {step === 0 && <button type="button" className="link" onClick={onClassic}>Use a single folder instead (classic)</button>}
        <span className="spacer" />
        {step < 5 ? <button type="button" className="primary" disabled={!canNext} onClick={() => go(step + 1)}>Next</button>
          : <button type="button" className="primary" disabled={busy || !plan?.ok} onClick={create}>{busy ? 'Creating…' : 'Create project'}</button>}
      </div>
    </div>
  );
}

function Done({ r }: { r: any; host: Host }) {
  return (
    <div className="wizard">
      <h1>Project created</h1>
      <ul>{r.done.map((d: string) => <li key={d}>{d}</li>)}</ul>
      <Learn title="Your first 5 minutes">
        <ol>
          <li><b>Research: New experiment</b> — describe the question and hypothesis.</li>
          <li>In the <b>Work</b> tab, add a plan and tasks with acceptance checks (or dispatch the <i>planner</i> agent to draft them).</li>
          <li>Open a task → choose <b>Agent</b>, <b>Model</b>, <b>Mode</b> → <b>Preview</b>, then <b>Start</b>. The agent runs inside that experiment’s worktree.</li>
          <li>Watch it under <b>Settings → Agents &amp; Providers</b> (state: prepared → running → completed). Completed means the agent exited; then run <b>Verify</b>.</li>
          <li>Want a different agent for the next step? Dispatch the same task with another provider. History carries over.</li>
        </ol>
      </Learn>
      <p className="muted">The folder reopens as the multi-root workspace{r.workspace_file ? ` (${r.workspace_file.split('/').pop()})` : ''}. The quick start is <code>docs/QUICKSTART.md</code> in the Research OS repository.</p>
    </div>
  );
}
