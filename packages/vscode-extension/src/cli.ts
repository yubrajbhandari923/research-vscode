/** Locates and version-checks the installed `research` CLI, which is the extension's backend. */
import * as cp from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';
import { canSelfUpdate, coreCompat, sh } from './util';

export const INSTALL_CMD = 'pip install --upgrade <wheel from the GitHub release>   # or: uv tool install <wheel>';
const RELEASES = 'https://github.com/yubrajbhandari923/research-vscode/releases';

export interface CliState {
  path: string;
  found?: string;
  status: 'ok' | 'missing' | 'older' | 'newer';
}

export const cli: { state?: CliState } = {};

/** Command line for terminals: `research ...` via the resolved CLI. */
export function commandLine(args: string[]): string {
  return [cli.state?.path || 'research', ...args.map(sh)].join(' ');
}

function probe(exe: string): any {
  try {
    const r = cp.spawnSync(exe, ['version', '--json'], { encoding: 'utf8', timeout: 15000 });
    if (r.status === 0) return JSON.parse(r.stdout.trim().split(/\r?\n/).pop() || '');
  } catch { /* fall through */ }
  try {   // a CLI older than 3.0 has no `version --json`: `--version` still tells us it is installed and too old
    const r = cp.spawnSync(exe, ['--version'], { encoding: 'utf8', timeout: 15000 });
    const m = r.status === 0 && /(\d+\.\d+\.\d+)/.exec(r.stdout || '');
    if (m) return { version: m[1] };
  } catch { /* not usable */ }
  return undefined;
}

export function checkCli(ctx: vscode.ExtensionContext, out: vscode.OutputChannel): CliState {
  const cfg = vscode.workspace.getConfiguration('research').get<string>('cliPath', '');
  const home = os.homedir();
  const cands = cfg ? [cfg] : ['research', path.join(home, '.local', 'bin', 'research'), path.join(home, '.local', 'pipx', 'venvs', 'research-panel', 'bin', 'research')];
  const required: string = ctx.extension.packageJSON.researchCore;
  for (const c of cands) {
    if (c !== 'research' && !fs.existsSync(c)) continue;
    const info = probe(c);
    if (!info?.version) continue;
    const status = ({ ok: 'ok', older: 'older', newer: 'newer', invalid: 'older' } as const)[coreCompat(info.version, required)];
    out.appendLine(`[cli] ${c}: core ${info.version} (extension needs ${required}) -> ${status}`);
    cli.state = { path: c, found: info.version, status };
    return cli.state;
  }
  out.appendLine(`[cli] no usable research CLI found (tried ${cands.join(', ')})`);
  return (cli.state = { path: cfg || 'research', status: 'missing' });
}

export function showVersionInfo(ctx: vscode.ExtensionContext) {
  const s = cli.state;
  const fix = { missing: 'The research CLI was not found. Install it from the GitHub release (pip install the wheel), or set "research.cliPath" if it lives elsewhere, then reload the window.',
    older: canSelfUpdate(s?.found) ? 'The CLI is too old for this extension. Run `research self-update`, then reload the window.'
      : 'The CLI is too old for this extension and predates `research self-update`. Install the new wheel from the GitHub release (pip install --upgrade <wheel>, or uv tool install <wheel>), then reload the window.',
    newer: 'This extension is older than your CLI. Install a newer VSIX from the GitHub releases page, then reload the window.', ok: 'Everything matches.' }[s?.status || 'missing'];
  vscode.window.showInformationMessage(
    `Research Panel ${ctx.extension.packageJSON.version} requires core ${ctx.extension.packageJSON.researchCore} or newer (same major version). ` +
    `Found: ${s?.found ? 'core ' + s.found : 'none'} (${s?.path || 'research'}). ${fix}`, { modal: true });
}

/** One non-modal warning for a bad CLI state. */
export function warnCli(s: CliState) {
  const w = vscode.window;
  if (s.status === 'missing') {
    w.showWarningMessage('Research Panel needs the `research` CLI, which was not found. Install it from the GitHub release (pip install the wheel), then `research self-update` keeps it current.', 'Copy install command', 'Open releases page', 'Set CLI path')
      .then((c) => c === 'Copy install command' ? vscode.env.clipboard.writeText(INSTALL_CMD)
        : c === 'Open releases page' ? vscode.env.openExternal(vscode.Uri.parse(RELEASES))
        : c === 'Set CLI path' ? vscode.commands.executeCommand('workbench.action.openSettings', 'research.cliPath') : undefined);
  } else if (s.status === 'older' && !canSelfUpdate(s.found)) {
    w.showWarningMessage(`The research CLI (core ${s.found}) is too old for this extension and predates \`research self-update\`. Install the new wheel from the GitHub release, then reload the window.`, 'Copy install command', 'Open releases page')
      .then((c) => { if (c === 'Copy install command') void vscode.env.clipboard.writeText(INSTALL_CMD); else if (c === 'Open releases page') void vscode.env.openExternal(vscode.Uri.parse(RELEASES)); });
  } else if (s.status === 'older') {
    w.showWarningMessage(`The research CLI (core ${s.found}) is too old for this extension. Update the CLI: run \`research self-update\`.`, 'Run research self-update')
      .then((c) => { if (c) { const t = w.createTerminal('research self-update'); t.show(); t.sendText(`${sh(s.path)} self-update`); } });
  } else if (s.status === 'newer') {
    w.showWarningMessage(`This extension is older than your research CLI (core ${s.found}): install a newer VSIX from GitHub releases.`, 'Open releases page')
      .then((c) => c && vscode.env.openExternal(vscode.Uri.parse(RELEASES)));
  }
}
