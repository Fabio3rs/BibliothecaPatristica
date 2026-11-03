import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readdir, writeFile, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { DiskCache, digest } from '../dist/cache.js';

async function cache(t, options = {}) {
  const directory = await mkdtemp(path.join(tmpdir(), 'bibliotheca-cache-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return new DiskCache({ directory, namespace: 'dataset:build1', ...options });
}
test('cache deduplicates concurrent downloads, writes atomically and repairs corruption', async t => {
  let requests = 0;
  const c = await cache(t, { fetchImpl: async () => { requests++; return new Response('hello'); } });
  const result = await Promise.all(Array.from({ length: 12 }, () => c.get('https://test/x', { sha256: digest('hello') })));
  assert.equal(requests, 1);
  assert.equal(result[0].toString(), 'hello');
  const files = await readdir(c.directory);
  assert.equal(files.length, 1); assert.ok(files[0].endsWith('.cache'));
  await writeFile(path.join(c.directory, files[0]), 'corrupt');
  assert.equal((await c.get('https://test/x', { sha256: digest('hello') })).toString(), 'hello');
  assert.equal(requests, 2);
});
test('cache bounds disk use and separates dataset, build and integrity identities', async t => {
  let requests = 0;
  const c = await cache(t, { maxBytes: 140, fetchImpl: async () => { requests++; return new Response('payload'); } });
  await c.get('https://test/a'); await c.get('https://test/b');
  let size = 0; for (const file of await readdir(c.directory)) size += (await stat(path.join(c.directory, file))).size;
  assert.ok(size <= 140);
  c.namespace = 'dataset:build2'; await c.get('https://test/b');
  assert.equal(requests, 3);
});
test('warm cache avoids scans on repeated reads, rechecks periodically and enforces a reduced limit', async t => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_700_000_000_000 });
  let requests = 0;
  const c = await cache(t, { fetchImpl: async () => { requests++; return new Response('payload'); } });
  await c.get('https://test/a'); await c.get('https://test/b');
  const prune = t.mock.method(c, 'prune');
  for (let i = 0; i < 20; i++) await c.get('https://test/a');
  assert.equal(prune.mock.callCount(), 0);
  assert.equal(requests, 2);
  t.mock.timers.tick(60_000);
  await c.get('https://test/a');
  assert.equal(prune.mock.callCount(), 1);
  const smaller = new DiskCache({ directory: c.directory, namespace: c.namespace, maxBytes: 71,
    fetchImpl: async () => { throw new Error('Cache hit must not use network'); } });
  assert.equal((await smaller.get('https://test/a')).toString(), 'payload');
  let bytes = 0;
  for (const file of await readdir(c.directory)) bytes += (await stat(path.join(c.directory, file))).size;
  assert.ok(bytes <= 71);
});
test('network errors and bad gzip are never persisted and can be retried', async t => {
  let response = () => new Response('offline', { status: 503 });
  const c = await cache(t, { fetchImpl: async () => response() });
  await assert.rejects(c.get('https://test/a'), /HTTP 503/);
  response = () => new Response(new Uint8Array([0x1f, 0x8b, 1, 2]));
  await assert.rejects(c.get('https://test/a'));
  assert.equal((await readdir(c.directory)).length, 0);
  response = () => new Response('ok');
  assert.equal((await c.get('https://test/a')).toString(), 'ok');
});
test('one cancelled subscriber does not cancel shared download, last subscriber does', async t => {
  let aborted = false;
  let finish;
  const c = await cache(t, { fetchImpl: (_url, { signal }) => new Promise((resolve, reject) => {
    finish = () => resolve(new Response('ok'));
    signal.addEventListener('abort', () => { aborted = true; reject(signal.reason); }, { once: true });
  }) });
  const controller = new AbortController();
  const p = c.get('https://test/a', { signal: controller.signal });
  const rejected = assert.rejects(p, { name: 'AbortError' });
  const other = c.get('https://test/a');
  while (!finish) await new Promise(resolve => setTimeout(resolve, 1));
  controller.abort(); await rejected;
  assert.equal(aborted, false); finish();
  assert.equal((await other).toString(), 'ok');
  finish = undefined; aborted = false;
  const last = new AbortController();
  const q = c.get('https://test/b', { signal: last.signal });
  const lastRejected = assert.rejects(q, { name: 'AbortError' });
  while (!finish) await new Promise(resolve => setTimeout(resolve, 1));
  last.abort(); await lastRejected;
  assert.equal(aborted, true);
});
test('download timeout interrupts a stalled request', async t => {
  const c = await cache(t, { timeoutMs: 25, fetchImpl: (_url, { signal }) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => resolve(new Response('too late')), 1000);
    signal.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
  }) });
  await assert.rejects(c.get('https://test/a'), { name: 'TimeoutError' });
});
