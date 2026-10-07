import * as fs from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { CoreClient, detectPython } from './client';
import { registerCommands } from './commands';
import { Model } from './model';
import { OverviewView, Panels } from './panels';
import { Shim } from './shim';
import {
  agentRoots, artifactRoots, checkpointRoots, decisionRoots, experimentRoots, findingRoots, noteRoots, planRoots, questionRoots, ResearchTree,
} from './trees';

function pickRoot(): string | undefined {
  const cfg = vscode.workspace.getConfiguration('research').get<string>('projectRoot', '');
  const folders = vscode.workspace.workspaceFolders || [];
  if (cfg) return path.isAbsolute(cfg) || !folders.length ? cfg : path.join(folders[0].uri.fsPath, cfg);
  const withResearch = folders.find((f) => f.uri.scheme === 'file' && fs.existsSync(path.join(f.uri.fsPath, '.research', 'config.yaml')));
  return (withResearch || folders.find((f) => f.uri.scheme === 'file'))?.uri.fsPath;
}

export async function activate(ctx: vscode.ExtensionContext) {
  const out = vscode.window.createOutputChannel('Research');
  ctx.subscriptions.push(out);
  const root = pickRoot();
  if (!root) {
    vscode.commands.executeCommand('setContext', 'research.ready', true);
    vscode.commands.executeCommand('setContext', 'research.initialized', false);
    ctx.subscriptions.push(vscode.commands.registerCommand('research.init', () => vscode.window.showWarningMessage('Open a folder first to create a research project.')));
    return;
  }
  out.appendLine(`[research] project root: ${root} (remote: ${vscode.env.remoteName || 'no'})`);

  const cfg = () => vscode.workspace.getConfiguration('research');
  const python = detectPython(cfg().get<string>('pythonPath', ''), out);
  const corePath = CoreClient.corePath(ctx);
  CoreClient.pythonPathEnv = corePath;
  const client = new CoreClient(root, python, out);
  const model = new Model(client, out);
  const shim = new Shim(ctx, python, corePath);
  shim.install(cfg().get<boolean>('terminalCommand', true));
  const panels = new Panels(ctx, model, out);
  ctx.subscriptions.push(client, model, panels);

  // ------------------------------------------------------------------ views
  ctx.subscriptions.push(vscode.window.registerWebviewViewProvider('research.overview', new OverviewView(ctx, model, panels), { webviewOptions: { retainContextWhenHidden: true } }));
  const trees: [string, (m: Model) => any[]][] = [
    ['research.plans', planRoots], ['research.questions', questionRoots], ['research.experiments', experimentRoots], ['research.findings', findingRoots],
    ['research.decisions', decisionRoots], ['research.checkpoints', checkpointRoots], ['research.notes', noteRoots],
    ['research.artifacts', artifactRoots], ['research.agent', agentRoots],
  ];
  const views: Record<string, vscode.TreeView<any>> = {};
  for (const [id, roots] of trees) {
    const tv = vscode.window.createTreeView(id, { treeDataProvider: new ResearchTree(model, roots), showCollapseAll: id !== 'research.notes' });
    views[id] = tv;
    ctx.subscriptions.push(tv);
  }

  // ------------------------------------------------------------------ status bar + badges
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  status.command = 'research.resume';
  ctx.subscriptions.push(status);
  model.onChange(() => {
    const r = model.resume;
    if (!model.initialized || !r) {
      status.hide();
      return;
    }
    const needs = (r.needs_synthesis || []).length;
    const active = (r.active_runs || []).length;
    const parts = [`$(beaker) ${r.project?.name || 'Research'}`];
    if (active) parts.push(`$(sync~spin) ${active}`);
    if (needs) parts.push(`$(warning) ${needs}`);
    status.text = parts.join('  ');
    status.tooltip = new vscode.MarkdownString(
      `**${r.project?.name}** — ${r.project?.goal || 'no goal set'}\n\n` +
        (active ? `$(sync) ${active} active run(s)\n\n` : '') +
        (needs ? `$(warning) ${needs} experiment(s) need synthesis\n\n` : '') +
        (r.latest_checkpoint ? `$(bookmark) checkpoint ${r.latest_checkpoint.id}` : '$(bookmark) no checkpoint yet') + '\n\n_Click to resume_',
      true,
    );
    status.backgroundColor = needs ? new vscode.ThemeColor('statusBarItem.warningBackground') : undefined;
    status.show();
    const exp = views['research.experiments'];
    exp.badge = needs ? { value: needs, tooltip: `${needs} experiment(s) need synthesis` } : undefined;
    exp.message = undefined;
    const fnd = views['research.findings'];
    fnd.message = model.tree && !model.tree.findings.length ? 'No findings yet — they are the reusable conclusions of experiments.' : undefined;
    const q = views['research.questions'];
    q.message = model.tree && !model.tree.questions.length ? 'What are you trying to understand? Add a question.' : undefined;
    if (model.tree && !model.tree.experiments.length) exp.message = 'No experiments yet. Register one before running anything substantial.';
  });

  // ------------------------------------------------------------------ project-local CLI (.research/bin/research)
  // Agents whose shells don't inherit VS Code's terminal PATH (e.g. Claude Code's Bash tool) call this launcher.
  let binDone = false;
  const installBin = async () => {
    if (binDone || !model.initialized || !cfg().get<boolean>('projectCli', true)) return;
    binDone = true;
    try {
      const r = await client.request('install_bin');
      if (r?.updated) out.appendLine(`[research] project CLI ready: ${r.path}`);
    } catch (e: any) {
      out.appendLine(`[research] could not write .research/bin: ${e.message}`);
    }
  };
  model.onChange(() => void installBin());

  // ------------------------------------------------------------------ commands + watchers
  registerCommands(ctx, model, panels, shim, out);

  const watcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root, '.research/**'));
  const onFs = (u: vscode.Uri) => {
    const rel = path.relative(root, u.fsPath);
    if (/research\.db|\/cache\/|\.tmp-\d+$|events\.jsonl$|^\.research[\\/]bin([\\/]|$)/.test(rel)) return;
    model.schedule(400);
  };
  watcher.onDidChange(onFs);
  watcher.onDidCreate(onFs);
  watcher.onDidDelete(onFs);
  ctx.subscriptions.push(watcher);
  const agentsWatcher = vscode.workspace.createFileSystemWatcher(new vscode.RelativePattern(root, '{AGENTS.md,.research/config.yaml}'));
  agentsWatcher.onDidChange(() => model.schedule(400));
  ctx.subscriptions.push(agentsWatcher);
  ctx.subscriptions.push(vscode.window.onDidChangeWindowState((s) => s.focused && model.initialized && model.schedule(200)));
  ctx.subscriptions.push(vscode.workspace.onDidChangeConfiguration((e) => {
    if (e.affectsConfiguration('research.terminalCommand')) shim.install(cfg().get<boolean>('terminalCommand', true));
    if (e.affectsConfiguration('research.projectCli')) { binDone = false; void installBin(); }
    if (e.affectsConfiguration('research.pythonPath')) vscode.window.showInformationMessage('Reload the window to use the new Python path.', 'Reload').then((c) => c && vscode.commands.executeCommand('workbench.action.reloadWindow'));
  }));

  await model.refresh();
  if (model.error) {
    vscode.window.showErrorMessage(`Research Panel: ${model.error}`, 'Show log', 'Set Python path').then((c) => {
      if (c === 'Show log') out.show();
      if (c === 'Set Python path') vscode.commands.executeCommand('workbench.action.openSettings', 'research.pythonPath');
    });
  }
  return { model, panels };
}

export function deactivate() {
  /* disposables handle cleanup */
}
