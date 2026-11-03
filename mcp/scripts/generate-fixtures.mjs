// Explicit maintainer operation; never runs during installation or regular tests.
import { mkdir, writeFile } from 'node:fs/promises';
import { gzipSync } from 'node:zlib';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { buildCompatibility } from '../../tools/build_mcp_compat.mjs';

const repo = fileURLToPath(new URL('../../', import.meta.url));
const root = path.join(repo, 'mcp/test/fixtures/public');
const build = 'a'.repeat(40);
const generated_at = '2026-01-01T00:00:00Z';
async function json(name, data) {
  await mkdir(path.dirname(path.join(root, name)), { recursive: true });
  const bytes = Buffer.from(JSON.stringify(data));
  await writeFile(path.join(root, name), name.endsWith('.gz') ? gzipSync(bytes) : bytes);
}
await json('volumes.json', { schema_version: 1, volumes: [{ id: 'PG001', collection_id: 'PG', page_first: 1, page_last: 3, page_count: 3, meta_url: 'meta/PG001.json.gz' }] });
await json('meta/PG001.json.gz', { schema_version: 1, page_blocks: [{ page_first: 1, page_last: 3, file: 'meta/PG001-pages-001.json.gz' }] });
await json('meta/PG001-pages-001.json.gz', { schema_version: 2, block_type: 'pages', pages: [1, 2, 3].map(page => ({ page,
  author: 'Clemens', work: 'Epistola Corinthios', summary_page: `Clemente ensina caridade e unidade. Página ${page}.`,
  keywords: ['k:caridade'], raw: { url: `https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/refs/heads/codex/teste/PG001/text/fixture-${page}.txt` },
})) });
await json('dict/keywords.json', { items: [{ id: 'k:caridade', label: 'caridade' }] });
await json('dict/keywords_lookup.json', { items: [{ id: 'k:caridade', label: 'caridade' }] });
await json('indices/manifest.json', { schema_version: 3, generated_at, volumes: [{ volume_id: 'PG001', collection: 'PG', path: 'indices/PG001.json.gz', works_total: 1, sections_total: 1, entries_total: 2 }] });
await json('indices/PG001.json.gz', { schema_version: 3, volume: { volume_id: 'PG001', collection: 'PG', volume_label: 'Clemens' },
  works: [{ work_key: 'w1', author_display: { original: 'Clemens' }, title_display: { original: 'Epistola Corinthios' } }],
  sections: [{ section_key: 's1', work_key: 'w1', heading_display: { original: 'Caridade' }, entries: [1, 2].map(n => ({ id: String(n), entry_raw: 'Caridade', reference_page: n, editorial_reference_page: 40 + n, page_ref_raw: `col. ${40 + n}` })) }],
});
await json('scripture/v3/manifest.json', { schema: 'bibliotheca-scripture-book-shards-v3', v: 3, routes: { joao: { label: 'João', aliases: ['john', 'ioannes'], url: 'joao.json.gz' } } });
await json('scripture/v3/joao.json.gz', { book: ['joao', 'João'], volumes: ['PG001'], references: [[2, [3, 16, 3, 16], [[0, [1, 4, 1, 1]]]]] });
const binary = process.env.INDEXADOR_BINARY || path.join(repo, '.work/PatrologiaIndexer/build-codex-clang19/indexador');
const run = (cmd, args) => execFileSync(cmd, args, { cwd: repo, stdio: 'inherit' });
run(process.execPath, ['tools/build_search_indexador_from_shards.mjs', '--public', root, '--source-manifest', `${root}/volumes.json`, '--out', `${root}/indexador/search/${build}`, '--binary', binary, '--base', '/BibliothecaPatristica', '--layout', 'unified', '--min-importance', '0', '--all']);
run(process.execPath, ['tools/build_indices_indexador_from_json.mjs', '--source-manifest', `${root}/indices/manifest.json`, '--out', `${root}/indexador/indices/${build}`, '--binary', binary, '--base', '/BibliothecaPatristica', '--min-importance', '0', '--all']);
run('python3', ['tools/build_scripture_docid_sidecars.py', '--public', root, '--search-manifest', `${root}/indexador/search/${build}/manifest.json`, '--out', `${root}/indexador/search/${build}/scripture-docids`, '--jobs', '1']);
await buildCompatibility({ publicDir: root, build, indexadorVersion: 'v0.8.5', indexadorCommit: '028cd943561c546974ad78eefc32e33eb6b5a1a6' });
