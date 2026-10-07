import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { Model } from './model';
import { openPath, Panels } from './panels';
import { Field, FormSpec } from './render/form';
import { Shim } from './shim';

const Q_STATUS: [string, string, string][] = [['open', 'open', 'blue'], ['investigating', 'investigating', 'yellow'], ['answered', 'answered', 'green'], ['blocked', 'blocked', 'red'], ['abandoned', 'abandoned', 'gray']];
const E_STATUS: [string, string, string][] = [['proposed', 'proposed', 'gray'], ['ready', 'ready', 'blue'], ['running', 'running', 'yellow'], ['needs_review', 'needs review', 'orange'], ['completed', 'completed', 'green'], ['failed', 'failed', 'red'], ['abandoned', 'abandoned', 'gray']];
const F_STATUS: [string, string, string][] = [['preliminary', 'preliminary', 'yellow'], ['supported', 'supported', 'green'], ['contradicted', 'contradicted', 'orange'], ['superseded', 'superseded', 'gray']];
const D_STATUS: [string, string, string][] = [['active', 'active', 'green'], ['reversed', 'reversed', 'gray'], ['superseded', 'superseded', 'gray']];
const CONF: [string, string, string][] = [['low', 'low', 'orange'], ['medium', 'medium', 'yellow'], ['high', 'high', 'green']];
const PLAN_STATUS: [string, string, string][] = [['active', 'active', 'blue'], ['completed', 'completed', 'green'], ['blocked', 'blocked', 'red'], ['abandoned', 'abandoned', 'gray']];
const TASK_STATUS: [string, string, string][] = [['todo', 'to do', 'gray'], ['running', 'running', 'yellow'], ['verify', 'verify', 'blue'], ['done', 'done', 'green'], ['blocked', 'blocked', 'red']];
const TASK_TYPES: [string, string, string][] = [['research', 'research', 'blue'], ['implementation', 'implementation', 'purple'], ['experiment', 'experiment', 'yellow'], ['analysis', 'analysis', 'green'], ['verification', 'verification', 'orange'], ['synthesis', 'synthesis', 'blue'], ['debug', 'debug', 'red'], ['data', 'data', 'gray']];
const TASK_ROLES: [string, string, string][] = [['planner', 'planner', 'blue'], ['implementer', 'implementer', 'purple'], ['verifier', 'verifier', 'orange'], ['analyst', 'analyst', 'green'], ['human', 'human', 'yellow']];

function idArg(a: any): string | undefined {
  if (!a) return undefined;
  if (typeof a === 'string') return a;
  if (a.key && typeof a.key === 'string') return a.key.split('/').pop(); // tree node
  if (a.id) return a.id;
  return undefined;
}

