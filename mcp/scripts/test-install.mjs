import { execFileSync } from 'node:child_process';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/client';
import { StdioClientTransport } from '@modelcontextprotocol/client/stdio';
import { startFixtureServer } from '../test/helpers.mjs';
import { digest } from '../src/cache.js';

const root = fileURLToPath(new URL('../', import.meta.url));
const manifest = JSON.parse(await readFile(path.join(root, 'artifacts/build-manifest.json')));
const archive = path.join(root, 'artifacts', manifest.tarball);
assert.equal(digest(await readFile(archive)), manifest.sha256);
const temporary = await mkdtemp(path.join(tmpdir(), 'bibliotheca-install-'));
const fixture = await startFixtureServer();
let client;
try {
  // Installing a tarball needs registry metadata that npm ci may not cache.
  // Use a fresh cache and allow dependency downloads, with lifecycle scripts disabled.
  execFileSync(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['install', '--prefix', temporary, '--cache', path.join(temporary, 'npm-cache'), '--ignore-scripts', '--no-audit', '--no-fund', '--no-package-lock', archive], { cwd: temporary, stdio: 'inherit' });
  const executable = path.join(temporary, 'node_modules', '.bin', 'bibliotheca-patristica-mcp');
  assert.equal(execFileSync(executable, ['--version'], { cwd: temporary, encoding: 'utf8' }).trim(), manifest.version);
  const transport = new StdioClientTransport({ command: executable, args: [], cwd: temporary, stderr: 'pipe', env: {
    ...process.env, BIBLIOTHECA_BASE_URL: fixture.base, BIBLIOTHECA_CACHE_DIR: path.join(temporary, 'cache'),
  } });
  let stderr = ''; transport.stderr?.on('data', data => { stderr += data; });
  client = new Client({ name: 'tarball-validation', version: '1.0.0' });
  await client.connect(transport);
  assert.equal((await client.listTools()).tools.length, 7);
  assert.equal(fixture.requests.length, 0);
  for (const name of ['list_volumes', 'search_corpus', 'search_indices']) {
    const result = await client.callTool({ name, arguments: name === 'list_volumes' ? {} : { query: 'caridade', limit: 1 } });
    assert.equal(result.isError, undefined, JSON.stringify(result));
    assert.ok(result.structuredContent.data.total > 0);
  }
  assert.equal(stderr, '');
  console.log(`Installed ${manifest.tarball} outside checkout; CLI, stdio, seven tools and real WASM searches passed.`);
} finally {
  await client?.close(); await fixture.close();
  await rm(temporary, { recursive: true, force: true });
}
