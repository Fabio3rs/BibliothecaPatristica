#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
import { readJsonMaybeGz } from './json_io.mjs';
import { isAdministrativePage } from './search_record_policy.mjs';

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_DEV_VOLUMES = ['PG001', 'PL001', 'PO002'];

function parseArgs() {
  const params = {
    publicDir: 'web/public',
    sourceManifest: 'web/public/volumes.json',
    outDir: 'web/public/indexador/search',
    binary: process.env.INDEXADOR_BINARY || path.join(REPOSITORY_ROOT, '.work/PatrologiaIndexer/build-codex-clang19/indexador'),
    base: '/BibliothecaPatristica',
    volumes: [...DEFAULT_DEV_VOLUMES],
    batchSize: 100,
    progressEvery: 10000,
    maxTermsPerShard: 5000,
    maxBytesPerShard: 307200,
    documentsPerShard: 500,
    minImportance: 2.01,
    keepTerms: null,
    layout: 'split',
    collectionConfig: {},
    includeV2Search: true,
    includeTranslations: true,
    includeOriginalHeader: false,
    includeKeywords: true,
    keywordLiterals: true,
    suggestionWordlists: true,
  };

  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--public') params.publicDir = args[++i];
    else if (arg === '--source-manifest') params.sourceManifest = args[++i];
    else if (arg === '--out') params.outDir = args[++i];
    else if (arg === '--binary') params.binary = args[++i];
    else if (arg === '--base') params.base = args[++i];
    else if (arg === '--volumes') params.volumes = args[++i].split(',').map((value) => value.trim()).filter(Boolean);
    else if (arg === '--all') params.volumes = null;
    else if (arg === '--batch-size') params.batchSize = Number(args[++i]);
    else if (arg === '--progress-every') params.progressEvery = Number(args[++i]);
    else if (arg === '--max-terms-per-shard') params.maxTermsPerShard = Number(args[++i]);
    else if (arg === '--max-bytes-per-shard') params.maxBytesPerShard = Number(args[++i]);
    else if (arg === '--documents-per-shard') params.documentsPerShard = Number(args[++i]);
    else if (arg === '--min-importance') params.minImportance = Number(args[++i]);
    else if (arg === '--keep-terms') params.keepTerms = args[++i].split(',').map((value) => value.trim()).filter(Boolean);
    else if (arg === '--no-keep-terms') params.keepTerms = [];
    else if (arg === '--layout') params.layout = args[++i];
    else if (arg === '--no-v2-search') params.includeV2Search = false;
    else if (arg === '--no-translations') params.includeTranslations = false;
    else if (arg === '--include-original-header') params.includeOriginalHeader = true;
    else if (arg === '--no-keywords') params.includeKeywords = false;
    else if (arg === '--no-keyword-literals') params.keywordLiterals = false;
    else if (arg === '--no-suggestion-wordlists') params.suggestionWordlists = false;
    else if (/^--(pg|pl|po)-(max-terms-per-shard|max-bytes-per-shard|documents-per-shard|min-importance)$/.test(arg)) {
      const [, collectionName, optionName] = arg.match(/^--(pg|pl|po)-(.+)$/);
      const collection = collectionName.toUpperCase();
      const property = {
        'max-terms-per-shard': 'maxTermsPerShard',
        'max-bytes-per-shard': 'maxBytesPerShard',
        'documents-per-shard': 'documentsPerShard',
        'min-importance': 'minImportance',
      }[optionName];
      params.collectionConfig[collection] ||= {};
      params.collectionConfig[collection][property] = Number(args[++i]);
    }
    else if (arg === '--help' || arg === '-h') params.help = true;
    else throw new Error(`Argumento não suportado: ${arg}`);
  }

  for (const [name, value] of [
    ['batch-size', params.batchSize],
    ['max-terms-per-shard', params.maxTermsPerShard],
    ['max-bytes-per-shard', params.maxBytesPerShard],
    ['documents-per-shard', params.documentsPerShard],
  ]) {
    if (!Number.isInteger(value) || value < 1) throw new Error(`--${name} deve ser um inteiro maior que zero.`);
  }
  if (!Number.isInteger(params.progressEvery) || params.progressEvery < 0) {
    throw new Error('--progress-every deve ser um inteiro maior ou igual a zero.');
  }
  if (!Number.isFinite(params.minImportance) || params.minImportance < 0) {
    throw new Error('--min-importance deve ser um número maior ou igual a zero.');
  }
  if (!['split', 'unified'].includes(params.layout)) {
    throw new Error('--layout deve ser split ou unified.');
  }
  if (params.keepTerms === null) {
    params.keepTerms = params.layout === 'unified' ? ['pg', 'pl', 'po'] : [];
  }
  for (const [collection, config] of Object.entries(params.collectionConfig)) {
    for (const [property, value] of Object.entries(config)) {
      if (property === 'minImportance') {
        if (!Number.isFinite(value) || value < 0) throw new Error(`--${collection.toLowerCase()}-min-importance inválido.`);
      } else if (!Number.isInteger(value) || value < 1) {
        throw new Error(`Override de shard inválido para ${collection}: ${property}.`);
      }
    }
  }
  return params;
}

