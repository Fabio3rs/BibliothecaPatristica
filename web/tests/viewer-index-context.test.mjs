import test from 'node:test';
import assert from 'node:assert/strict';

import { buildViewerIndexContext } from '../public/scripts/viewer-index-context.mjs';

const display = (original, translated = '') => ({
  original,
  translation: translated ? { 'pt-br': translated } : {},
});

test('uses resolved physical page ranges and returns the narrowest current work', () => {
  const context = buildViewerIndexContext({
    works: [
      {
        work_key: 'wide',
        title_display: display('Opus', 'Obra'),
        start_page: 900,
        end_page: 1400,
        reference_start_page: 20,
        reference_end_page: 200,
      },
      {
        work_key: 'precise',
        title_display: display('Liber secundus', 'Livro segundo'),
        reference_start_page: 80,
        reference_end_page: 120,
      },
    ],
  }, 100);

  assert.equal(context.work.key, 'precise');
  assert.equal(context.work.title.primary, 'Livro segundo');
  assert.equal(context.work.progress, 50);
});

test('groups exact and nearest resolved index entries and removes duplicates', () => {
  const repeated = {
    id: 2,
    entry_order: 2,
    target_display: display('Caput II', 'Capítulo II'),
    reference_page: 50,
  };
  const context = buildViewerIndexContext({
    sections: [
      {
        heading_display: display('Elenchus', 'Sumário'),
        entries: [
          { id: 1, entry_order: 1, target_display: display('Caput I', 'Capítulo I'), reference_page: 40 },
          repeated,
          { id: 3, entry_order: 3, target_display: display('Caput III', 'Capítulo III'), reference_page: 60 },
        ],
      },
      { heading_display: display('Ordo'), entries: [repeated] },
    ],
  }, 50);

  assert.deepEqual(context.current.map((entry) => entry.target.primary), ['Capítulo II']);
  assert.deepEqual(context.previous.map((entry) => entry.page), [40]);
  assert.deepEqual(context.next.map((entry) => entry.page), [60]);
  assert.equal(context.resolvedEntries, 3);
});
