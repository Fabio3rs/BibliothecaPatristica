import assert from 'node:assert/strict';
import test from 'node:test';

import {
  findScriptureBook,
  joinScriptureRoute,
  parseScriptureLocator,
  parseScriptureLocators,
  scriptureBookSlug,
  scriptureReferenceSlug,
  searchScriptureShard,
} from '../src/scripts/scripture-index.js';

test('compõe rotas filhas com uma única barra e barra final', () => {
  const base = '/BibliothecaPatristica/indices-alfabeticos/scripture/';

  assert.equal(
    joinScriptureRoute(base, 'joao'),
    '/BibliothecaPatristica/indices-alfabeticos/scripture/joao/',
  );
  assert.equal(
    joinScriptureRoute(`${base}/`, '/reference/'),
    '/BibliothecaPatristica/indices-alfabeticos/scripture/reference/',
  );
});

const manifest = {
  routes: {
    joao: {
      label: 'São João',
      url: '049-joao.json.gz',
      aliases: ['jean', 'john', 'giovanni', 'ioannes'],
    },
    '1 joao': { label: '1 João', url: '067-1-joao.json.gz' },
    '1 timoteo': {
      label: '1 Timóteo',
      url: '060-1-timoteo.json.gz',
      aliases: ['i timotheum', 'first timothy'],
    },
    jo: { label: 'Jó', url: '021-jo.json.gz' },
  },
};

const shard = {
  book: ['joao', 'São João'],
  volumes: ['PG001', 'PL010'],
  references: [
    [2, [3, 16, 3, 16], [[0, [100, 1, 5, 2, 5, 4]], [1, [20, 3]]]],
    [1, [3, 0, 3, 0], [[0, [50, 1]]]],
    [3, [3, 14, 3, 18], [[1, [40, 2]]]],
    [2, [13, 16, 13, 16], [[0, [200, 1]]]],
  ],
};

test('aceita vírgula e dois-pontos como separador de capítulo e versículo', () => {
  assert.deepEqual(parseScriptureLocator('João 3,16'), [3, 16, 3, 16]);
  assert.deepEqual(parseScriptureLocator('João 3:16'), [3, 16, 3, 16]);
  assert.deepEqual(parseScriptureLocator('João 3,14-18'), [3, 14, 3, 18]);
});

test('ignora o ordinal do livro e aceita capítulos romanos', () => {
  assert.deepEqual(parseScriptureLocator('1 Timóteo 6,13'), [6, 13, 6, 13]);
  assert.deepEqual(parseScriptureLocator('I Timotheum VI,13'), [6, 13, 6, 13]);
  assert.deepEqual(parseScriptureLocator('Ioan. III,16'), [3, 16, 3, 16]);
});

test('preserva listas descontínuas completas na consulta', () => {
  assert.deepEqual(parseScriptureLocators('João 3:14-16; 3:18'), [
    [3, 14, 3, 16],
    [3, 18, 3, 18],
  ]);
  assert.deepEqual(parseScriptureLocators('João 3,16 e 4,1'), [
    [3, 16, 3, 16],
    [4, 1, 4, 1],
  ]);
  assert.deepEqual(parseScriptureLocators('3:16; 4:1,3'), [
    [3, 16, 3, 16],
    [4, 1, 4, 1],
    [4, 3, 4, 3],
  ]);
  assert.deepEqual(parseScriptureLocators('Deuteronômio 12,13-14,17-18,26; 14,22-25'), [
    [12, 13, 12, 14],
    [12, 17, 12, 18],
    [12, 26, 12, 26],
    [14, 22, 14, 25],
  ]);
});

test('detecta livros e produz slugs sem espaços', () => {
  assert.equal(findScriptureBook(manifest, 'João 3,16'), 'joao');
  assert.equal(findScriptureBook(manifest, '1 João 2:1'), '1 joao');
  assert.equal(findScriptureBook(manifest, 'Jó 1:1'), 'jo');
  assert.equal(findScriptureBook(manifest, 'Jean 3,16'), 'joao');
  assert.equal(findScriptureBook(manifest, 'John 3:16'), 'joao');
  assert.equal(findScriptureBook(manifest, 'Giovanni 3,16'), 'joao');
  assert.equal(findScriptureBook(manifest, 'Ioannes III,16'), 'joao');
  assert.equal(findScriptureBook(manifest, '3:16', '1-joao'), '1 joao');
  assert.equal(findScriptureBook(manifest, 'I Timotheum 6,13'), '1 timoteo');
  assert.equal(findScriptureBook(manifest, 'First Timothy 6:13'), '1 timoteo');
  assert.equal(scriptureBookSlug('1 joao'), '1-joao');
});

