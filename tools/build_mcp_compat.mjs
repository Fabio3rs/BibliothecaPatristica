#!/usr/bin/env node
import { readFile, writeFile, readdir, mkdir, lstat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { SCHEMAS, relativePath, validateCompatibility } from '../mcp/src/compat.js';

const hash = b => createHash('sha256').update(b).digest('hex');
export async function buildCompatibility({ publicDir, build, indexadorVersion, indexadorCommit }) {
  if (!/^[a-f0-9]{40}$/.test(build)) throw new Error('Publication build must be a full Git SHA.');
  if (!indexadorVersion || !/^[a-f0-9]{40}$/.test(indexadorCommit)) throw new Error('PatrologiaIndexer version/commit required.');
  const json = async name => JSON.parse(await readFile(path.join(publicDir, name)));
  const searchPath = `indexador/search/${build}/manifest.json`;
  const indicesPath = `indexador/indices/${build}/manifest.json`;
  const sidecarPath = `indexador/search/${build}/scripture-docids/manifest.json`;
  const [search, indices, sidecars, catalog, editorial, scripture] = await Promise.all(
    [searchPath, indicesPath, sidecarPath, 'volumes.json', 'indices/manifest.json', 'scripture/v3/manifest.json'].map(json));
  if (search.schema_version !== SCHEMAS.search || indices.schema_version !== SCHEMAS.indices ||
      indices.content_schema_version !== SCHEMAS.editorial || catalog.schema_version !== SCHEMAS.catalog ||
      editorial.schema_version !== SCHEMAS.editorial || scripture.schema !== SCHEMAS.scripture || sidecars.schema !== SCHEMAS.sidecars) throw new Error('Cannot publish unsupported schemas.');
  if (search.engine !== 'PatrologiaIndexer' || indices.engine !== search.engine) throw new Error('Unexpected search engine.');
  if (indices.source_generated_at !== editorial.generated_at) throw new Error('Editorial source/index generation mismatch.');
  for (const entry of search.indexes) {
    relativePath(entry.path);
    if (entry.build_id !== sidecars.indexes?.[entry.id]?.build_id || entry.documents !== sidecars.indexes[entry.id].documents || entry.document_locations?.build_id !== entry.build_id) throw new Error(`Sidecar build mismatch: ${entry.id}`);
    await readFile(path.join(publicDir, `indexador/search/${build}/${entry.path}/index.map`));
  }
  const assets = {};
  async function collect(name) {
    relativePath(name);
    const file = path.join(publicDir, name);
    const info = await lstat(file);
    if (info.isSymbolicLink()) throw new Error(`Publication symlink refused: ${name}`);
    if (info.isDirectory()) {
      for (const entry of (await readdir(file)).sort()) await collect(`${name}/${entry}`);
    } else {
      const bytes = await readFile(file);
      assets[name] = { bytes: bytes.length, sha256: hash(bytes) };
    }
  }
  for (const name of ['volumes.json', 'meta', 'dict', 'indices', 'scripture/v3', `indexador/search/${build}`, `indexador/indices/${build}`]) await collect(name);
  const integrity = Buffer.from(JSON.stringify({ schema: 1, build, assets }) + '\n');
  const integrityPath = `compat/${build}/integrity.json`;
  const manifest = validateCompatibility({
    contract_version: 1, dataset: 'bibliotheca-patristica', build, commit: build,
    indexador: { version: indexadorVersion, commit: indexadorCommit, format: 1 },
    schemas: SCHEMAS,
    search: { path: searchPath, indexes: search.indexes.map(({ id, path, build_id }) => ({ id, path, build_id })) },
    indices: { path: indicesPath, build_id: `sha256:${assets[`indexador/indices/${build}/index.map`].sha256}` },
    sidecars: { path: sidecarPath, indexes: Object.fromEntries(Object.entries(sidecars.indexes).map(([id, value]) => [id, value.build_id])) },
    integrity: { path: integrityPath, sha256: hash(integrity), bytes: integrity.length },
  });
  await mkdir(path.join(publicDir, `compat/${build}`), { recursive: true });
  await writeFile(path.join(publicDir, integrityPath), integrity);
  await writeFile(path.join(publicDir, 'compat.json'), JSON.stringify(manifest, null, 2) + '\n');
  return manifest;
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [publicDir = 'web/dist', build = process.env.GITHUB_SHA,
    indexadorVersion = process.env.INDEXADOR_VERSION, indexadorCommit = process.env.INDEXADOR_COMMIT] = process.argv.slice(2);
  const manifest = await buildCompatibility({ publicDir, build, indexadorVersion, indexadorCommit });
  console.log(`Published compatibility contract 1 for ${manifest.build}`);
}
