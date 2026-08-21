import test from 'node:test';
import assert from 'node:assert/strict';

import { createAgentTools } from '../src/scripts/agent-chat-tools.js';

function emptySearchResult(suggestions) {
  return {
    total: 0,
    suggestions,
    async getRange() {
      return [];
    },
  };
}

test('search_corpus exposes bounded C++WASM corrections without repeating the shard scan', async () => {
  const created = [];
  const searches = [];
  const tools = createAgentTools({
    assetBase: '/BibliothecaPatristica/',
    searchIndexVersion: 'build-123',
    origin: 'https://example.test',
    getJsonImpl: async (url) => {
      assert.equal(url, '/BibliothecaPatristica/indexador/search/build-123/manifest.json');
      return {
        engine: 'PatrologiaIndexer',
        layout: 'unified',
        suggestions: { wordlists: true, neighbor_radius: 1 },
        indexes: [{ id: 'ALL', path: 'all', collections: ['PG', 'PL', 'PO'] }],
      };
    },
    loadIndexadorModule: async () => ({
      createIndexadorPagefind(options) {
        created.push(options);
        return {
          async init() {},
          async search(query, searchOptions) {
            searches.push({ query, searchOptions });
            return emptySearchResult([
              { value: 'agostinho', kind: 'correction', distance: 1 },
              { value: 'augustino', kind: 'correction', distance: 2 },
              { value: 'agostinho', kind: 'correction', distance: 1 },
            ]);
          },
        };
      },
    }),
  });

  const result = await tools.execute('search_corpus', { query: 'agostino', limit: 5 });

  assert.equal(created.length, 1);
  assert.equal(created[0].suggestions, true);
  assert.equal(created[0].suggestionWordlists, true);
  assert.equal(created[0].suggestionLimit, 5);
  assert.equal(created[0].maxWasmCacheBytes, 16 * 1024 * 1024);
  assert.equal(created[0].indexBaseUrl, '/BibliothecaPatristica/indexador/search/build-123/all');
  assert.equal(searches.length, 1);
  assert.deepEqual(searches[0].searchOptions, { suggestionsFor: 'agostino' });
  assert.match(searches[0].query, /\(agostino\)/);
  assert.deepEqual(result.data.query_suggestions, [
    {
      original_query: 'agostino',
      suggested_query: 'agostinho',
      kind: 'correction',
      edit_distance: 1,
    },
    {
      original_query: 'agostino',
      suggested_query: 'augustino',
      kind: 'correction',
      edit_distance: 2,
    },
  ]);
  assert.deepEqual(result.modelContext.query_suggestions, result.data.query_suggestions);
});

test('search_indices exposes prefix completions from already loaded index shards', async () => {
  let createdOptions;
  const tools = createAgentTools({
    assetBase: '/BibliothecaPatristica/',
    origin: 'https://example.test',
    loadIndexadorModule: async () => ({
      createIndexadorPagefind(options) {
        createdOptions = options;
        return {
          async init() {},
          async search(query, searchOptions) {
            assert.equal(query, 'agost');
            assert.deepEqual(searchOptions, { suggestionsFor: 'agost' });
            return emptySearchResult([
              { value: 'agostinho', kind: 'prefix', distance: 0 },
            ]);
          },
        };
      },
    }),
  });

  const result = await tools.execute('search_indices', { query: 'agost' });

  assert.equal(createdOptions.suggestions, true);
  assert.equal(createdOptions.suggestionWordlists, undefined);
  assert.equal(createdOptions.maxWasmCacheBytes, 8 * 1024 * 1024);
  assert.deepEqual(result.modelContext.query_suggestions, [{
    original_query: 'agost',
    suggested_query: 'agostinho',
    kind: 'completion',
    edit_distance: 0,
  }]);
});
