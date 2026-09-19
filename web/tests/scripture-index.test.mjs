import assert from 'node:assert/strict';
import test from 'node:test';

import { createAgentTools } from '../src/scripts/agent-chat-tools.js';
import { encodeScriptureDocIdSidecar } from '../src/scripts/scripture-docids.js';
import {
  collectScriptureBooleanReferences,
  containsScriptureBooleanSyntax,
  evaluateScriptureBooleanAst,
  evaluateScriptureBooleanMatches,
  findScriptureBook,
  joinScriptureRoute,
  matchScriptureShard,
  parseScriptureBooleanQuery,
  parseScriptureLocator,
  parseScriptureLocators,
  scriptureBookSlug,
  scriptureBookLabel,
  scriptureReferenceSlug,
  searchScriptureShard,
} from '../src/scripts/scripture-index.js';

test('resolve o nome bíblico pelo idioma com fallback compatível', () => {
  const route = {
    label: 'São João',
    labels: { 'pt-br': 'São João', en: 'John', it: 'Giovanni', fr: 'Jean' },
  };
  assert.equal(scriptureBookLabel(route, 'en', 'joao'), 'John');
  assert.equal(scriptureBookLabel(route, 'it', 'joao'), 'Giovanni');
  assert.equal(scriptureBookLabel(route, 'fr', 'joao'), 'Jean');
  assert.equal(scriptureBookLabel({ label: 'São João' }, 'en', 'joao'), 'São João');
  assert.equal(scriptureBookLabel(null, 'en', 'joao'), 'joao');
});

test('preserva referências inteiras na DSL booleana e aplica precedência', () => {
  const ast = parseScriptureBooleanQuery('João 3:16 || Romanos 8:1 && (Gálatas 5:22 || Efésios 4:3)');
  assert.deepEqual(ast, {
    type: 'or',
    left: { type: 'reference', value: 'João 3:16' },
    right: {
      type: 'and',
      left: { type: 'reference', value: 'Romanos 8:1' },
      right: {
        type: 'or',
        left: { type: 'reference', value: 'Gálatas 5:22' },
        right: { type: 'reference', value: 'Efésios 4:3' },
      },
    },
  });
  assert.deepEqual(collectScriptureBooleanReferences(ast), [
    'João 3:16',
    'Romanos 8:1',
    'Gálatas 5:22',
    'Efésios 4:3',
  ]);
  assert.equal(containsScriptureBooleanSyntax('João 3:16'), false);
  assert.equal(containsScriptureBooleanSyntax('João 3:16 && Romanos 8:1'), true);
});

test('rejeita operadores incompletos, parênteses inválidos e mais de quatro referências', () => {
  assert.throws(() => parseScriptureBooleanQuery('João 3:16 & Romanos 8:1'), /&& or \|\|/);
  assert.throws(() => parseScriptureBooleanQuery('(João 3:16 || Romanos 8:1'), /parentheses/);
  assert.throws(
    () => parseScriptureBooleanQuery('A 1:1 || B 1:1 || C 1:1 || D 1:1 || E 1:1'),
    /at most 4/,
  );
});

test('avalia união e interseção sem interpretar espaços como operadores', () => {
  const ast = parseScriptureBooleanQuery('João 3:16 || Romanos 8:1 && Gálatas 5:22');
  const values = new Map([
    ['João 3:16', new Set(['PG001:10'])],
    ['Romanos 8:1', new Set(['PG001:20', 'PG001:30'])],
    ['Gálatas 5:22', new Set(['PG001:30'])],
  ]);
  assert.deepEqual(
    [...evaluateScriptureBooleanAst(ast, (reference) => values.get(reference))].sort(),
    ['PG001:10', 'PG001:30'],
  );
});

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

test('combina páginas físicas e preserva as referências que justificam cada página', () => {
  const booleanShard = {
    book: ['joao', 'São João'],
    volumes: ['PG001'],
    references: [
      [2, [3, 16, 3, 16], [[0, [100, 1, 20, 4]]]],
      [2, [13, 16, 13, 16], [[0, [100, 2, 30, 1]]]],
    ],
  };
  const expression = 'João 3:16 && João 13:16';
  const ast = parseScriptureBooleanQuery(expression);
  const matches = new Map([
    ['João 3:16', matchScriptureShard(booleanShard, 'João 3:16', { matchMode: 'exact' })],
    ['João 13:16', matchScriptureShard(booleanShard, 'João 13:16', { matchMode: 'exact' })],
  ]);

  assert.deepEqual(evaluateScriptureBooleanMatches(ast, matches), [{
    volume_id: 'PG001',
    page: 100,
    source_mask: 3,
    source_types: ['direct', 'keyword_association'],
    matched_references: ['São João 3:16', 'São João 13:16'],
  }]);
});

