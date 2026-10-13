const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const { transformSync } = require('esbuild');
const fs = require('node:fs');
const path = require('node:path');

function fixture() {
  const children = [];
  const spawn = () => {
    const proc = new EventEmitter();
    proc.stdout = new PassThrough();
    proc.stderr = new PassThrough();
    proc.stdin = new PassThrough();
    proc.kill = () => {};
    children.push(proc);
    return proc;
  };
  const source = fs.readFileSync(path.join(__dirname, '../src/client.ts'), 'utf8');
  const code = transformSync(source, { loader: 'ts', format: 'cjs' }).code;
  const mod = { exports: {} };
  new Function('require', 'module', 'exports', code)(
    name => name === 'child_process' ? { spawn } : name === 'vscode' ? {} : require(name), mod, mod.exports);
  const client = new mod.exports.CoreClient('/tmp', 'python', { appendLine() {}, append() {} });
  return { client, children };
}
const tick = () => new Promise(resolve => setImmediate(resolve));
const ready = proc => proc.stdout.write('{"event":"ready","version":"test"}\n');

test('old process exit cannot reject new requests after restart', async () => {
  const { client, children } = fixture();
  try {
    const first = client.request('ping');
    ready(children[0]);
    await tick();
    const rejected = assert.rejects(first, /restarted/);
    client.restart();
    await rejected;
    const second = client.request('ping');
    ready(children[1]);
    await tick();
    children[0].emit('exit', 0, null);
    children[1].stdout.write('{"id":2,"result":"alive"}\n');
    assert.equal(await second, 'alive');
  } finally { client.dispose(); }
});

test('UTF-8 split across buffers and invalid response shapes', async () => {
  const { client, children } = fixture();
  try {
    const request = client.request('ping');
    ready(children[0]);
    await tick();
    children[0].stdout.write('null\n[]\n');
    const response = Buffer.from('{"id":1,"result":"α"}\n');
    const split = response.indexOf(Buffer.from('α')) + 1;
    children[0].stdout.write(response.subarray(0, split));
    children[0].stdout.write(response.subarray(split));
    assert.equal(await request, 'α');
  } finally { client.dispose(); }
});

test('stdin failure rejects pending requests; disposal prevents restart', async () => {
  const { client, children } = fixture();
  const request = client.request('ping');
  ready(children[0]);
  await tick();
  const rejected = assert.rejects(request, /input failed/);
  children[0].stdin.emit('error', new Error('EPIPE'));
  await rejected;
  client.dispose();
  await assert.rejects(client.request('ping'), /disposed/);
  assert.equal(children.length, 1);
});
