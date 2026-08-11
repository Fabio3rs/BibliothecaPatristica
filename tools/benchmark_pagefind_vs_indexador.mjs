#!/usr/bin/env node

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { pathToFileURL } from 'node:url';

import { readJsonMaybeGz } from './json_io.mjs';

const ROOT = path.resolve(import.meta.dirname, '..');
const PROGRESS_INTERVAL = 5000;

function progress(message) {
  process.stderr.write(`[benchmark] ${message}\n`);
}

function parseArgs(argv) {
  const args = {
    publicDir: path.join(ROOT, 'web', 'public'),
    indexadorBinary: path.join(
      ROOT,
      '.work',
      'PatrologiaIndexer',
      'build-codex-clang19',
      'indexador',
    ),
    volumes: ['PG001', 'PL001', 'PO002'],
    rpcBatchSize: 250,
    maxTermsPerShard: 5000,
    maxBytesPerShard: 307200,
    documentsPerShard: 5000,
    minImportance: 2.01,
    minCount: 1,
    pagefindChunkSize: null,
    outputRoot: null,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--public') args.publicDir = path.resolve(argv[++i]);
    else if (arg === '--indexador-binary') args.indexadorBinary = path.resolve(argv[++i]);
    else if (arg === '--volumes') args.volumes = argv[++i].split(',').map((v) => v.trim()).filter(Boolean);
    else if (arg === '--rpc-batch-size') args.rpcBatchSize = Number(argv[++i]);
    else if (arg === '--max-terms-per-shard') args.maxTermsPerShard = Number(argv[++i]);
    else if (arg === '--max-bytes-per-shard') args.maxBytesPerShard = Number(argv[++i]);
    else if (arg === '--documents-per-shard') args.documentsPerShard = Number(argv[++i]);
    else if (arg === '--min-importance') args.minImportance = Number(argv[++i]);
    else if (arg === '--min-count') args.minCount = Number(argv[++i]);
    else if (arg === '--pagefind-chunk-size') args.pagefindChunkSize = Number(argv[++i]);
    else if (arg === '--output-root') args.outputRoot = path.resolve(argv[++i]);
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Argumento desconhecido: ${arg}`);
  }

  for (const [name, value] of [
    ['rpc-batch-size', args.rpcBatchSize],
    ['max-terms-per-shard', args.maxTermsPerShard],
    ['max-bytes-per-shard', args.maxBytesPerShard],
    ['documents-per-shard', args.documentsPerShard],
    ['min-count', args.minCount],
  ]) {
    if (!Number.isFinite(value) || value <= 0) throw new Error(`--${name} deve ser positivo`);
  }
  if (!Number.isFinite(args.minImportance) || args.minImportance < 0) {
    throw new Error('--min-importance deve ser finito e não negativo');
  }
  if (args.pagefindChunkSize !== null &&
      (!Number.isFinite(args.pagefindChunkSize) || args.pagefindChunkSize <= 0)) {
    throw new Error('--pagefind-chunk-size deve ser positivo');
  }
  if (!args.volumes.length) throw new Error('--volumes não pode ser vazio');
  return args;
}

function usage() {
  return `Uso: node tools/benchmark_pagefind_vs_indexador.mjs [opções]

Compara Pagefind e PatrologiaIndexer com os mesmos registros enriquecidos.
Por padrão usa uma amostra estratificada: PG001, PL001 e PO002.

  --volumes PG001,PL001,PO002
  --public web/public
  --indexador-binary .work/PatrologiaIndexer/build-codex-clang19/indexador
  --rpc-batch-size 250
  --min-importance 2.01
  --min-count 1
  --max-terms-per-shard 5000
  --max-bytes-per-shard 307200
  --documents-per-shard 5000
  --pagefind-chunk-size 20000
  --output-root /tmp/benchmark-explicito
`;
}

function normalizeWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function extractBookName(label) {
  if (!label || typeof label !== 'string') return null;
  let result = label.replace(/\s*\([^)]*\)\s*$/g, '');
  result = result.replace(/[:;,-]+\s*$/g, '').trim();
  result = result.split(/\/|-|—/)[0].trim();
  return result || null;
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function usableKeyword(meta, minCount) {
  if (!meta) return false;
  if (meta.iscit) return Boolean(meta.label);
  if (meta.count !== undefined && meta.count < minCount) return false;
  return Boolean(meta.label);
}

function buildRecord(keywordMap, docId, page, minCount) {
  const keywordMetas = [];
  const seenLabels = new Set();
  for (const keywordId of page.keyword_ids || []) {
    const meta = keywordMap.get(keywordId) || { id: keywordId, label: keywordId };
    if (!usableKeyword(meta, minCount) || seenLabels.has(meta.label)) continue;
    seenLabels.add(meta.label);
    keywordMetas.push(meta);
  }

  const keywordLabels = keywordMetas.map((meta) => meta.label);
  const bookNames = unique(keywordMetas
    .filter((meta) => meta.iscit)
    .map((meta) => extractBookName(meta.label)));
  const topKeywords = keywordLabels.slice(0, 3);
  const hint = topKeywords.length
    ? topKeywords.join(' • ')
    : unique([page.author, page.work]).join(' — ');
  const title = hint
    ? `${docId} p.${page.page} — ${hint}`.slice(0, 220)
    : `${docId} p.${page.page}`;
  const content = normalizeWhitespace(unique([
    title,
    page.summary_page,
    page.summary_global,
    page.author,
    page.work,
    keywordLabels.join(' '),
    bookNames.join(' '),
  ]).join(' '));
  const filters = {
    collection: [docId.slice(0, 2)],
    volume: [docId],
  };
  if (bookNames.length) filters.book = bookNames;

  const meta = {
    title,
    author: page.author || '',
    work: page.work || '',
    doc: docId,
    page: String(page.page),
    collection: docId.slice(0, 2),
    rawUrl: page.raw?.url || '',
  };
  if (keywordLabels.length) meta.keywords = keywordLabels.slice(0, 10).join(' • ');
  if (bookNames.length) meta.books = bookNames.join(' • ');

  return {
    url: `/BibliothecaPatristica/viewer?doc=${encodeURIComponent(docId)}&page=${encodeURIComponent(String(page.page))}`,
    content,
    language: 'pt',
    meta,
    filters,
  };
}

async function loadRecords(publicDir, volumeIds, minCount) {
  const [volumesData, keywordsData] = await Promise.all([
    readJsonMaybeGz(path.join(publicDir, 'volumes.json')),
    readJsonMaybeGz(path.join(publicDir, 'dict', 'keywords.json')),
  ]);
  const volumeMap = new Map((volumesData.volumes || []).map((volume) => [volume.id, volume]));
  const keywordMap = new Map((keywordsData.items || []).map((item) => [item.id, item]));
  const records = [];
  const perVolume = {};

  for (const [volumeIndex, volumeId] of volumeIds.entries()) {
    const volume = volumeMap.get(volumeId);
    if (!volume) throw new Error(`Volume não encontrado em volumes.json: ${volumeId}`);
    const metadata = await readJsonMaybeGz(path.join(publicDir, volume.meta_url));
    let count = 0;
    for (const block of metadata.page_blocks || []) {
      const blockData = await readJsonMaybeGz(path.join(publicDir, block.file));
      for (const page of blockData.pages || []) {
        records.push(buildRecord(keywordMap, volumeId, page, minCount));
        count += 1;
      }
    }
    perVolume[volumeId] = count;
    if ((volumeIndex + 1) % 10 === 0 || volumeIndex + 1 === volumeIds.length) {
      progress(`carregados ${volumeIndex + 1}/${volumeIds.length} volumes (${records.length} páginas)`);
    }
  }

  return { records, perVolume };
}

function resolvePagefindModule(publicDir) {
  const candidates = [
    path.resolve(publicDir, '..', 'node_modules', 'pagefind', 'lib', 'index.js'),
    path.join(ROOT, 'web', 'node_modules', 'pagefind', 'lib', 'index.js'),
  ];
  const found = candidates.find((candidate) => fs.existsSync(candidate));
  if (!found) throw new Error('Pagefind local não encontrado em web/node_modules');
  return found;
}

async function buildPagefind(records, publicDir, outputDir, chunkSize) {
  if (chunkSize !== null) {
    process.env.PAGEFIND_UNSTABLE_INDEX_CHUNK_SIZE = String(chunkSize);
  } else {
    delete process.env.PAGEFIND_UNSTABLE_INDEX_CHUNK_SIZE;
  }
  const modulePath = resolvePagefindModule(publicDir);
  const pagefind = await import(pathToFileURL(modulePath).href);
  const started = performance.now();
  const { index, errors } = await pagefind.createIndex({
    rootSelector: null,
    writePlayground: false,
    keepIndexUrl: false,
    site: '/BibliothecaPatristica',
  });
  if (errors?.length) throw new Error(`Pagefind createIndex: ${errors.join('; ')}`);

  const addStarted = performance.now();
  for (const [recordIndex, record] of records.entries()) {
    const result = await index.addCustomRecord(record);
    if (result?.errors?.length) {
      throw new Error(`Pagefind addCustomRecord: ${result.errors.join('; ')}`);
    }
    if ((recordIndex + 1) % PROGRESS_INTERVAL === 0) {
      progress(`Pagefind: ${recordIndex + 1}/${records.length} registros adicionados`);
    }
  }
  const addFinished = performance.now();
  const writeStarted = performance.now();
  await index.writeFiles({ outputPath: outputDir });
  const writeFinished = performance.now();
  await pagefind.close();

  return {
    total_ms: writeFinished - started,
    add_records_ms: addFinished - addStarted,
    write_files_ms: writeFinished - writeStarted,
  };
}

class RpcClient {
  constructor(binary, args) {
    this.child = spawn(binary, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    this.nextId = 0;
    this.pending = new Map();
    this.stderr = '';
    this.exitPromise = new Promise((resolve, reject) => {
      this.child.once('error', reject);
      this.child.once('exit', (code, signal) => resolve({ code, signal }));
    });
    this.child.stderr.setEncoding('utf8');
    this.child.stderr.on('data', (chunk) => { this.stderr += chunk; });
    const lines = readline.createInterface({ input: this.child.stdout });
    lines.on('line', (line) => {
      let response;
      try {
        response = JSON.parse(line);
      } catch (error) {
        this.rejectAll(new Error(`Resposta RPC inválida: ${line}\n${error.message}`));
        return;
      }
      const pending = this.pending.get(response.message_id);
      if (!pending) return;
      this.pending.delete(response.message_id);
      const payload = response.payload || {};
      if (payload.type === 'Error') pending.reject(new Error(payload.message || 'Erro RPC'));
      else pending.resolve(payload);
    });
    this.child.once('exit', (code, signal) => {
      if (this.pending.size) {
        this.rejectAll(new Error(`Indexador encerrou antes da resposta: code=${code} signal=${signal}`));
      }
    });
  }

  rejectAll(error) {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }

  send(payload) {
    const messageId = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(messageId, { resolve, reject });
      this.child.stdin.write(`${JSON.stringify({ message_id: messageId, payload })}\n`);
    });
  }

  async close() {
    if (this.child.exitCode === null) {
      await this.send({ type: 'Shutdown' });
      this.child.stdin.end();
    }
    const exit = await this.exitPromise;
    if (exit.code !== 0) {
      throw new Error(`Indexador encerrou com ${exit.code}: ${this.stderr}`);
    }
  }
}

async function buildIndexador(records, args, outputDir) {
  const commandArgs = [
    '--stdio',
    '--output-dir', outputDir,
    '--max-terms-per-shard', String(args.maxTermsPerShard),
    '--max-bytes-per-shard', String(args.maxBytesPerShard),
    '--documents-per-shard', String(args.documentsPerShard),
    '--min-importance', String(args.minImportance),
  ];
  const started = performance.now();
  const rpc = new RpcClient(args.indexadorBinary, commandArgs);
  await rpc.send({ type: 'Ping' });
  const ready = performance.now();

  const ingestStarted = performance.now();
  for (let i = 0; i < records.length; i += args.rpcBatchSize) {
    const documents = records.slice(i, i + args.rpcBatchSize).map((record) => ({
      name: record.meta.title,
      url: record.url,
      content: record.content,
    }));
    await rpc.send({ type: 'AddBatch', documents });
    const ingested = Math.min(i + documents.length, records.length);
    if (ingested % PROGRESS_INTERVAL < documents.length || ingested === records.length) {
      progress(`indexador: ${ingested}/${records.length} registros adicionados`);
    }
  }
  const ingestFinished = performance.now();
  const stats = await rpc.send({ type: 'Stats' });
  const dumpStarted = performance.now();
  const dump = await rpc.send({ type: 'DumpIndex' });
  const dumpFinished = performance.now();
  await rpc.close();
  const finished = performance.now();

  return {
    total_ms: finished - started,
    startup_ms: ready - started,
    ingest_ms: ingestFinished - ingestStarted,
    dump_ms: dumpFinished - dumpStarted,
    stats,
    dump,
  };
}

function pagefindCategory(relativePath) {
  if (relativePath.includes('/fragment/') || relativePath.startsWith('fragment/')) return 'fragments';
  if (relativePath.includes('/index/') || relativePath.startsWith('index/')) return 'index';
  if (relativePath.includes('/filter/') || relativePath.startsWith('filter/')) return 'filters';
  if (relativePath.endsWith('.pf_meta')) return 'metadata';
  return 'runtime_other';
}

function indexadorCategory(relativePath) {
  if (/^index_\d+\.gz$/.test(relativePath)) return 'index';
  if (/^documents_\d+\.gz$/.test(relativePath)) return 'documents';
  if (relativePath === 'index.map') return 'map';
  return 'other';
}

async function directoryStats(rootDir, categorize) {
  const totals = { files: 0, logical_bytes: 0, allocated_bytes: 0 };
  const categories = {};

  async function visit(directory) {
    for (const entry of await fs.promises.readdir(directory, { withFileTypes: true })) {
      const fullPath = path.join(directory, entry.name);
      if (entry.isDirectory()) {
        await visit(fullPath);
        continue;
      }
      if (!entry.isFile()) continue;
      const stat = await fs.promises.stat(fullPath, { bigint: true });
      const logical = Number(stat.size);
      const allocated = Number(stat.blocks * 512n);
      const category = categorize(path.relative(rootDir, fullPath));
      const bucket = categories[category] ||= { files: 0, logical_bytes: 0, allocated_bytes: 0 };
      for (const target of [totals, bucket]) {
        target.files += 1;
        target.logical_bytes += logical;
        target.allocated_bytes += allocated;
      }
    }
  }

  await visit(rootDir);
  return { ...totals, categories };
}

function ratio(numerator, denominator) {
  return denominator === 0 ? null : numerator / denominator;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(usage());
    return;
  }
  if (!fs.existsSync(args.indexadorBinary)) {
    throw new Error(`Binário do indexador não encontrado: ${args.indexadorBinary}`);
  }

  const outputRoot = args.outputRoot ||
    await fs.promises.mkdtemp(path.join(os.tmpdir(), 'pagefind-vs-indexador-'));
  const pagefindDir = path.join(outputRoot, 'pagefind');
  const indexadorDir = path.join(outputRoot, 'indexador');
  await fs.promises.mkdir(pagefindDir, { recursive: true });
  await fs.promises.mkdir(indexadorDir, { recursive: true });

  progress(`saída: ${outputRoot}`);
  progress(`preparando ${args.volumes.join(', ')}`);
  const prepareStarted = performance.now();
  const corpus = await loadRecords(args.publicDir, args.volumes, args.minCount);
  const prepareMs = performance.now() - prepareStarted;
  const contentBytes = corpus.records.reduce(
    (total, record) => total + Buffer.byteLength(record.content),
    0,
  );

  progress(`iniciando Pagefind com ${corpus.records.length} registros`);
  const pagefindTiming = await buildPagefind(
    corpus.records,
    args.publicDir,
    pagefindDir,
    args.pagefindChunkSize,
  );
  progress('iniciando indexador via RPC stdio');
  const indexadorTiming = await buildIndexador(corpus.records, args, indexadorDir);
  const [pagefindSize, indexadorSize] = await Promise.all([
    directoryStats(pagefindDir, pagefindCategory),
    directoryStats(indexadorDir, indexadorCategory),
  ]);

  const report = {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    output_root: outputRoot,
    corpus: {
      volumes: args.volumes,
      pages_per_volume: corpus.perVolume,
      records: corpus.records.length,
      searchable_content_bytes: contentBytes,
      preparation_ms: prepareMs,
    },
    configuration: {
      pagefind_chunk_size: args.pagefindChunkSize,
      indexador_rpc_batch_size: args.rpcBatchSize,
      indexador_max_terms_per_shard: args.maxTermsPerShard,
      indexador_max_bytes_per_shard: args.maxBytesPerShard,
      indexador_documents_per_shard: args.documentsPerShard,
      indexador_min_importance: args.minImportance,
      pagefind_keyword_min_count: args.minCount,
    },
    pagefind: { timing: pagefindTiming, size: pagefindSize },
    indexador: { timing: indexadorTiming, size: indexadorSize },
    comparison: {
      pagefind_over_indexador: {
        total_time_ratio: ratio(pagefindTiming.total_ms, indexadorTiming.total_ms),
        logical_bytes_ratio: ratio(pagefindSize.logical_bytes, indexadorSize.logical_bytes),
        allocated_bytes_ratio: ratio(pagefindSize.allocated_bytes, indexadorSize.allocated_bytes),
        file_count_ratio: ratio(pagefindSize.files, indexadorSize.files),
      },
    },
  };
  const reportPath = path.join(outputRoot, 'report.json');
  await fs.promises.writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, 'utf8');
  process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
