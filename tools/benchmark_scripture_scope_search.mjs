#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import zlib from 'node:zlib';

import { createIndexadorPagefind } from '../web/public/indexador/runtime/indexador-pagefind.js';
import {
  decodeScriptureSegments,
  formatScriptureLocator,
  matchScriptureShard,
} from '../web/src/scripts/scripture-index.js';

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_REFERENCES = [
  'proverbios:22:21',
  'galatas:6:9',
  'joao:3:16',
  'atos:13',
  'joao:1:14',
  'joao:1:1',
];

function parseArgs(argv) {
  const args = {
    publicDir: path.join(REPOSITORY_ROOT, 'web', 'public'),
    term: 'agostinho',
    references: [],
    targetHits: 10,
    scanLimit: 2_000,
    scanBatch: 100,
    maxPairDsl: 300,
    docIdPoc: true,
    output: '',
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--public') args.publicDir = path.resolve(argv[++index]);
    else if (value === '--term') args.term = String(argv[++index] || '').trim();
    else if (value === '--reference') args.references.push(String(argv[++index] || '').trim());
    else if (value === '--target-hits') args.targetHits = Number(argv[++index]);
    else if (value === '--scan-limit') args.scanLimit = Number(argv[++index]);
    else if (value === '--scan-batch') args.scanBatch = Number(argv[++index]);
    else if (value === '--max-pair-dsl') args.maxPairDsl = Number(argv[++index]);
    else if (value === '--no-doc-id-poc') args.docIdPoc = false;
    else if (value === '--output') args.output = path.resolve(argv[++index]);
    else if (value === '--help' || value === '-h') args.help = true;
    else throw new Error(`Argumento desconhecido: ${value}`);
  }
  if (!args.references.length) args.references = DEFAULT_REFERENCES;
  for (const [name, number] of Object.entries({
    targetHits: args.targetHits,
    scanLimit: args.scanLimit,
    scanBatch: args.scanBatch,
    maxPairDsl: args.maxPairDsl,
  })) {
    if (!Number.isInteger(number) || number <= 0) throw new Error(`--${name} deve ser inteiro positivo`);
  }
  if (!args.term) throw new Error('--term não pode ser vazio');
  return args;
}

function usage() {
  return `Uso: node tools/benchmark_scripture_scope_search.mjs [opções]

Mede o fluxo índice de Escrituras -> busca ampla no PatrologiaIndexer sem browser.

Opções:
  --term agostinho          termo da busca ampla
  --reference joao:3:16     referência; pode ser repetida
  --target-hits 10          páginas exatas desejadas
  --scan-limit 2000         máximo de candidatos lidos por estratégia
  --scan-batch 100          documentos lidos por lote
  --max-pair-dsl 300        não gera DSL de pares acima deste limite
  --no-doc-id-poc           pula Set/vetor ordenado/bitset sobre doc_id
  --output resultado.json   também grava o relatório JSON
  --public web/public       raiz dos artefatos publicados`;
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, 'utf8'));
}

function parseReferenceSpec(value) {
  const separator = value.indexOf(':');
  if (separator <= 0 || separator === value.length - 1) {
    throw new Error(`Referência inválida: ${value}; use livro:capítulo[:versículo]`);
  }
  return {
    book: value.slice(0, separator).trim().replaceAll('-', ' '),
    locator: value.slice(separator + 1).trim(),
  };
}

