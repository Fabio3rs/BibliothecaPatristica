#!/usr/bin/env node
// Build Pagefind indexes from the enrichment NDJSON shards.
//
// Modes:
// - batch mode: build one shard group into `--out`
// - sequential mode: build every shard group under `--out/<batch>`
//   and emit `manifest.json` at the root

import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';
import {
  buildPagefindManifest,
  loadEnrichmentDocIds,
  planPagefindBatches,
  readJSON,
  writeJSON,
} from './pagefind_batches.mjs';

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const params = {
    publicDir: 'web/public',
    outDir: 'web/public/pagefind',
    base: '/BibliothecaPatristica',
    minCount: 1,
    batchSize: 25,
    batchIndex: null,
    enrichmentIndex: 'data/shards/enrichment/index.json',
  };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--public') params.publicDir = args[++i];
    else if (a === '--out') params.outDir = args[++i];
    else if (a === '--base') params.base = args[++i];
    else if (a === '--min-count') params.minCount = parseInt(args[++i], 10) || 1;
    else if (a === '--batch-size') params.batchSize = parseInt(args[++i], 10) || 25;
    else if (a === '--batch-index') params.batchIndex = parseInt(args[++i], 10);
    else if (a === '--enrichment-index') params.enrichmentIndex = args[++i];
  }

  return params;
}

function resolvePagefindModule(publicDir) {
  const candidates = [
    path.resolve(publicDir, '..', 'node_modules', 'pagefind', 'lib', 'index.js'),
    path.resolve('node_modules', 'pagefind', 'lib', 'index.js'),
    path.resolve('web', 'node_modules', 'pagefind', 'lib', 'index.js'),
  ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }

  return null;
}

function normalizeWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function extractBookName(label) {
  if (!label || typeof label !== 'string') return null;
  let s = label.replace(/\s*\([^)]*\)\s*$/g, '');
  s = s.replace(/[:;,-]+\s*$/g, '').trim();
  s = s.split(/\/|-|—/)[0].trim();
  return s || null;
}

function usableKeyword(meta, minCount) {
  if (!meta) return false;
  if (meta.iscit) return !!meta.label;
  if (meta.count !== undefined && meta.count < minCount) return false;
  return !!meta.label;
}

function unique(values) {
  return Array.from(new Set(values.filter(Boolean)));
}

function buildRecordUrl(base, docId, page) {
  const cleanBase = base === '/' ? '' : base.replace(/\/$/, '');
  return `${cleanBase}/viewer?doc=${encodeURIComponent(docId)}&page=${encodeURIComponent(String(page))}`;
}

function buildCustomRecord(params, kwMap, docId, row) {
  const keywordMetas = [];
  const seenKeywordLabels = new Set();
  for (const label of row.keywords || []) {
    const meta = kwMap.get(label) || { label };
    if (!meta?.label || seenKeywordLabels.has(meta.label)) continue;
    if (!usableKeyword(meta, params.minCount)) continue;
    seenKeywordLabels.add(meta.label);
    keywordMetas.push(meta);
  }
  const keywordLabels = keywordMetas.map((meta) => meta.label);

  const bookNames = [];
  const bookSet = new Set();
  for (const meta of keywordMetas) {
    if (!meta.iscit) continue;
    const bookName = extractBookName(meta.label);
    if (bookName && !bookSet.has(bookName)) {
      bookSet.add(bookName);
      bookNames.push(bookName);
    }
  }

  const topKeywords = keywordLabels.slice(0, 3);
  const hint = topKeywords.length
    ? topKeywords.join(' • ')
    : unique([row.author, row.work]).join(' — ');
  const title = hint
    ? `${docId} p.${row.page} — ${hint}`.slice(0, 220)
    : `${docId} p.${row.page}`;

  const contentParts = unique([
    title,
    row.summary_page,
    row.summary_global,
    row.author,
    row.work,
    keywordLabels.join(' '),
    bookNames.join(' '),
  ]);
  const content = normalizeWhitespace(contentParts.join(' '));

  const filters = {
    collection: [docId.slice(0, 2)],
    volume: [docId],
  };
  if (bookNames.length) filters.book = bookNames;

  const meta = {
    title,
    author: row.author || '',
    work: row.work || '',
    doc: docId,
    page: String(row.page),
    collection: docId.slice(0, 2),
  };
  if (keywordLabels.length) meta.keywords = keywordLabels.slice(0, 10).join(' • ');
  if (bookNames.length) meta.books = bookNames.join(' • ');

  return {
    url: buildRecordUrl(params.base, docId, row.page),
    content,
    language: 'pt',
    meta,
    filters,
  };
}

