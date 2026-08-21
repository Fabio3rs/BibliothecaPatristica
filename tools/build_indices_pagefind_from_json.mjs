#!/usr/bin/env node
// Build a small Pagefind index for the indices page.
//
// This index is intentionally separate from the main corpus index because it
// targets the semi-dynamic /indices experience and links back into the volume
// explorer with focus parameters.

import fs from 'fs';
import path from 'path';
import { readJsonMaybeGz } from './json_io.mjs';

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const params = {
    publicDir: 'web/public',
    outDir: 'web/public/indices-pagefind',
    base: '/BibliothecaPatristica',
    sourceManifest: 'web/public/indices/manifest.json',
    customIndexUrl: process.env.CUSTOM_INDEX_ADD_BATCH_URL || 'http://localhost:8080/add_batch',
    customIndexBatchSize: Number(process.env.CUSTOM_INDEX_BATCH_SIZE || 50),
    customIndexRequired: false,
  };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--public') params.publicDir = args[++i];
    else if (a === '--out') params.outDir = args[++i];
    else if (a === '--base') params.base = args[++i];
    else if (a === '--source-manifest') params.sourceManifest = args[++i];
    else if (a === '--custom-index-url') params.customIndexUrl = args[++i];
    else if (a === '--custom-index-batch-size') params.customIndexBatchSize = Number(args[++i]);
    else if (a === '--no-custom-index') params.customIndexUrl = '';
    else if (a === '--custom-index-required') params.customIndexRequired = true;
  }

  if (!Number.isInteger(params.customIndexBatchSize) || params.customIndexBatchSize < 1) {
    throw new Error('--custom-index-batch-size deve ser um inteiro maior que zero.');
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

async function readJSON(filePath) {
  return readJsonMaybeGz(filePath);
}

function normalizeWhitespace(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function esc(v) {
  return String(v || '').trim();
}

function buildUrl(base, volumeId, params = {}) {
  const cleanBase = base === '/' ? '' : base.replace(/\/$/, '');
  const qp = new URLSearchParams({ doc: volumeId });
  for (const [key, value] of Object.entries(params)) {
    if (value) qp.set(key, value);
  }
  return `${cleanBase}/indices?${qp.toString()}`;
}

function makeRecord({ base, volume, kind, title, content, params, metaExtra = {}, filters = {} }) {
  const volumeId = volume.volume_id || volume.volumeId || volume.id;
  const fullTitle = `${volumeId} — ${title}`.slice(0, 240);
  return {
    url: buildUrl(base, volumeId, params),
    content: normalizeWhitespace(content || fullTitle),
    language: 'pt',
    meta: {
      title: fullTitle,
      kind,
      volume: volumeId,
      kindLabel: kind === 'work' ? 'Obra' : kind === 'section' ? 'Seção' : kind === 'entry' ? 'Entrada' : 'Volume',
      collection: volume.collection || '',
      ...metaExtra,
    },
    filters: {
      collection: volume.collection ? [volume.collection] : [],
      volume: [volumeId],
      ...filters,
    },
  };
}

function entryContent(volume, section, entry) {
  return [
    volume.volume_label,
    concatenateRecordValues(volume.display),
    section?.index_kind,
    concatenateRecordValues(section?.heading_display),
    entry?.entry_raw,
    entry?.normalized_target,
    concatenateRecordValues(entry?.target_display),
    concatenateRecordValues(entry?.note_display),
  ].filter(Boolean).join(' ');
}

function concatenateRecordValues(value) {
  const values = [];

  function collect(current) {
    if (current === null || current === undefined || current === '') return;
    if (Array.isArray(current)) {
      for (const item of current) collect(item);
      return;
    }
    if (typeof current === 'object') {
      for (const item of Object.values(current)) collect(item);
      return;
    }
    values.push(String(current));
  }

  collect(value);
  return normalizeWhitespace(values.join(' '));
}

function createCustomIndexSender(params) {
  const pending = [];
  let sent = 0;
  let failed = 0;
  let disabled = !params.customIndexUrl;

  async function flush() {
    if (disabled || pending.length === 0) return;

    const batch = pending.splice(0, pending.length);
    try {
      const response = await fetch(params.customIndexUrl, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(batch),
      });
      if (!response.ok) {
        const detail = normalizeWhitespace(await response.text()).slice(0, 500);
        throw new Error(`HTTP ${response.status}${detail ? `: ${detail}` : ''}`);
      }
      sent += batch.length;
    } catch (err) {
      failed += batch.length;
      if (params.customIndexRequired) throw err;
      disabled = true;
      console.warn(
        `[AVISO] Falha ao enviar para ${params.customIndexUrl}: ${err.message}. ` +
        'O índice Pagefind continuará; use --custom-index-required para tornar a falha fatal.',
      );
    }
  }

  return {
    async add(record) {
      if (disabled) return;
      pending.push({
        name: record.meta?.title || record.url,
        url: record.url,
        content: concatenateRecordValues(record),
      });
      if (pending.length >= params.customIndexBatchSize) await flush();
    },
    async finish() {
      await flush();
      return { sent, failed, disabled };
    },
  };
}

async function buildIndexForAll(pagefindModule, params, outputDir) {
  if (fs.existsSync(outputDir)) fs.rmSync(outputDir, { recursive: true, force: true });
  ensureDir(outputDir);

  const manifest = await readJSON(params.sourceManifest);
  const volumes = Array.isArray(manifest.volumes) ? manifest.volumes : [];
  const { createIndex, close } = pagefindModule;
  const siteBase = params.base === '/' ? undefined : params.base;
  /*const { index, errors } = await createIndex({
    rootSelector: null,
    writePlayground: false,
    keepIndexUrl: false,
    site: siteBase,
  });*/
  //if (errors?.length) throw new Error(`Falha ao iniciar Pagefind: ${errors.join('; ')}`);

  let total = 0;
  const customIndex = createCustomIndexSender(params);
  let customIndexResult = { sent: 0, failed: 0, disabled: true };
  try {
    for (const volume of volumes) {
      const volumeId = volume.volume_id || volume.volumeId || volume.id;
      const volumeDir = path.dirname(params.sourceManifest);
      const gzPath = path.join(volumeDir, `${volumeId}.json.gz`);
      const plainPath = path.join(volumeDir, `${volumeId}.json`);
      const volumePath = fs.existsSync(gzPath) ? gzPath : plainPath;
      if (!fs.existsSync(volumePath)) {
        throw new Error(`JSON do volume ausente: ${gzPath} ou ${plainPath}`);
      }
      const doc = await readJSON(volumePath);
      const vol = doc.volume || { volume_id: volumeId, collection: volume.collection || '' };

      const volumeRecord = makeRecord({
        base: params.base,
        volume: vol,
        kind: 'volume',
        title: vol.display?.original || vol.volume_label || volumeId,
        content: [concatenateRecordValues(vol.display), volume.works_total, volume.sections_total, volume.entries_total].filter(Boolean).join(' '),
        params: {},
        metaExtra: {
          workCount: String(volume.works_total || 0),
          sectionCount: String(volume.sections_total || 0),
          entryCount: String(volume.entries_total || 0),
          completeness: String(volume.target_coverage ?? volume.completeness ?? ''),
        },
      });
      //await index.addCustomRecord(volumeRecord);
      await customIndex.add(volumeRecord);
      total++;

      for (const work of doc.works || []) {
        const workRecord = makeRecord({
          base: params.base,
          volume: vol,
          kind: 'work',
          title: `${work.author_display?.original ? `${work.author_display.original} — ` : ''}${work.title_display?.original || work.work_key || ''}`,
          content: [
            concatenateRecordValues(work.author_display),
            concatenateRecordValues(work.title_display),
          ].filter(Boolean).join(' '),
          params: { work: work.work_key || '' },
          metaExtra: {
            workKey: work.work_key || '',
            author: work.author_display?.original || '',
            workTitle: work.title_display?.original || '',
            pageStart: String(work.reference_start_page || work.start_page || ''),
            pageEnd: String(work.reference_end_page || work.end_page || ''),
          },
        });
        //await index.addCustomRecord(workRecord);
        await customIndex.add(workRecord);
        total++;
      }

      for (const section of doc.sections || []) {
        const sectionRecord = makeRecord({
          base: params.base,
          volume: vol,
          kind: 'section',
          title: `${section.index_kind || section.heading_display?.original || section.section_key || ''}`,
          content: [
            section.index_kind,
            concatenateRecordValues(section.heading_display),
          ].filter(Boolean).join(' '),
          params: { section: section.section_key || '' },
          metaExtra: {
            sectionKey: section.section_key || '',
            sectionTitle: section.heading_display?.original || '',
            pageStart: String(section.reference_page_start || section.page_start || ''),
            pageEnd: String(section.reference_page_end || section.page_end || ''),
          },
        });
        //await index.addCustomRecord(sectionRecord);
        await customIndex.add(sectionRecord);
        total++;

        for (const entry of section.entries || []) {
          const entryRecord = makeRecord({
            base: params.base,
            volume: vol,
            kind: 'entry',
            title: entry.target_display?.original || entry.entry_raw || entry.id || '',
            content: entryContent(vol, section, entry),
            params: {
              section: section.section_key || '',
              entry: String(entry.id || ''),
            },
          metaExtra: {
            sectionKey: section.section_key || '',
            entryId: String(entry.id || ''),
            entryText: entry.target_display?.original || entry.entry_raw || '',
            targetText: entry.target_display?.original || '',
            page: String(entry.reference_page || entry.editorial_reference_page || ''),
          },
          });
          //await index.addCustomRecord(entryRecord);
          await customIndex.add(entryRecord);
          total++;
        }
      }
    }

    customIndexResult = await customIndex.finish();
    //await index.writeFiles({ outputPath: outputDir });
  } finally {
    await close();
  }

  console.log(`[OK] Indices Pagefind: ${total} registros em ${outputDir}`);
  if (params.customIndexUrl) {
    console.log(
      `[OK] Índice customizado: ${customIndexResult.sent} registros enviados para ${params.customIndexUrl}` +
      (customIndexResult.failed ? `; ${customIndexResult.failed} falharam` : ''),
    );
  }
}

async function main() {
  const params = parseArgs();
  const pagefindPath = resolvePagefindModule(params.publicDir);
  if (!pagefindPath) throw new Error('pagefind não encontrado. Instale `pagefind` em web/.');
  const pagefindModule = await import(pathToFileURL(pagefindPath).href);
  const pf = pagefindModule?.default || pagefindModule?.pagefind || pagefindModule;
  if (!pf || typeof pf.createIndex !== 'function') {
    throw new Error('Módulo pagefind inválido.');
  }
  params.sourceManifest = path.resolve(params.sourceManifest);
  await buildIndexForAll(pf, params, path.resolve(params.outDir));
}

import { pathToFileURL } from 'url';

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
