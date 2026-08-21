import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { access } from 'node:fs/promises';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const locales = ['pt-br', 'en', 'it', 'fr'];
const localizedRoutes = [
  'index.astro',
  'search.astro',
  'indices.astro',
  'viewer.astro',
  'indices-alfabeticos.astro',
  'indices-alfabeticos/names.astro',
  'indices-alfabeticos/subjects.astro',
  'indices-alfabeticos/scripture/index.astro',
  'indices-alfabeticos/scripture/[book].astro',
  'indices-alfabeticos/scripture/reference.astro',
  'seo/volumes/[volumeId].astro',
  'seo/volumes/[volumeId]/indice-tematico.astro',
];

function keysFrom(source) {
  return [...source.matchAll(/^  ([A-Za-z0-9_]+):/gm)].map((match) => match[1]).sort();
}

test('all locale dictionaries expose the same keys', async () => {
  const dictionaries = await Promise.all(locales.map(async (locale) => {
    const source = await readFile(join(root, 'src', 'i18n', `${locale}.ts`), 'utf8');
    return [locale, keysFrom(source)];
  }));
  const expected = dictionaries[0][1];
  assert.ok(expected.length > 400, 'expected the complete UI dictionary');
  for (const [locale, keys] of dictionaries.slice(1)) {
    assert.deepEqual(keys, expected, `${locale} dictionary differs from pt-br`);
  }
});

test('French routes have parity with English and Italian routes', async () => {
  for (const route of localizedRoutes) {
    await access(join(root, 'src', 'pages', 'fr', route));
  }
});
