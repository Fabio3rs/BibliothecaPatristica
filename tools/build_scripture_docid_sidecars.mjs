#!/usr/bin/env node

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import zlib from 'node:zlib';

import { decodeScriptureLocations } from '../web/src/scripts/scripture-index.js';
import { encodeScriptureDocIdSidecar, scriptureLocationKey } from '../web/src/scripts/scripture-docids.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function parseArgs(argv) {
  const args = {
    publicDir: path.join(ROOT, 'web', 'public'),
    searchManifest: '',
    scriptureManifest: '',
    outputDir: '',
    minGlobalCoverage: 0.99,
    minBookCoverage: 0.95,
    validateOnly: false,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--public') args.publicDir = path.resolve(argv[++index]);
    else if (value === '--search-manifest') args.searchManifest = path.resolve(argv[++index]);
    else if (value === '--scripture-manifest') args.scriptureManifest = path.resolve(argv[++index]);
    else if (value === '--out') args.outputDir = path.resolve(argv[++index]);
    else if (value === '--min-global-coverage') args.minGlobalCoverage = Number(argv[++index]);
    else if (value === '--min-book-coverage') args.minBookCoverage = Number(argv[++index]);
    else if (value === '--validate-only') args.validateOnly = true;
    else if (value === '--help' || value === '-h') args.help = true;
    else throw new Error(`Argumento desconhecido: ${value}`);
  }
  args.searchManifest ||= path.join(args.publicDir, 'indexador', 'search', 'manifest.json');
  args.scriptureManifest ||= path.join(args.publicDir, 'scripture', 'v3', 'manifest.json');
  args.outputDir ||= path.join(path.dirname(args.searchManifest), 'scripture-docids');
  for (const [name, value] of [
    ['--min-global-coverage', args.minGlobalCoverage],
    ['--min-book-coverage', args.minBookCoverage],
  ]) {
    if (!Number.isFinite(value) || value < 0 || value > 1) {
      throw new Error(`${name} deve estar entre 0 e 1.`);
    }
  }
  return args;
}

function usage() {
  return `Uso: node tools/build_scripture_docid_sidecars.mjs [opções]

Gera mapas compactos página bíblica -> doc_id para filtrar a busca ampla antes de carregar documentos.

  --public web/public
  --search-manifest web/public/indexador/search/manifest.json
  --scripture-manifest web/public/scripture/v3/manifest.json
  --out web/public/indexador/search/<versão>/scripture-docids
  --min-global-coverage 0.99
  --min-book-coverage 0.95
  --validate-only`;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function readMaybeGzipJson(filePath) {
  const input = fs.readFileSync(filePath);
  const bytes = input[0] === 0x1f && input[1] === 0x8b ? zlib.gunzipSync(input) : input;
  return JSON.parse(bytes.toString('utf8'));
}

function writeJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
}

function writeJsonAtomic(filePath, value) {
  const temporary = `${filePath}.tmp-${process.pid}`;
  writeJson(temporary, value);
  fs.renameSync(temporary, filePath);
}

function coverageRatio(mapped, total) {
  return total > 0 ? mapped / total : 1;
}

function coverageSummary(books) {
  const summary = Object.values(books || {}).reduce((result, book) => ({
    pages_total: result.pages_total + Number(book.pages_total || 0),
    pages_mapped: result.pages_mapped + Number(book.pages_mapped || 0),
    pages_missing: result.pages_missing + Number(book.pages_missing || 0),
  }), { pages_total: 0, pages_mapped: 0, pages_missing: 0 });
  return {
    ...summary,
    ratio: coverageRatio(summary.pages_mapped, summary.pages_total),
  };
}

function resolvePublishedFile(outputDir, relativeUrl) {
  const root = path.resolve(outputDir);
  const target = path.resolve(root, String(relativeUrl || ''));
  if (target === root || !target.startsWith(`${root}${path.sep}`)) {
    throw new Error(`Caminho de sidecar inválido: ${relativeUrl}`);
  }
  return target;
}

