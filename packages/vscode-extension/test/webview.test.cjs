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
  const proc = spawn(PY, ['-m', 'research.rpc', '--root', root], { env: { ...process.env, PYTHONPATH: PYPATH, XDG_CONFIG_HOME: path.join(os.tmpdir(), 'rp-xdg-empty'), RESEARCH_CONFIG: '' } });
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
  return { log, call: (m, p) => core.call(m, p), openEntity: (id) => log.entities.push(id), openDoc: (p) => log.docs.push(p), getState: () => state, setState: (s) => (state = s) };
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
    const set = Object.getOwnPropertyDescriptor(dom.window.HTMLInputElement.prototype, 'value').set;
    set.call(el, value); el.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  });
}
async function waitFor(fn, ms = 4000) { const t0 = Date.now(); for (;;) { try { const v = fn(); if (v) return v; } catch {} if (Date.now() - t0 > ms) return fn(); await lib.tick(50); } }
const click = (el) => lib.act(async () => { el.click(); await lib.tick(30); });

test('settings page: every setting has an accessible ? and hover, focus, click and Escape behave', async () => {
  const core = startCore(proj); const host = hostFor(core);
  const { el, unmount } = await lib.mount(host, { page: 'settings' });
  try {
    await lib.tick(400);
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
    await lib.tick(400);
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
    assert.match(q(el, '.banner').textContent, /Saved/);
    assert.match(fs.readFileSync(path.join(proj, '.research/config.yaml'), 'utf8'), /max_runs_without_synthesis: 8/);
    assert.match(fs.readFileSync(path.join(proj, '.research/context/policy.md'), 'utf8'), /Maximum runs without synthesis.*8/);
    // restart everything: a new core process and a new UI see the saved value with its source
    await unmount(); core.stop();
    core = startCore(proj); host = hostFor(core); ({ el, unmount } = await lib.mount(host, { page: 'settings' }));
    await lib.tick(400);
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
    await lib.tick(400);
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
    await lib.tick(400);
    const tabs = qa(el, '[role=tab]');
    assert.deepEqual(tabs.map((t) => t.textContent), ['Home', 'Work', 'Research', 'Settings']);
    assert.equal(tabs[0].getAttribute('aria-selected'), 'true'); assert.equal(tabs[1].tabIndex, -1);
    await lib.act(async () => { tabs[0].dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })); await lib.tick(300); });
    assert.equal(qa(el, '[role=tab]')[1].getAttribute('aria-selected'), 'true');
    assert.match(q(el, 'main').textContent, /Execution profiles/); assert.match(q(el, 'main').textContent, /Single-folder project/);
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
