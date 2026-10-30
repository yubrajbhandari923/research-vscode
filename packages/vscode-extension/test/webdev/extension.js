// Development harness for VS Code Web (code serve-web): runs inside the real extension host next to the installed VSIX.
// Reads <workspace>/.vscode/harness.json: { page, target } opens the Control Center; { suite: true } runs the shared check suite.
const vscode = require('vscode');
const fs = require('fs');
const path = require('path');

exports.activate = async () => {
  const ws = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0].uri.fsPath;
  if (!ws) return;
  let cfg;
  try { cfg = JSON.parse(fs.readFileSync(path.join(ws, '.vscode', 'harness.json'), 'utf8')); } catch { return; }
  const note = (m) => fs.appendFileSync(path.join(ws, '.vscode', 'harness.log'), m + '\n');
  try {
    if (cfg.suite) { note('suite start'); await require(cfg.suitePath).run(); note('suite done'); }
    if (cfg.page !== undefined) {
      await new Promise((r) => setTimeout(r, cfg.delay || 3000));
      await vscode.commands.executeCommand('workbench.action.closeSidebar');
      await vscode.commands.executeCommand('research.openControlCenter', cfg.page || undefined, cfg.target);
      note('control center opened: ' + cfg.page);
    }
  } catch (e) { note('error: ' + (e && e.stack || e)); }
};
