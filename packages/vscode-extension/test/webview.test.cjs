// Interaction tests for the React Control Center against the REAL Python core (JSON-RPC over stdio), in jsdom.
const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const esbuild = require('esbuild');
const { JSDOM } = require('jsdom');

const ROOT = path.resolve(__dirname, '../../..');
const PY = process.env.RESEARCH_PYTHON || path.join(ROOT, '.venv/bin/python');
const PYPATH = [path.join(ROOT, 'packages/core'), path.join(ROOT, 'packages/cli')].join(path.delimiter);
let proj, lib, dom;

function startCore(root) {
  const proc = spawn(PY, ['-m', 'research.rpc', '--root', root], { env: { ...process.env, XDG_CONFIG_HOME: path.join(os.tmpdir(), 'rp-xdg-empty'), PYTHONPATH: PYPATH, XDG_CONFIG_HOME: path.join(os.tmpdir(), 'rp-xdg-empty'), RESEARCH_CONFIG: '' } });
  let buf = '', seq = 0; const waiting = new Map(); let ready;
  const up = new Promise((r) => (ready = r));
  proc.stdout.on('data', (d) => {
    buf += d; let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (!line.trim()) continue;
      const m = JSON.parse(line);
      if (m.event === 'ready') ready();
      else if (waiting.has(m.id)) { const w = waiting.get(m.id); waiting.delete(m.id); m.error ? w.err(new Error(m.error.message)) : w.ok(m.result); }
    }
  });
  const call = async (method, params = {}) => { await up; return new Promise((ok, err) => { const id = ++seq; waiting.set(id, { ok, err }); proc.stdin.write(JSON.stringify({ id, method, params }) + '\n'); }); };
  return { call, stop: () => proc.kill() };
}
function hostFor(core) {
  const log = { entities: [], docs: [] }; let state = {};
  return { log, call: (m, p) => core.call(m, p), openEntity: (id) => log.entities.push(id), run: (c, a) => (log.runs = log.runs || []).push([c, a]), openDoc: (p) => log.docs.push(p), openFile: (p) => (log.files = log.files || []).push(p), openReport: (f) => (log.reports = log.reports || []).push(f), openGuide: (d, f) => (log.guide = log.guide || []).push([d, f]), getState: () => state, setState: (s) => (state = s) };
}

before(async () => {
  proj = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-ui-'));
  execFileSync('git', ['init', '-q'], { cwd: proj });
  execFileSync(PY, ['-c', `from research.store import Project; Project.init(${JSON.stringify(proj)}, name="ui-test", goal="Measure convergence").close()`], { env: { ...process.env, PYTHONPATH: PYPATH } });
  dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost/', pretendToBeVisual: true });
  for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'Node', 'MouseEvent', 'KeyboardEvent', 'Event', 'getComputedStyle', 'MessageEvent', 'HTMLInputElement']) {
    try { Object.defineProperty(globalThis, k, { value: dom.window[k], configurable: true, writable: true }); } catch {}
  }
  globalThis.requestAnimationFrame = (f) => setTimeout(f, 0);
  const out = path.join(os.tmpdir(), 'rp-webview-test.cjs');
  await esbuild.build({ entryPoints: [path.join(__dirname, 'webview-entry.tsx')], bundle: true, platform: 'node', format: 'cjs', outfile: out, jsx: 'automatic', logLevel: 'silent',
    define: { 'process.env.NODE_ENV': '"development"' } });
  lib = require(out);
});
after(() => { try { fs.rmSync(proj, { recursive: true, force: true }); } catch {} });

const q = (root, sel) => root.querySelector(sel);
const qa = (root, sel) => Array.from(root.querySelectorAll(sel));
const byText = (root, sel, t) => qa(root, sel).find((e) => e.textContent.includes(t));
async function type(el, value) {
  await lib.act(async () => {
    const set = Object.getOwnPropertyDescriptor(el.tagName === 'TEXTAREA' ? dom.window.HTMLTextAreaElement.prototype : dom.window.HTMLInputElement.prototype, 'value').set;
    set.call(el, value); el.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  });
}
async function waitFor(fn, ms = 4000) { const t0 = Date.now(); for (;;) { try { const v = fn(); if (v) return v; } catch {} if (Date.now() - t0 > ms) return fn(); await lib.tick(50); } }
const ready = async (el) => { await waitFor(() => q(el, '[role=tab]') && !/Loading…|Starting…/.test(el.textContent), 8000); await lib.tick(100); };
const click = (el) => lib.act(async () => { el.click(); await lib.tick(30); });