async function loadKeywordMap(publicDir) {
  const candidates = [
    path.join(publicDir, 'dict', 'keywords_lookup.json'),
    path.join(publicDir, 'dict', 'keywords.json'),
  ];
  let keywordsData = null;
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      keywordsData = await readJSON(candidate);
      break;
    }
  }
  if (!keywordsData) {
    throw new Error('keywords_lookup.json ou keywords.json não encontrado em public/dict');
  }
  const keywords = Array.isArray(keywordsData.items) ? keywordsData.items : [];
  return new Map(keywords.map((item) => [item.label, item]));
}

async function loadVolumeRows(enrichmentDir, docId) {
  const filePath = path.join(enrichmentDir, `${docId}.ndjson`);
  const text = await fs.promises.readFile(filePath, 'utf-8');
  const rows = [];

  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) continue;
    try {
      rows.push(JSON.parse(line));
    } catch (err) {
      throw new Error(`Falha ao ler ${filePath}: ${err.message}`);
    }
  }

  return rows;
}

async function buildIndexForBatch(pagefindModule, batch, params, kwMap, outputDir) {
  if (fs.existsSync(outputDir)) {
    fs.rmSync(outputDir, { recursive: true, force: true });
  }
  ensureDir(outputDir);

  const { createIndex, close } = pagefindModule;
  const siteBase = params.base === '/' ? undefined : params.base;
  const { index, errors: initErrors } = await createIndex({
    rootSelector: null,
    writePlayground: false,
    keepIndexUrl: false,
    site: siteBase,
  });
  if (initErrors?.length) {
    throw new Error(`Falha ao iniciar Pagefind: ${initErrors.join('; ')}`);
  }

  let totalRecords = 0;
  const startTime = Date.now();

  try {
    for (const docId of batch.docs) {
      const rows = await loadVolumeRows(params.enrichmentDir, docId);
      for (const row of rows) {
        const record = buildCustomRecord(params, kwMap, docId, row);
        const result = await index.addCustomRecord(record);
        if (result?.errors?.length) {
          console.error(`Pagefind addCustomRecord errors em ${docId} p.${row.page}:`, result.errors);
        }
        totalRecords += 1;
        if (totalRecords % 5000 === 0) {
          const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
          console.log(`[${batch.batch_id}] ${totalRecords} registros indexados em ${elapsed}s`);
        }
      }
    }

    if (!totalRecords) {
      throw new Error(`Nenhum registro encontrado no lote ${batch.batch_id}`);
    }

    console.log(`[${batch.batch_id}] escrevendo arquivos em ${outputDir}...`);
    await index.writeFiles({ outputPath: outputDir });
  } finally {
    await close();
  }

  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  console.log(`[OK] ${batch.batch_id}: ${totalRecords} registros, ${elapsed}s`);
}

async function main() {
  const params = parseArgs();
  const pagefindPath = resolvePagefindModule(params.publicDir);
  if (!pagefindPath) {
    throw new Error('pagefind não encontrado. Instale `pagefind` em web/.');
  }

  params.enrichmentIndex = path.resolve(params.enrichmentIndex);
  params.enrichmentDir = path.dirname(params.enrichmentIndex);

  const pagefindModule = await import(pathToFileURL(pagefindPath).href);
  const kwMap = await loadKeywordMap(params.publicDir);
  const docIds = await loadEnrichmentDocIds(params.enrichmentIndex);
  const batches = planPagefindBatches(docIds, params.batchSize);

  if (!batches.length) {
    throw new Error('Nenhum documento encontrado no manifesto de enriquecimento.');
  }

  if (params.batchIndex !== null && !Number.isNaN(params.batchIndex)) {
    const batch = batches[params.batchIndex - 1];
    if (!batch) {
      throw new Error(`batch-index inválido: ${params.batchIndex}`);
    }
    await buildIndexForBatch(pagefindModule, batch, params, kwMap, path.resolve(params.outDir));
    return;
  }

  if (fs.existsSync(params.outDir)) {
    fs.rmSync(params.outDir, { recursive: true, force: true });
  }
  ensureDir(params.outDir);

  for (const batch of batches) {
    const batchOutDir = path.join(params.outDir, batch.batch_dir);
    await buildIndexForBatch(pagefindModule, batch, params, kwMap, batchOutDir);
  }

  const manifest = buildPagefindManifest(batches, params.batchSize);
  await writeJSON(path.join(params.outDir, 'manifest.json'), manifest, true);
  console.log(`[OK] manifesto escrito em ${path.join(params.outDir, 'manifest.json')}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