function median(values) {
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function round(value, digits = 3) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function locationKey(volumeId, page) {
  return `${volumeId}:${page}`;
}

function documentKey(document) {
  const url = new URL(document.url, 'https://bibliotheca.invalid');
  return locationKey(url.searchParams.get('doc') || '', Number(url.searchParams.get('page')));
}

function groupTerms(values) {
  const terms = [...new Set(values.map((value) => String(value).trim()).filter(Boolean))];
  if (!terms.length) return '';
  return terms.length === 1 ? `(${terms[0]})` : `(${terms.map((term) => `(${term})`).join(' || ')})`;
}

function buildVolumeQuery(term, locations) {
  return `(${term}) && ${groupTerms(locations.map((location) => location.volume_id))}`;
}

function buildPairQuery(term, locations) {
  const clauses = locations.map((location) => (
    `((${location.volume_id}) && (${location.page}))`
  ));
  return `(${term}) && (${clauses.join(' || ')})`;
}

function snapshotMetrics(metrics) {
  return {
    requests: metrics.requests,
    wire_bytes: metrics.wireBytes,
    decoded_bytes: metrics.decodedBytes,
    files: metrics.files.length,
  };
}

function diffMetrics(after, before) {
  return Object.fromEntries(Object.keys(after).map((key) => [key, after[key] - before[key]]));
}

function createFetchMetrics() {
  return { requests: 0, wireBytes: 0, decodedBytes: 0, files: [] };
}

function createProgressMetrics() {
  return { events: 0, by_phase: {}, last_event: null };
}

function recordProgress(metrics, event) {
  metrics.events += 1;
  const key = `${event.phase}:${event.state}`;
  metrics.by_phase[key] = (metrics.by_phase[key] || 0) + 1;
  metrics.last_event = event;
}

function loadWasmFactory(runtimeDir) {
  const source = fs.readFileSync(path.join(runtimeDir, 'indexador_wasm.js'), 'utf8');
  return Function(`${source}\nreturn Module;`)();
}

function installLocalFetch(activeMetrics) {
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (!url.startsWith('file:')) throw new Error(`Benchmark recusou URL não local: ${url}`);
    const filePath = fileURLToPath(url);
    const bytes = fs.readFileSync(filePath);
    const gzip = bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
    const decodedBytes = gzip ? zlib.gunzipSync(bytes).length : bytes.length;
    const metrics = activeMetrics.current;
    metrics.requests += 1;
    metrics.wireBytes += bytes.length;
    metrics.decodedBytes += decodedBytes;
    metrics.files.push({ file: path.basename(filePath), wire_bytes: bytes.length, decoded_bytes: decodedBytes });
    return new Response(bytes, { status: 200 });
  };
}

async function createEngine({ runtimeDir, indexDir, wasmFactory, wasmBytes, activeMetrics }) {
  const metrics = createFetchMetrics();
  const progressMetrics = createProgressMetrics();
  activeMetrics.current = metrics;
  const loader = () => wasmFactory({ wasmBinary: wasmBytes });
  const engine = createIndexadorPagefind({
    assetBaseUrl: pathToFileURL(runtimeDir).href,
    indexBaseUrl: pathToFileURL(indexDir).href,
    wasmModuleLoader: loader,
    PAGE_SIZE: 100,
    MAX_PER_TOKEN: 10_000,
    MAX_CONCURRENT_FETCHES: 6,
    prefixMatch: true,
    prefixMinLength: 3,
    prefixMaxTerms: 32,
    prefixMaxPerTerm: 10_000,
    onProgress: (event) => recordProgress(progressMetrics, event),
  });
  globalThis.gc?.();
  const heapBefore = process.memoryUsage().heapUsed;
  const started = performance.now();
  await engine.init();
  const initMs = performance.now() - started;
  const heapAfter = process.memoryUsage().heapUsed;
  return {
    engine,
    metrics,
    progressMetrics,
    init: {
      ms: round(initMs),
      heap_delta_bytes: heapAfter - heapBefore,
      network: snapshotMetrics(metrics),
    },
  };
}

async function scanExactMatches(result, allowed, options) {
  const maximum = Math.min(result.total, options.scanLimit);
  let scanned = 0;
  let exact = 0;
  const sample = [];
  while (scanned < maximum && exact < options.targetHits) {
    const end = Math.min(maximum, scanned + options.scanBatch);
    const documents = await result.getRange(scanned, end);
    for (const document of documents) {
      if (!allowed.has(documentKey(document))) continue;
      exact += 1;
      if (sample.length < 5) sample.push(documentKey(document));
    }
    scanned = end;
  }
  return { scanned, exact_hits: exact, exact_hit_sample: sample, truncated: scanned < result.total };
}

async function benchmarkStrategy(context, strategy, query, locations) {
  const allowed = new Set(locations.map((location) => locationKey(location.volume_id, location.page)));
  const created = await createEngine(context);
  const beforeSearch = snapshotMetrics(created.metrics);
  globalThis.gc?.();
  const heapBefore = process.memoryUsage().heapUsed;
  const searchStarted = performance.now();
  try {
    const result = await created.engine.search(query);
    const searchMs = performance.now() - searchStarted;
    const afterSearch = snapshotMetrics(created.metrics);
    const scanStarted = performance.now();
    const scan = await scanExactMatches(result, allowed, context.options);
    const scanMs = performance.now() - scanStarted;
    const afterScan = snapshotMetrics(created.metrics);
    const heapAfter = process.memoryUsage().heapUsed;
    return {
      strategy,
      query_bytes: Buffer.byteLength(query),
      init: created.init,
      progress: created.progressMetrics,
      search: {
        ms: round(searchMs),
        candidates: result.total,
        heap_delta_bytes: heapAfter - heapBefore,
        network: diffMetrics(afterSearch, beforeSearch),
      },
      scan: {
        ...scan,
        ms: round(scanMs),
        network: diffMetrics(afterScan, afterSearch),
      },
    };
  } catch (error) {
    return {
      strategy,
      query_bytes: Buffer.byteLength(query),
      init: created.init,
      error: error?.message || String(error),
      failed_after_ms: round(performance.now() - searchStarted),
      network: diffMetrics(snapshotMetrics(created.metrics), beforeSearch),
    };
  }
}