test('settings page: every setting has an accessible ? and hover, focus, click and Escape behave', async () => {
  const core = startCore(proj); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'settings' });
  try {
    await ready(el);
    const btn = byText(el, 'button', '?') && qa(el, 'button.help-btn').find((b) => b.getAttribute('aria-label') === 'About Maximum runs without synthesis');
    assert.ok(btn, 'help button with an accessible name');
    assert.equal(btn.getAttribute('aria-expanded'), 'false');
    // hover
    await lib.act(async () => { btn.parentElement.dispatchEvent(new dom.window.MouseEvent('mouseover', { bubbles: true })); });
    let pop = q(el, '[role=note]');
    assert.ok(pop && /blocked/.test(pop.textContent), 'hover explains what happens when the limit is reached');
    assert.match(pop.textContent, /Default\s*5/); assert.match(pop.textContent, /agent_policy\.max_runs_without_synthesis/); assert.match(pop.textContent, /only a human/);
    await lib.act(async () => { btn.parentElement.dispatchEvent(new dom.window.MouseEvent('mouseout', { bubbles: true })); await lib.tick(250); });
    assert.equal(q(el, '[role=note]'), null, 'hover help closes');
    // click pins (also how touch works), Escape closes and restores focus
    await click(btn);
    assert.ok(q(el, '[role=note]')); await lib.act(async () => { btn.parentElement.dispatchEvent(new dom.window.MouseEvent('mouseout', { bubbles: true })); await lib.tick(250); });
    assert.ok(q(el, '[role=note]'), 'pinned help stays open');
    btn.focus();
    await lib.act(async () => { btn.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); document.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    assert.equal(q(el, '[role=note]'), null, 'Escape closes');
    // keyboard focus opens
    await lib.act(async () => { btn.blur(); btn.focus(); });
    assert.ok(q(el, '[role=note]'), 'focus opens help');
    await lib.act(async () => { document.body.dispatchEvent(new dom.window.MouseEvent('mousedown', { bubbles: true })); btn.blur(); });
    assert.equal(q(el, '[role=note]'), null, 'outside click closes');
    // every visible setting has one
    const rows = qa(el, '.setting'); assert.ok(rows.length >= 5);
    for (const r of rows) assert.ok(q(r, 'button.help-btn'), 'help for ' + q(r, '.setting-label').textContent);
  } finally { await unmount(); core.stop(); }
});

test('editing a setting: validation, diff review, save, audit, persists after restart, reset', async () => {
  let core = startCore(proj); let host = hostFor(core);
  let { el, unmount } = await lib.mount(host, { page: 'settings' });
  try {
    await ready(el);
    const input = () => q(qa(el, '.setting').find((r) => q(r, '.setting-label').textContent === 'Maximum runs without synthesis'), 'input[type=number]');
    await type(input(), '0'); await lib.tick(300);
    assert.match(q(el, '.field-error').textContent, /at least 1/);
    assert.ok(!byText(el, 'button', 'Review changes') || byText(el, 'button', 'Review changes').disabled, 'cannot review an invalid value');
    await type(input(), '8'); await lib.tick(300);
    assert.equal(q(el, '.field-error'), null);
    await click(byText(el, 'button', 'Review changes'));
    const dlg = q(el, '[role=dialog]'); assert.ok(dlg, 'review dialog');
    assert.match(dlg.textContent, /\+\s*max_runs_without_synthesis: 8/); assert.match(dlg.textContent, /policy\.md/);
    // focus is trapped inside and Escape cancels without writing
    assert.ok(dlg.contains(document.activeElement));
    await lib.act(async () => { dlg.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    assert.equal(q(el, '[role=dialog]'), null);
    assert.ok(!fs.readFileSync(path.join(proj, '.research/config.yaml'), 'utf8').split('\n').some((l) => !l.startsWith('#') && l.includes('max_runs')));
    await click(byText(el, 'button', 'Review changes'));
    await click(byText(q(el, '[role=dialog]'), 'button', 'Save changes'));
    await lib.tick(500);
    assert.match(q(el, '.banner.ok').textContent, /Saved/);
    assert.match(fs.readFileSync(path.join(proj, '.research/config.yaml'), 'utf8'), /max_runs_without_synthesis: 8/);
    assert.match(fs.readFileSync(path.join(proj, '.research/context/policy.md'), 'utf8'), /Maximum runs without synthesis.*8/);
    // restart everything: a new core process and a new UI see the saved value with its source
    await unmount(); core.stop();
    core = startCore(proj); host = hostFor(core); ({ el, unmount } = await lib.mount(host, { page: 'settings' }));
    await ready(el);
    const row = qa(el, '.setting').find((r) => q(r, '.setting-label').textContent === 'Maximum runs without synthesis');
    assert.equal(q(row, 'input').value, '8'); assert.ok(q(row, '.src-project'), 'shows it is a project override');
    // reset removes only the override
    await click(byText(row, 'button', 'Reset')); await click(byText(q(el, '[role=dialog]'), 'button', 'Remove override')); await lib.tick(500);
    const rowOf = () => qa(el, '.setting').find((r) => q(r, '.setting-label').textContent === 'Maximum runs without synthesis');
    await waitFor(() => q(rowOf(), 'input').value === '5');
    assert.equal(q(rowOf(), 'input').value, '5'); assert.ok(q(rowOf(), '.src-default'));
  } finally { await unmount(); core.stop(); }
});

test('search finds settings across categories; unknown search says so', async () => {
  const core = startCore(proj); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'settings' });
  try {
    await ready(el);
    await type(q(el, '#setting-search'), 'sandbox'); await lib.tick(50);
    const labels = qa(el, '.setting-label').map((e) => e.textContent);
    assert.ok(labels.includes('Apply the default sandbox profile'), labels.join('|'));
    await type(q(el, '#setting-search'), 'zzzzzz'); await lib.tick(50);
    assert.match(q(el, '.list').textContent, /No settings match/);
    assert.equal(q(el, '.cats button.on'), null, 'no category is highlighted while searching');
    assert.equal(q(el, '.field-error'), null, 'no setting reports an error against its own default');
  } finally { await unmount(); core.stop(); }
});