function usage() {
  return [
    'Uso: node tools/build_search_indexador_from_shards.mjs [opções]',
    '',
    'Gera índices a partir dos shards publicados.',
    `Sem --volumes/--all, usa a amostra: ${DEFAULT_DEV_VOLUMES.join(', ')}.`,
    'Use --all explicitamente para processar o corpus completo.',
    'Progresso: --progress-every 10000 (use 0 para desativar).',
    'Documentos: --documents-per-shard 500 (padrão ajustado para busca client-side).',
    'Layouts: --layout split (padrão) ou --layout unified.',
    'Conteúdo v2/traduções entram por padrão; use --no-v2-search ou --no-translations para comparar.',
    'Cabeçalho OCR é opt-in com --include-original-header; keywords podem ser excluídas com --no-keywords.',
    'Keywords compostas ganham um token com _; use --no-keyword-literals para medir/desativar.',
    'No layout unified, preserva pg,pl,po por padrão; ajuste com --keep-terms ou --no-keep-terms.',
    'Wordlists compactas de sugestão são geradas por padrão; use --no-suggestion-wordlists para omiti-las.',
    'Cada opção de shard aceita override por coleção, por exemplo:',
    '  --pg-max-bytes-per-shard 614400 --pl-documents-per-shard 10000',
  ].join('\n');
}

function normalizeWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

const INDEXADOR_SEPARATORS = /[\s.,!?*\-+'"()[\];\\/=<>|#:«»“”$&@%‘’—]+/gu;

export function keywordLiteral(label) {
  const literal = normalizeWhitespace(label).replace(INDEXADOR_SEPARATORS, '_').replace(/^_+|_+$/g, '');
  return literal.includes('_') ? literal : '';
}

function extractBookName(label) {
  if (!label || typeof label !== 'string') return null;
  let value = label.replace(/\s*\([^)]*\)\s*$/g, '');
  value = value.replace(/[:;,-]+\s*$/g, '').trim();
  value = value.split(/\/|-|—/)[0].trim();
  return value || null;
}