async function buildDocumentIdMap(context) {
  const created = await createEngine({ ...context, options: context.options });
  const beforeSearch = snapshotMetrics(created.metrics);
  const searchStarted = performance.now();
  const result = await created.engine.search('((PG) || (PL) || (PO))');
  const searchMs = performance.now() - searchStarted;
  const afterSearch = snapshotMetrics(created.metrics);
  const documentsStarted = performance.now();
  const byLocation = new Map();
  const batchSize = 2_000;
  for (let offset = 0; offset < result.total; offset += batchSize) {
    const documents = await result.getRange(offset, Math.min(result.total, offset + batchSize));
    for (const document of documents) byLocation.set(documentKey(document), document.id);
  }
  const afterDocuments = snapshotMetrics(created.metrics);
  return {
    byLocation,
    report: {
      purpose: 'Prototype-only page key to search doc_id map. In production these IDs should be emitted during the static build, not reconstructed in the browser.',
      documents: result.total,
      mapped_locations: byLocation.size,
      init: created.init,
      collection_search_ms: round(searchMs),
      collection_search_network: diffMetrics(afterSearch, beforeSearch),
      document_map_ms: round(performance.now() - documentsStarted),
      document_map_network: diffMetrics(afterDocuments, afterSearch),
      progress: created.progressMetrics,
    },
  };
}

function medianFilterResult(result, allowedIds, algorithm) {
  const timings = [];
  let filtered = null;
  for (let iteration = 0; iteration < 21; iteration += 1) {
    const started = performance.now();
    filtered = result.filterDocumentIds(allowedIds, { algorithm });
    timings.push(performance.now() - started);
  }
  return { filtered, median_ms: round(median(timings)) };
}

function encodeDeltaVarints(values) {
  const sorted = [...new Set(values)].sort((left, right) => left - right);
  const bytes = [];
  let previous = 0;
  for (const value of sorted) {
    let delta = value - previous;
    previous = value;
    while (delta >= 0x80) {
      bytes.push((delta & 0x7f) | 0x80);
      delta >>>= 7;
    }
    bytes.push(delta);
  }
  return Buffer.from(bytes);
}

function docIdTransportStats(values) {
  const unique = [...new Set(values)];
  const json = Buffer.from(JSON.stringify(unique));
  const deltaVarints = encodeDeltaVarints(unique);
  const maximum = unique.reduce((current, value) => Math.max(current, value), 0);
  const bitset = Buffer.alloc(Math.ceil((maximum + 1) / 8));
  for (const value of unique) bitset[value >> 3] |= 1 << (value & 7);
  return {
    json_bytes: json.length,
    json_gzip_bytes: zlib.gzipSync(json, { level: 9 }).length,
    delta_varint_bytes: deltaVarints.length,
    delta_varint_gzip_bytes: zlib.gzipSync(deltaVarints, { level: 9 }).length,
    bitset_bytes: bitset.length,
    bitset_gzip_bytes: zlib.gzipSync(bitset, { level: 9 }).length,
  };
}