test('search_scripture aceita booleanos entre shards e usa uma referência por padrão no modo exato', async () => {
  const payloads = new Map([
    ['/scripture/v3/manifest.json', {
      routes: {
        joao: { label: 'São João', labels: { en: 'John' }, url: 'joao.json', aliases: ['john'] },
        romanos: { label: 'Romanos', labels: { en: 'Romans' }, url: 'romanos.json', aliases: ['romans'] },
      },
    }],
    ['/scripture/v3/joao.json', {
      book: ['joao', 'São João'],
      volumes: ['PG001'],
      references: [[2, [3, 16, 3, 16], [[0, [100, 1, 20, 1]]]]],
    }],
    ['/scripture/v3/romanos.json', {
      book: ['romanos', 'Romanos'],
      volumes: ['PG001'],
      references: [[2, [5, 8, 5, 8], [[0, [100, 4, 30, 4]]]]],
    }],
  ]);
  const tools = createAgentTools({
    assetBase: '/',
    origin: 'https://example.test',
    locale: 'en',
    getJsonImpl: async (url) => payloads.get(url),
  });

  const simple = await tools.execute('search_scripture', { reference: 'João 3:16' });
  assert.equal(simple.data.reference_matches_limit, 1);
  assert.equal(simple.data.items.length, 1);

  const combined = await tools.execute('search_scripture', {
    reference: 'João 3:16 && Romanos 5:8',
    locations_per_reference: 10,
  });
  assert.equal(combined.data.boolean_expression, true);
  assert.deepEqual(combined.data.book_keys, ['joao', 'romanos']);
  assert.equal(simple.data.book_label, 'John');
  assert.equal(simple.data.items[0].reference, 'John 3:16');
  assert.equal(combined.data.items[0].relation, 'boolean_expression');
  assert.deepEqual(combined.data.items[0].locations[0].matched_references, [
    'John 3:16',
    'Romans 5:8',
  ]);
  assert.equal(combined.data.items[0].locations[0].source_mask, 5);
  assert.equal(combined.sources.length, 1);
});

