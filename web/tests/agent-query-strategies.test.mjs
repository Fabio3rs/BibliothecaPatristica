import test from 'node:test';
import assert from 'node:assert/strict';

import {
  fuseRankedResults,
  normalizeQuerySet,
} from '../src/scripts/agent-query-strategies.js';

test('normalizes string, array and explicit alternatives without duplicates', () => {
  assert.deepEqual(normalizeQuerySet({
    query: [' processão  do Espírito ', 'processio Spiritus Sancti'],
    alternatives: ['processio Spiritus Sancti', 'πνεῦμα ἅγιον'],
  }), {
    queries: ['processão do Espírito', 'processio Spiritus Sancti', 'πνεῦμα ἅγιον'],
    strategy: 'rank_fusion',
    truncated: false,
  });
});

test('bounds query count and reports truncation', () => {
  const result = normalizeQuerySet({ query: ['a', 'b', 'c', 'd', 'e'] });
  assert.deepEqual(result.queries, ['a', 'b', 'c', 'd']);
  assert.equal(result.truncated, true);
  assert.throws(
    () => normalizeQuerySet({ query: ['valid', 2] }),
    (error) => error.code === 'invalid_arguments',
  );
});

test('uses reciprocal-rank fusion with deterministic tie breaking', () => {
  const a = { url: '/viewer?doc=PG001&page=1' };
  const b = { url: '/viewer?doc=PL001&page=2' };
  const c = { url: '/viewer?doc=PO001&page=3' };
  const fused = fuseRankedResults([[a, b], [b, c]]);

  assert.equal(fused[0].item, b);
  assert.deepEqual(fused[0].queryIndexes, [0, 1]);
  assert.ok(fused[0].rrfScore > fused[1].rrfScore);
  assert.deepEqual(fused.slice(1).map((entry) => entry.key), [a.url, c.url]);
});