export function buildRecord(params, keywordMap, volumeId, page, metadataBlock) {
  const keywordLabels = unique(params.includeKeywords ? (page.keyword_labels || []) : []);
  const canonicalIds = unique(params.includeKeywords ? (page.keyword_ids || []) : []);
  const canonicalMetas = canonicalIds.map((id) => keywordMap.get(id)).filter((item) => item?.label);
  const canonicalLabels = unique(canonicalMetas.map((item) => item.label));
  const displayCount = Number.isInteger(page.keyword_display_count)
    ? Math.max(0, page.keyword_display_count)
    : keywordLabels.length;
  const displayedLabels = keywordLabels.length ? keywordLabels.slice(0, displayCount) : canonicalLabels;
  const literalLabels = unique([...displayedLabels, ...canonicalLabels]);
  const keywordLiterals = params.keywordLiterals ? unique(literalLabels.map(keywordLiteral)) : [];
  const bookNames = unique(canonicalMetas.filter((item) => item.iscit).map((item) => extractBookName(item.label)));
  const hint = displayedLabels.slice(0, 3).join(' • ') || unique([page.author, page.work]).join(' — ');
  const name = (hint ? `${volumeId} p.${page.page} — ${hint}` : `${volumeId} p.${page.page}`).slice(0, 220);
  const translatedTexts = params.includeTranslations
    ? Object.values(page.translations || {}).flatMap((entry) => [
        entry?.summary_page,
        entry?.summary_global,
        entry?.search_text,
      ])
    : [];
  const content = normalizeWhitespace(unique([
    name,
    volumeId,
    volumeId.slice(0, 2),
    page.summary_page,
    page.summary_global,
    params.includeV2Search ? page.search_text_pt : '',
    params.includeOriginalHeader ? page.header_original : '',
    ...translatedTexts,
    page.author,
    page.work,
    keywordLabels.join(' '),
    canonicalLabels.join(' '),
    keywordLiterals.join(' '),
    bookNames.join(' '),
  ]).join(' '));
  const cleanBase = params.base === '/' ? '' : params.base.replace(/\/$/, '');
  const routing = metadataBlock?.file
    ? `&mb=${encodeURIComponent(String(metadataBlock.file).replace(/^\/+/, ''))}`
    : '';
  const url = `${cleanBase}/viewer?doc=${encodeURIComponent(volumeId)}&page=${encodeURIComponent(String(page.page))}${routing}`;
  return { name, url, content };
}

class IndexadorRpcClient {
  constructor(binary, args) {
    this.nextId = 1;
    this.pending = new Map();
    this.stderr = '';
    this.closed = false;
    this.child = spawn(binary, args, { stdio: ['pipe', 'pipe', 'pipe'] });
    this.lines = readline.createInterface({ input: this.child.stdout });

    this.lines.on('line', (line) => {
      let response;
      try {
        response = JSON.parse(line);
      } catch (error) {
        this.rejectAll(new Error(`Resposta JSONL inválida: ${line}\n${error.message}`));
        return;
      }
      const pending = this.pending.get(response.message_id);
      if (!pending) return;
      this.pending.delete(response.message_id);
      if (response.payload?.type === 'Error') {
        const at = response.payload.index === undefined ? '' : ` (documento ${response.payload.index})`;
        pending.reject(new Error(`${response.payload.message || 'Erro RPC'}${at}`));
      } else {
        pending.resolve(response.payload);
      }
    });
    this.child.stderr.on('data', (chunk) => {
      const message = chunk.toString();
      this.stderr += message;
      process.stderr.write(`[indexador] ${message}`);
    });
    this.child.on('error', (error) => this.rejectAll(error));
    this.child.on('close', (code, signal) => {
      this.closed = true;
      if (!this.pending.size) return;
      const detail = this.stderr.trim();
      this.rejectAll(new Error(
        `O indexador encerrou antes da resposta (código ${code}, sinal ${signal || 'nenhum'})${detail ? `:\n${detail}` : ''}`,
      ));
    });
  }

  rejectAll(error) {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }

  request(payload) {
    if (this.closed || !this.child.stdin.writable) {
      return Promise.reject(new Error('O processo RPC do indexador não está disponível.'));
    }
    const messageId = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(messageId, { resolve, reject });
      this.child.stdin.write(`${JSON.stringify({ message_id: messageId, payload })}\n`, (error) => {
        if (!error) return;
        this.pending.delete(messageId);
        reject(error);
      });
    });
  }

  async shutdown() {
    if (this.closed) return;
    await this.request({ type: 'Shutdown' });
    this.child.stdin.end();
  }
}

async function directorySize(root) {
  let bytes = 0;
  for (const entry of await fs.promises.readdir(root, { withFileTypes: true })) {
    const filePath = path.join(root, entry.name);
    bytes += entry.isDirectory() ? await directorySize(filePath) : (await fs.promises.stat(filePath)).size;
  }
  return bytes;
}