export function registerCommands(ctx: vscode.ExtensionContext, model: Model, panels: Panels, shim: Shim, out: vscode.OutputChannel) {
  const rpc = <T = any>(m: string, p: any = {}) => model.client.request<T>(m, p);
  const reg = (id: string, fn: (...a: any[]) => any) =>
    ctx.subscriptions.push(
      vscode.commands.registerCommand(id, async (...a: any[]) => {
        try {
          return await fn(...a);
        } catch (e: any) {
          const msg = e.message + (e.hint ? ` — ${e.hint}` : '');
          if (e.code === 2) {
            const pick = await vscode.window.showWarningMessage(msg, 'Initialize Project');
            if (pick) vscode.commands.executeCommand('research.init');
          } else vscode.window.showErrorMessage(msg);
          out.appendLine(`[command ${id}] ${e.stack || e.message}`);
        }
      }),
    );
  const done = (msg?: string) => {
    model.schedule(0);
    if (msg) vscode.window.setStatusBarMessage(`$(check) ${msg}`, 3000);
  };
  const pickEntity = async (type: string, placeHolder: string, filter?: (x: any) => boolean): Promise<string | undefined> => {
    if (!model.index) await model.refresh();
    const items = (model.index?.items || []).filter((i) => i.type === type && (!filter || filter(i)));
    if (!items.length) {
      vscode.window.showInformationMessage(`No ${type}s yet.`);
      return undefined;
    }
    const p = await vscode.window.showQuickPick(items.slice().reverse().map((i) => ({ label: i.id, description: i.title, detail: i.status, id: i.id })), { placeHolder, matchOnDescription: true });
    return p?.id;
  };

  // ------------------------------------------------------------------ basics
  reg('research.init', async () => {
    const name = await vscode.window.showInputBox({ prompt: 'Project name', value: path.basename(model.root), ignoreFocusOut: true });
    if (name === undefined) return;
    const goal = await vscode.window.showInputBox({ prompt: 'What is this project trying to understand? (goal — shown when you resume)', placeHolder: 'e.g. Recover physically plausible material maps from CT without paired supervision', ignoreFocusOut: true });
    if (goal === undefined) return;
    await rpc('init', { name, goal });
    await model.refresh();
    vscode.window.showInformationMessage(`Research project initialized: .research/ + AGENTS.md block.`, 'New Question', 'Open AGENTS.md').then((c) => {
      if (c === 'New Question') vscode.commands.executeCommand('research.question.create');
      if (c === 'Open AGENTS.md') vscode.commands.executeCommand('research.agents.open');
    });
  });
  reg('research.refresh', async () => {
    await rpc('run_refresh', {});
    await model.refresh();
  });
  reg('research.resume', () => panels.openResume());
  reg('research.open', async (a: any, side?: boolean) => {
    let id = idArg(a);
    if (!id) {
      const icons: Record<string, string> = { question: 'question', experiment: 'beaker', run: 'play-circle', finding: 'lightbulb', decision: 'law',
        checkpoint: 'bookmark', artifact: 'file', plan: 'list-ordered', task: 'tasklist' };
      const pick = await vscode.window.showQuickPick((model.index?.items || []).map((i: any) => ({
        label: `$(${icons[i.type] || 'circle-outline'}) ${i.title || i.id}`, description: i.id, detail: i.status ? `${i.type} · ${i.status}` : i.type, id: i.id })),
        { placeHolder: 'Open a question, plan, task, experiment, finding…', matchOnDescription: true, matchOnDetail: true });
      id = pick?.id;
    }
    if (id) return panels.openEntity(id, !!side);
  });
  reg('research.focus', (view: string) => vscode.commands.executeCommand(`research.${view}.focus`));
  reg('research.openFile', (p: string) => openPath(model.root, p));
  reg('research.revealFile', (a: any) => {
    const p = typeof a === 'string' ? a : a?.resourceUri?.fsPath;
    if (!p) return;
    return vscode.commands.executeCommand('revealInExplorer', vscode.Uri.file(path.isAbsolute(p) ? p : path.join(model.root, p)));
  });
  reg('research.copyId', async (a: any) => {
    const id = idArg(a);
    if (id) {
      await vscode.env.clipboard.writeText(id);
      vscode.window.setStatusBarMessage(`$(copy) Copied ${id}`, 2000);
    }
  });
  reg('research.copyPath', async (a: any) => {
    const p = a?.resourceUri?.fsPath || (typeof a === 'string' ? a : undefined);
    if (p) await vscode.env.clipboard.writeText(p);
  });
  reg('research.copyText', (t: string) => vscode.env.clipboard.writeText(t || ''));
  reg('research.showOutput', () => out.show());
  reg('research.config.open', () => openPath(model.root, '.research/config.yaml'));
  reg('research.agents.open', () => openPath(model.root, 'AGENTS.md'));
  reg('research.rebuild', async () => {
    const ok = await vscode.window.showWarningMessage('Rebuild research.db from the text files in .research/? (A backup of the current index is kept in .research/cache/.)', { modal: true }, 'Rebuild');
    if (!ok) return;
    const c = await rpc('rebuild');
    done(`Index rebuilt: ${Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ')}`);
  });
  reg('research.context.regenerate', async () => {
    const r = await rpc('write_context');
    await openPath(model.root, r.path);
  });
  reg('research.project.setGoal', async () => {
    const goal = await vscode.window.showInputBox({ prompt: 'Project goal', value: model.resume?.project?.goal || '', ignoreFocusOut: true });
    if (goal === undefined) return;
    await rpc('update_project', { goal });
    done('Goal updated');
  });
  reg('research.openMirror', async (a: any) => {
    const id = idArg(a);
    if (!id) return;
    const dir = { Q: 'questions', EXP: 'experiments', F: 'findings', D: 'decisions', CP: 'checkpoints' }[id.split('-')[0]];
    if (dir) await openPath(model.root, `.research/${dir}/${id}.md`);
  });
  reg('research.terminal.here', async (a: any) => {
    const id = idArg(a);
    let cwd = model.root;
    if (id?.startsWith('RUN-')) {
      const r = await rpc('show', { id });
      cwd = path.join(model.root, r.working_dir || '.');
    }
    const t = vscode.window.createTerminal({ name: id ? `Research · ${id}` : 'Research', cwd, env: shim.env() });
    t.show();
  });

  // ------------------------------------------------------------------ questions
  reg('research.question.create', async (a?: any) => {
    const parent = a?.parent || (typeof a === 'object' && a?.key?.startsWith('q') ? idArg(a) : undefined);
    const spec: FormSpec = {
      kind: 'question', title: 'New question', icon: 'question', submit: 'Create question',
      subtitle: 'What are you trying to understand? Questions organize experiments and findings.',
      groups: [{ fields: [
        { name: 'title', label: 'Question', type: 'text', required: true, autofocus: true, placeholder: 'e.g. Does entropy regularization reduce material mixing?' },
        { name: 'description', label: 'Context', type: 'textarea', rows: 4, placeholder: 'Why it matters, what would answer it, known unknowns…' },
        [{ name: 'parent', label: 'Part of', type: 'entity', filter: ['question'], value: parent, placeholder: 'Parent question (optional)' },
          { name: 'status', label: 'Status', type: 'seg', options: Q_STATUS, value: 'open' }],
        { name: 'tags', label: 'Tags', type: 'list', placeholder: 'comma separated' },
      ] }],
    };
    panels.openForm('new-question', spec, async (v) => (await rpc('create', { type: 'question', ...v, status: v.status || 'open' })).id);
  });

  // ------------------------------------------------------------------ experiments
  const expFields = (e: any = {}): FormSpec['groups'] => [
    { title: 'What', icon: 'beaker', fields: [
      { name: 'title', label: 'Title', type: 'text', required: true, autofocus: !e.title, value: e.title, placeholder: 'e.g. Entropy weight sweep' },
      [{ name: 'question', label: 'Question', type: 'entity', filter: ['question'], value: e.question_id || e.question },
        { name: 'parent', label: 'Derived from', type: 'entity', filter: ['experiment'], value: e.parent_id || e.parent }],
    ] },
    { title: 'Design — filled before running', icon: 'milestone', fields: [
      { name: 'hypothesis', label: 'Hypothesis', type: 'textarea', value: e.hypothesis, hint: 'a falsifiable claim', placeholder: 'e.g. λ≈1e-3 reduces mixture entropy without hurting CT MAE by more than 2%' },
      { name: 'motivation', label: 'Motivation', type: 'textarea', value: e.motivation, hint: 'why now', rows: 2 },
      { name: 'method', label: 'Method', type: 'textarea', value: e.method, rows: 2 },
      [{ name: 'expected_outcome', label: 'Expected outcome', type: 'textarea', value: e.expected_outcome, rows: 2 },
        { name: 'success_criteria', label: 'Success criteria', type: 'textarea', value: e.success_criteria, rows: 2 }],
      { name: 'stop_conditions', label: 'Stop conditions', type: 'textarea', rows: 2, value: e.stop_conditions, placeholder: 'e.g. stop after 3 runs, or if loss diverges in the sanity run' },
    ] },
    { title: 'Parameters & evidence', icon: 'settings', fields: [
      { name: 'parameters', label: 'Parameters', type: 'kv', value: e.parameters || {} },
      [{ name: 'metrics', label: 'Metrics to evaluate', type: 'list', value: e.metrics_requested || [], placeholder: 'rmse, runtime' },
        { name: 'expected_artifacts', label: 'Expected artifacts', type: 'list', value: e.expected_artifacts || [], placeholder: 'plot.png, metrics.csv' },
        { name: 'planned_runs', label: 'Planned runs', type: 'number', value: e.planned_runs }],
      { name: 'tags', label: 'Tags', type: 'list', value: e.tags || [] },
    ] },
  ];
  reg('research.experiment.create', async (a?: any) => {
    const pre = typeof a === 'object' && a && !a.key ? a : a?.key ? { question: idArg(a) } : {};
    panels.openForm('new-experiment', {
      kind: 'experiment', title: 'Register experiment', icon: 'beaker', submit: 'Register experiment',
      subtitle: 'An experiment is a designed test. Register it <b>before</b> running anything substantial — runs will attach to it.',
      groups: [...expFields(pre), { fields: [{ name: 'status', label: 'Status', type: 'seg', options: E_STATUS.slice(0, 2), value: 'proposed' }] }],
    }, async (v) => (await rpc('create', { type: 'experiment', ...v, status: v.status || 'proposed' })).id);
  });
  reg('research.experiment.variant', async (a?: any) => {
    const id = idArg(a) || (await pickEntity('experiment', 'Create a variant of…'));
    if (!id) return;
    const e = await rpc('show', { id });
    panels.openForm(`variant-${id}`, {
      kind: 'variant', title: `Variant of ${id}`, icon: 'git-compare', submit: 'Create variant',
      subtitle: `Copies the design of <b>${id} · ${e.title}</b>. Change only what differs — the delta is recorded.`,
      context: { source: id },
      groups: [{ fields: [
        { name: 'params', label: 'Parameters', type: 'kv', value: e.parameters || {}, base: e.parameters || {} },
        { name: 'title', label: 'Title', type: 'text', placeholder: 'auto: original title + changed params' },
        { name: 'motivation', label: 'Why this variant?', type: 'textarea', rows: 2, placeholder: 'auto: “Variant of … : param a → b”' },
      ] }],
    }, async (v, c) => (await rpc('create', { type: 'variant', source: c.source, params: diffParams(v.params, e.parameters || {}), title: v.title, motivation: v.motivation })).id);
  });
  reg('research.edit', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const d = await rpc('show', { id });
    const t = d.type;
    if (t === 'experiment') {
      panels.openForm(`edit-${id}`, {
        kind: 'experiment', title: `Edit ${id}`, icon: 'edit', submit: 'Save', groups: [...expFields(d),
          { title: 'After the fact', icon: 'note', fields: [{ name: 'limitations', label: 'Known limitations', type: 'textarea', value: d.limitations }, { name: 'notes', label: 'Notes', type: 'textarea', value: d.notes }] }],
      }, async (v) => {
        await rpc('update', { type: 'experiment', id, ...blank(v), question: v.question || '', parent: v.parent || '' });
        return id;
      });
    } else if (t === 'question') {
      panels.openForm(`edit-${id}`, { kind: 'question', title: `Edit ${id}`, icon: 'edit', submit: 'Save', groups: [{ fields: [
        { name: 'title', label: 'Question', type: 'text', required: true, value: d.title },
        { name: 'description', label: 'Context', type: 'textarea', rows: 5, value: d.description },
        [{ name: 'parent', label: 'Part of', type: 'entity', filter: ['question'], value: d.parent_id }, { name: 'status', label: 'Status', type: 'seg', options: Q_STATUS, value: d.status }],
        { name: 'tags', label: 'Tags', type: 'list', value: d.tags },
      ] }] }, async (v) => {
        await rpc('update', { type: 'question', id, ...blank(v), parent: v.parent || '' });
        return id;
      });
    } else if (t === 'finding') {
      panels.openForm(`edit-${id}`, findingSpec(`Edit ${id}`, d), async (v) => {
        await rpc('update', { type: 'finding', id, ...blank(v) });
        return id;
      });
    } else if (t === 'decision') {
      panels.openForm(`edit-${id}`, decisionSpec(`Edit ${id}`, { ...d, findings: d.links?.supporting_findings, experiments: d.links?.experiments }), async (v) => {
        await rpc('update', { type: 'decision', id, statement: v.statement, reason: v.reason ?? '', status: v.status, supporting_findings: v.findings, experiments: v.experiments });
        return id;
      });
    } else if (t === 'plan') {
      panels.openForm(`edit-${id}`, { kind: 'plan', title: `Edit ${id}`, icon: 'edit', submit: 'Save', groups: planGroups(d, true) }, async (v) => {
        await rpc('update', { type: 'plan', id, ...blank(v), root_question_id: v.root_question_id || '' });
        return id;
      });
    } else if (t === 'task') {
      panels.openForm(`edit-${id}`, { kind: 'task', title: `Edit ${id}`, icon: 'edit', submit: 'Save', groups: taskGroups(d, true) }, async (v) => {
        await rpc('update', { type: 'task', id, ...taskValues(blank(v)) });
        return id;
      });
    } else await vscode.commands.executeCommand('research.openMirror', id);
  });
  reg('research.setStatus', async (a?: any, status?: string) => {
    const id = idArg(a);
    if (!id) return;
    const t = id.split('-')[0];
    const opts = { Q: Q_STATUS, EXP: E_STATUS, F: F_STATUS, D: D_STATUS, PLAN: PLAN_STATUS, T: TASK_STATUS, RUN: [['completed'], ['failed'], ['cancelled'], ['unknown']] as any }[t];
    if (!opts) return;
    const s = status || (await vscode.window.showQuickPick(opts.map((o: any) => o[0]), { placeHolder: `New status for ${id}` }));
    if (!s) return;
    if (t === 'EXP' && (s === 'completed' || s === 'failed')) {
      const st = await rpc('experiment_state', { id });
      if (st.unsynthesized > 0) {
        const c = await vscode.window.showWarningMessage(`${id} has ${st.unsynthesized} unsynthesized run(s). Synthesize before closing it?`, 'Synthesize first', `Mark ${s} anyway`);
        if (!c) return;
        if (c === 'Synthesize first') return vscode.commands.executeCommand('research.experiment.synthesize', id, s);
      }
    }
    // For plans and tasks, use the update endpoint
    if (t === 'PLAN') {
      await rpc('update', { type: 'plan', id, status: s });
    } else if (t === 'T') {
      await rpc('update', { type: 'task', id, status: s });
    } else {
      await rpc('set_status', { id, status: s });
    }
    done(`${id} → ${s}`);
  });
  reg('research.experiment.complete', (a) => vscode.commands.executeCommand('research.setStatus', idArg(a), 'completed'));
  reg('research.experiment.fail', (a) => vscode.commands.executeCommand('research.setStatus', idArg(a), 'failed'));
  reg('research.setBaseline', async (a?: any) => {
    const id = a === null ? null : idArg(a) || (await pickEntity('experiment', 'Which experiment is the baseline?'));
    if (id === undefined) return;
    await rpc('set_baseline', { id });
    done(id ? `${id} is now the baseline` : 'Baseline cleared');
  });

  reg('research.experiment.synthesize', async (a?: any, mark?: string) => {
    const id = idArg(a) || (await pickEntity('experiment', 'Synthesize which experiment?'));
    if (!id) return;
    const e = await rpc('show', { id });
    const st = e.state;
    const runs = (e.runs || []).map((r: any) => `${r.id} (${r.status}${r.label ? ', ' + r.label : ''})`).join(', ');
    panels.openForm(`synth-${id}`, {
      kind: 'synthesis', title: `Synthesize ${id}`, icon: 'sparkle', submit: 'Save synthesis',
      subtitle: `<b>${e.title}</b> — ${st.runs} runs, ${st.unsynthesized} not yet synthesized. ${e.hypothesis ? `<br><span class="muted">Hypothesis: ${escapeHtml(e.hypothesis)}</span>` : ''}`,
      context: { id },
      groups: [
        { title: 'Observations', icon: 'eye', fields: [
          { name: 'what_happened', label: 'What happened', type: 'textarea', autofocus: true, placeholder: `Runs: ${runs || '—'}` },
          [{ name: 'what_worked', label: 'What worked', type: 'textarea' }, { name: 'what_failed', label: 'What did not work', type: 'textarea' }],
        ] },
        { title: 'Interpretation', icon: 'lightbulb', fields: [
          { name: 'interpretation', label: 'Interpretation', type: 'textarea', hint: 'what it means relative to the hypothesis' },
          [{ name: 'limitations', label: 'Limitations', type: 'textarea', rows: 2 }, { name: 'unresolved', label: 'Unresolved questions', type: 'textarea', rows: 2 }],
          { name: 'next_experiment', label: 'Recommended next experiment', type: 'textarea', rows: 2 },
        ] },
        { fields: [{ name: 'mark', label: 'Then mark the experiment as', type: 'seg', options: [['', 'leave as is', 'gray'], ['completed', 'completed', 'green'], ['failed', 'failed', 'red']], value: mark || '' },
          { name: 'make_finding', label: 'Create a finding from this synthesis next', type: 'check', value: false }] },
      ],
    }, async (v, c) => {
      const { make_finding, mark: m, ...rest } = v;
      await rpc('synthesize', { id: c.id, ...rest, mark: m || undefined });
      if (make_finding) setTimeout(() => vscode.commands.executeCommand('research.finding.create', { supports: [c.id], statement: rest.interpretation || '' }), 200);
      return make_finding ? undefined : c.id;
    });
  });

  // ------------------------------------------------------------------ runs
  reg('research.experiment.run', async (a?: any) => {
    const id = idArg(a) || (await pickEntity('experiment', 'Run which experiment?', (x) => !['completed', 'abandoned'].includes(x.status)));
    if (!id) return;
    const budget = await rpc('check_run_budget', { experiment: id });
    let override: string | undefined;
    if (!budget.ok) {
      const c = await vscode.window.showWarningMessage(`${id}: ${budget.messages.join('; ')}. Synthesize before running more?`, { modal: true }, 'Synthesize', 'Run anyway');
      if (!c) return;
      if (c === 'Synthesize') return vscode.commands.executeCommand('research.experiment.synthesize', id);
      override = 'human override from VS Code';
    }
    const e = await rpc('show', { id });
    const last = (e.runs || []).slice(-1)[0];
    const mode = vscode.workspace.getConfiguration('research').get<string>('defaultRunMode', 'terminal');
    const slurm = (await rpc('config')).backends?.slurm || {};
    const params = last?.parameters && Object.keys(last.parameters).length ? last.parameters : scalarParams(e.parameters || {});
    panels.openForm(`run-${id}`, {
      kind: 'run', title: `Run ${id}`, icon: 'play', submit: 'Start run',
      subtitle: `<b>${escapeHtml(e.title)}</b> · ${e.state.runs} runs so far${e.planned_runs ? ` of ${e.planned_runs} planned` : ''}. Output, exit code, git state and metrics are captured automatically.`,
      context: { id, override },
      groups: [
        { fields: [
          { name: 'command', label: 'Command', type: 'textarea', mono: true, rows: 2, required: true, autofocus: true, value: last?.command || '', placeholder: 'python train.py --alpha 0.5' },
          [{ name: 'label', label: 'Label', type: 'text', placeholder: 'e.g. sanity, alpha=0.5' }, { name: 'working_dir', label: 'Working directory', type: 'text', mono: true, value: last?.working_dir || '.', hint: 'relative to project' }],
          { name: 'parameters', label: 'Run parameters', type: 'kv', value: params },
          { name: 'mode', label: 'Where', type: 'seg', value: mode, options: [['terminal', 'Terminal (live output)', 'blue'], ['background', 'Background', 'purple'], ['slurm', 'SLURM', 'orange']] },
        ] },
        { title: 'SLURM resources', icon: 'server', cls: 'slurm-only', fields: [
          [{ name: 'partition', label: 'Partition', type: 'text', value: slurm.partition || '' }, { name: 'time', label: 'Time', type: 'text', value: slurm.time || '01:00:00' },
            { name: 'account', label: 'Account', type: 'text', value: slurm.account || '' }],
          [{ name: 'gpus', label: 'GPUs', type: 'text', value: slurm.gpus ?? '' }, { name: 'cpus', label: 'CPUs', type: 'number', value: slurm.cpus ?? '' }, { name: 'mem', label: 'Memory', type: 'text', value: slurm.mem || '', placeholder: '16G' }],
          { name: 'sbatch_args', label: 'Extra #SBATCH lines', type: 'list', placeholder: '--constraint=a100, --qos=short' },
        ] },
      ],
    }, async (v, c) => {
      const wd = v.working_dir || '.';
      if (v.mode === 'terminal') {
        const args = ['run', 'exec', c.id];
        for (const [k, val] of Object.entries(v.parameters || {})) args.push('--param', `${k}=${typeof val === 'string' ? val : JSON.stringify(val)}`);
        if (v.label) args.push('--label', v.label);
        if (c.override) args.push('--override', c.override);
        args.push('--cwd', wd, '--');
        const cmdline = shim.commandLine(args) + ' ' + v.command.trim();
        const t = vscode.window.createTerminal({ name: `▶ ${c.id}${v.label ? ' · ' + v.label : ''}`, cwd: model.root, env: shim.env() });
        t.show();
        t.sendText(cmdline, true);
        setTimeout(() => model.schedule(0), 1500);
        return undefined;
      }
      const params: any = { experiment: c.id, command: v.command.trim(), parameters: v.parameters, label: v.label || undefined, working_dir: path.join(model.root, wd), override: c.override };
      if (v.mode === 'slurm') {
        params.backend = 'slurm';
        params.slurm = { partition: v.partition || undefined, time: v.time || undefined, account: v.account || undefined, gpus: v.gpus || undefined, cpus: v.cpus || undefined, mem: v.mem || undefined, extra_args: v.sbatch_args?.length ? v.sbatch_args : undefined };
      }
      const r = await rpc('run_start', params);
      vscode.window.setStatusBarMessage(`$(play) ${r.id} ${r.status}${r.slurm_job_id ? ' · job ' + r.slurm_job_id : ''}`, 4000);
      return r.id;
    });
  });

  reg('research.run.attach', async (a?: any) => {
    const id = idArg(a) || (await pickEntity('experiment', 'Attach a run to which experiment?'));
    if (!id) return;
    panels.openForm(`attach-${id}`, {
      kind: 'run', title: `Attach existing run to ${id}`, icon: 'link', submit: 'Attach run',
      subtitle: 'For something you already ran yourself (terminal, notebook, another machine).', context: { id },
      groups: [{ fields: [
        { name: 'command', label: 'Command', type: 'textarea', mono: true, rows: 2, autofocus: true },
        [{ name: 'status', label: 'Outcome', type: 'seg', value: 'completed', options: [['completed', 'completed', 'green'], ['failed', 'failed', 'red'], ['running', 'still running', 'yellow']] },
          { name: 'exit_code', label: 'Exit code', type: 'number' }, { name: 'label', label: 'Label', type: 'text' }],
        { name: 'parameters', label: 'Parameters', type: 'kv', value: {} },
        [{ name: 'stdout_path', label: 'stdout log', type: 'text', mono: true, placeholder: 'path (optional)' }, { name: 'stderr_path', label: 'stderr log', type: 'text', mono: true },
          { name: 'slurm_job_id', label: 'SLURM job id', type: 'text' }],
        { name: 'notes', label: 'Notes', type: 'textarea', rows: 2 },
      ] }],
    }, async (v, c) => {
      const p: any = { experiment: c.id, ...v, status: v.status || 'completed' };
      for (const k of ['stdout_path', 'stderr_path']) if (p[k]) p[k] = path.isAbsolute(p[k]) ? p[k] : path.join(model.root, p[k]);
      const r = await rpc('run_attach', p);
      return r.id;
    });
  });
  reg('research.run.cancel', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const ok = await vscode.window.showWarningMessage(`Cancel ${id}?`, { modal: true }, 'Cancel run');
    if (!ok) return;
    await rpc('run_cancel', { id });
    done(`${id} cancelled`);
  });
  reg('research.run.refresh', async (a?: any) => {
    await rpc('run_refresh', { id: idArg(a) });
    done();
  });
  reg('research.run.logs', async (a?: any, which?: string) => {
    const id = idArg(a);
    if (!id) return;
    const r = await rpc('show', { id });
    const p = which === 'stderr' ? r.stderr_path_abs : r.stdout_path_abs;
    if (!p || !fs.existsSync(p)) return vscode.window.showInformationMessage(`${id}: no ${which || 'stdout'} log yet.`);
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(p));
    const ed = await vscode.window.showTextDocument(doc, { preview: true });
    const last = doc.lineCount - 1;
    ed.revealRange(new vscode.Range(last, 0, last, 0));
  });
  reg('research.metric.add', async (a?: any) => {
    const target = typeof a === 'object' && a && !a.key ? a : { run: idArg(a) };
    const name = await vscode.window.showInputBox({ prompt: `Metric name for ${target.run || target.experiment}`, placeHolder: 'rmse' });
    if (!name) return;
    const value = await vscode.window.showInputBox({ prompt: `${name} value`, placeHolder: '0.123' });
    if (value === undefined) return;
    await rpc('log_metric', { name, value, ...target });
    done(`${name} = ${value}`);
  });

  // ------------------------------------------------------------------ artifacts
  reg('research.artifact.register', async (a?: any, multi?: vscode.Uri[]) => {
    let uris: vscode.Uri[] = [];
    let target: any = {};
    if (a instanceof vscode.Uri) uris = multi && multi.length ? multi : [a];
    else if (a && typeof a === 'object' && (a.run || a.experiment)) target = a;
    if (!uris.length) {
      const picked = await vscode.window.showOpenDialog({ canSelectMany: true, canSelectFolders: true, defaultUri: vscode.Uri.file(model.root), openLabel: 'Register artifact' });
      if (!picked) return;
      uris = picked;
    }
    if (!target.run && !target.experiment) {
      const items = [
        ...(model.tree?.experiments || []).flatMap((e) => (e.runs || []).slice().reverse().map((r: any) => ({ label: r.id, description: `${e.id} · ${r.label || r.command || ''}`, detail: r.status, run: r.id }))),
        ...(model.tree?.experiments || []).map((e) => ({ label: e.id, description: e.title, detail: 'experiment-level', experiment: e.id })),
      ];
      const pick: any = await vscode.window.showQuickPick(items, { placeHolder: 'Attach to which run (or experiment)?', matchOnDescription: true });
      if (!pick) return;
      target = pick.run ? { run: pick.run } : { experiment: pick.experiment };
    }
    const description = await vscode.window.showInputBox({ prompt: 'What does it show? (optional, but future-you will thank you)' });
    const ids: string[] = [];
    for (const u of uris) ids.push((await rpc('register_artifact', { path: u.fsPath, ...target, description: description || undefined })).id);
    done(`Registered ${ids.join(', ')}`);
    if (ids.length === 1) panels.openEntity(ids[0]);
  });

  // ------------------------------------------------------------------ findings & decisions
  const findingSpec = (title: string, f: any = {}): FormSpec => ({
    kind: 'finding', title, icon: 'lightbulb', submit: f.id ? 'Save' : 'Create finding',
    subtitle: f.id ? undefined : 'A finding is a <b>reusable, interpreted result</b> backed by evidence. Failed directions are findings too.',
    groups: [
      { fields: [
        { name: 'title', label: 'Short title', type: 'text', required: true, autofocus: !f.id, value: f.title, placeholder: 'e.g. λ≈1e-3 reduces mixing at <2% MAE cost' },
        { name: 'statement', label: 'Statement', type: 'textarea', rows: 3, value: f.statement, placeholder: 'The precise claim, with numbers.' },
        [{ name: 'kind', label: 'Kind', type: 'seg', value: f.kind || 'result', options: [['result', 'result', 'blue'], ['failure', 'failed direction', 'red']] },
          { name: 'status', label: 'Status', type: 'seg', value: f.status || 'preliminary', options: F_STATUS.slice(0, 3) },
          { name: 'confidence', label: 'Confidence', type: 'seg', value: f.confidence || '', options: CONF }],
      ] },
      { title: 'Evidence', icon: 'link', fields: [
        { name: 'supports', label: 'Supporting evidence', type: 'entities', filter: ['experiment', 'run', 'artifact', 'finding'], value: f.links?.supports || f.supports || [], hint: 'experiments, runs, artifacts' },
        { name: 'contradicts', label: 'Contradicting evidence', type: 'entities', filter: ['experiment', 'run', 'artifact', 'finding'], value: f.links?.contradicts || [] },
        [{ name: 'questions', label: 'Addresses questions', type: 'entities', filter: ['question'], value: f.links?.questions || f.questions || [] },
          { name: 'related', label: 'Related findings', type: 'entities', filter: ['finding'], value: f.links?.related || [] }],
      ] },
      { title: 'Scope', icon: 'warning', fields: [
        { name: 'limitations', label: 'Limitations', type: 'textarea', rows: 2, value: f.limitations, placeholder: 'e.g. only 5 validation subjects' },
        { name: 'contradicting_evidence', label: 'Contradicting evidence (text)', type: 'textarea', rows: 2, value: f.contradicting_evidence },
        { name: 'tags', label: 'Tags', type: 'list', value: f.tags || [] },
      ] },
    ],
  });
  reg('research.finding.create', async (a?: any) => {
    const pre = a && typeof a === 'object' && !a.key ? a : a?.key ? { supports: [idArg(a)] } : {};
    panels.openForm('new-finding', findingSpec('New finding', pre), async (v) => (await rpc('create', { type: 'finding', ...v, kind: v.kind || 'result', status: v.status || 'preliminary', confidence: v.confidence || undefined })).id);
  });
  reg('research.finding.supersede', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const by = await pickEntity('finding', `Superseded by which finding? (Esc = none)`, (x) => x.id !== id);
    const reason = await vscode.window.showInputBox({ prompt: `Why is ${id} superseded?` });
    if (reason === undefined) return;
    await rpc('supersede_finding', { id, by, reason });
    done(`${id} superseded`);
  });
  const decisionSpec = (title: string, d: any = {}): FormSpec => ({
    kind: 'decision', title, icon: 'law', submit: d.id ? 'Save' : 'Record decision',
    subtitle: d.id ? undefined : 'Something deliberately chosen, based on findings — e.g. a new baseline, a direction abandoned.',
    context: { supersedes: d.supersedes },
    groups: [{ fields: [
      { name: 'statement', label: 'Decision', type: 'textarea', rows: 2, required: true, autofocus: !d.id, value: d.statement, placeholder: 'e.g. Use entropy_weight=1e-3 as the baseline for future experiments.' },
      { name: 'reason', label: 'Reason', type: 'textarea', rows: 3, value: d.reason },
      { name: 'findings', label: 'Based on findings', type: 'entities', filter: ['finding'], value: d.findings || [] },
      { name: 'experiments', label: 'Related experiments', type: 'entities', filter: ['experiment'], value: d.experiments || [] },
      ...(d.id ? [{ name: 'status', label: 'Status', type: 'seg', options: D_STATUS, value: d.status } as Field] : []),
    ] }],
  });
  reg('research.decision.create', async (a?: any) => {
    const pre = a && typeof a === 'object' && !a.key ? a : a?.key ? { findings: [idArg(a)] } : {};
    panels.openForm('new-decision', decisionSpec(pre.supersedes ? `Supersede ${pre.supersedes}` : 'New decision', pre), async (v, c) =>
      (await rpc('create', { type: 'decision', statement: v.statement, reason: v.reason, supporting_findings: v.findings, experiments: v.experiments, supersedes: c?.supersedes })).id);
  });

  // ------------------------------------------------------------------ checkpoints
  reg('research.checkpoint.create', async () => {
    const d = await rpc('checkpoint_draft');
    panels.openForm('new-checkpoint', {
      kind: 'checkpoint', title: 'Create research checkpoint', icon: 'bookmark', submit: 'Save checkpoint',
      subtitle: 'Pre-filled from the current state. Edit it into what <b>you</b> would want to read after a month away.',
      groups: [
        { title: 'Understanding', icon: 'book', fields: [
          { name: 'title', label: 'Title', type: 'text', value: d.title },
          { name: 'goal', label: 'Current goal', type: 'textarea', rows: 2, value: d.goal },
          { name: 'understanding', label: 'What do we currently understand?', type: 'textarea', rows: 5, value: d.understanding, autofocus: true },
          [{ name: 'baseline', label: 'Current baseline', type: 'textarea', rows: 2, value: d.baseline }, { name: 'baseline_experiment', label: 'Baseline experiment', type: 'entity', filter: ['experiment'], value: d.baseline_experiment_id }],
        ] },
        { title: 'State', icon: 'pulse', fields: [
          { name: 'current_problem', label: 'What is currently broken / blocking?', type: 'textarea', rows: 3, value: d.current_problem },
          { name: 'next_experiment', label: 'What should happen next?', type: 'textarea', rows: 2, value: d.next_experiment },
          { name: 'notes', label: 'Anything else future-you needs to know', type: 'textarea', rows: 2 },
        ] },
        { title: 'Pointers', icon: 'link', fields: [
          { name: 'findings', label: 'Important findings', type: 'entities', filter: ['finding'], value: d.finding_ids },
          { name: 'failures', label: 'Known failures', type: 'entities', filter: ['finding'], value: d.failure_ids },
          [{ name: 'questions', label: 'Open questions', type: 'entities', filter: ['question'], value: d.question_ids },
            { name: 'experiments', label: 'Active experiments', type: 'entities', filter: ['experiment'], value: d.experiment_ids }],
        ] },
        { fields: [{ name: 'commit', label: 'Also commit .research/ to git (only .research/ — nothing else, never pushed)', type: 'check', value: !!d.commit_default }] },
      ],
    }, async (v) => (await rpc('create', { type: 'checkpoint', ...v, use_draft: false })).id);
  });

  // ------------------------------------------------------------------ plans & tasks
  reg('research.plan.create', async (a?: any) => {
    const pre = typeof a === 'object' && a && !a.key ? a : a?.key ? { root_question_id: idArg(a) } : {};
    panels.openForm('new-plan', {
      kind: 'plan', title: 'Create plan', icon: 'list-ordered', submit: 'Create plan',
      subtitle: 'A plan turns a research objective into concrete, tracked work. Define what success looks like before starting.',
      groups: planGroups(pre),
    }, async (v) => (await rpc('create', { type: 'plan', ...v, status: 'active' })).id);
  });

  reg('research.task.create', async (a?: any) => {
    const planId = typeof a === 'string' ? a : idArg(a);
    if (!planId?.startsWith('PLAN-')) {
      // Need to pick a plan
      const plans = ((model.tree as any)?.plans || []).filter((p: any) => p.status === 'active');
      if (!plans.length) {
        const create = await vscode.window.showWarningMessage('No active plans. Create one first?', 'Create Plan');
        if (create) return vscode.commands.executeCommand('research.plan.create');
        return;
      }
      const pick: any = await vscode.window.showQuickPick(
        plans.map((p: any) => ({ label: p.id, description: p.title, id: p.id })),
        { placeHolder: 'Add task to which plan?' }
      );
      if (!pick) return;
      return vscode.commands.executeCommand('research.task.create', pick.id);
    }
    const plan = await rpc('show', { id: planId });
    const existingTasks = plan.tasks || [];

    panels.openForm(`new-task-${planId}`, {
      kind: 'task', title: `Add task to ${planId}`, icon: 'list-ordered', submit: 'Add task',
      subtitle: `<b>${escapeHtml(plan.title)}</b> — ${existingTasks.length} tasks so far`,
      context: { plan_id: planId },
      groups: taskGroups({ task_type: 'research' }),
    }, async (v, c) => (await rpc('create', { type: 'task', plan_id: c.plan_id, ...taskValues(v), status: 'todo' })).id);
  });

  reg('research.task.start', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    await rpc('update', { type: 'task', id, status: 'running' });
    done(`${id} started`);
  });

  reg('research.task.complete', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const result = await vscode.window.showInputBox({ prompt: 'Task result (optional)', placeHolder: 'What was the outcome?' });
    if (result === undefined) return; // Esc cancels; an empty string completes without a result
    await rpc('update', { type: 'task', id, status: 'done', result: result || undefined });
    done(`${id} completed`);
  });

  reg('research.task.block', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const blockers = await vscode.window.showInputBox({ prompt: 'What is blocking this task?', placeHolder: 'e.g. Missing calibration data' });
    if (!blockers) return;
    await rpc('update', { type: 'task', id, status: 'blocked', blockers });
    done(`${id} blocked`);
  });

  // ------------------------------------------------------------------ notes & skills
  reg('research.note.create', async (a?: any) => {
    const title = await vscode.window.showInputBox({ prompt: 'Note title', placeHolder: 'e.g. Thoughts on the calibration failure' });
    if (!title) return;
    const link = idArg(a);
    const n = await rpc('create', { type: 'note', title, links: link ? [link] : [] });
    done();
    const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(path.join(model.root, n.path)));
    const ed = await vscode.window.showTextDocument(doc);
    const end = doc.lineAt(doc.lineCount - 1).range.end;
    ed.selection = new vscode.Selection(end, end);
  });
  const noteId = (a: any) => (a?.key ? a.key.replace(/^n\//, '') : idArg(a));
  reg('research.note.pin', async (a) => { await rpc('pin_note', { id: noteId(a), pinned: true }); done('Pinned'); });
  reg('research.note.unpin', async (a) => { await rpc('pin_note', { id: noteId(a), pinned: false }); done('Unpinned'); });
  reg('research.note.link', async (a) => {
    const id = noteId(a);
    const items = (model.index?.items || []).filter((i) => ['question', 'experiment', 'finding', 'decision'].includes(i.type));
    const picks = await vscode.window.showQuickPick(items.map((i) => ({ label: i.id, description: i.title })), { canPickMany: true, placeHolder: 'Link note to…' });
    if (!picks?.length) return;
    await rpc('link_note', { id, targets: picks.map((p) => p.label) });
    done('Linked');
  });
  reg('research.skill.create', async () => {
    const name = await vscode.window.showInputBox({ prompt: 'Skill name', placeHolder: 'e.g. evaluate-material-map' });
    if (!name) return;
    const description = await vscode.window.showInputBox({ prompt: 'When should an agent use this skill?', placeHolder: 'Use when …' });
    const s = await rpc('create', { type: 'skill', name, description: description || '' });
    done();
    openPath(model.root, s.path);
  });
  reg('research.skill.duplicate', async (a) => {
    const src = a?.key?.split('/').pop() || (await vscode.window.showQuickPick((model.tree?.skills || []).map((s) => s.dir || s.name), { placeHolder: 'Duplicate which skill?' }));
    if (!src) return;
    const name = await vscode.window.showInputBox({ prompt: 'New skill name', value: `${src}-copy` });
    if (!name) return;
    const s = await rpc('duplicate_skill', { src, name });
    done();
    openPath(model.root, s.path);
  });

  // ------------------------------------------------------------------ agents: dispatch, planning
  const ROLE_TARGET: Record<string, string> = { implementer: 'task', verifier: 'finding', analyst: 'experiment', planner: 'plan' };
  const defaultRole = (id?: string): string => {
    if (!id) return 'planner';
    const p = id.split('-')[0];
    if (p === 'F') return 'verifier';
    if (p === 'EXP') return 'analyst';
    if (p === 'PLAN') return 'planner';
    if (p === 'T') {
      const t = (model.tree?.plans || []).flatMap((x: any) => x.tasks || []).find((x: any) => x.id === id);
      if (t?.status === 'verify' || t?.task_type === 'verification') return 'verifier';
      if (['planner', 'implementer', 'verifier', 'analyst'].includes(t?.assigned_role)) return t.assigned_role;
      return 'implementer';
    }
    return 'analyst';
  };
  reg('research.dispatch', async (a?: any, roleArg?: string, objectiveArg?: string) => {
    let target = idArg(a);
    let role = roleArg;
    if (!role) {
      const def = defaultRole(target);
      const roles = ['implementer', 'verifier', 'analyst', 'planner'].sort((x, y) => (x === def ? -1 : y === def ? 1 : 0));
      const pick = await vscode.window.showQuickPick(roles.map((r) => ({ label: r, description: r === def ? 'suggested' : '' })), { placeHolder: `Which role should the agent take${target ? ' on ' + target : ''}?` });
      if (!pick) return;
      role = pick.label;
    }
    let objective = objectiveArg;
    if (role === 'planner' && !target && !objective) {
      objective = await vscode.window.showInputBox({ prompt: 'What should the plan achieve?', placeHolder: 'e.g. Reproduce Figure 3 of the paper within 5% RMSE', ignoreFocusOut: true });
      if (!objective) return;
    }
    if (!target && role !== 'planner') {
      target = await pickEntity(ROLE_TARGET[role], `Which ${ROLE_TARGET[role]} should the ${role} work on?`);
      if (!target) return;
    }
    const ag = await rpc('agents');
    const preferred = ag.roles?.[role] || ag.default_profile;
    const profiles = (ag.profiles || []).slice().sort((x: any, y: any) => (x.name === preferred ? -1 : y.name === preferred ? 1 : Number(y.available) - Number(x.available)));
    const prof: any = await vscode.window.showQuickPick(profiles.map((x: any) => ({
      label: x.name, description: `${x.command}${x.model ? ' · ' + x.model : ''}${x.name === preferred ? ' · configured for ' + role : ''}`,
      detail: x.available ? undefined : '$(warning) not found on PATH', name: x.name,
    })), { placeHolder: 'Which agent? (profiles live in .research/config.yaml → agents)' });
    if (!prof) return;
    const d = await rpc('dispatch', { role, target, objective, profile: prof.name });
    const term = vscode.window.createTerminal({ name: `🤖 ${d.profile}/${d.role}${target ? ' · ' + target : ''}`, cwd: model.root, env: { ...shim.env(), ...d.env } });
    term.show();
    term.sendText(d.argv.map(shq).join(' '), true);
    done(`${d.profile}/${d.role} dispatched`);
    if (!d.available) vscode.window.showWarningMessage(`\`${d.argv[0]}\` was not found on PATH. Install it, or point the "${d.profile}" profile at the right command in .research/config.yaml.`);
  });
  reg('research.plan.withAgent', async () => vscode.commands.executeCommand('research.dispatch', undefined, 'planner'));

  // ------------------------------------------------------------------ retrieval: search, report, brief
  reg('research.search', async () => {
    const qp = vscode.window.createQuickPick<any>();
    qp.placeholder = 'Search findings, experiments, tasks, runs, notes, skills…';
    qp.matchOnDescription = true;
    qp.matchOnDetail = true;
    let timer: any;
    qp.onDidChangeValue((v) => {
      clearTimeout(timer);
      if (!v.trim()) { qp.items = []; return; }
      timer = setTimeout(async () => {
        qp.busy = true;
        try {
          const rows = await rpc('search', { query: v, limit: 40 });
          qp.items = rows.map((r: any) => ({ label: `${r.id}  ${r.title || ''}`, description: `${r.type}${r.status ? ' · ' + r.status : ''}`, detail: r.snippet, alwaysShow: true, row: r }));
        } catch { /* ignore */ }
        qp.busy = false;
      }, 200);
    });
    qp.onDidAccept(async () => {
      const r = qp.selectedItems[0]?.row;
      qp.hide();
      if (!r) return;
      if (r.type === 'skill') {
        const sk = (model.tree?.skills || []).find((x: any) => `skill:${x.name}` === r.id);
        if (sk) openPath(model.root, sk.path);
      } else if (r.type === 'synthesis' || r.type === 'review') panels.openEntity(r.id.split('/')[0]);
      else panels.openEntity(r.id);
    });
    qp.show();
  });
  reg('research.report', async (a?: any) => {
    let scope = idArg(a);
    if (!scope) {
      const items = [{ label: '$(project) Whole project', id: '' },
        ...(model.index?.items || []).filter((i: any) => ['question', 'plan', 'experiment'].includes(i.type)).map((i: any) => ({ label: i.id, description: i.title, id: i.id }))];
      const pick: any = await vscode.window.showQuickPick(items, { placeHolder: 'Report on what?', matchOnDescription: true });
      if (!pick) return;
      scope = pick.id || undefined;
    }
    const r = await rpc('report', { scope, fmt: 'both' });
    const mdPath = r.paths.find((x: string) => x.endsWith('.md'));
    const htmlPath = r.paths.find((x: string) => x.endsWith('.html'));
    if (mdPath) await vscode.commands.executeCommand('markdown.showPreview', vscode.Uri.file(path.join(model.root, mdPath)));
    done('report written');
    const c = await vscode.window.showInformationMessage(`Report written: ${r.paths.join(', ')}`, 'Open HTML in browser', 'Open Markdown');
    if (c === 'Open HTML in browser' && htmlPath) vscode.env.openExternal(vscode.Uri.file(path.join(model.root, htmlPath)));
    if (c === 'Open Markdown' && mdPath) openPath(model.root, mdPath);
  });
  reg('research.task.brief', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const md = await rpc('task_brief', { id });
    const doc = await vscode.workspace.openTextDocument({ content: md, language: 'markdown' });
    vscode.window.showTextDocument(doc, { preview: true });
  });

  // ------------------------------------------------------------------ coordination & verification
  reg('research.task.verify', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const r = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Running ${id} checks…` }, () => rpc('verify_task', { id }));
    done();
    const failed = r.results.filter((x: any) => !x.ok);
    if (r.passed) vscode.window.showInformationMessage(`${id}: all ${r.results.length} checks passed${r.task.status === 'done' ? ' — task done' : ''}.`);
    else vscode.window.showWarningMessage(`${id}: ${failed.length}/${r.results.length} checks failed — ${failed.map((x: any) => `${x.check} (${x.detail})`).join('; ')}`, { modal: false });
  });
  reg('research.task.note', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const text = await vscode.window.showInputBox({ prompt: `Progress note for ${id}`, placeHolder: 'What was done, what was found, what is left…', ignoreFocusOut: true });
    if (!text) return;
    await rpc('task_note', { id, text });
    done(`${id} note added`);
  });
  reg('research.task.release', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const note = await vscode.window.showInputBox({ prompt: `Release ${id} back to the queue — hand-off note (optional)`, placeHolder: 'Where things stand for whoever picks it up' });
    if (note === undefined) return;
    await rpc('release_task', { id, note: note || undefined });
    done(`${id} released`);
  });
  reg('research.finding.review', async (a?: any) => {
    const id = idArg(a);
    if (!id) return;
    const f = await rpc('show', { id });
    panels.openForm(`review-${id}`, {
      kind: 'review', title: `Review ${id}`, icon: 'comment-discussion', submit: 'Record review',
      subtitle: `<b>${escapeHtml(f.title)}</b> — ${escapeHtml(f.statement || '')}<br><span class="muted">Does the evidence (${(f.evidence?.supports || []).map((x: any) => x.id).join(', ') || 'none linked'}) support the claim? Is the interpretation overreaching?</span>`,
      groups: [{ fields: [
        { name: 'verdict', label: 'Verdict', type: 'seg', required: true, value: 'supported', options: [['supported', 'supported', 'green'], ['needs_work', 'needs work', 'orange'], ['contradicted', 'contradicted', 'red']] },
        { name: 'notes', label: 'Reasoning', type: 'textarea', rows: 4, required: true, autofocus: true, placeholder: 'What you checked, and why you reached this verdict' },
        { name: 'confidence', label: 'Confidence', type: 'seg', options: CONF, value: f.confidence || '' },
      ] }],
    }, async (v) => {
      await rpc('review_finding', { id, verdict: v.verdict, notes: v.notes, confidence: v.confidence || undefined });
      return id;
    });
  });
  reg('research.run.compare', async (a?: any, b?: string) => {
    let first = typeof a === 'string' || a?.key ? idArg(a) : undefined;
    const exp = a && typeof a === 'object' && !a.key ? a.experiment : undefined;
    const runFilter = (x: any) => !exp || x.experiment_id === exp;
    if (!first) first = await pickEntity('run', 'First run', runFilter);
    if (!first) return;
    let second = b;
    if (!second) second = await pickEntity('run', `Compare ${first} with…`, (x) => x.id !== first && runFilter(x));
    if (!second) return;
    await panels.openCompare(first, second);
  });

  // ------------------------------------------------------------------ skills management & MCP
  reg('research.skill.import', async () => {
    const how: any = await vscode.window.showQuickPick([
      { label: '$(folder) From a folder…', detail: 'A folder with SKILL.md, or a downloaded repo with skills/<name>/SKILL.md', id: 'folder' },
      { label: '$(github) From a git URL or owner/repo…', detail: 'e.g. DietrichGebert/ponytail · https://github.com/org/repo/tree/main/skills/x', id: 'git' },
    ], { placeHolder: 'Import skills into .research/skills/' });
    if (!how) return;
    let source: string | undefined;
    if (how.id === 'folder') {
      const uri = await vscode.window.showOpenDialog({ canSelectFolders: true, canSelectFiles: false, openLabel: 'Import skills from here' });
      source = uri?.[0]?.fsPath;
    } else source = await vscode.window.showInputBox({ prompt: 'Git URL or owner/repo', placeHolder: 'DietrichGebert/ponytail', ignoreFocusOut: true });
    if (!source) return;
    const preview = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Reading skills…' }, () => rpc('skills_add', { source, list_only: true }));
    let only: string[] | undefined;
    if (preview.available.length > 1) {
      const picks: any = await vscode.window.showQuickPick(preview.available.map((x: any) => ({ label: x.name, description: x.subpath, detail: x.description, picked: false })), { canPickMany: true, placeHolder: `${preview.available.length} skills found — which ones?` });
      if (!picks || !picks.length) return;
      only = picks.map((x: any) => x.label);
    }
    const mode: any = await vscode.window.showQuickPick([
      { label: 'On demand', detail: 'Suggested in task briefs when assigned or matched; agents load it when needed', always: false },
      { label: 'Always on', detail: 'Listed in current.md for every agent session (e.g. a coding-style skill)', always: true },
    ], { placeHolder: 'How should agents use it?' });
    if (!mode) return;
    const run = (force: boolean) => vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: 'Importing skills…' },
      () => rpc('skills_add', { source, only, always: mode.always || undefined, force }));
    let r: any;
    try { r = await run(false); } catch (e: any) {
      if (!/already exists/.test(e.message)) throw e;
      const c = await vscode.window.showWarningMessage(`${e.message}. Replace? (The old copy is kept in .research/cache/removed-skills/.)`, { modal: true }, 'Replace');
      if (c !== 'Replace') return;
      r = await run(true);
    }
    done(`Imported ${r.added.join(', ')}`);
    vscode.window.showInformationMessage(`Imported ${r.added.join(', ')} — available to agents via .claude/skills and .agents/skills.`);
  });
  const skillName = async (a: any) => a?.key?.split('/').pop() || (await vscode.window.showQuickPick((model.tree?.skills || []).map((s: any) => s.dir || s.name), { placeHolder: 'Which skill?' }));
  reg('research.skill.remove', async (a?: any) => {
    const name = await skillName(a);
    if (!name) return;
    const c = await vscode.window.showWarningMessage(`Remove skill "${name}"? It is moved to .research/cache/removed-skills/.`, { modal: true }, 'Remove');
    if (c !== 'Remove') return;
    await rpc('skills_remove', { name });
    done(`${name} removed`);
  });
  reg('research.skill.update', async (a?: any) => {
    const name = await skillName(a);
    if (!name) return;
    const r = await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: `Updating ${name}…` }, () => rpc('skills_update', { names: [name] }));
    done(`updated ${r.updated.join(', ')}`);
  });
  reg('research.skill.toggleAlways', async (a?: any) => {
    const name = await skillName(a);
    if (!name) return;
    const cur = (model.tree?.skills || []).find((s: any) => (s.dir || s.name) === name);
    const r = await rpc('skills_set', { name, always: !cur?.always });
    done(`${r.name}: ${r.always ? 'always on' : 'on demand'}`);
  });
  reg('research.skill.link', async () => {
    const r = await rpc('skills_link');
    const lines = Object.entries(r).map(([k, v]: any) => `${k}: ${v.linked.length} linked${v.skipped.length ? `, left alone: ${v.skipped.join(', ')}` : ''}`);
    vscode.window.showInformationMessage(lines.join(' · ') || 'Skill linking is off (config skills.link).');
  });
  reg('research.mcp.install', async () => {
    const picks: any = await vscode.window.showQuickPick([
      { label: 'claude', description: '.mcp.json (Claude Code)', picked: true },
      { label: 'vscode', description: '.vscode/mcp.json (VS Code / Copilot agent mode)', picked: true },
      { label: 'cursor', description: '.cursor/mcp.json' },
      { label: 'gemini', description: '.gemini/settings.json' },
      { label: 'codex', description: 'prints the `codex mcp add` command (per-user config)' },
    ], { canPickMany: true, placeHolder: 'Register the research MCP server for which agents?' });
    if (!picks || !picks.length) return;
    const r = await rpc('mcp_install', { clients: picks.map((x: any) => x.label) });
    const codex = r.installed.codex;
    const msg = Object.entries(r.installed).filter(([k]) => k !== 'codex').map(([k, v]) => `${k}: ${v}`).join(' · ');
    const c = await vscode.window.showInformationMessage(`MCP server registered${msg ? ' — ' + msg : ''}. Agents started from a VS Code terminal find \`research\` on PATH.`, ...(codex ? ['Copy Codex command'] : []));
    if (c === 'Copy Codex command') vscode.env.clipboard.writeText('codex mcp add research -- research mcp');
  });
}