test('produz slugs de referência tipados e sem ambiguidades', () => {
  assert.equal(scriptureReferenceSlug([[1, 0, 1, 0]]), 'chapter-1');
  assert.equal(scriptureReferenceSlug([[1, 1, 1, 1]]), 'chapter-1-verse-1');
  assert.equal(
    scriptureReferenceSlug([[1, 0, 3, 0]]),
    'chapter-1-to-chapter-3',
  );
  assert.equal(
    scriptureReferenceSlug([[1, 14, 1, 16], [1, 18, 1, 18]]),
    'chapter-1-verse-14-to-chapter-1-verse-16--and--chapter-1-verse-18',
  );
});

test('só classifica como exata uma lista canônica completamente igual', () => {
  const listShard = {
    book: ['joao', 'São João'],
    volumes: ['PG001'],
    references: [
      [4, [3, 14, 3, 16, 3, 18, 3, 18], [[0, [100, 1]]]],
      [2, [3, 18, 3, 18], [[0, [101, 1]]]],
    ],
  };

  const single = searchScriptureShard(listShard, 'João 3:18', { matchMode: 'exact' });
  assert.equal(single.total, 1);
  assert.equal(single.items[0].reference, 'São João 3:18');

  const list = searchScriptureShard(listShard, 'João 3:14-16; 3:18', { matchMode: 'exact' });
  assert.equal(list.total, 1);
  assert.equal(list.items[0].reference, 'São João 3:14–16; 18');

  const overlaps = searchScriptureShard(listShard, 'João 3:18');
  assert.deepEqual(overlaps.items.map((item) => item.relation), ['exact', 'contains_query']);
});

test('ordena a referência exata, inclui sobreposições e exclui 13:16', () => {
  const result = searchScriptureShard(shard, 'João 3,16', { limit: 10, locationsPerReference: 10 });
  assert.equal(result.total, 3);
  assert.deepEqual(result.items.map((item) => item.reference), [
    'São João 3:16',
    'São João 3',
    'São João 3:14–18',
  ]);
  assert.equal(result.items[0].relation, 'exact');
  assert.deepEqual(result.items[0].locations, [
    { volume_id: 'PG001', page: 100, source_mask: 1, source_types: ['direct'] },
    { volume_id: 'PG001', page: 105, source_mask: 2, source_types: ['keyword_association'] },
    { volume_id: 'PG001', page: 110, source_mask: 4, source_types: ['ocr'] },
    { volume_id: 'PL010', page: 20, source_mask: 3, source_types: ['direct', 'keyword_association'] },
  ]);
});

test('aplica modo exato e filtros de proveniência', () => {
  const exact = searchScriptureShard(shard, 'João 3:16', { matchMode: 'exact', source: 'all' });
  assert.equal(exact.total, 1);
  assert.equal(exact.items[0].reference, 'São João 3:16');

  const associated = searchScriptureShard(shard, 'João 3:16', { matchMode: 'exact', source: 'associated' });
  assert.deepEqual(associated.items[0].locations.map(({ volume_id, page }) => [volume_id, page]), [
    ['PG001', 105],
    ['PL010', 20],
  ]);

  const ocr = searchScriptureShard(shard, 'João 3:16', { matchMode: 'exact', source: 'ocr' });
  assert.deepEqual(ocr.items[0].locations.map(({ volume_id, page }) => [volume_id, page]), [
    ['PG001', 110],
  ]);
});

test('pagina referências e páginas internas com próximos offsets explícitos', () => {
  const firstReferences = searchScriptureShard(shard, 'João 3:16', {
    offset: 0,
    limit: 1,
    locationsPerReference: 1,
  });
  assert.equal(firstReferences.total, 3);
  assert.equal(firstReferences.offset, 0);
  assert.equal(firstReferences.next_offset, 1);
  assert.equal(firstReferences.items[0].reference, 'São João 3:16');
  assert.equal(firstReferences.items[0].location_offset, 0);
  assert.equal(firstReferences.items[0].next_location_offset, 1);
  assert.deepEqual(firstReferences.items[0].locations.map(({ volume_id, page }) => [volume_id, page]), [['PG001', 100]]);

  const nextReference = searchScriptureShard(shard, 'João 3:16', { offset: 1, limit: 1 });
  assert.equal(nextReference.items[0].reference, 'São João 3');
  assert.equal(nextReference.next_offset, 2);

  const nextLocations = searchScriptureShard(shard, 'João 3:16', {
    matchMode: 'exact',
    locationOffset: 1,
    locationsPerReference: 1,
  });
  assert.equal(nextLocations.next_offset, null);
  assert.equal(nextLocations.items[0].location_offset, 1);
  assert.equal(nextLocations.items[0].next_location_offset, 2);
  assert.deepEqual(nextLocations.items[0].locations.map(({ volume_id, page }) => [volume_id, page]), [['PG001', 105]]);

  const finalLocations = searchScriptureShard(shard, 'João 3:16', {
    matchMode: 'exact',
    locationOffset: 2,
    locationsPerReference: 2,
  });
  assert.equal(finalLocations.items[0].next_location_offset, null);
  assert.deepEqual(finalLocations.items[0].locations.map(({ volume_id, page }) => [volume_id, page]), [
    ['PG001', 110],
    ['PL010', 20],
  ]);
});