async function benchmarkDocumentIdAlgorithms(context, locations, documentIds) {
  const allowedKeys = new Set(locations.map((location) => locationKey(location.volume_id, location.page)));
  const allowedIds = [];
  for (const key of allowedKeys) {
    const id = documentIds.get(key);
    if (id !== undefined) allowedIds.push(id);
  }
  const created = await createEngine({ ...context, options: context.options });
  const beforeSearch = snapshotMetrics(created.metrics);
  const query = `(${context.options.term}) && (((PG) || (PL) || (PO)))`;
  const searchStarted = performance.now();
  const result = await created.engine.search(query);
  const searchMs = performance.now() - searchStarted;
  const afterSearch = snapshotMetrics(created.metrics);
  const algorithms = {};
  let bitsetResult = null;
  for (const algorithm of ['set', 'sorted', 'bitset']) {
    const measured = medianFilterResult(result, allowedIds, algorithm);
    algorithms[algorithm] = {
      median_ms: measured.median_ms,
      candidates: measured.filtered.total,
      representation_bytes: algorithm === 'bitset'
        ? Math.ceil(((Math.max(0, ...allowedIds) + 1) / 8))
        : algorithm === 'sorted'
          ? new Set(allowedIds).size * 4
          : null,
    };
    if (algorithm === 'bitset') bitsetResult = measured.filtered;
  }
  const beforeLoad = snapshotMetrics(created.metrics);
  const loadStarted = performance.now();
  const documents = await bitsetResult.getRange(0, context.options.targetHits);
  const afterLoad = snapshotMetrics(created.metrics);
  return {
    scope_pages: allowedKeys.size,
    mapped_doc_ids: allowedIds.length,
    missing_doc_ids: allowedKeys.size - allowedIds.length,
    transport_encodings: docIdTransportStats(allowedIds),
    base_search: {
      query_bytes: Buffer.byteLength(query),
      ms: round(searchMs),
      candidates: result.total,
      network: diffMetrics(afterSearch, beforeSearch),
    },
    algorithms,
    exact_results: {
      candidates: bitsetResult.total,
      loaded: documents.length,
      all_verified_in_scope: documents.every((document) => allowedKeys.has(documentKey(document))),
      ms: round(performance.now() - loadStarted),
      network: diffMetrics(afterLoad, beforeLoad),
    },
    progress: created.progressMetrics,
  };
}

function runtimeCoreStats(publicDir) {
  const relativeFiles = [
    'indexador/runtime/indexador-pagefind.js',
    'indexador/runtime/indexador-lib.js',
    'indexador/runtime/query-dsl.mjs',
    'indexador/runtime/indexador_wasm.js',
    'indexador/runtime/indexador_wasm.wasm',
    'indexador/search/manifest.json',
    'indexador/search/all/index.map',
  ];
  const files = relativeFiles.map((relative) => ({
    file: relative,
    bytes: fs.statSync(path.join(publicDir, relative)).size,
  }));
  return { raw_bytes: files.reduce((sum, item) => sum + item.bytes, 0), files };
}

function scriptureScopeDistribution(publicDir, manifest) {
  const rows = [];
  for (const [book, route] of Object.entries(manifest.routes || {})) {
    const compressed = fs.readFileSync(path.join(publicDir, 'scripture', 'v3', route.url));
    const shard = JSON.parse(zlib.gunzipSync(compressed));
    for (const reference of shard.references || []) {
      const pages = (reference[2] || []).reduce(
        (sum, volumePosting) => sum + Math.floor((volumePosting[1] || []).length / 2),
        0,
      );
      rows.push({
        reference: `${route.label} ${formatScriptureLocator(decodeScriptureSegments(reference[1]))}`,
        book,
        pages,
        volumes: (reference[2] || []).length,
      });
    }
  }
  rows.sort((left, right) => left.pages - right.pages || left.reference.localeCompare(right.reference));
  const percentile = (fraction) => rows[Math.min(rows.length - 1, Math.floor(fraction * (rows.length - 1)))];
  return {
    references: rows.length,
    percentiles: Object.fromEntries([0.5, 0.75, 0.9, 0.95, 0.99].map((fraction) => [
      `p${Math.round(fraction * 100)}`,
      percentile(fraction),
    ])),
    references_over_50_pages: rows.filter((row) => row.pages > 50).length,
    references_over_100_pages: rows.filter((row) => row.pages > 100).length,
    references_over_500_pages: rows.filter((row) => row.pages > 500).length,
    references_over_1000_pages: rows.filter((row) => row.pages > 1_000).length,
    largest: rows.at(-1),
  };
}

