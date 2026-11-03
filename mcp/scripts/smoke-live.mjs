import assert from 'node:assert/strict';
import { BibliothecaRuntime } from '../dist/runtime.js';
const runtime = new BibliothecaRuntime();
const catalog = await runtime.execute('list_volumes', { query: 'PG001', limit: 1 });
assert.ok(catalog.data.total > 0);
for (const name of ['search_corpus', 'search_indices']) {
  const result = await runtime.execute(name, { query: 'caridade', limit: 1 });
  assert.ok(result.data.total > 0, `${name} returned no results`);
  console.log(`${name}: ${result.data.total} results; build ${result.dataset.build}`);
}
for (const [name, args] of [
  ['get_page_metadata', { volume_id: 'PG001', page: 100 }],
  ['get_page_ocr', { volume_id: 'PG001', page: 100 }],
  ['get_volume_index', { volume_id: 'PG001', limit: 1 }],
  ['search_scripture', { reference: 'João 3:16', locations_per_reference: 1 }],
]) { assert.equal((await runtime.execute(name, args)).ok, true); console.log(`${name}: OK`); }