test('navigation: tabs are a roving-tabindex tablist; attention items open the right workflow', async () => {
  const core = startCore(proj);
  await core.call('create', { type: 'experiment', title: 'Step size', hypothesis: 'h' });
  const host = hostFor(core);
  const { el, unmount } = await lib.mount(host);
  try {
    await ready(el);
    const tabs = qa(el, '[role=tab]');
    assert.deepEqual(tabs.map((t) => t.textContent), ['Home', 'Work', 'Research', 'Plans', 'Guide', 'Agents', 'Settings']);
    assert.equal(tabs[0].getAttribute('aria-selected'), 'true'); assert.equal(tabs[1].tabIndex, -1);
    await lib.act(async () => { tabs[0].dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await lib.tick(300); });
    assert.equal(qa(el, '[role=tab]')[1].getAttribute('aria-selected'), 'true');
    assert.match(q(el, 'main').textContent, /Job isolation/); assert.match(q(el, 'main').textContent, /Optional: try ideas on separate git branches/);
    await click(qa(el, '[role=tab]')[2]); await lib.tick(300);
    await click(byText(el, 'button.link', 'EXP-001')); assert.deepEqual(host.log.entities, ['EXP-001']);
    assert.match(q(el, 'main').textContent, /Step size/);
  } finally { await unmount(); core.stop(); }
});

test('empty and error states are explained, not blank', async () => {
  const bad = { call: async () => { throw new Error('core unavailable'); }, openEntity() {}, openDoc() {}, getState: () => ({}), setState() {} };
  const { el, unmount } = await lib.mount(bad);
  try { await lib.tick(100); assert.match(q(el, '[role=alert]').textContent, /core unavailable/); assert.ok(byText(el, 'button', 'Try again')); }
  finally { await unmount(); }
});

test('promotion dialog: gates are explained, tests run, self-review is refused, and merging stays locked', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-promo-'));
  const repo = path.join(dir, 'proj');
  const script = `
import os, subprocess, sys
from research.store import Project
from research import workspace as W, services, promotion as PR
repo = ${JSON.stringify(repo)}
os.makedirs(repo)
g = lambda *a, cwd=repo: subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
g("init", "-q", "-b", "main"); open(repo + "/a.py", "w").write("1\\n"); g("add", "."); g("commit", "-qm", "init")
Project.init(repo, name="promo").close()
W.enable(repo, ${JSON.stringify(path.join(dir, 'ledger'))})
from research.util import detect_author
p = Project(repo); p.author = detect_author()
p.save_config({"promotion.test_command": "test -f a.py", "promotion.require_supported_finding": False}); p.config = p.load_config()
e = services.create_experiment(p, "E", hypothesis="h")
ws = W.create(p, e["id"], name="x")
open(ws["path"] + "/solver.py", "w").write("def f(): return 1\\n"); g("add", "solver.py", cwd=ws["path"]); g("commit", "-qm", "add solver", cwd=ws["path"])
sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws["path"], capture_output=True, text=True).stdout.strip()
PR.prepare(p, ws["id"], commits=[sha], title="Solver")
`;
  const human = { ...process.env, PYTHONPATH: PYPATH };
  for (const k of ['CLAUDECODE', 'CLAUDE_CODE_ENTRYPOINT', 'RESEARCH_AGENT', 'CODEX_SANDBOX']) delete human[k];   // the fixture is prepared by a person, like the UI user
  execFileSync(PY, ['-c', script], { env: human });
  const core = startCore(repo); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'work' });
  try {
    await waitFor(() => /Promotion candidates/.test(el.textContent));
    assert.match(q(el, 'main').textContent, /PROMO-001/); assert.match(q(el, 'main').textContent, /Nothing merges without tests/);
    await click(byText(el, 'button', 'Open…'));
    let dlg = q(el, '[role=dialog]'); assert.ok(dlg);
    await waitFor(() => /solver\.py/.test(q(el, '[role=dialog]').textContent));
    assert.match(dlg.textContent, /Patch main would receive/); assert.match(dlg.textContent, /unverified/);
    assert.ok(byText(dlg, 'button', 'Merge into main').disabled, 'merge locked before any gate passes');
    await click(byText(dlg, 'button', 'Run tests'));
    await waitFor(() => /passed/.test(q(el, '[role=dialog]').textContent));
    await click(byText(q(el, '[role=dialog]'), 'button', 'Approve code review'));
    await waitFor(() => q(el, '[role=dialog] [role=alert]'));
    assert.match(q(el, '[role=dialog] [role=alert]').textContent, /cannot review their own/i);
    assert.ok(byText(q(el, '[role=dialog]'), 'button', 'Approve promotion').disabled && byText(q(el, '[role=dialog]'), 'button', 'Merge into main').disabled);
    assert.equal(execFileSync('git', ['rev-parse', 'main'], { cwd: repo }).toString().trim(), execFileSync('git', ['rev-parse', 'main'], { cwd: repo }).toString().trim());
    assert.ok(!fs.existsSync(path.join(repo, 'solver.py')), 'main still has no solver.py');
  } finally { await unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});