function assertSafeOutputDir(outputDir, searchManifestPath) {
  const searchRoot = path.resolve(path.dirname(searchManifestPath));
  const target = path.resolve(outputDir);
  if (target === searchRoot || !target.startsWith(`${searchRoot}${path.sep}`)) {
    throw new Error('O diretório de sidecars deve ficar dentro da pasta do índice de busca.');
  }
}

export function validateSidecarPublication({
  searchManifest,
  scriptureManifest,
  sidecarManifest,
  outputDir,
  minGlobalCoverage = 0.99,
  minBookCoverage = 0.95,
}) {
  if (sidecarManifest?.schema !== 'bibliotheca-scripture-docids-v1') {
    throw new Error('Schema de sidecar bíblico inválido.');
  }
  const searchEntries = searchManifest?.indexes || [];
  const sidecarIndexes = sidecarManifest?.indexes || {};
  if (Object.keys(sidecarIndexes).length !== searchEntries.length) {
    throw new Error('Quantidade de índices diverge entre busca e sidecars bíblicos.');
  }
  const expectedBooks = Object.keys(scriptureManifest?.routes || {});
  const publication = { indexes: 0, books: 0, pages_total: 0, pages_mapped: 0, pages_missing: 0 };

  for (const entry of searchEntries) {
    const index = sidecarIndexes[entry.id];
    if (!entry?.build_id || !index?.build_id || entry.build_id !== index.build_id) {
      throw new Error(`Índice ${entry.id}: build_id ausente ou incompatível.`);
    }
    if (Number(index.documents) !== Number(entry.documents)) {
      throw new Error(`Índice ${entry.id}: contagem de documentos incompatível.`);
    }
    const books = index.books || {};
    if (Object.keys(books).length !== expectedBooks.length) {
      throw new Error(`Índice ${entry.id}: quantidade de livros bíblicos incompatível.`);
    }
    for (const bookKey of expectedBooks) {
      const book = books[bookKey];
      if (!book?.url) throw new Error(`Índice ${entry.id}: sidecar ausente para ${bookKey}.`);
      const total = Number(book.pages_total);
      const mapped = Number(book.pages_mapped);
      const missing = Number(book.pages_missing);
      if (![total, mapped, missing, Number(book.gzip_bytes), Number(book.raw_bytes)].every(Number.isSafeInteger)) {
        throw new Error(`Índice ${entry.id}/${bookKey}: métricas inválidas.`);
      }
      if (total < 0 || mapped < 0 || missing < 0 || mapped + missing !== total) {
        throw new Error(`Índice ${entry.id}/${bookKey}: cobertura inconsistente.`);
      }
      const ratio = coverageRatio(mapped, total);
      if (total > 0 && ratio < minBookCoverage) {
        throw new Error(`Índice ${entry.id}/${bookKey}: cobertura ${(ratio * 100).toFixed(3)}% abaixo do mínimo ${(minBookCoverage * 100).toFixed(3)}%.`);
      }
      const filePath = resolvePublishedFile(outputDir, book.url);
      const stat = fs.statSync(filePath);
      if (!stat.isFile() || stat.size !== book.gzip_bytes || stat.size < 1) {
        throw new Error(`Índice ${entry.id}/${bookKey}: arquivo publicado incompatível.`);
      }
    }
    const actualCoverage = coverageSummary(books);
    const declaredCoverage = index.coverage || actualCoverage;
    for (const field of ['pages_total', 'pages_mapped', 'pages_missing']) {
      if (Number(declaredCoverage[field]) !== actualCoverage[field]) {
        throw new Error(`Índice ${entry.id}: resumo de cobertura incompatível em ${field}.`);
      }
    }
    if (actualCoverage.ratio < minGlobalCoverage) {
      throw new Error(`Índice ${entry.id}: cobertura global ${(actualCoverage.ratio * 100).toFixed(3)}% abaixo do mínimo ${(minGlobalCoverage * 100).toFixed(3)}%.`);
    }
    publication.indexes += 1;
    publication.books += expectedBooks.length;
    publication.pages_total += actualCoverage.pages_total;
    publication.pages_mapped += actualCoverage.pages_mapped;
    publication.pages_missing += actualCoverage.pages_missing;
  }
  publication.ratio = coverageRatio(publication.pages_mapped, publication.pages_total);
  return publication;
}

