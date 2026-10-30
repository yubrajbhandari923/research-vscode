// Clean-install test: installs dist/*.vsix into a fresh extensions dir of a downloaded VS Code,
// opens a copy of the demo project at a NEW path, and runs test/harness/suite in the extension host.
import { downloadAndUnzipVSCode, resolveCliArgsFromVSCodeExecutablePath, runTests } from '@vscode/test-electron';
import { spawnSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

const here = path.dirname(new URL(import.meta.url).pathname);
const repo = path.resolve(here, '..', '..', '..');
const vsix = fs.readdirSync(path.join(repo, 'dist')).find((f) => f.endsWith('.vsix'));
if (!vsix) throw new Error('build the VSIX first: npm run package');
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'research-clean-'));
const extDir = path.join(tmp, 'extensions');
const userDir = path.join(tmp, 'user');
const workspace = process.env.RESEARCH_TEST_WORKSPACE;
if (!workspace) throw new Error('set RESEARCH_TEST_WORKSPACE to a demo project');
const testWorkspace = path.join(tmp, 'workspace');
fs.cpSync(workspace, testWorkspace, { recursive: true });
fs.rmSync(path.join(testWorkspace, '.research', 'research.db'), { force: true });

let exe = process.env.RESEARCH_TEST_VSCODE_EXECUTABLE || await downloadAndUnzipVSCode('stable');
// Newer macOS builds name the binary `Code` instead of `Electron`; older @vscode/test-electron still expects `Electron`.
if (!fs.existsSync(exe) && fs.existsSync(path.join(path.dirname(exe), 'Code'))) exe = path.join(path.dirname(exe), 'Code');
let [cli, ...args] = resolveCliArgsFromVSCodeExecutablePath(exe);
let cliEnv = process.env;
if (process.platform === 'linux') {   // bin/code asks for confirmation under WSL; run the CLI entry point with the bundled Electron-as-Node instead
  cli = exe;
  args = [path.join(path.dirname(exe), 'resources', 'app', 'out', 'cli.js')];
  cliEnv = { ...process.env, ELECTRON_RUN_AS_NODE: '1' };
}
const r = spawnSync(cli, [...args, '--extensions-dir', extDir, '--user-data-dir', userDir, '--install-extension', path.join(repo, 'dist', vsix)], { encoding: 'utf8', env: cliEnv });
console.log(r.stdout, r.stderr);
if (r.status !== 0) throw new Error('VSIX install failed');
const out = path.join(tmp, 'results.json');
try {
  await runTests({
    vscodeExecutablePath: exe,
    extensionDevelopmentPath: path.join(here, 'harness'),
    extensionTestsPath: path.join(here, 'harness', 'suite', 'index.js'),
    launchArgs: [testWorkspace, '--extensions-dir', extDir, '--user-data-dir', userDir, '--disable-workspace-trust', '--skip-welcome', ...(process.platform === 'linux' ? ['--no-sandbox'] : [])],
    extensionTestsEnv: { RESEARCH_TEST_OUT: out, RESEARCH_TEST_USERDIR: userDir },
  });
} finally {
  if (fs.existsSync(out)) {
    const res = JSON.parse(fs.readFileSync(out, 'utf8'));
    for (const x of res) console.log(`${x.ok ? '✓' : '✗'} ${x.name}${x.ok ? '' : '  ' + JSON.stringify(x.extra)}`);
    console.log(`${res.filter((x) => x.ok).length}/${res.length} checks passed`);
  }
}