test('first run: two answers create a v2 project; advanced options stay folded away; unavailable choices explain themselves', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-fresh-'));
  const core = startCore(dir); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host);
  try {
    await waitFor(() => /Initialize Research Project/.test(el.textContent), 8000);
    await click(byText(el, 'button', 'Use a single folder instead'));
    await waitFor(() => /Set up Research OS/.test(el.textContent), 8000);
    assert.equal(q(el, '#su-name').value, path.basename(dir), 'the folder name is the default project name');
    assert.equal(q(el, '[role=tab]'), null, 'no navigation until a project exists');
    await type(q(el, '#su-goal'), 'Learn the effect of alpha');
    await click(byText(el, 'button', 'Next'));
    assert.match(q(el, 'main, .wizard').textContent, /Try ideas on separate git branches/);
    const box = byText(el, 'label', 'Use a shared research ledger').querySelector('input');
    assert.ok(box.disabled, 'the ledger option is disabled without a git repository, and says why');
    assert.match(el.textContent, /Needs an existing git repository/);
    await click(byText(el, 'button', 'Skip to review'));
    await waitFor(() => /Research OS will:/.test(el.textContent));
    assert.match(el.textContent, /project format 2/);
    assert.ok(!fs.existsSync(path.join(dir, '.research')), 'nothing is written before Create');
    await click(byText(el, 'button', 'Create project'));
    await waitFor(() => q(el, '[role=tab]'), 12000);
    assert.ok(fs.existsSync(path.join(dir, '.research', 'format.json')) && fs.existsSync(path.join(dir, 'AGENTS.md')));
    assert.match(fs.readFileSync(path.join(dir, '.research', 'config.yaml'), 'utf8'), /Learn the effect of alpha/);
    assert.ok(!fs.existsSync(path.join(dir, '.git')), 'no git repository was created behind your back');
  } finally { await unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('Initialize Project: teaches first, previews the layout, creates main/ledger/experiments and shows the Agents page', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-init-'));
  const core = startCore(dir); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host);
  try {
    await waitFor(() => /Initialize Research Project/.test(el.textContent), 8000);
    assert.match(el.textContent, /How Research OS works/); assert.match(el.textContent, /Switch agents any time/);
    assert.match(el.textContent, /belong to one git repository/);
    await click(byText(el, 'button', 'Next'));
    await type(q(el, '#ip-goal'), 'Find alpha');
    await click(byText(el, 'button', 'Next'));
    assert.match(el.textContent, /top level/); await click(byText(el, 'button', 'Next'));
    assert.match(el.textContent, /Claude Code/); assert.match(el.textContent, /OpenAI Codex/); assert.match(el.textContent, /Google Antigravity/);
    assert.match(el.textContent, /sonnet_first/);
    await click(byText(el, 'button', 'Next')); assert.match(el.textContent, /copy your ledger/);
    await click(byText(el, 'button', 'Next'));
    // the Guide step teaches what the guide is, how citations work, and when an agent writes
    assert.match(el.textContent, /Scientist Guide: where you explain the science/); assert.match(el.textContent, /references\.bib/);
    assert.match(el.textContent, /Quarto/); assert.match(el.textContent, /never writes into the guide until you press/);
    assert.equal(q(el, 'input[type=checkbox]').checked, true, 'the guide is created by default');
    await lib.act(async () => { const s = q(el, '#g-tr'); Object.getOwnPropertyDescriptor(dom.window.HTMLSelectElement.prototype, 'value').set.call(s, 'checkpoint'); s.dispatchEvent(new dom.window.Event('change', { bubbles: true })); });
    await type(q(el, '#g-w'), '120');
    await click(byText(el, 'button', 'Next'));
    await waitFor(() => /do exactly this/.test(el.textContent));
    assert.match(el.textContent, /Scientist Guide \(a Quarto book/); assert.match(el.textContent, /at most 120 words per section/); assert.match(el.textContent, /publishes it as a website on every push/);
    assert.ok(!fs.existsSync(path.join(dir, 'main')), 'nothing is written before Create');
    await click(byText(el, 'button', 'Create project'));
    await waitFor(() => /Project created/.test(el.textContent), 30000);
    assert.match(el.textContent, /Your first 5 minutes/); assert.match(el.textContent, /Scientist Guide created/);
    for (const d of ['main/.git', 'ledger/.research/config.yaml', 'experiments', 'main/docs/scientist-guide/_quarto.yml', 'main/docs/scientist-guide/references.bib', 'main/.github/workflows/publish-guide.yml']) assert.ok(fs.existsSync(path.join(dir, d)), d);
    const cfg = fs.readFileSync(path.join(dir, 'ledger/.research/config.yaml'), 'utf8');
    assert.match(cfg, /max_words: 120/); assert.match(cfg, /trigger: checkpoint/);
    assert.deepEqual(fs.readdirSync(path.join(dir, 'experiments')), []);
  } finally { await unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('Agents & Providers page lists the three providers with readiness and capabilities', async () => {
  const core = startCore(proj);
  const { el, unmount } = await lib.mount(hostFor(core));
  try {
    await waitFor(() => q(el, '[role=tab]'), 12000);
    await click(byText(el, '[role=tab]', 'Agents'));
    await waitFor(() => /Agents & Providers/.test(el.textContent) && /Google Antigravity/.test(el.textContent), 15000);
    assert.match(el.textContent, /OpenAI Codex/); assert.match(el.textContent, /Claude Code/);
    assert.match(el.textContent, /Completed = the agent process exited successfully/);
  } finally { await unmount(); core.stop(); }
});

test('a v1 project is shown a clear explanation and nothing is changed', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-v1-'));
  fs.mkdirSync(path.join(dir, '.research'));
  fs.writeFileSync(path.join(dir, '.research', 'config.yaml'), 'project:\n  name: old\n');
  const core = startCore(dir); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host);
  try {
    await waitFor(() => /not a Research OS 2 project/.test(el.textContent), 8000);
    assert.match(q(el, '[role=alert]').textContent, /v1 project/); assert.match(el.textContent, /\.research-v1-archive/);
    assert.deepEqual(fs.readdirSync(path.join(dir, '.research')), ['config.yaml']);
  } finally { await unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('new experiment: title is enough, options are folded, nothing runs, and the result says what exists', async () => {
  const core = startCore(proj); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host);
  try {
    await ready(el);
    await click(byText(el, 'button', 'New experiment'));
    const dlg = () => q(el, '[role=dialog]');
    assert.ok(dlg()); assert.ok(byText(dlg(), 'button', 'Review').disabled, 'cannot continue without a title');
    assert.ok(!q(dlg(), 'details[open]'), 'extra options are folded away');
    await type(q(dlg(), '#ne-title'), 'Learning rate sweep');
    await click(byText(dlg(), 'button', 'Review'));
    await waitFor(() => /This will:/.test(dlg().textContent));
    assert.match(dlg().textContent, /It will not run anything/);
    await click(byText(dlg(), 'button', 'Create'));
    await waitFor(() => /created/.test(dlg().textContent), 8000);
    assert.match(dlg().textContent, /EXP-\d+/); assert.match(dlg().textContent, /Nothing has been run yet/);
  } finally { await unmount(); core.stop(); }
});

test('project health lives in Settings; advanced settings are hidden until asked for', async () => {
  const core = startCore(proj); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'settings', target: 'agents' });
  try {
    await ready(el);
    const labels = () => qa(el, '.setting-label').map((e) => e.textContent);
    assert.ok(!labels().includes('Fast-model attempts'), 'tuning knobs are not shown by default');
    await click(byText(el, 'label', 'Show advanced settings').querySelector('input'));
    assert.ok(labels().includes('Fast-model attempts'));
    await click(byText(el, 'button', 'Project health'));
    await waitFor(() => /Healthy|Needs attention|problem/.test(el.textContent), 12000);
    assert.match(el.textContent, /optional/); assert.match(el.textContent, /passing/);
  } finally { await unmount(); core.stop(); }
});

test('files are authoritative: origin file and key are shown, manual edits appear, conflicts and bad YAML never overwrite', async () => {
  const cfg = path.join(proj, '.research/config.yaml');
  const original = fs.readFileSync(cfg, 'utf8');
  const label = 'Maximum failed runs without review';
  const rowOf = (el) => qa(el, '.setting').find((r) => q(r, '.setting-label').textContent === label);
  let core = startCore(proj); let host = hostFor(core);
  let m = await lib.mount(host, { page: 'settings' });
  try {
    await ready(m.el);
    // transparency: exact key + "not written" for a default; a manual edit while closed shows up with its file
    assert.match(rowOf(m.el).textContent, /agent_policy\.max_failed_runs_without_review/); assert.match(rowOf(m.el).textContent, /not written to any file/);
    await click(byText(m.el, 'button', 'Open configuration file')); assert.deepEqual(host.log.files, ['.research/config.yaml']);
    await m.unmount(); core.stop();
    fs.writeFileSync(cfg, original + '\n# kept comment\nagent_policy:\n  max_failed_runs_without_review: 6\n');
    core = startCore(proj); host = hostFor(core); m = await lib.mount(host, { page: 'settings' });
    await ready(m.el);
    assert.equal(q(rowOf(m.el), 'input').value, '6'); assert.ok(q(rowOf(m.el), '.src-project')); assert.match(rowOf(m.el).textContent, /config\.yaml/);
    // concurrent edit: change in the UI, edit the file elsewhere, save -> refused, file untouched
    await type(q(rowOf(m.el), 'input'), '9'); await lib.tick(300);
    const other = fs.readFileSync(cfg, 'utf8') + '\n# edited in another editor\n';
    fs.writeFileSync(cfg, other);
    await click(byText(m.el, 'button', 'Review changes'));
    await waitFor(() => /changed on disk/.test(m.el.textContent));
    assert.match(q(m.el, '.banner.bad').textContent, /changed on disk/); assert.ok(byText(m.el, 'button', 'Reload from disk'));
    assert.equal(fs.readFileSync(cfg, 'utf8'), other, 'the other editor\'s file is never overwritten');
    // invalid YAML: a clear screen with a way to open the file, nothing rewritten
    await m.unmount(); core.stop();
    const broken = 'project: [unclosed\n  name: x: y\n';
    fs.writeFileSync(cfg, broken);
    core = startCore(proj); host = hostFor(core); m = await lib.mount(host, { page: 'settings' });
    await waitFor(() => /could not be read/.test(m.el.textContent), 8000);
    assert.match(m.el.textContent, /could not be read/); assert.ok(byText(m.el, 'button', 'Open configuration file'));
    assert.equal(fs.readFileSync(cfg, 'utf8'), broken);
  } finally { await m.unmount(); core.stop(); fs.writeFileSync(cfg, original); }
});


test('plans page: import YAML through the core, run state and phases are shown, settings expose the supervisor', async () => {
  const core = startCore(proj); const host = hostFor(core);
  const yaml = `schema_version: 1
plans:
  - key: ui-plan
    title: UI plan
    objective: show phases
    experiments:
      - key: e1
        title: Experiment one
        tasks:
          - {key: first, title: First task, prompt: do it, checks: ["cmd:true"]}
          - {key: second, title: Second task, prompt: then this, depends_on: [first], execution: {requires_approval: true}}
`;
  try {
    const bad = await core.call('plan_yaml_validate', { text: 'schema_version: 7\nplans: []\n' }).then(() => null, (e) => e.message);
    assert.match(bad, /not supported|non-empty/);
    const d = await core.call('plan_yaml_diff', { text: yaml });
    assert.equal(d.operations > 0, true);
    await core.call('plan_yaml_apply', { text: yaml });
    const again = await core.call('plan_yaml_apply', { text: yaml });
    assert.equal(again.plans[0].changed, false, 'applying twice changes nothing');
    const { el, unmount } = await lib.mount(host, { page: 'plans' });
    try {
      await waitFor(() => /UI plan/.test(el.textContent), 8000);
      assert.match(el.textContent, /not run|Run plan/);
      await click(byText(el, 'button', 'Show tasks'));
      await waitFor(() => /First task/.test(el.textContent), 8000);
      assert.match(el.textContent, /waiting|ready/);
      assert.ok(byText(el, 'button', 'Details'));
    } finally { await unmount(); }
    const { el: s2, unmount: u2 } = await lib.mount(host, { page: 'settings', target: 'supervisor' });
    try {
      await waitFor(() => /Agents at once|Attempts per task/.test(s2.textContent), 8000);
      assert.match(s2.textContent, /supervisor\.max_attempts_per_task/);
    } finally { await u2(); }
  } finally { core.stop(); }
});

async function clickText(el, text, sel = 'button') { const b = await waitFor(() => byText(el, sel, text), 8000); assert.ok(b, `button ${text}`); await click(b); }
test('guide page: create, important results, star, limits, references and publishing, against the real core', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-guide-'));
  execFileSync('git', ['init', '-q'], { cwd: dir });
  execFileSync(PY, ['-c', `
from research.store import Project
from research import services as S, runs as R
p = Project.init(${JSON.stringify(dir)}, name="guide-ui", goal="Does alpha matter?")
e = S.create_experiment(p, "alpha sweep")["id"]
r = R.create_run(p, e, command="x", status="completed")["id"]
S.create_finding(p, "Alpha 0.5 minimises error", statement="Error is lowest at alpha 0.5", supports=[r])
p.save_config({"guide.highlights": "all"})
p.close()`], { env: { ...process.env, PYTHONPATH: PYPATH } });
  const core = startCore(dir); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'guide' });
  try {
    await ready(el);
    // no guide yet: an explanation and one clear action, nothing is written until it is pressed
    await waitFor(() => /no Scientist Guide yet/.test(el.textContent), 8000);
    assert.match(el.textContent, /Quarto book/); assert.ok(!fs.existsSync(path.join(dir, 'docs/scientist-guide')));
    await clickText(el, 'Create the guide');
    await waitFor(() => q(el, '#results'), 8000);
    for (const f of ['_quarto.yml', 'theme.scss', 'references.bib', 'ideas.qmd', 'results.qmd']) assert.ok(fs.existsSync(path.join(dir, 'docs/scientist-guide', f)), f);
    assert.ok(fs.existsSync(path.join(dir, '.github/workflows/publish-guide.yml')));
    // important results: the finding is listed, needs a draft, and can be logged, drafted and starred
    await waitFor(() => /Alpha 0\.5 minimises error/.test(el.textContent), 8000);
    assert.match(el.textContent, /Chosen by.*every live finding/);
    assert.ok(byText(el, 'button', 'Log now'));
    await clickText(el, 'Log now');
    await waitFor(() => /Logged F-001/.test(el.textContent), 8000);
    assert.match(fs.readFileSync(path.join(dir, 'docs/scientist-guide/results.qmd'), 'utf8'), /research:result F-001/);
    await waitFor(() => /entry, needs a draft/.test(el.textContent), 8000);
    await clickText(el, 'Draft…');
    assert.deepEqual(host.log.runs.pop(), ['research.guide.draft', 'F-001']);
    await clickText(el, 'Open entry');
    assert.deepEqual(host.log.guide.pop(), [path.join(dir, 'docs/scientist-guide'), 'results.qmd']);
    await clickText(el, 'Star');
    await waitFor(() => /marked important/.test(el.textContent), 8000);
    await waitFor(() => byText(el, 'button', 'Unstar'), 8000); assert.ok(q(el, '[aria-label=starred]'));
    // chapters: ownership and evidence state, human-owned chapters are named
    assert.match(q(el, '#chapters').closest('section').textContent, /ideas\.qmd[\s\S]*human/);
    assert.match(q(el, '#chapters').closest('section').textContent, /results\.qmd[\s\S]*agent may draft/);
    // limits are stated plainly and link to Settings
    const lim = q(el, '#limits').closest('section').textContent;
    assert.match(lim, /150 words/); assert.match(lim, /never edits human-owned chapters/); assert.match(lim, /remind me when an important result has no draft/);
    // references: an invalid entry is explained, a good one is added and the count updates
    await clickText(el, 'Add a reference');
    await waitFor(() => q(el, '[role=dialog]'));
    const add = () => byText(q(el, '[role=dialog]'), 'button', 'Add');
    assert.equal(add().disabled, true);
    await type(q(el, '#rf-bib'), 'just words');
    await lib.act(async () => { add().click(); await lib.tick(30); });
    await waitFor(() => /does not look like BibTeX/.test(q(el, '[role=dialog]').textContent), 8000);
    await type(q(el, '#rf-bib'), '@article{lee2021alpha, author = {Lee, Wei}, title = {Alpha in CT}, year = {2021}}');
    await lib.act(async () => { add().click(); await lib.tick(30); });
    await waitFor(() => /cite it as \[@lee2021alpha\]/.test(el.textContent), 8000);
    await waitFor(() => /References1/.test(q(el, '#refs').closest('section').textContent), 8000);
    // a citation that is not in references.bib is called out
    fs.appendFileSync(path.join(dir, 'docs/scientist-guide/ideas.qmd'), '\nSee [@ghost2000].\n');
    await click(q(el, 'button[aria-label=Refresh]'));
    await waitFor(() => /Cited but not in references\.bib/.test(el.textContent), 8000);
    assert.match(q(el, '#refs').closest('section').textContent, /@ghost2000/);
    // publishing: the workflow is shown with the one-time GitHub step
    const pub = q(el, '#publish').closest('section').textContent;
    assert.match(pub, /workflow present/); assert.match(pub, /Source: GitHub Actions/); assert.match(pub, /never shows Research OS identifiers/);
    assert.ok(byText(el, 'button', 'Edit the theme'));
    await clickText(el, 'Edit the theme');
    assert.deepEqual(host.log.guide.pop(), [path.join(dir, 'docs/scientist-guide'), 'theme.scss']);
    // Quarto is only needed to build: without it the button explains why
    const st = await core.call('guide_status');
    const build = byText(el, 'button', 'Build site');
    if (!st.quarto.ok) { assert.equal(build.disabled, true); assert.match(el.textContent, /Quarto is not installed/); } else assert.equal(build.disabled, false);
  } finally { await unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});

