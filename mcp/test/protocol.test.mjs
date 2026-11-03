import test from 'node:test';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { InMemoryTransport } from '@modelcontextprotocol/server';
import { createServer } from '../dist/server.js';
import { definitions, BibliothecaRuntime } from '../dist/runtime.js';
import { mkdtemp, rm } from 'node:fs/promises';
import path from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { calls, fixtureFetch, fixtureBase, startFixtureServer } from './helpers.mjs';

test('stdio handshake and listing require no network, stdout is only JSON-RPC', async t => {
  const transport = new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))], stderr: 'pipe', env: { ...process.env, BIBLIOTHECA_BASE_URL: 'http://127.0.0.1:1/' } });
  const client = new Client({ name: 'offline-test', version: '1.0.0' });
  t.after(() => client.close());
  await client.connect(transport);
  assert.equal(client.getServerVersion().version, '0.1.0');
  const { tools } = await client.listTools();
  assert.deepEqual(tools.map(t => t.name), definitions().map(d => d.function.name));
  for (const tool of tools) {
    assert.deepEqual(tool.inputSchema, definitions().find(d => d.function.name === tool.name).function.parameters);
    assert.ok(!tool.description.includes('[sN]'));
    assert.ok(!tool.description.includes('source_id'));
  }
});
test('SDK validates original JSON Schemas and executes all tools with structured/text results', async t => {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'bibliotheca-protocol-'));
  const server = createServer({ runtime: new BibliothecaRuntime({ baseUrl: fixtureBase, cacheDir, fetchImpl: fixtureFetch }) });
  const client = new Client({ name: 'test', version: '1.0.0' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); await rm(cacheDir, { recursive: true, force: true }); });
  await server.connect(serverTransport); await client.connect(clientTransport);
  for (const [name, args] of calls) {
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, undefined, JSON.stringify(result));
    assert.equal(result.structuredContent.ok, true);
    assert.deepEqual(JSON.parse(result.content[0].text), result.structuredContent);
    assert.ok(!JSON.stringify(result).includes('citationId'));
  }
  for (const [name, args] of [['list_volumes', { limit: 51 }], ['get_page_ocr', { volume_id: 'PG001' }], ['search_corpus', { query: 'x'.repeat(201) }], ['list_volumes', { unexpected: true }]]) {
    const result = await client.callTool({ name, arguments: args });
    assert.equal(result.isError, true);
  }
});
test('stdio performs actual HTTP loopback downloads and WASM searches', async t => {
  const fixture = await startFixtureServer();
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'bibliotheca-stdio-'));
  const transport = new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL('../dist/cli.js', import.meta.url))], stderr: 'pipe', env: { ...process.env, BIBLIOTHECA_BASE_URL: fixture.base, BIBLIOTHECA_CACHE_DIR: cacheDir } });
  let stderr = ''; transport.stderr?.on('data', data => { stderr += data; });
  const client = new Client({ name: 'loopback-test', version: '1.0.0' });
  t.after(async () => { await client.close(); await fixture.close(); await rm(cacheDir, { recursive: true, force: true }); });
  await client.connect(transport);
  assert.equal(fixture.requests.length, 0);
  await client.listTools();
  assert.equal(fixture.requests.length, 0);
  for (const name of ['search_corpus', 'search_indices']) {
    const result = await client.callTool({ name, arguments: { query: 'caridade', limit: 1 } });
    assert.equal(result.isError, undefined, JSON.stringify(result));
    assert.ok(result.structuredContent.data.total > 0);
  }
  assert.ok(fixture.requests.some(p => p.endsWith('index.map')));
  assert.ok(fixture.requests.some(p => p.endsWith('.gz')));
  assert.ok(!fixture.requests.some(p => p.endsWith('.js') || p.endsWith('.wasm')));
  assert.equal(stderr, '');
});

test('MCP cancellation reaches an active download and subsequent calls recover', { timeout: 3000 }, async t => {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'bibliotheca-cancel-'));
  let started;
  const downloading = new Promise(resolve => { started = resolve; });
  let slow = true;
  let aborted = false;
  const runtime = new BibliothecaRuntime({ baseUrl: fixtureBase, cacheDir, fetchImpl: (url, options) => {
    if (!slow || !String(url).endsWith('volumes.json')) return fixtureFetch(url, options);
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => { aborted = true; reject(options.signal.reason); }, { once: true });
      started();
    });
  } });
  const server = createServer({ runtime });
  const client = new Client({ name: 'cancel-test', version: '1.0.0' });
  const [left, right] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.close(); await rm(cacheDir, { recursive: true, force: true }); });
  await server.connect(right); await client.connect(left);
  const controller = new AbortController();
  const pending = client.callTool({ name: 'list_volumes', arguments: {} }, { signal: controller.signal });
  const cancelled = assert.rejects(pending);
  await downloading;
  controller.abort();
  await cancelled;
  slow = false;
  const recovered = await client.callTool({ name: 'list_volumes', arguments: {} });
  assert.equal(aborted, true);
  assert.equal(recovered.structuredContent.ok, true);
});