function loadWasmFactory(runtimeDir) {
  const source = fs.readFileSync(path.join(runtimeDir, 'indexador_wasm.js'), 'utf8');
  return Function(`${source}\nreturn Module;`)();
}

function installLocalFetch() {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (!url.startsWith('file:')) throw new Error(`A geração recusou URL não local: ${url}`);
    return new Response(fs.readFileSync(fileURLToPath(url)), { status: 200 });
  };
}

function documentLocation(document) {
  const url = new URL(document.url, 'https://bibliotheca.invalid');
  const volumeId = url.searchParams.get('doc') || '';
  const page = Number(url.searchParams.get('page'));
  if (!volumeId || !Number.isInteger(page)) return null;
  return { volume_id: volumeId, page, doc_id: Number(document.id) };
}

function routeFile(scriptureManifestPath, route) {
  return path.join(path.dirname(scriptureManifestPath), String(route.url));
}

function collectShardLocations(shard) {
  const locations = new Map();
  for (const reference of shard.references || []) {
    for (const location of decodeScriptureLocations(shard, reference)) {
      locations.set(scriptureLocationKey(location.volume_id, location.page), location);
    }
  }
  return [...locations.values()];
}

async function loadDocuments({ entry, searchManifestPath, runtimeDir, wasmFactory, wasmBytes, createIndexadorPagefind }) {
  const searchRoot = path.dirname(searchManifestPath);
  const engine = createIndexadorPagefind({
    assetBaseUrl: pathToFileURL(runtimeDir).href,
    indexBaseUrl: pathToFileURL(path.join(searchRoot, entry.path)).href,
    wasmModuleLoader: () => wasmFactory({ wasmBinary: wasmBytes }),
    PAGE_SIZE: 2_000,
    MAX_PER_TOKEN: Math.max(Number(entry.documents || 0), 300_000),
    MAX_CONCURRENT_FETCHES: 8,
    prefixMatch: false,
    suggestions: false,
  });
  await engine.init();
  const collections = entry.collections?.length ? entry.collections : [entry.id];
  const query = collections.length === 1
    ? `(${collections[0]})`
    : `(${collections.map((value) => `(${value})`).join(' || ')})`;
  const result = await engine.search(query);
  const documents = [];
  for (let offset = 0; offset < result.total; offset += 2_000) {
    const batch = await result.getRange(offset, Math.min(result.total, offset + 2_000));
    for (const document of batch) {
      const location = documentLocation(document);
      if (location) documents.push(location);
    }
  }
  documents.sort((left, right) => left.doc_id - right.doc_id);
  if (documents.length !== result.total || (entry.documents && documents.length !== entry.documents)) {
    throw new Error(`Índice ${entry.id}: esperados ${entry.documents || result.total} documentos, mapeados ${documents.length}.`);
  }
  const hash = crypto.createHash('sha256');
  for (const item of documents) hash.update(`${item.doc_id}\0${item.volume_id}:${item.page}\n`);
  return { documents, buildId: `sha256:${hash.digest('hex')}` };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  const searchManifest = readJson(args.searchManifest);
  const scriptureManifest = readJson(args.scriptureManifest);
  if (args.validateOnly) {
    const sidecarManifest = readJson(path.join(args.outputDir, 'manifest.json'));
    const publication = validateSidecarPublication({
      searchManifest,
      scriptureManifest,
      sidecarManifest,
      outputDir: args.outputDir,
      minGlobalCoverage: args.minGlobalCoverage,
      minBookCoverage: args.minBookCoverage,
    });
    console.log(`[docids] validação concluída: ${publication.pages_mapped}/${publication.pages_total} associações (${(publication.ratio * 100).toFixed(3)}%).`);
    return;
  }
  const runtimeDir = path.join(args.publicDir, 'indexador', 'runtime');
  const runtimeModule = await import(pathToFileURL(path.join(runtimeDir, 'indexador-pagefind.js')).href);
  if (typeof runtimeModule.createIndexadorPagefind !== 'function') {
    throw new Error('Runtime indexador-pagefind.js não exporta createIndexadorPagefind.');
  }
  const wasmFactory = loadWasmFactory(runtimeDir);
  const wasmBytes = fs.readFileSync(path.join(runtimeDir, 'indexador_wasm.wasm'));
  installLocalFetch();
  assertSafeOutputDir(args.outputDir, args.searchManifest);
  const temporaryOutputDir = `${args.outputDir}.tmp-${process.pid}-${Date.now()}`;
  fs.mkdirSync(temporaryOutputDir, { recursive: true });

  const sidecarManifest = {
    schema: 'bibliotheca-scripture-docids-v1',
    generated_at: new Date().toISOString(),
    scripture_manifest: path.relative(args.publicDir, args.scriptureManifest).split(path.sep).join('/'),
    indexes: {},
  };

  try {
    for (const entry of searchManifest.indexes || []) {
      console.log(`[docids] mapeando doc_ids do índice pronto ${entry.id} (sem reindexar)...`);
      const loaded = await loadDocuments({
        entry,
        searchManifestPath: args.searchManifest,
        runtimeDir,
        wasmFactory,
        wasmBytes,
        createIndexadorPagefind: runtimeModule.createIndexadorPagefind,
      });
      entry.build_id = loaded.buildId;
      const byLocation = new Map(loaded.documents.map((item) => [scriptureLocationKey(item.volume_id, item.page), item]));
      const books = {};
      const indexDir = path.join(temporaryOutputDir, String(entry.id).toLocaleLowerCase());
      fs.mkdirSync(indexDir, { recursive: true });
      for (const [bookKey, route] of Object.entries(scriptureManifest.routes || {})) {
        const shard = readMaybeGzipJson(routeFile(args.scriptureManifest, route));
        const locations = collectShardLocations(shard);
        const mapped = locations.map((location) => byLocation.get(scriptureLocationKey(location.volume_id, location.page))).filter(Boolean);
        const raw = encodeScriptureDocIdSidecar(shard.volumes || [], mapped);
        const gzip = zlib.gzipSync(raw, { level: 9 });
        const digest = crypto.createHash('sha256').update(gzip).digest('hex').slice(0, 16);
        const filename = `${bookKey.replaceAll(' ', '-')}.${digest}.bin.gz`;
        fs.writeFileSync(path.join(indexDir, filename), gzip);
        books[bookKey] = {
          url: `${String(entry.id).toLocaleLowerCase()}/${filename}`,
          raw_bytes: raw.length,
          gzip_bytes: gzip.length,
          pages_total: locations.length,
          pages_mapped: mapped.length,
          pages_missing: locations.length - mapped.length,
        };
      }
      sidecarManifest.indexes[entry.id] = {
        build_id: loaded.buildId,
        documents: loaded.documents.length,
        books,
        coverage: coverageSummary(books),
      };
    }

    writeJson(path.join(temporaryOutputDir, 'manifest.json'), sidecarManifest);
    const publication = validateSidecarPublication({
      searchManifest,
      scriptureManifest,
      sidecarManifest,
      outputDir: temporaryOutputDir,
      minGlobalCoverage: args.minGlobalCoverage,
      minBookCoverage: args.minBookCoverage,
    });
    fs.rmSync(args.outputDir, { recursive: true, force: true });
    fs.renameSync(temporaryOutputDir, args.outputDir);
    writeJsonAtomic(args.searchManifest, searchManifest);
    console.log(`[docids] pronto: ${path.relative(ROOT, args.outputDir)}; ${publication.pages_mapped}/${publication.pages_total} associações (${(publication.ratio * 100).toFixed(3)}%).`);
  } catch (error) {
    fs.rmSync(temporaryOutputDir, { recursive: true, force: true });
    throw error;
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) await main();