test('search_corpus aplica escopo bíblico por doc_id antes de carregar documentos', async () => {
  const buildId = 'sha256:test-build';
  const searchVersion = 'commit-abc123';
  const payloads = new Map([
    [`/indexador/search/${searchVersion}/manifest.json`, {
      layout: 'unified',
      indexes: [{ id: 'ALL', path: 'all', collections: ['PG', 'PL', 'PO'], build_id: buildId }],
    }],
    ['/scripture/v3/manifest.json', {
      routes: { joao: { label: 'São João', url: 'joao.json' } },
    }],
    ['/scripture/v3/joao.json', {
      book: ['joao', 'São João'],
      volumes: ['PG001'],
      references: [[2, [3, 16, 3, 16], [[0, [100, 4]]]]],
    }],
    [`/indexador/search/${searchVersion}/scripture-docids/manifest.json`, {
      indexes: { ALL: { build_id: buildId, books: { joao: { url: 'all/joao.bin.gz' } } } },
    }],
    ['/volumes.json', {
      volumes: [{ id: 'PG001', collection_id: 'PG', page_first: 1, page_last: 200, meta_url: 'meta/PG001.json' }],
    }],
    ['/meta/PG001.json', { page_blocks: [{ page_first: 1, page_last: 200, file: 'meta/PG001-pages.json' }] }],
    ['/meta/PG001-pages.json', {
      raw_base_url: '/raw/PG001',
      pages: [{ page: 100, author: 'Augustinus', work: 'De test', summary_page: 'Resumo sobre caridade.' }],
    }],
  ]);
  const sidecar = encodeScriptureDocIdSidecar(['PG001'], [
    { volume_id: 'PG001', page: 100, doc_id: 42 },
  ]);
  const observed = { baseRangeCalls: 0, progress: [] };
  const engine = {
    async init(options) {
      observed.initOptions = options;
      engineOptions.onProgress({
        phase: 'init',
        state: 'start',
        operation_id: options.operationId,
      });
      engineOptions.onProgress({
        phase: 'init',
        state: 'done',
        operation_id: options.operationId,
      });
    },
    async search(query, options) {
      observed.query = query;
      observed.searchOptions = options;
      return {
        total: 2,
        suggestions: [],
        async getRange() {
          observed.baseRangeCalls += 1;
          return [];
        },
        filterDocumentIds(ids, filterOptions) {
          observed.ids = ids;
          observed.filterOptions = filterOptions;
          return {
            total: 1,
            suggestions: [],
            async getRange(_from, _to, options) {
              observed.documentOptions = options;
              return [{ id: 42, name: 'PG001 p.100', url: '/viewer?doc=PG001&page=100', score: 3, hits: ['agostinho'] }];
            },
          };
        },
      };
    },
  };
  let engineOptions;
  const tools = createAgentTools({
    assetBase: '/',
    origin: 'https://example.test',
    searchIndexVersion: searchVersion,
    getJsonImpl: async (url) => payloads.get(url),
    getBinaryImpl: async (url) => {
      observed.sidecarUrl = url;
      return sidecar;
    },
    loadIndexadorModule: async () => ({
      createIndexadorPagefind(options) {
        engineOptions = options;
        return engine;
      },
    }),
  });

  const result = await tools.execute('search_corpus', {
    query: 'agostinho',
    scripture_reference: 'João 3:16',
  }, {
    operationId: 'call-1',
    onProgress: (event) => observed.progress.push(event),
  });

  assert.equal(engineOptions.indexBuildId, buildId);
  assert.equal(engineOptions.requireIndexBuildId, true);
  assert.equal(observed.initOptions.operationId, 'call-1:init:ALL');
  assert.equal(observed.query, '(agostinho)');
  assert.equal(observed.searchOptions.operationId, 'call-1:0:ALL:search');
  assert.deepEqual(observed.ids, [42]);
  assert.equal(observed.filterOptions.indexBuildId, buildId);
  assert.equal(observed.filterOptions.operationId, 'call-1:0:ALL:scope');
  assert.equal(observed.documentOptions.operationId, 'call-1:0:ALL:documents');
  assert.equal(observed.sidecarUrl, `/indexador/search/${searchVersion}/scripture-docids/all/joao.bin.gz`);
  assert.equal(observed.baseRangeCalls, 0);
  assert.equal(result.data.total, 1);
  assert.equal(result.data.items[0].volume_id, 'PG001');
  assert.equal(result.data.scripture_scope.reference, 'João 3:16');
  assert.equal(result.modelContext.retrieval_scope.kind, 'scripture_reference');
  assert.ok(observed.progress.some((event) => event.phase === 'init' && event.state === 'start'));
  assert.ok(observed.progress.some((event) => event.phase === 'scope_mapping' && event.state === 'start'));
  assert.ok(observed.progress.some((event) => event.phase === 'scope_mapping' && event.state === 'done'));
});

test('aborta a preparação do escopo bíblico sem esperar o fetch compartilhado', async () => {
  const controller = new AbortController();
  const progress = [];
  const never = new Promise(() => {});
  let markScriptureStarted;
  const scriptureStarted = new Promise((resolve) => { markScriptureStarted = resolve; });
  const tools = createAgentTools({
    assetBase: '/',
    origin: 'https://example.test',
    getJsonImpl: async (url) => {
      if (url === '/indexador/search/manifest.json') {
        return { layout: 'unified', indexes: [{ id: 'ALL', path: 'all', collections: ['PG'], build_id: 'sha256:test' }] };
      }
      if (url === '/scripture/v3/manifest.json') {
        markScriptureStarted();
        return never;
      }
      throw new Error(`Unexpected URL: ${url}`);
    },
  });

  const pending = tools.execute('search_corpus', {
    query: 'gratia',
    scripture_reference: 'João 3:16',
  }, {
    operationId: 'abort-scope',
    signal: controller.signal,
    onProgress: (event) => progress.push(event),
  });
  await scriptureStarted;
  controller.abort();

  await assert.rejects(pending, (error) => error?.name === 'AbortError');
  assert.ok(progress.some((event) => event.phase === 'scripture_scope' && event.state === 'start'));
  assert.ok(progress.some((event) => event.phase === 'scripture_scope' && event.state === 'aborted'));
});

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
