#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { spawn } from 'node:child_process';
import { performance } from 'node:perf_hooks';
import { fileURLToPath } from 'node:url';
import { readJsonMaybeGz } from './json_io.mjs';

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DEFAULT_DEV_VOLUMES = ['PG001', 'PL001', 'PO002'];

function parseArgs() {
  const params = {
    sourceManifest: 'web/public/indices/manifest.json',
    outDir: 'web/public/indexador/indices',
    binary: process.env.INDEXADOR_BINARY || path.join(REPOSITORY_ROOT, '.work/PatrologiaIndexer/build-codex-clang19/indexador'),
    base: '/BibliothecaPatristica',
    volumes: [...DEFAULT_DEV_VOLUMES],
    batchSize: 100,
    maxTermsPerShard: 5000,
    maxBytesPerShard: 307200,
    documentsPerShard: 500,
    minImportance: 2.01,
    smokeQueries: [],
  };

  const args = process.argv.slice(2);
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--source-manifest') params.sourceManifest = args[++i];
    else if (arg === '--out') params.outDir = args[++i];
    else if (arg === '--binary') params.binary = args[++i];
    else if (arg === '--base') params.base = args[++i];
    else if (arg === '--volumes') params.volumes = args[++i].split(',').map((value) => value.trim()).filter(Boolean);
    else if (arg === '--all') params.volumes = null;
    else if (arg === '--batch-size') params.batchSize = Number(args[++i]);
    else if (arg === '--max-terms-per-shard') params.maxTermsPerShard = Number(args[++i]);
    else if (arg === '--max-bytes-per-shard') params.maxBytesPerShard = Number(args[++i]);
    else if (arg === '--documents-per-shard') params.documentsPerShard = Number(args[++i]);
    else if (arg === '--min-importance') params.minImportance = Number(args[++i]);
    else if (arg === '--smoke-query') params.smokeQueries.push(args[++i]);
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
  if (!Number.isFinite(params.minImportance) || params.minImportance < 0) {
    throw new Error('--min-importance deve ser um número maior ou igual a zero.');
  }
  return params;
}

function usage() {
  return [
    'Uso: node tools/build_indices_indexador_from_json.mjs [opções]',
    '',
    `Sem --volumes/--all, indexa somente a amostra de desenvolvimento: ${DEFAULT_DEV_VOLUMES.join(', ')}.`,
    'Use --all explicitamente para processar todos os volumes do manifest.',
    'Use --smoke-query <termo>[::trecho-da-url] repetidamente para validar o conteúdo antes de gravar os shards.',
  ].join('\n');
}

function normalizeWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function flattenSearchValues(values) {
  const flattened = [];
  const visit = (value) => {
    if (value === null || value === undefined || value === '') return;
    if (Array.isArray(value)) {
      for (const item of value) visit(item);
      return;
    }
    if (typeof value === 'object') {
      for (const item of Object.values(value)) visit(item);
      return;
    }
    const text = normalizeWhitespace(value);
    if (text) flattened.push(text);
  };
  visit(values);
  return [...new Set(flattened)];
}

function displaySearchValues(display) {
  if (!display || typeof display !== 'object') return [];
  return flattenSearchValues([
    display.original,
    display.search,
    display.translation,
  ]);
}

function workSearchValues(work) {
  if (!work) return [];
  return flattenSearchValues([
    displaySearchValues(work.author_display),
    displaySearchValues(work.title_display),
  ]);
}

function sectionSearchValues(section, work) {
  return flattenSearchValues([
    workSearchValues(work),
    section?.scope_kind,
    section?.index_kind,
    displaySearchValues(section?.heading_display),
  ]);
}

function entrySearchValues(entry) {
  return flattenSearchValues([
    entry?.entry_raw,
    entry?.normalized_target,
    displaySearchValues(entry?.target_display),
    displaySearchValues(entry?.note_display),
  ]);
}

function parseSmokeCheck(value) {
  const [query, ...urlParts] = String(value || '').split('::');
  return {
    query: normalizeWhitespace(query),
    urlIncludes: normalizeWhitespace(urlParts.join('::')),
  };
}

