import assert from 'node:assert/strict';
import test from 'node:test';

import {
  decodeScriptureDocIdSidecar,
  encodeScriptureDocIdSidecar,
  mapScriptureLocationsToDocumentIds,
} from '../src/scripts/scripture-docids.js';

test('compacta e restaura doc_ids com deltas positivos e negativos', () => {
  const volumes = ['PG001', 'PL002'];
  const encoded = encodeScriptureDocIdSidecar(volumes, [
    { volume_id: 'PG001', page: 10, doc_id: 101 },
    { volume_id: 'PG001', page: 11, doc_id: 99 },
    { volume_id: 'PL002', page: 4, doc_id: 1200 },
  ]);
  assert.equal(Buffer.from(encoded).toString('hex'), '42534449310200020aca010103010104e012');
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