async function loadKeywordMap(publicDir) {
  const data = await readJsonMaybeGz(path.join(publicDir, 'dict', 'keywords.json'));
  return new Map((data.items || []).map((item) => [item.id, item]));
}

async function loadVolumeRecords(params, keywordMap, volume) {
  const meta = await readJsonMaybeGz(path.join(params.publicDir, volume.meta_url));
  const records = [];
  let skippedAdministrative = 0;
  for (const block of meta.page_blocks || []) {
    const blockData = await readJsonMaybeGz(path.join(params.publicDir, block.file));
    for (const page of blockData.pages || []) {
      if (isAdministrativePage(page)) {
        skippedAdministrative += 1;
        continue;
      }
      records.push(buildRecord(params, keywordMap, volume.id, page, block));
    }
  }
  return { records, skippedAdministrative };
}

async function buildIndex(params, definition, keywordMap) {
  const outDir = path.join(params.outDir, definition.path);
  const config = {
    maxTermsPerShard: params.maxTermsPerShard,
    maxBytesPerShard: params.maxBytesPerShard,
    documentsPerShard: params.documentsPerShard,
    minImportance: params.minImportance,
    keepTerms: params.keepTerms,
    suggestionWordlists: params.suggestionWordlists,
    ...(definition.collection ? params.collectionConfig[definition.collection] || {} : {}),
  };
  await fs.promises.mkdir(outDir, { recursive: true });
  const client = new IndexadorRpcClient(params.binary, [
    '--stdio',
    '--output-dir', outDir,
  ]);
  let total = 0;
  let skippedAdministrative = 0;
  let batch = [];
  let nextProgress = params.progressEvery;
  const ingestionStartedAt = performance.now();
  const reportProgress = (volumeId) => {
    if (!params.progressEvery || total < nextProgress) return;
    const elapsedSeconds = Math.max((performance.now() - ingestionStartedAt) / 1000, 0.001);
    console.log(
      `[PROGRESSO] ${definition.id}: ${total} páginas ingeridas; `
      + `volume ${volumeId}; ${(total / elapsedSeconds).toFixed(0)} páginas/s.`,
    );
    while (nextProgress <= total) nextProgress += params.progressEvery;
  };
  try {
    await client.request({ type: 'Ping' });
    for (const volume of definition.volumes) {
      const loaded = await loadVolumeRecords(params, keywordMap, volume);
      const { records } = loaded;
      skippedAdministrative += loaded.skippedAdministrative;
      for (const item of records) {
        batch.push(item);
        if (batch.length >= params.batchSize) {
          await client.request({ type: 'AddBatch', documents: batch });
          total += batch.length;
          batch = [];
          reportProgress(volume.id);
        }
      }
    }
    if (batch.length) {
      await client.request({ type: 'AddBatch', documents: batch });
      total += batch.length;
      reportProgress(definition.volumes.at(-1)?.id || definition.id);
    }
    const stats = await client.request({ type: 'Stats' });
    const dump = await client.request({
      type: 'DumpIndex',
      config: {
        max_terms_per_shard: config.maxTermsPerShard,
        max_bytes_per_shard: config.maxBytesPerShard,
        documents_per_shard: config.documentsPerShard,
        min_importance: config.minImportance,
        keep_terms: config.keepTerms,
        write_suggestion_wordlists: config.suggestionWordlists,
      },
    });
    await client.shutdown();
    return {
      id: definition.id,
      path: definition.path,
      collections: definition.collections,
      volumes: definition.volumes.map((volume) => volume.id),
      documents: total,
      skipped_administrative: skippedAdministrative,
      terms: stats.total_terms,
      index_shards: dump.index_shards,
      document_shards: dump.document_shards,
      bytes: await directorySize(outDir),
      config: {
        max_terms_per_shard: config.maxTermsPerShard,
        max_bytes_per_shard: config.maxBytesPerShard,
        documents_per_shard: config.documentsPerShard,
        min_importance: config.minImportance,
        keep_terms: config.keepTerms,
        write_suggestion_wordlists: config.suggestionWordlists,
        keyword_literals: params.keywordLiterals,
      },
    };
  } catch (error) {
    try {
      await client.shutdown();
    } catch {
      client.child.kill('SIGTERM');
    }
    throw error;
  }
}

