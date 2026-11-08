const { test } = require('node:test');
const assert = require('node:assert/strict');
const { transformSync } = require('esbuild');
const fs = require('node:fs');
const path = require('node:path');
const code = transformSync(fs.readFileSync(path.join(__dirname, '../src/util.ts'), 'utf8'), { loader: 'ts', format: 'cjs' }).code;
const mod = { exports: {} };
new Function('require', 'module', 'exports', code)(require, mod, mod.exports);
const { coreCompat, findProjectRoot, canSelfUpdate } = mod.exports;

test('coreCompat', () => {
  assert.equal(coreCompat('3.0.0', '3.0'), 'ok');
  assert.equal(coreCompat('3.2.1', '3.1'), 'ok');
  assert.equal(coreCompat('3.0.9', '3.1'), 'older');
  assert.equal(coreCompat('2.9.0', '3.0'), 'older');
  assert.equal(coreCompat('4.0.0', '3.0'), 'newer');
  assert.equal(coreCompat('x', '3.0'), 'invalid');
});

test('findProjectRoot walks up', () => {
  const has = new Set(['/a/b/.research/config.yaml']);
  const ex = (p) => has.has(p);
  assert.equal(findProjectRoot('/a/b/c/d', ex), '/a/b');
  assert.equal(findProjectRoot('/a/b', ex), '/a/b');
  assert.equal(findProjectRoot('/x/y', ex), undefined);
});

test('canSelfUpdate: only CLIs that already have `self-update` are told to run it', () => {
  assert.equal(canSelfUpdate('3.0.0'), true);
  assert.equal(canSelfUpdate('3.1.4'), true);
  assert.equal(canSelfUpdate('2.6.0'), false);
  assert.equal(canSelfUpdate(undefined), false);
});
