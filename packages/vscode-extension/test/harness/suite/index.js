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
    for (const v of ['questions', 'experiments', 'findings', 'decisions', 'checkpoints', 'notes', 'artifacts', 'agent']) {
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
