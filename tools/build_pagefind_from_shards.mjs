#!/usr/bin/env node
// build_pagefind_from_shards_v2.mjs
// Versão otimizada: serializa addCustomRecord para evitar memory pressure e thrashing

import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';

async function readJSON(p) {
  const data = await fs.promises.readFile(p, 'utf-8');
  return JSON.parse(data);
}
function ensureDir(p) { fs.mkdirSync(p, { recursive: true }); }

function parseArgs() {
  const args = process.argv.slice(2);
  const params = {
    publicDir: 'web/public',
    outDir: 'web/public/pagefind',
    base: '/BibliothecaPatristica',
    minCount: 1,
  };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--public') params.publicDir = args[++i];
    else if (a === '--out') params.outDir = args[++i];
    else if (a === '--base') params.base = args[++i];
    else if (a === '--min-count') params.minCount = parseInt(args[++i], 10) || 1;
  }
  return params;
}

function resolvePagefindModule(publicDir) {
  const candidates = [
    path.resolve(publicDir, '..', 'node_modules', 'pagefind', 'lib', 'index.js'),
    path.resolve('node_modules', 'pagefind', 'lib', 'index.js'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

function extractBookName(label) {
  if (!label || typeof label !== 'string') return null;
  let s = label.replace(/\s*\([^)]*\)\s*$/g, '');
  s = s.replace(/[:;,-]+\s*$/g, '').trim();
  s = s.split(/\/|-|—/)[0].trim();
  return s || null;
}

function usable(meta, minCount) {
  if (!meta) return false;
  if (meta.iscit) return !!meta.label;
  if (meta.count !== undefined && meta.count < minCount) return false;
  return !!meta.label;
}

async function main() {
  const params = parseArgs();
  const base = params.base.endsWith('/') ? params.base.slice(0, -1) : params.base;
  const modPath = resolvePagefindModule(params.publicDir);
  if (!modPath) {
    console.error('pagefind não encontrado. Instale com `npm install pagefind --save-dev` em web/.');
    process.exit(1);
  }
  const { createIndex, close } = await import(pathToFileURL(modPath).href);

  const volumesData = await readJSON(path.join(params.publicDir, 'volumes.json'));
  const volumes = volumesData.volumes || [];
  const keywordsData = await readJSON(path.join(params.publicDir, 'dict', 'keywords.json'));
  const keywords = keywordsData.items || [];
  const kwMap = new Map(keywords.map(k => [k.id, k]));

  if (fs.existsSync(params.outDir)) {
    console.log(`Limpando índice anterior em ${params.outDir}...`);
    fs.rmSync(params.outDir, { recursive: true, force: true });
  }
  ensureDir(params.outDir);

  const { index, errors: initErrors } = await createIndex({
    rootSelector: null,
    writePlayground: false,
    keepIndexUrl: false,
    site: params.base === '/' ? undefined : params.base,
  });
  if (initErrors?.length) {
    console.error('Falha ao iniciar Pagefind:', initErrors);
    process.exit(1);
  }

  let totalRecords = 0;
  const startTime = Date.now();
  const reportEvery = 5000;

  async function indexVolume(vol) {
    const vid = vol.id;
    const metaPath = path.join(params.publicDir, vol.meta_url);
    let metaObj;
    try {
      metaObj = await readJSON(metaPath);
    } catch (e) {
      console.error(`Erro lendo meta de ${vid}: ${e.message}`);
      return;
    }
    const blocks = metaObj.page_blocks || [];

    for (const pb of blocks) {
      let block;
      try {
        block = await readJSON(path.join(params.publicDir, pb.file));
      } catch (e) {
        continue;
      }

      for (const p of block.pages || []) {
        const kwMetas = (p.keyword_ids || [])
          .map(id => kwMap.get(id))
          .filter(kw => kw && usable(kw, params.minCount));

        const kwLabels = kwMetas.map(meta => meta.label);
        const bookNames = [];
        const bookSet = new Set();
        for (const m of kwMetas) {
          if (m.iscit) {
            const bn = extractBookName(m.label);
            if (bn && !bookSet.has(bn)) {
              bookSet.add(bn);
              bookNames.push(bn);
            }
          }
        }

        const topKeywords = kwLabels.slice(0, 3);
        const enrichedTitle = topKeywords.length
          ? `${vid} p.${p.page} — ${topKeywords.join(' • ')}`
          : `${vid} p.${p.page}`;

        const contentPieces = [p.summary_page || ''];
        if (kwLabels.length) contentPieces.push(kwLabels.join(' '));
        if (bookNames.length) contentPieces.push(bookNames.join(' '));
        const content = contentPieces.join(' ').replace(/<[^>]*>?/gm, ' ').replace(/\s+/g, ' ').trim();

        const filters = {
          collection: [vol.collection_id],
          volume: [vid],
        };
        if (bookNames.length) filters.book = bookNames;

        const metaObj = {
          title: enrichedTitle,
          volume: vid,
          page: String(p.page),
          collection: vol.collection_id,
        };
        if (bookNames.length) {
          metaObj.books = bookNames.join(' • ');
        }

        await index.addCustomRecord({
          url: `${base}/viewer?doc=${vid}&page=${p.page}`,
          content,
          meta: metaObj,
          filters,
          language: 'pt',
        });

        totalRecords++;
        if (totalRecords % reportEvery === 0) {
          const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
          const rps = (totalRecords / ((Date.now() - startTime) / 1000)).toFixed(1);
          console.log(`[${elapsed}s] ${totalRecords} records indexed (${rps} r/s) — last: ${vid} p.${p.page}`);
        }
      }
    }
  }

  for (const vol of volumes) {
    await indexVolume(vol);
  }

  if (!totalRecords) {
    console.error('Nenhum record adicionado ao índice Pagefind. Abortando.');
    process.exit(1);
  }

  console.log("Escrevendo arquivos do índice (aguarde)...");
  await index.writeFiles({ outputPath: params.outDir });
  await close();
  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  console.log(`[OK] Pagefind index written to ${params.outDir} (total records: ${totalRecords}, time: ${elapsed}s)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