function createIndexDefinitions(layout, selected) {
  if (layout === 'unified') {
    return [{
      id: 'ALL',
      path: 'all',
      collections: unique(selected.map((volume) => volume.collection_id)),
      volumes: selected,
    }];
  }

  return ['PG', 'PL', 'PO']
    .map((collection) => ({
      id: collection,
      path: collection.toLowerCase(),
      collection,
      collections: [collection],
      volumes: selected.filter((volume) => volume.collection_id === collection),
    }))
    .filter((definition) => definition.volumes.length);
}

async function main() {
  const params = parseArgs();
  if (params.help) {
    console.log(usage());
    return;
  }

  params.publicDir = path.resolve(params.publicDir);
  params.sourceManifest = path.resolve(params.sourceManifest);
  params.outDir = path.resolve(params.outDir);
  params.binary = path.resolve(params.binary);
  if (!fs.existsSync(params.binary)) throw new Error(`Binário do indexador não encontrado: ${params.binary}`);

  const startedAt = performance.now();
  const manifest = await readJsonMaybeGz(params.sourceManifest);
  const available = Array.isArray(manifest.volumes) ? manifest.volumes : [];
  const requested = params.volumes ? new Set(params.volumes) : null;
  const selected = requested ? available.filter((volume) => requested.has(volume.id)) : available;
  if (requested) {
    const found = new Set(selected.map((volume) => volume.id));
    const missing = [...requested].filter((id) => !found.has(id));
    if (missing.length) throw new Error(`Volumes ausentes do manifest: ${missing.join(', ')}`);
  }
  if (!selected.length) throw new Error('Nenhum volume selecionado para indexação.');

  await fs.promises.rm(params.outDir, { recursive: true, force: true });
  await fs.promises.mkdir(params.outDir, { recursive: true });
  const keywordMap = await loadKeywordMap(params.publicDir);
  const indexes = [];
  for (const definition of createIndexDefinitions(params.layout, selected)) {
    const result = await buildIndex(params, definition, keywordMap);
    indexes.push(result);
    console.log(
      `[OK] ${result.id}: ${result.documents} páginas, `
      + `${result.skipped_administrative} administrativas omitidas, `
      + `${result.terms} termos, ${(result.bytes / 1024).toFixed(1)} KiB.`,
    );
  }

  const outputManifest = {
    schema_version: 2,
    engine: 'PatrologiaIndexer',
    generated_at: new Date().toISOString(),
    sample: params.volumes !== null,
    layout: params.layout,
    config: {
      max_terms_per_shard: params.maxTermsPerShard,
      max_bytes_per_shard: params.maxBytesPerShard,
      documents_per_shard: params.documentsPerShard,
      min_importance: params.minImportance,
      keep_terms: params.keepTerms,
      write_suggestion_wordlists: params.suggestionWordlists,
      keyword_literals: params.keywordLiterals,
    },
    suggestions: {
      wordlists: params.suggestionWordlists,
      pattern: 'words_{shard}.gz',
      neighbor_radius: 1,
    },
    collections: unique(selected.map((volume) => volume.collection_id)),
    indexes,
    total_documents: indexes.reduce((sum, item) => sum + item.documents, 0),
  };
  await fs.promises.writeFile(path.join(params.outDir, 'manifest.json'), `${JSON.stringify(outputManifest, null, 2)}\n`);
  const seconds = (performance.now() - startedAt) / 1000;
  console.log(`[OK] POC de busca: ${outputManifest.total_documents} páginas em ${seconds.toFixed(2)} s.`);
  console.log(`[OK] Manifesto: ${path.join(params.outDir, 'manifest.json')}`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(`[ERRO] ${error.stack || error.message || error}`);
    process.exitCode = 1;
  });
}
