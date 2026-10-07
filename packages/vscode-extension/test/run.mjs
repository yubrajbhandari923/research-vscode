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

let exe = await downloadAndUnzipVSCode('stable');
// Newer macOS builds name the binary `Code` instead of `Electron`; older @vscode/test-electron still expects `Electron`.
if (!fs.existsSync(exe) && fs.existsSync(path.join(path.dirname(exe), 'Code'))) exe = path.join(path.dirname(exe), 'Code');
const [cli, ...args] = resolveCliArgsFromVSCodeExecutablePath(exe);
const r = spawnSync(cli, [...args, '--extensions-dir', extDir, '--user-data-dir', userDir, '--install-extension', path.join(repo, 'dist', vsix)], { encoding: 'utf8' });
console.log(r.stdout, r.stderr);
if (r.status !== 0) throw new Error('VSIX install failed');
const out = path.join(tmp, 'results.json');
try {
  await runTests({
    vscodeExecutablePath: exe,
    extensionDevelopmentPath: path.join(here, 'harness'),
    extensionTestsPath: path.join(here, 'harness', 'suite', 'index.js'),
    launchArgs: [workspace, '--extensions-dir', extDir, '--user-data-dir', userDir, '--disable-workspace-trust', '--skip-welcome'],
    extensionTestsEnv: { RESEARCH_TEST_OUT: out, RESEARCH_TEST_USERDIR: userDir },
  });
} finally {
  if (fs.existsSync(out)) {
    const res = JSON.parse(fs.readFileSync(out, 'utf8'));
    for (const x of res) console.log(`${x.ok ? '✓' : '✗'} ${x.name}${x.ok ? '' : '  ' + JSON.stringify(x.extra)}`);
    console.log(`${res.filter((x) => x.ok).length}/${res.length} checks passed`);
  }
}
