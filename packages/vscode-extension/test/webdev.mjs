// Real VS Code (web build served by `code serve-web`) + the installed VSIX, driven by headless Chrome.
//   node test/webdev.mjs <demo-project> <outDir> [suite|ui|all]
// suite: the shared extension-host check suite (test/harness/suite) runs inside the real extension host.
// ui: Playwright (playwright-core in C:\\Windows\\Temp\\rp-pw) opens the Control Center from the command palette and exercises it, light and dark.
// Needs: dist/*.vsix, a VS Code CLI (`code` or ~/.vscode-server/code-*), and Chrome (the Windows host's under WSL, or $CHROME).
import { execFileSync, spawn, spawnSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

const here = path.dirname(new URL(import.meta.url).pathname);
const repo = path.resolve(here, '..', '..', '..');
const [, , demo, outDir, mode = 'all'] = process.argv;
if (!demo || !outDir) throw new Error('usage: node test/webdev.mjs <demo-project> <outDir> [suite|ui|all]');
const vsix = path.join(repo, 'dist', fs.readdirSync(path.join(repo, 'dist')).find((f) => f.endsWith('.vsix')));
const CHROME = process.env.CHROME || (fs.existsSync('/mnt/c/Program Files/Google/Chrome/Application/chrome.exe') ? '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome');
const wsl = CHROME.startsWith('/mnt/c/');
const stage = wsl ? '/mnt/c/Windows/Temp/rp-webdev' : fs.mkdtempSync('/tmp/rp-webdev-');
const win = (p) => (p.startsWith('/mnt/c/') ? 'C:\\' + p.slice(7).replace(/\//g, '\\') : p);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
fs.mkdirSync(stage, { recursive: true });
fs.mkdirSync(outDir, { recursive: true });

const home = os.homedir();
const cliCandidates = [process.env.VSCODE_CLI, ...fs.readdirSync(path.join(home, '.vscode-server')).filter((f) => f.startsWith('code-')).map((f) => path.join(home, '.vscode-server', f))].filter(Boolean);
const cli = cliCandidates.find((c) => spawnSync(c, ['serve-web', '--help']).status === 0);
if (!cli) throw new Error('no VS Code CLI with serve-web found (set VSCODE_CLI)');

const data = fs.mkdtempSync('/tmp/rp-vsw-');
const port = 8700 + Math.floor(Math.random() * 200);
const resultsFile = path.join(data, 'suite-results.json');
const server = spawn(cli, ['serve-web', '--host', '127.0.0.1', '--port', String(port), '--without-connection-token', '--accept-server-license-terms', '--server-data-dir', data],
  { env: { ...process.env, RESEARCH_TEST_OUT: resultsFile, RESEARCH_TEST_USERDIR: path.join(data, 'data') }, stdio: 'ignore' });
const stop = () => { try { server.kill(); } catch {} };
process.on('exit', stop);

async function up() {
  for (let i = 0; i < 120; i++) { try { if ((await fetch(`http://127.0.0.1:${port}/`)).status === 200) return; } catch {} await sleep(1000); }
  throw new Error('VS Code web server did not start');
}
await up();
const serverBin = (() => { const d = path.join(home, '.vscode', 'cli', 'serve-web'); for (const c of fs.readdirSync(d)) { const f = path.join(d, c, 'bin', 'code-server'); if (fs.existsSync(f)) return f; } })();
const ext = path.join(data, 'extensions');
fs.mkdirSync(ext, { recursive: true });
const hv = path.join(stage, 'harness.vsix');
execFileSync('npx', ['vsce', 'package', '--no-dependencies', '--allow-missing-repository', '--skip-license', '-o', win(hv).replace(/^C:.*/, hv)], { cwd: path.join(here, 'webdev'), stdio: 'ignore' });
for (const v of [vsix, hv]) {
  const r = spawnSync(serverBin, ['--extensions-dir', ext, '--install-extension', v], { encoding: 'utf8' });
  if (!/successfully installed/.test(r.stdout)) throw new Error('install failed: ' + v + '\n' + r.stdout + r.stderr);
}
console.log(`VS Code web on :${port}, extensions installed: ${fs.readdirSync(ext).filter((x) => !x.endsWith('.json')).join(', ')}`);

function userSettings(settings) {   // machine/user level: workspace-level settings are ignored while the folder is untrusted
  const d = path.join(data, 'data', 'User');
  fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(d, 'settings.json'), JSON.stringify({ 'security.workspace.trust.enabled': false, 'workbench.startupEditor': 'none', 'window.commandCenter': false,
    'chat.commandCenter.enabled': false, 'workbench.secondarySideBar.defaultVisibility': 'hidden', ...settings }));
}
function workspace(name, harness, settings) {
  userSettings({ 'workbench.colorTheme': settings['workbench.colorTheme'] || 'Default Dark Modern' });
  const w = path.join(os.tmpdir(), `rp-ws-${name}`);
  fs.rmSync(w, { recursive: true, force: true });
  fs.cpSync(demo, w, { recursive: true });
  fs.mkdirSync(path.join(w, '.vscode'), { recursive: true });
  fs.writeFileSync(path.join(w, '.vscode', 'harness.json'), JSON.stringify(harness));
  fs.writeFileSync(path.join(w, '.vscode', 'settings.json'), JSON.stringify({ 'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'window.commandCenter': false,
    'workbench.tips.enabled': false, 'workbench.colorTheme': 'Default Dark Modern', ...settings }));
  return w;
}
const NODE_WIN = '/mnt/c/Program Files/nodejs/node.exe';
const driver = (name, extra = [], script = 'webdev-drive.cjs') => {
  const dst = '/mnt/c/Windows/Temp/rp-pw/' + script;
  fs.copyFileSync(path.join(here, script), dst);
  return spawn(NODE_WIN, ['C:\\Windows\\Temp\\rp-pw\\' + script, String(port), WS_FOLDER, win(stage), name, ...extra], { stdio: 'inherit', cwd: '/mnt/c/Windows/Temp/rp-pw' });
};
let WS_FOLDER = '';
let failed = 0;

if (mode === 'suite' || mode === 'all') {
  const w = workspace('suite', { suite: true, suitePath: path.join(here, 'harness', 'suite', 'index.js') }, {});
  WS_FOLDER = w;
  const c = driver('suite', ['hold', '200']);
  for (let i = 0; i < 200 && !fs.existsSync(resultsFile); i++) await sleep(1000);
  c.kill();
  if (!fs.existsSync(resultsFile)) { console.log('suite did not finish; harness log:\n' + (fs.existsSync(path.join(w, '.vscode', 'harness.log')) ? fs.readFileSync(path.join(w, '.vscode', 'harness.log'), 'utf8') : '(none)')); failed++; }
  else {
    const res = JSON.parse(fs.readFileSync(resultsFile, 'utf8'));
    for (const x of res) console.log(`${x.ok ? '✓' : '✗'} ${x.name}${x.ok ? '' : '  ' + JSON.stringify(x.extra)}`);
    console.log(`${res.filter((x) => x.ok).length}/${res.length} checks passed in real VS Code`);
    failed += res.filter((x) => !x.ok).length;
  }
}

if (mode === 'ui' || mode === 'all') {
  for (const theme of [['dark', 'Default Dark Modern'], ['light', 'Default Light Modern']].filter((t) => !process.env.THEME || t[0] === process.env.THEME)) {
    WS_FOLDER = workspace(`ui-${theme[0]}`, {}, { 'workbench.colorTheme': theme[1] });
    await new Promise((resolve) => { const d = driver(theme[0]); d.on('exit', (code) => { if (code) failed++; resolve(); }); });
    const rf = path.join(stage, `${theme[0]}-results.json`);
    if (fs.existsSync(rf)) failed += JSON.parse(fs.readFileSync(rf, 'utf8')).filter((x) => !x.ok).length;
    for (const f of fs.readdirSync(stage).filter((x) => x.startsWith(theme[0] + '-') && x.endsWith('.png'))) fs.copyFileSync(path.join(stage, f), path.join(outDir, `vscode-${f}`));
  }
}
if (mode === 'init') {   // Initialize Project from an EMPTY folder, then the Agents tab of the created project (demo arg is ignored)
  const empty = path.join(os.tmpdir(), 'rp-ws-init', 'sparse-ct');
  fs.rmSync(path.dirname(empty), { recursive: true, force: true });
  fs.mkdirSync(empty, { recursive: true });
  userSettings({ 'workbench.colorTheme': 'Default Dark Modern' });
  fs.mkdirSync(path.join(empty, '.vscode'), { recursive: true });   // trust is off only via workspace settings in this harness
  fs.writeFileSync(path.join(empty, '.vscode', 'settings.json'), JSON.stringify({ 'workbench.startupEditor': 'none', 'security.workspace.trust.enabled': false, 'window.commandCenter': false }));
  for (const [phase, folder] of [[undefined, empty], ['2', path.join(empty, 'main')]]) {
    WS_FOLDER = folder;
    await new Promise((resolve) => { const d = driver(phase ? 'init2' : 'init', phase ? ['2'] : [], 'webdev-init-drive.cjs'); d.on('exit', (code) => { if (code) failed++; resolve(); }); });
  }
  for (const n of ['init', 'init2']) { const rf = path.join(stage, n + '-results.json'); if (fs.existsSync(rf)) failed += JSON.parse(fs.readFileSync(rf, 'utf8')).filter((x) => !x.ok).length; }
  for (const f of fs.readdirSync(stage).filter((x) => x.startsWith('init') && x.endsWith('.png'))) fs.copyFileSync(path.join(stage, f), path.join(outDir, `vscode-${f}`));
  for (const d of ['main/.git', 'ledger/.research/config.yaml', 'experiments', 'sparse-ct.code-workspace']) console.log((fs.existsSync(path.join(empty, d)) ? '✓ on disk: ' : '✗ MISSING: ') + d);
}
stop();
fs.rmSync(stage, { recursive: true, force: true });
process.exit(failed ? 1 : 0);