function freshProject(name) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-' + name + '-'));
  execFileSync('git', ['init', '-q'], { cwd: dir });
  execFileSync(PY, ['-c', `from research.store import Project; Project.init(${JSON.stringify(dir)}, name=${JSON.stringify(name)}, goal="g").close()`], { env: { ...process.env, PYTHONPATH: PYPATH } });
  return dir;
}

test('agents page: skills, hooks and MCP are explained, loaded, and fixable in one click', async () => {
  const dir = freshProject('tools'); const core = startCore(dir); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'agents' });
  try {
    await waitFor(() => q(el, '#tools'), 15000);
    const sec = () => q(el, '#tools').closest('section');
    assert.match(sec().textContent, /Skills tell it how to work, hooks are programs that check its work/);
    await waitFor(() => /run-experiment/.test(sec().textContent), 15000);
    assert.match(sec().textContent, /scientific-briefing/);
    // a classic project that never linked its skills is told, and fixed with one button
    assert.match(sec().textContent, /claude: 0 of \d+ linked/);
    await clickText(el, 'Link skills now');
    await waitFor(() => /claude: (\d+) of \1 linked/.test(sec().textContent), 15000);
    assert.ok(fs.existsSync(path.join(dir, '.claude/skills/run-experiment/SKILL.md')));
    // hooks: none yet; starters are added and approved by the human pressing the button, then can be disabled and run
    assert.match(sec().textContent, /No hooks yet/);
    await clickText(el, 'Add starter hooks');
    await waitFor(() => !/No hooks yet/.test(sec().textContent) && q(sec(), 'table + p, tbody td .badge.k-ok'), 15000);
    assert.match(sec().textContent, /never blocks/); assert.match(sec().textContent, /git diff --check/);
    await clickText(el, 'Run now');
    await waitFor(() => /syntax-check: true|whitespace-check: true/.test(sec().textContent), 20000);
    await clickText(el, 'Disable');
    await waitFor(() => /disabled/.test(sec().textContent), 15000);
    assert.match(sec().textContent, /MCP server/); assert.match(sec().textContent, /research_current/);
  } finally { await unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});

