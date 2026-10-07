// Runs inside a real VS Code extension host against the INSTALLED VSIX.
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');
const cp = require('child_process');

exports.run = async function () {
  const results = [];
  const check = (name, cond, extra) => { results.push({ name, ok: !!cond, extra }); };
  const out = process.env.RESEARCH_TEST_OUT;
  try {
    const ext = vscode.extensions.getExtension('research-local.research-panel');
    check('extension installed', !!ext, ext && ext.extensionPath);
    const api = await ext.activate();
    const { model } = api;
    check('extension path is the installed VSIX (not the dev tree)', !ext.extensionPath.includes('packages/vscode-extension'), ext.extensionPath);
    check('bundled python core present', fs.existsSync(path.join(ext.extensionPath, 'python', 'research', '__init__.py')));
    await model.refresh();
    check('core started + project initialized', model.initialized, model.error);
    {
      const ws = vscode.workspace.workspaceFolders[0].uri.fsPath;
      const bin = path.join(ws, '.research', 'bin', 'research');
      for (let i = 0; i < 40 && !fs.existsSync(bin); i++) await new Promise((r) => setTimeout(r, 250));
      check('project-local CLI written to .research/bin', fs.existsSync(bin), bin);
      const r = cp.spawnSync(bin, ['--json', 'status'], { cwd: ws, env: { HOME: process.env.HOME, PATH: '/usr/bin:/bin' }, encoding: 'utf8' });
      check('project-local CLI runs without PATH or install', r.status === 0, r.stderr);
    }
    const t = model.tree;
    check('questions reconstructed', t.questions.length === 2, t.questions.map((q) => q.id));
    check('experiments reconstructed', t.experiments.length === 2, t.experiments.map((e) => e.id));
    const e1 = t.experiments.find((e) => e.id === 'EXP-001');
    check('runs nested under experiment', e1 && e1.runs.length === 3, e1 && e1.runs.map((r) => r.id));
    check('findings', t.findings.length === 2);
    check('decisions', t.decisions.length === 1);
    check('checkpoints', t.checkpoints.length === 1);
    check('notes (pinned)', t.notes.length === 1 && t.notes[0].pinned);
    check('skills', t.skills.length >= 4, t.skills.map((s) => s.name));
    const r = model.resume;
    check('resume: latest checkpoint', r.latest_checkpoint && r.latest_checkpoint.id === 'CP-001');
    check('resume: baseline', r.baseline && r.baseline.id === 'EXP-001');
    check('resume: failed directions', r.failed_directions.length === 1);
    const arts = t.artifacts;
    check('artifact paths are project-relative', arts.every((a) => !path.isAbsolute(a.path)), arts.map((a) => a.path));
    check('artifacts resolve in the moved project', arts.every((a) => a.exists && a.abspath.startsWith(vscode.workspace.workspaceFolders[0].uri.fsPath)));
    // UI commands must not throw
    for (const [cmd, arg] of [['research.resume'], ['research.open', 'EXP-001'], ['research.open', 'F-001'], ['research.open', 'RUN-0002'],
      ['research.open', 'A-0007'], ['research.open', 'CP-001'], ['research.open', 'Q-001'], ['research.open', 'D-001'], ['research.refresh']]) {
      try { await vscode.commands.executeCommand(cmd, arg); check(`command ${cmd} ${arg || ''}`, true); }
      catch (err) { check(`command ${cmd} ${arg || ''}`, false, String(err)); }
    }
    // ---------------------------------------------------------------- plans & tasks through the real form handlers
    const { panels } = api;
    const form = (key) => panels.panels.get('f:' + key);
    const submit = async (name, key, values, ctx) => {
      const ep = form(key);
      if (!ep) return check(`${name}: form opened`, false, key);
      try { const id = await ep.handler(values, ctx || {}); check(name, !!id, id); return id; }
      catch (err) { check(name, false, String(err && err.message || err)); }
    };
    await vscode.commands.executeCommand('research.plan.create');
    check('plan form has root_question_id field', form('new-plan') && form('new-plan').panel.webview.html.includes('data-field="root_question_id"'));
    const planId = await submit('New Plan form saves (with question)', 'new-plan',
      { title: 'Harness plan', objective: 'Check plans work', success_criteria: null, root_question_id: 'Q-001', context: null });
    const taskVals = (title, deps) => ({ title, goal: null, task_type: 'research', assigned_role: null, depends_on: deps,
      inputs: null, expected_outputs: null, related_question_id: null, related_experiment_id: 'EXP-001', acceptance_criteria: null, verification: null });
    await vscode.commands.executeCommand('research.task.create', planId);
    const tA = await submit('Add Task form saves', `new-task-${planId}`, taskVals('Task A', []), { plan_id: planId });
    await vscode.commands.executeCommand('research.task.create', planId);
    const tB = await submit('Add Task form saves (with dependency)', `new-task-${planId}`, taskVals('Task B', [tA]), { plan_id: planId });
    let next = await model.client.request('next_task', { plan_id: planId });
    check('next ready task is the one without deps', next && next.id === tA, next && next.id);
    await vscode.commands.executeCommand('research.task.start', tA);
    await vscode.commands.executeCommand('research.setStatus', tA, 'done');
    next = await model.client.request('next_task', { plan_id: planId });
    check('Start + Change Status → dependent task becomes ready', next && next.id === tB, next && next.id);
    await vscode.commands.executeCommand('research.edit', tB);
    await submit('Edit task form saves', `edit-${tB}`, { ...taskVals('Task B edited', []), goal: 'new goal', task_type: null,
      related_experiment_id: null, status: 'verify', result: null, blockers: null, notes: null });
    const tb = await model.client.request('show', { id: tB });
    check('task edit applied (title, goal, cleared type/link/deps, status)', tb.title === 'Task B edited' && tb.goal === 'new goal'
      && tb.task_type === null && tb.related_experiment_id === null && !tb.depends_on && tb.status === 'verify', tb);
    await vscode.commands.executeCommand('research.edit', planId);
    await submit('Edit plan form saves', `edit-${planId}`, { title: 'Harness plan v2', objective: 'Check plans work', success_criteria: 'all green',
      status: 'active', root_question_id: null, context: null });
    const pd = await model.client.request('show', { id: planId });
    check('plan edit applied + question cleared', pd.title === 'Harness plan v2' && pd.success_criteria === 'all green' && pd.root_question_id === null, pd);
    check('plan page has activity events', (pd.events || []).length >= 4, (pd.events || []).length);
    for (const id of [planId, tB]) {
      try { await vscode.commands.executeCommand('research.open', id); check(`command research.open ${id}`, true); }
      catch (err) { check(`command research.open ${id}`, false, String(err)); }
    }
    const home = await model.client.request('home');
    check('Research Home: active plan + verify attention item', home.active_plan && home.active_plan.id === planId
      && home.attention_items.some((a) => a.kind === 'verification_needed' && a.id === tB), home.attention_items);
    await vscode.commands.executeCommand('research.setStatus', planId, 'completed');
    await model.refresh();
    const tp = model.tree.plans.find((p) => p.id === planId);
    check('plan status change reflected in Plans tree data', tp && tp.status === 'completed' && tp.tasks.length === 2, tp && tp.status);
    const cur = fs.readFileSync(path.join(vscode.workspace.workspaceFolders[0].uri.fsPath, '.research', 'context', 'current.md'), 'utf8');
    check('current.md has Active plans section', cur.includes('## Active plans'));

    // ---------------------------------------------------------------- Research OS: checks, reviews, compare, skills, …
    const ws = vscode.workspace.workspaceFolders[0].uri.fsPath;
    const shimPath = path.join(process.env.RESEARCH_TEST_USERDIR, 'User', 'globalStorage', 'research-local.research-panel', 'bin', 'research');
    await vscode.commands.executeCommand('research.task.create', planId);
    const tC = await submit('Add Task form saves (checks + skills)', `new-task-${planId}`, {
      ...taskVals('Task C', []), checks: 'file: outputs/plot.png\nmetric: EXP-001:final_error <= 1e9', skills: ['run-experiment'] }, { plan_id: planId });
    let tc = await model.client.request('show', { id: tC });
    check('task form stored checks + skills', (tc.checks_desc || []).length === 2 && (tc.skills_info || []).some((s) => s.name === 'run-experiment'), tc.checks_desc);
    await vscode.commands.executeCommand('research.task.verify', tC);
    tc = await model.client.request('show', { id: tC });
    check('Verify command runs checks (pass recorded)', tc.verifications && tc.verifications[0] && tc.verifications[0].verdict === 'pass', tc.verifications);
    const brief = await model.client.request('task_brief', { id: tC });
    check('agent brief lists checks and skills', brief.includes('file: outputs/plot.png') && brief.includes('run-experiment'));
    cp.execSync(`${shimPath} finding create "agent claim for review" --supports RUN-0001`, { cwd: ws, env: { ...process.env, CLAUDECODE: '1' } });
    // look it up through the core directly: model.refresh() may coalesce with a watcher refresh already in flight
    const af = (await model.client.request('tree')).findings.find((f) => f.title === 'agent claim for review');
    let home2 = await model.client.request('home');
    check('Research Home: agent finding awaiting review', af && home2.attention_items.some((a) => a.kind === 'awaiting_review' && a.id === af.id), home2.attention_items.map((a) => a.kind));
    await vscode.commands.executeCommand('research.finding.review', af.id);
    await submit('Review form saves', `review-${af.id}`, { verdict: 'supported', notes: 'checked the run', confidence: 'medium' });
    const afd = await model.client.request('show', { id: af.id });
    check('human review makes the agent finding supported', afd.status === 'supported' && afd.reviews.length === 1 && afd.reviews[0].author_type === 'human', afd.status);
    await vscode.commands.executeCommand('research.run.compare', 'RUN-0001', 'RUN-0002');
    await new Promise((res) => setTimeout(res, 800));
    const cmpPanel = panels.panels.get('cmp:RUN-0001:RUN-0002');
    check('compare panel renders', cmpPanel && cmpPanel.panel.webview.html.includes('RUN-0001 vs RUN-0002'), cmpPanel && cmpPanel.panel.webview.html.slice(0, 200));
    const skillSrc = fs.mkdtempSync(path.join(require('os').tmpdir(), 'harness-skill-src-'));
    const skillDir = path.join(skillSrc, 'skills', 'harness-skill');
    fs.mkdirSync(skillDir, { recursive: true });
    fs.writeFileSync(path.join(skillDir, 'SKILL.md'), '---\nname: harness-skill\ndescription: test skill\n---\n# hi\n');
    const imp = await model.client.request('skills_add', { source: skillSrc, always: true });
    const tree2 = await model.client.request('tree');
    check('skill import → tree + .claude/skills link', imp.added[0] === 'harness-skill' && tree2.skills.some((s) => s.name === 'harness-skill' && s.always)
      && fs.existsSync(path.join(ws, '.claude', 'skills', 'harness-skill', 'SKILL.md')), imp);
    const hits = await model.client.request('search', { query: 'alpha' });
    check('search finds records', hits.length > 0, hits.length);
    const rep = await model.client.request('report', { scope: null, fmt: 'both' });
    check('report written', rep.paths.every((p) => fs.existsSync(path.join(ws, p))), rep.paths);
    const disp = await model.client.request('dispatch', { role: 'verifier', target: af.id });
    check('dispatch prepares agent command + brief', disp.argv[0] === 'claude' && disp.env.RESEARCH_AGENT_ROLE === 'verifier' && fs.existsSync(path.join(ws, disp.brief)), disp.argv);
    for (const id of [tC, af.id]) {
      try { await vscode.commands.executeCommand('research.open', id); check(`command research.open ${id} (new sections)`, true); }
      catch (err) { check(`command research.open ${id}`, false, String(err)); }
    }

    for (const v of ['plans', 'questions', 'experiments', 'findings', 'decisions', 'checkpoints', 'notes', 'artifacts', 'agent']) {
      try { await vscode.commands.executeCommand(`research.${v}.focus`); check(`view ${v} opens`, true); } catch (err) { check(`view ${v}`, false, String(err)); }
    }
    await vscode.commands.executeCommand('research.overview.focus');
    // mutation through the RPC → visible after refresh
    const q = await model.client.request('create', { type: 'question', title: 'Created from the VS Code test' });
    await model.refresh();
    check('create via UI core is reflected in tree', model.tree.questions.some((x) => x.id === q.id), q.id);
    check('UI actions are authored as human', q.author_type === 'human', q.author_type);
    // terminal shim
    const found = path.join(process.env.RESEARCH_TEST_USERDIR, 'User', 'globalStorage', 'research-local.research-panel', 'bin', 'research');
    check('terminal shim written', fs.existsSync(found), found);
    if (found) {
      const outp = cp.execSync(`${found} --json status`, { cwd: vscode.workspace.workspaceFolders[0].uri.fsPath, env: { ...process.env, CLAUDECODE: '' } }).toString();
      check('shim runs bundled CLI', JSON.parse(outp).project.name === 'alpha-sweep demo');
    }
    // file watcher: CLI change shows up without manual refresh
    cp.execSync(`${found} finding create "watcher test finding"`, { cwd: vscode.workspace.workspaceFolders[0].uri.fsPath, env: { ...process.env, CLAUDECODE: '1' } });
    await new Promise((res) => setTimeout(res, 2500));
    const wf = model.tree.findings.find((f) => f.title === 'watcher test finding');
    check('file watcher picks up agent CLI change', !!wf, wf && wf.author_type);
    check('agent authorship detected from terminal (CLAUDECODE=1)', wf && wf.author_type === 'agent' && wf.author_name === 'claude-code');
  } catch (err) {
    check('suite crashed', false, String(err && err.stack || err));
  }
  fs.writeFileSync(out, JSON.stringify(results, null, 2));
  const failed = results.filter((r) => !r.ok);
  if (failed.length) throw new Error(`${failed.length} checks failed`);
};