function loadCitation(publicDir, manifest, spec) {
  const route = manifest.routes?.[spec.book];
  if (!route?.url) throw new Error(`Livro ausente do manifesto: ${spec.book}`);
  const shardPath = path.join(publicDir, 'scripture', 'v3', route.url);
  const compressed = fs.readFileSync(shardPath);
  const timings = [];
  let match = null;
  let rawBytes = 0;
  for (let iteration = 0; iteration < 7; iteration += 1) {
    const started = performance.now();
    const raw = zlib.gunzipSync(compressed);
    const shard = JSON.parse(raw);
    match = matchScriptureShard(shard, spec.locator, {
      matchMode: 'exact',
      bookLabel: route.label,
    })[0] || null;
    timings.push(performance.now() - started);
    rawBytes = raw.length;
  }
  if (!match) throw new Error(`Referência exata não encontrada: ${spec.book}:${spec.locator}`);
  return {
    match,
    citation: {
      reference: match.reference,
      book: spec.book,
      locator: spec.locator,
      pages: match.page_count,
      volumes: match.volume_count,
      manifest_bytes: fs.statSync(path.join(publicDir, 'scripture', 'v3', 'manifest.json')).size,
      shard_wire_bytes: compressed.length,
      shard_decoded_bytes: rawBytes,
      decode_parse_match_median_ms: round(median(timings)),
      scope_set_estimated_bytes: Buffer.byteLength(JSON.stringify(
        match.locations.map((location) => [location.volume_id, location.page]),
      )),
    },
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  const publicDir = fs.realpathSync(args.publicDir);
  const runtimeDir = path.join(publicDir, 'indexador', 'runtime');
  const indexDir = path.join(publicDir, 'indexador', 'search', 'all');
  const manifest = readJson(path.join(publicDir, 'scripture', 'v3', 'manifest.json'));
  const wasmFactory = loadWasmFactory(runtimeDir);
  const wasmBytes = fs.readFileSync(path.join(runtimeDir, 'indexador_wasm.wasm'));
  const activeMetrics = { current: createFetchMetrics() };
  installLocalFetch(activeMetrics);

  const report = {
    generated_at: new Date().toISOString(),
    method: 'Local Node benchmark over the production Scripture v3 and PatrologiaIndexer artifacts.',
    caveat: 'Timings exclude network latency; wire bytes are exact local artifact sizes and JS/JSON may receive additional HTTP compression in production.',
    config: {
      term: args.term,
      target_hits: args.targetHits,
      scan_limit: args.scanLimit,
      scan_batch: args.scanBatch,
      max_pair_dsl: args.maxPairDsl,
      doc_id_poc: args.docIdPoc,
    },
    runtime_cold_core: runtimeCoreStats(publicDir),
    scripture_scope_distribution: scriptureScopeDistribution(publicDir, manifest),
    cases: [],
  };

  const context = {
    runtimeDir,
    indexDir,
    wasmFactory,
    wasmBytes,
    activeMetrics,
    options: args,
  };
  const documentIdPoc = args.docIdPoc ? await buildDocumentIdMap(context) : null;
  if (documentIdPoc) report.doc_id_map_prototype = documentIdPoc.report;
  for (const rawReference of args.references) {
    const spec = parseReferenceSpec(rawReference);
    const loaded = loadCitation(publicDir, manifest, spec);
    const locations = loaded.match.locations;
    const strategies = [];
    strategies.push(await benchmarkStrategy(
      context,
      'unfiltered_then_exact_page_postfilter',
      `(${args.term}) && (((PG) || (PL) || (PO)))`,
      locations,
    ));
    strategies.push(await benchmarkStrategy(
      context,
      'volume_prefilter_then_exact_page_postfilter',
      buildVolumeQuery(args.term, locations),
      locations,
    ));
    if (locations.length <= args.maxPairDsl) {
      strategies.push(await benchmarkStrategy(
        context,
        'volume_page_pairs_encoded_in_dsl',
        buildPairQuery(args.term, locations),
        locations,
      ));
    } else {
      strategies.push({
        strategy: 'volume_page_pairs_encoded_in_dsl',
        skipped: true,
        reason: `${locations.length} pares excedem --max-pair-dsl=${args.maxPairDsl}`,
        estimated_query_bytes: Buffer.byteLength(buildPairQuery(args.term, locations)),
      });
    }
    const docIdAlgorithms = documentIdPoc
      ? await benchmarkDocumentIdAlgorithms(context, locations, documentIdPoc.byLocation)
      : null;
    report.cases.push({
      citation: loaded.citation,
      strategies,
      ...(docIdAlgorithms ? { doc_id_intersection: docIdAlgorithms } : {}),
    });
    console.error(`[OK] ${loaded.citation.reference}: ${loaded.citation.pages} páginas, ${loaded.citation.volumes} volumes`);
  }

  const serialized = `${JSON.stringify(report, null, 2)}\n`;
  if (args.output) fs.writeFileSync(args.output, serialized);
  process.stdout.write(serialized);
}

main().catch((error) => {
  console.error(error?.stack || error);
  process.exitCode = 1;
});