function buildUrl(base, volumeId, params = {}) {
  const cleanBase = base === '/' ? '' : base.replace(/\/$/, '');
  const query = new URLSearchParams({ volume: volumeId });
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && String(value) !== '') query.set(key, String(value));
  }
  return `${cleanBase}/indices?${query.toString()}`;
}

function record(volume, base, title, values, params = {}) {
  const volumeId = volume.volume_id || volume.volumeId || volume.id;
  const name = normalizeWhitespace(`${volumeId} — ${title}`).slice(0, 500);
  const contentParts = flattenSearchValues([
    volumeId,
    volume.collection,
    title,
    ...values,
  ]);
  const content = contentParts.join(' ');
  return { name, url: buildUrl(base, volumeId, params), content };
}

function recordsForVolume(doc, manifestVolume, base) {
  const volume = doc.volume || {
    volume_id: manifestVolume.volume_id,
    collection: manifestVolume.collection || '',
  };
  const volumeId = volume.volume_id || manifestVolume.volume_id;
  const works = doc.works || [];
  const worksByKey = new Map(works.map((work) => [work.work_key, work]));
  const result = [record(
    volume,
    base,
    volume.display?.original || volume.volume_label || volumeId,
    [
      volume.volume_label,
      displaySearchValues(volume.display),
      volume.notes,
      works.map(workSearchValues),
    ],
  )];

  for (const work of works) {
    const author = work.author_display?.original || '';
    const title = `${author ? `${author} — ` : ''}${work.title_display?.original || work.work_key || ''}`;
    result.push(record(volume, base, title, workSearchValues(work), { work: work.work_key || '' }));
  }

  for (const section of doc.sections || []) {
    const work = worksByKey.get(section.work_key) || null;
    const sectionTitle = section.index_kind || section.heading_display?.original || section.section_key || '';
    result.push(record(
      volume,
      base,
      sectionTitle,
      sectionSearchValues(section, work),
      { section: section.section_key || '' },
    ));

    for (const entry of section.entries || []) {
      const entryTitle = entry.target_display?.original || entry.entry_raw || entry.id || '';
      result.push(record(volume, base, entryTitle, [
        sectionSearchValues(section, work),
        entrySearchValues(entry),
      ], {
        section: section.section_key || '',
        entry: String(entry.id || ''),
      }));
    }
  }
  return result;
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
        this.rejectAll(new Error(`Resposta JSONL inválida do indexador: ${line}\n${error.message}`));
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
      const text = chunk.toString();
      this.stderr += text;
      process.stderr.write(`[indexador] ${text}`);
    });
    this.child.on('error', (error) => this.rejectAll(error));
    this.child.on('close', (code, signal) => {
      this.closed = true;
      if (this.pending.size) {
        const detail = this.stderr.trim();
        this.rejectAll(new Error(
          `O indexador encerrou antes da resposta (código ${code}, sinal ${signal || 'nenhum'})${detail ? `:\n${detail}` : ''}`,
        ));
      }
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

async function main() {
  const params = parseArgs();
  if (params.help) {
    console.log(usage());
    return;
  }

  const startedAt = performance.now();
  const sourceManifest = path.resolve(params.sourceManifest);
  const outDir = path.resolve(params.outDir);
  const binary = path.resolve(params.binary);
  if (!fs.existsSync(binary)) throw new Error(`Binário do indexador não encontrado: ${binary}`);

  const manifest = await readJsonMaybeGz(sourceManifest);
  const manifestVolumes = Array.isArray(manifest.volumes) ? manifest.volumes : [];
  const requested = params.volumes ? new Set(params.volumes) : null;
  const volumes = requested
    ? manifestVolumes.filter((item) => requested.has(item.volume_id || item.volumeId || item.id))
    : manifestVolumes;
  if (requested) {
    const found = new Set(volumes.map((item) => item.volume_id || item.volumeId || item.id));
    const missing = [...requested].filter((volumeId) => !found.has(volumeId));
    if (missing.length) throw new Error(`Volumes ausentes do manifest: ${missing.join(', ')}`);
  }
  if (!volumes.length) throw new Error('Nenhum volume selecionado para indexação.');

  await fs.promises.rm(outDir, { recursive: true, force: true });
  await fs.promises.mkdir(outDir, { recursive: true });

  const client = new IndexadorRpcClient(binary, [
    '--stdio',
    '--output-dir', outDir,
    '--max-terms-per-shard', String(params.maxTermsPerShard),
    '--max-bytes-per-shard', String(params.maxBytesPerShard),
    '--documents-per-shard', String(params.documentsPerShard),
    '--min-importance', String(params.minImportance),
  ]);

  let total = 0;
  let batch = [];
  try {
    await client.request({ type: 'Ping' });
    for (const manifestVolume of volumes) {
      const volumeId = manifestVolume.volume_id || manifestVolume.volumeId || manifestVolume.id;
      const volumeDir = path.dirname(sourceManifest);
      const volumePath = path.join(volumeDir, `${volumeId}.json.gz`);
      const doc = await readJsonMaybeGz(volumePath);
      for (const item of recordsForVolume(doc, manifestVolume, params.base)) {
        batch.push(item);
        if (batch.length >= params.batchSize) {
          await client.request({ type: 'AddBatch', documents: batch });
          total += batch.length;
          batch = [];
        }
      }
    }
    if (batch.length) {
      await client.request({ type: 'AddBatch', documents: batch });
      total += batch.length;
    }
    const smokeChecks = [];
    for (const value of params.smokeQueries) {
      const { query, urlIncludes } = parseSmokeCheck(value);
      if (!query) throw new Error('Smoke query não pode ser vazia.');
      const response = await client.request({ type: 'Search', query });
      const matchedUrl = !urlIncludes || response.results?.some((item) => item.url?.includes(urlIncludes));
      if (!response.total || !matchedUrl) {
        const detail = urlIncludes ? ` com URL contendo "${urlIncludes}"` : '';
        throw new Error(`Smoke query sem resultados esperados antes do dump: ${query}${detail}`);
      }
      smokeChecks.push({ query, url_contains: urlIncludes || null, total: response.total });
      console.log(`[OK] Smoke query "${query}": ${response.total} documentos${urlIncludes ? `; URL contém "${urlIncludes}"` : ''}.`);
    }
    const stats = await client.request({ type: 'Stats' });
    const dump = await client.request({ type: 'DumpIndex' });
    await client.shutdown();
    await fs.promises.writeFile(path.join(outDir, 'manifest.json'), JSON.stringify({
      schema_version: 1,
      engine: 'PatrologiaIndexer',
      generated_at: new Date().toISOString(),
      source_generated_at: manifest.generated_at || null,
      sample: Boolean(params.volumes),
      volumes: volumes.length,
      total_documents: total,
      total_terms: stats.total_terms,
      index_shards: dump.index_shards,
      document_shards: dump.document_shards,
      documents_per_shard: params.documentsPerShard,
      min_importance: params.minImportance,
      content_schema_version: 3,
      smoke_checks: smokeChecks,
    }, null, 2) + '\n');
    const bytes = await directorySize(outDir);
    const seconds = (performance.now() - startedAt) / 1000;
    console.log(`[OK] Indexador: ${total} documentos de ${volumes.length} volumes em ${seconds.toFixed(2)} s.`);
    console.log(`[OK] Termos: ${stats.total_terms}; shards: ${dump.index_shards} de termos, ${dump.document_shards} de documentos.`);
    console.log(`[OK] Saída: ${outDir} (${(bytes / 1024).toFixed(1)} KiB).`);
  } catch (error) {
    try {
      await client.shutdown();
    } catch {
      client.child.kill('SIGTERM');
    }
    throw error;
  }
}

main().catch((error) => {
  console.error(`[ERRO] ${error.stack || error.message || error}`);
  process.exitCode = 1;
});
