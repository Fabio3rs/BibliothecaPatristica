import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { BibliothecaRuntime, loadWasm } from '../dist/runtime.js';
import { fixtureBase, fixtureFetch, calls, build, fixtureRoot, ocrText } from './helpers.mjs';
import { validateCompatibility, validateIntegrity, relativePath, baseUrl } from '../dist/compat.js';

async function runtime(t, fetchImpl = fixtureFetch, options = {}) {
  const cacheDir = await mkdtemp(path.join(tmpdir(), 'bibliotheca-test-'));
  t.after(() => rm(cacheDir, { recursive: true, force: true }));
  return new BibliothecaRuntime({ baseUrl: fixtureBase, cacheDir, fetchImpl, githubToken: '', ...options });
}
test('token OCR uses Contents API, keeps canonical sources and reuses the anonymous disk cache', async t => {
  const token = 'offline-test-token';
  let downloads = 0;
  const app = await runtime(t, (input, options) => {
    const url = new URL(input);
    if (url.hostname === 'api.github.com') {
      downloads++;
      assert.equal(url.searchParams.get('ref'), build);
      assert.match(url.pathname, /^\/repos\/Fabio3rs\/BibliothecaPatristica\/contents\/teste\/PG001\/text\//);
      assert.equal(options.headers.Authorization, `Bearer ${token}`);
      return new Response(ocrText);
    }
    assert.equal(options.headers, undefined);
    return fixtureFetch(input, options);
  }, { githubToken: token });
  const args = { volume_id: 'PG001', page: 1, max_chars: 500 };
  const result = await app.execute('get_page_ocr', args);
  assert.equal(result.ok, true);
  assert.match(JSON.stringify(result), /raw\.githubusercontent\.com/);
  assert.ok(!JSON.stringify(result).includes('api.github.com'));
  assert.ok(!JSON.stringify(result).includes(token));
  const anonymous = new BibliothecaRuntime({ baseUrl: fixtureBase, githubToken: '', cacheDir: app.cacheOptions.directory,
    fetchImpl: (url, options) => {
      assert.ok(!String(url).includes('githubusercontent.com'));
      return fixtureFetch(url, options);
    } });
  assert.deepEqual(await anonymous.execute('get_page_ocr', args), result);
  assert.equal(downloads, 1);
  const { readdir } = await import('node:fs/promises');
  for (const file of await readdir(app.cacheOptions.directory)) {
    assert.ok(!(await readFile(path.join(app.cacheOptions.directory, file))).includes(Buffer.from(token)));
  }
});
test('all seven tools execute offline, including real C++ WASM corpus and editorial searches', async t => {
  assert.equal(typeof (await loadWasm()).QueryEngine, 'function');
  const app = await runtime(t);
  for (const [name, args] of calls) {
    const result = await app.execute(name, args);
    assert.equal(result.ok, true, `${name}: ${JSON.stringify(result)}`);
    assert.equal(result.dataset.build, build);
    if (name.startsWith('search_')) assert.ok((result.data.total ?? result.data.reference_matches_total) > 0, JSON.stringify(result));
    if (name === 'search_corpus') {
      assert.equal(result.data.items[0].excerpt_kind, 'automated_summary_pt_br');
      assert.equal(result.data.next_offset, 1);
      assert.ok(result.sources[0].provenance.includes('search_corpus'));
      assert.match(result.data.items[0].viewer_url, /^https:\/\/fixture.test\/BibliothecaPatristica\/viewer\?/);
    }
    if (name === 'get_volume_index') {
      assert.equal(result.data.entries[0].viewer_page, 1);
      assert.equal(result.data.entries[0].printed_page, 41);
    }
    if (name === 'get_page_ocr') {
      assert.ok(JSON.stringify(result).includes('Caritas patiens'));
      assert.ok(JSON.stringify(result).includes('ocr_read'));
      assert.ok(JSON.stringify(result).includes(build));
    }
  }
});
test('real WASM scripture scope, pagination, filters, and evidence', async t => {
  const app = await runtime(t);
  const scoped = await app.execute('search_corpus', { query: 'caridade', scripture_reference: 'João 3:16', scripture_source: 'ocr', limit: 20 });
  assert.equal(scoped.data.items.length, 1);
  assert.equal(scoped.data.items[0].page, 1);
  assert.ok(scoped.sources[0].provenance.includes('search_scripture_scope'));
  const page2 = await app.execute('search_corpus', { query: 'caridade', offset: 1, limit: 1, volumes: ['PG001'] });
  assert.equal(page2.data.items.length, 1);
  assert.equal(page2.data.items[0].rank, 2);
  const none = await app.execute('search_indices', { query: 'caridade', volume_id: 'PL001' });
  assert.equal(none.data.total, 0);
  const bible = await app.execute('search_scripture', { reference: 'João 3:16', locations_per_reference: 1 });
  assert.equal(bible.data.items[0].next_location_offset, 1);
  const next = await app.execute('search_scripture', { reference: 'João 3:16', locations_per_reference: 1, location_offset: 1 });
  assert.equal(next.data.items[0].locations[0].page, 2);
  const ocr = await app.execute('get_page_ocr', { volume_id: 'PG001', page: 1, max_chars: 500 });
  assert.ok(JSON.stringify(ocr).includes('next_cursor'));
});
test('compatibility discovery is lazy, pinned, retryable after network failure', async t => {
  let requests = 0;
  let fail = true;
  const app = await runtime(t, async (...args) => { requests++; if (fail) throw new Error('offline'); return fixtureFetch(...args); });
  assert.equal(requests, 0);
  await assert.rejects(app.execute('list_volumes', {}), /offline/);
  fail = false;
  await app.execute('list_volumes', {});
  const previous = requests;
  await app.execute('list_volumes', {});
  assert.equal(requests, previous);
});
test('unknown formats, mixed builds and path attacks are rejected', async t => {
  const m = JSON.parse(await readFile(path.join(fixtureRoot, 'compat.json')));
  for (const mutate of [x => x.contract_version++, x => x.schemas.search++, x => x.build = 'b'.repeat(40), x => x.sidecars.indexes.ALL = 'sha256:' + '0'.repeat(64)]) {
    const bad = structuredClone(m); mutate(bad); assert.throws(() => validateCompatibility(bad));
  }
  assert.throws(() => validateIntegrity({ schema: 1, build: 'wrong', assets: {} }, m));
  for (const p of ['../a', '/etc/passwd', 'https://evil.test', 'a/%2e%2e/b', 'a\\b', 'a//b']) assert.throws(() => relativePath(p));
  assert.throws(() => baseUrl('http://example.com'));
  const app = await runtime(t);
  await app.discover();
  for (const u of ['https://evil.test/x', 'file:///etc/passwd', '../volumes.json', `${fixtureBase}indexador/runtime/indexador-lib.js`, `${fixtureBase}meta/%2e%2e/volumes.json`, `${fixtureBase}constructor`]) assert.throws(() => app.resolve(u));
});
test('downloaded asset from another build is rejected before parsing', async t => {
  const app = await runtime(t, (url, options) => String(url).endsWith('/volumes.json') ? new Response('{"volumes":[]}') : fixtureFetch(url, options));
  await assert.rejects(app.execute('list_volumes', {}), /integrity mismatch/);
});
test('aborted request does not poison the next execution', async t => {
  const app = await runtime(t);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(app.execute('search_corpus', { query: 'caridade' }, { signal: controller.signal }), { name: 'AbortError' });
  assert.equal((await app.execute('search_corpus', { query: 'caridade' })).ok, true);
});
