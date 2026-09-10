import test from 'node:test';
import assert from 'node:assert/strict';

import {
  findOcrParagraph,
  parseOcrDocument,
  readOcrWindow,
  repairLineBreakHyphens,
} from '../public/scripts/ocr-text.mjs';
import { createAgentTools } from '../src/scripts/agent-chat-tools.js';

const structured = `<pagina estado="com_texto">
  <bloco tipo="texto_principal" script="latino" bbox="10,20,300,400">
    processio Spiri-\n    tus Sancti manet.

    Hic aliud paragraphum est.
  </bloco>
  <bloco tipo="rodape" script="latino" bbox="20,410,300,450">
    Finis-
  </bloco>
</pagina>`;

test('repairs line-break hyphens only inside a block and preserves a terminal hyphen', () => {
  assert.equal(repairLineBreakHyphens('Spiri-\n tus'), 'Spiritus');
  assert.equal(repairLineBreakHyphens('Finis-'), 'Finis-');
  const parsed = parseOcrDocument(structured);
  assert.equal(parsed.blocks[0].paragraphs[0].text, 'processio Spiritus Sancti manet.');
  assert.equal(parsed.blocks[1].paragraphs[0].text, 'Finis-');
});

test('matches a complete paragraph with normalized and fuzzy OCR tokens', () => {
  const parsed = parseOcrDocument(structured);
  const exact = findOcrParagraph(parsed, ['processio Spiritus Sancti']);
  assert.equal(exact.matched, true);
  assert.equal(exact.paragraph.id, 'b1:p1');
  assert.match(exact.text, /processio Spiritus Sancti/);

  const fuzzy = findOcrParagraph(parsed, ['processio Spirilus Sancti']);
  assert.equal(fuzzy.matched, true);
  assert.equal(fuzzy.match_kind, 'fuzzy_tokens');
});

test('returns no text when a query does not match instead of returning the page beginning', () => {
  const result = findOcrParagraph(parseOcrDocument(structured), ['expressão inexistente']);
  assert.equal(result.matched, false);
  assert.equal(result.text, null);
  assert.equal(result.paragraph, null);
});

test('reads an OCR page incrementally with an explicit cursor', () => {
  const parsed = parseOcrDocument(structured);
  const first = readOcrWindow(parsed, { maxChars: 30 });
  assert.equal(first.start, 0);
  assert.equal(first.truncated, true);
  assert.equal(typeof first.next_cursor, 'number');
  const second = readOcrWindow(parsed, { cursor: first.next_cursor, maxChars: 30 });
  assert.equal(second.start, first.next_cursor);
  assert.notEqual(second.text, first.text);
});

test('get_page_ocr exposes an unequivocal no-match and cursor pagination', async () => {
  const getJsonImpl = async (url) => {
    if (url.endsWith('volumes.json')) {
      return { volumes: [{ id: 'PG001', collection_id: 'PG', page_first: 1, page_last: 1, meta_url: 'meta/PG001.json' }] };
    }
    if (url.endsWith('meta/PG001.json')) return { page_blocks: [{ page_first: 1, page_last: 1, file: 'meta/PG001-1.json' }] };
    if (url.endsWith('meta/PG001-1.json')) {
      return { pages: [{ page: 1, work: 'Test', raw: { url: 'http://raw.test/page.txt' } }] };
    }
    throw new Error(`Unexpected JSON URL: ${url}`);
  };
  const tools = createAgentTools({
    assetBase: '/assets/',
    locale: 'pt-br',
    origin: 'http://site.test',
    getJsonImpl,
    fetchImpl: async () => new Response(structured, { status: 200 }),
    loadOcrTextModule: async () => ({ findOcrParagraph, parseOcrDocument, readOcrWindow }),
  });

  const missing = await tools.execute('get_page_ocr', { volume_id: 'PG001', page: 1, query: 'inexistente total' });
  assert.equal(missing.data.matched_query, false);
  assert.equal(missing.data.text, null);
  assert.equal(missing.data.reason, 'query_not_found_on_page');

  const sequential = await tools.execute('get_page_ocr', { volume_id: 'PG001', page: 1, max_chars: 500 });
  assert.equal(sequential.data.matched_query, null);
  assert.match(sequential.data.text, /processio Spiritus/);
  assert.equal(sequential.data.evidence_kind, 'automatic_ocr');
});
