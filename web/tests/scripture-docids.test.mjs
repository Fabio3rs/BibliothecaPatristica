import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { validateSidecarPublication } from '../../tools/build_scripture_docid_sidecars.mjs';
import {
  decodeScriptureDocIdSidecar,
  encodeScriptureDocIdSidecar,
  mapScriptureLocationsToDocumentIds,
} from '../src/scripts/scripture-docids.js';

function publicationFixture(t, overrides = {}) {
  const outputDir = fs.mkdtempSync(path.join(os.tmpdir(), 'scripture-docids-test-'));
  t.after(() => fs.rmSync(outputDir, { recursive: true, force: true }));
  fs.mkdirSync(path.join(outputDir, 'all'));
  const sidecarPath = path.join(outputDir, 'all', 'joao.bin.gz');
  fs.writeFileSync(sidecarPath, new Uint8Array([0x1f, 0x8b, 0x01]));
  const book = {
    url: 'all/joao.bin.gz',
    raw_bytes: 10,
    gzip_bytes: 3,
    pages_total: 100,
    pages_mapped: 99,
    pages_missing: 1,
    ...(overrides.book || {}),
  };
  return {
    outputDir,
    searchManifest: { indexes: [{ id: 'ALL', build_id: 'sha256:test', documents: 10 }] },
    scriptureManifest: { routes: { joao: { url: 'joao.json.gz' } } },
    sidecarManifest: {
      schema: 'bibliotheca-scripture-docids-v1',
      indexes: {
        ALL: {
          build_id: overrides.buildId || 'sha256:test',
          documents: 10,
          books: { joao: book },
          coverage: {
            pages_total: book.pages_total,
            pages_mapped: book.pages_mapped,
            pages_missing: book.pages_missing,
            ratio: book.pages_mapped / book.pages_total,
          },
        },
      },
    },
  };
}

test('compacta e restaura doc_ids com deltas positivos e negativos', () => {
  const volumes = ['PG001', 'PL002'];
  const encoded = encodeScriptureDocIdSidecar(volumes, [
    { volume_id: 'PG001', page: 10, doc_id: 101 },
    { volume_id: 'PG001', page: 11, doc_id: 99 },
    { volume_id: 'PL002', page: 4, doc_id: 1200 },
  ]);
  const decoded = decodeScriptureDocIdSidecar(encoded, volumes);
  assert.equal(decoded.get('PG001:10'), 101);
  assert.equal(decoded.get('PG001:11'), 99);
  assert.equal(decoded.get('PL002:4'), 1200);
});

test('deduplica IDs e informa páginas ausentes', () => {
  const documents = new Map([['PG001:10', 7], ['PG001:11', 7]]);
  const result = mapScriptureLocationsToDocumentIds([
    { volume_id: 'PG001', page: 10 },
    { volume_id: 'PG001', page: 11 },
    { volume_id: 'PL001', page: 20 },
  ], [documents]);
  assert.deepEqual(result.ids, [7]);
  assert.deepEqual(result.missing, ['PL001:20']);
});

test('valida build_id, arquivos e cobertura dos sidecars publicados', (t) => {
  const fixture = publicationFixture(t);
  const result = validateSidecarPublication(fixture);
  assert.equal(result.indexes, 1);
  assert.equal(result.books, 1);
  assert.equal(result.pages_mapped, 99);
  assert.equal(result.ratio, 0.99);
});

test('rejeita sidecar desatualizado ou com cobertura degradada', (t) => {
  const stale = publicationFixture(t, { buildId: 'sha256:stale' });
  assert.throws(() => validateSidecarPublication(stale), /build_id/);

  const degraded = publicationFixture(t, {
    book: { pages_total: 100, pages_mapped: 80, pages_missing: 20 },
  });
  assert.throws(
    () => validateSidecarPublication({ ...degraded, minBookCoverage: 0.95 }),
    /cobertura 80\.000% abaixo/,
  );
});