/** POSIX shell quoting for terminal command lines. */
function shq(s: string): string {
  return /^[\w@%+=:,./-]+$/.test(s) ? s : `'${s.replace(/'/g, `'\\''`)}'`;
}

/** Edit forms: an emptied text field means "clear it", not "leave unchanged". */
function planGroups(d: any, edit = false): any[] {
  return [
    { title: 'What', icon: 'target', fields: [
      { name: 'title', label: 'Plan title', type: 'text', required: true, autofocus: true, value: d.title, placeholder: 'e.g. Reproduce Figure 3 from paper' },
      { name: 'objective', label: 'Objective', type: 'textarea', rows: 3, value: d.objective, placeholder: 'What are we trying to achieve? Be specific.' },
      { name: 'success_criteria', label: 'Success criteria', type: 'textarea', rows: 2, value: d.success_criteria, placeholder: 'How will we know the plan succeeded?' },
      ...(edit ? [{ name: 'status', label: 'Status', type: 'seg', options: PLAN_STATUS, value: d.status }] : []),
    ] },
    { title: 'Context', icon: 'link', fields: [
      { name: 'root_question_id', label: 'Addresses question', type: 'entity', filter: ['question'], value: d.root_question_id },
      { name: 'context', label: 'Additional context', type: 'textarea', rows: 2, value: d.context, placeholder: 'Relevant background, constraints, dependencies…' },
    ] },
  ];
}

function taskGroups(d: any, edit = false): any[] {
  return [
    { title: 'Task definition', icon: 'symbol-method', fields: [
      { name: 'title', label: 'Task title', type: 'text', required: true, autofocus: true, value: d.title, placeholder: 'e.g. Run validation on test subjects' },
      { name: 'goal', label: 'Goal', type: 'textarea', rows: 2, value: d.goal, placeholder: 'What should this task accomplish?' },
      [{ name: 'task_type', label: 'Type', type: 'seg', options: TASK_TYPES, value: d.task_type || '' },
        { name: 'assigned_role', label: 'Role', type: 'seg', options: TASK_ROLES, value: d.assigned_role || '' }],
      ...(edit ? [{ name: 'status', label: 'Status', type: 'seg', options: TASK_STATUS, value: d.status }] : []),
    ] },
    { title: 'Workflow', icon: 'git-merge', fields: [
      { name: 'depends_on', label: 'Dependencies', type: 'entities', filter: ['task'], value: d.depends_on || [], hint: 'Tasks that must complete first' },
      [{ name: 'inputs', label: 'Inputs', type: 'textarea', rows: 2, value: d.inputs, placeholder: 'What does this task need?' },
        { name: 'expected_outputs', label: 'Expected outputs', type: 'textarea', rows: 2, value: d.expected_outputs, placeholder: 'What will this task produce?' }],
      [{ name: 'related_question_id', label: 'Related question', type: 'entity', filter: ['question'], value: d.related_question_id },
        { name: 'related_experiment_id', label: 'Related experiment', type: 'entity', filter: ['experiment'], value: d.related_experiment_id }],
    ] },
    { title: 'Quality', icon: 'checklist', fields: [
      { name: 'acceptance_criteria', label: 'Acceptance criteria', type: 'textarea', rows: 2, value: d.acceptance_criteria, placeholder: 'How do we know this task is done correctly?' },
      { name: 'verification', label: 'Verification approach', type: 'textarea', rows: 2, value: d.verification, placeholder: 'How should we verify the outputs?' },
      { name: 'checks', label: 'Checks (one per line — they gate completion)', type: 'textarea', rows: 3, mono: true, value: (d.checks_desc || checkLines(d.checks)).join('\n'),
        placeholder: 'cmd: pytest -q\nfile: outputs/fig3.png\nmetric: EXP-002:rmse <= 0.05', hint: 'cmd: … · file: … · metric: [EXP-…:]name <= value' },
      { name: 'skills', label: 'Skills to load', type: 'list', value: d.skills || [], placeholder: 'e.g. ponytail, nv-segment-ct', hint: 'comma-separated project skill names' },
      ...(edit ? [
        { name: 'result', label: 'Result', type: 'textarea', rows: 2, value: d.result },
        { name: 'blockers', label: 'Blockers', type: 'textarea', rows: 2, value: d.blockers },
        { name: 'notes', label: 'Notes', type: 'textarea', rows: 2, value: d.notes },
      ] : []),
    ] },
  ];
}

/** Stored checks → the one-per-line text the form shows (`metric: name <= 0.05` style, same as describe_check). */
function checkLines(checks: any[] | undefined): string[] {
  return (checks || []).map((c: any) => typeof c === 'string' ? c : c.type === 'command' ? `cmd: ${c.run}` : c.type === 'file' ? `file: ${c.path}`
    : `metric: ${c.target ? c.target + ':' : ''}${c.name} ${c.op} ${c.value}`);
}

/** Unset type/role/links are sent as null (cleared), never as ''. Checks: one per line, `metric: a <= b` spacing allowed. */
function taskValues(v: any): any {
  const checks = typeof v.checks === 'string' ? v.checks.split('\n').map((x: string) => x.trim()).filter(Boolean)
    .map((x: string) => x.replace(/^metric:\s*(.+)$/i, (_m: string, rest: string) => 'metric:' + rest.replace(/\s+/g, ''))) : v.checks;
  return {
    ...v,
    checks: checks || [],
    skills: v.skills || [],
    task_type: v.task_type || null,
    assigned_role: v.assigned_role || null,
    related_question_id: v.related_question_id || null,
    related_experiment_id: v.related_experiment_id || null,
    depends_on: v.depends_on || [],
  };
}

function blank(v: any): any {
  const out: any = {};
  for (const [k, x] of Object.entries(v)) out[k] = x === null && k !== 'planned_runs' ? '' : x;
  return out;
}

function diffParams(now: any, base: any): any {
  const out: any = {};
  for (const [k, v] of Object.entries(now || {})) if (JSON.stringify(base[k]) !== JSON.stringify(v)) out[k] = v;
  return out;
}

function scalarParams(p: any): any {
  const out: any = {};
  for (const [k, v] of Object.entries(p || {})) out[k] = Array.isArray(v) ? v[0] : v;
  return out;
}

function escapeHtml(s: string) {
  return String(s || '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}
