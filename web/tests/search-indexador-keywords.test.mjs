import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildRecord,
  keywordLiteral,
} from '../../tools/build_search_indexador_from_shards.mjs';

test('keywordLiteral follows the custom indexer separator contract', () => {
  assert.equal(keywordLiteral('palavra chave'), 'palavra_chave');
  assert.equal(keywordLiteral('palavra-chave'), 'palavra_chave');
  assert.equal(keywordLiteral('João 3:16'), 'João_3_16');
  assert.equal(keywordLiteral('graça'), '');
});

test('buildRecord indexes source labels and their literal forms', () => {
  const record = buildRecord(
    {
      base: '/BibliothecaPatristica',
      includeKeywords: true,
      keywordLiterals: true,
      includeTranslations: false,
      includeV2Search: false,
      includeOriginalHeader: false,
    },
    new Map([['k:lider', { id: 'k:lider', label: 'Líder inadequado', iscit: false }]]),
    'PG001',
    {
      page: 1,
      keyword_ids: ['k:lider'],
      keyword_labels: ['Tradição Católica', 'Termo histórico completo'],
      keyword_display_count: 1,
    },
    { file: 'meta/PG001-pages-001.json.gz' },
  );

  assert.match(record.name, /Tradição Católica/);
  assert.match(record.content, /Tradição Católica/);
  assert.match(record.content, /Tradição_Católica/);
  assert.match(record.content, /Termo histórico completo/);
  assert.doesNotMatch(record.content, /Termo_histórico_completo/);
  assert.match(record.content, /Líder_inadequado/);
  assert.match(record.content, /Líder inadequado/);
  assert.doesNotMatch(record.name, /Termo histórico completo/);
});