test('settings shows the Research OS version, and the Research page writes and opens reports', async () => {
  const dir = freshProject('reports'); const core = startCore(dir); const host = hostFor(core);
  const ver = (await core.call('ping')).version;
  assert.match(ver, /^\d+\.\d+\.\d+/);
  const { el, unmount } = await lib.mount(host, { page: 'settings' });
  try {
    await waitFor(() => q(el, '[data-testid=about]') && q(el, '[data-testid=about]').textContent.includes(ver), 15000);
    assert.match(q(el, '[data-testid=about]').textContent, new RegExp(`Research OS\\s*${ver.replace(/\./g, '\\.')}`));
  } finally { await unmount(); }
  const r = await lib.mount(host, { page: 'research' });
  try {
    await waitFor(() => q(r.el, '#reports'), 15000);
    await waitFor(() => /No report written yet/.test(q(r.el, '#reports').closest('section').textContent), 15000);
    assert.match(q(r.el, '#reports').closest('section').textContent, /It is not the story you tell/);
    await clickText(r.el, 'Write project report');
    await waitFor(() => /Report written: .*project\.html/.test(r.el.textContent), 20000);
    await waitFor(() => host.log.reports && host.log.reports.length === 1, 5000);
    assert.match(host.log.reports[0], /\.research\/reports\/project\.html$/); assert.ok(fs.existsSync(host.log.reports[0]));
    await waitFor(() => byText(r.el, 'button', 'Open in browser'), 15000);
    await clickText(r.el, 'Full export');
    await waitFor(() => /every record accounted for/.test(r.el.textContent), 30000);
    assert.match(host.log.reports.at(-1), /project-full\.md$/);
  } finally { await r.unmount(); core.stop(); fs.rmSync(dir, { recursive: true, force: true }); }
});
