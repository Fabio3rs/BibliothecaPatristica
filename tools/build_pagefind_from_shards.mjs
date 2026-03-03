#!/usr/bin/env node
// build_pagefind_from_shards.mjs
// Gera índice Pagefind usando createIndex/addCustomRecord
// Uso: node tools/build_pagefind_from_shards.mjs --public web/public --out web/public/pagefind --base /BibliothecaPatristica

import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';

function readJSON(p) { return JSON.parse(fs.readFileSync(p, 'utf-8')); }
function ensureDir(p) { fs.mkdirSync(p, { recursive: true }); }

function parseArgs() {
  const args = process.argv.slice(2);
  const params = { publicDir: 'web/public', outDir: 'web/public/pagefind', base: '/BibliothecaPatristica' };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--public') params.publicDir = args[++i];
    else if (a === '--out') params.outDir = args[++i];
    else if (a === '--base') params.base = args[++i];
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

async function main() {
  const params = parseArgs();
  const base = params.base.endsWith('/') ? params.base.slice(0, -1) : params.base;
  const modPath = resolvePagefindModule(params.publicDir);
  if (!modPath) {
    console.error('pagefind não encontrado. Instale com `npm install pagefind --save-dev` em web/.');
    process.exit(1);
  }
  const { createIndex, close } = await import(pathToFileURL(modPath).href);

  const volumes = readJSON(path.join(params.publicDir, 'volumes.json')).volumes || [];
  const keywords = readJSON(path.join(params.publicDir, 'dict', 'keywords.json')).items || [];
  const kwMap = new Map(keywords.map(k => [k.id, k.label]));

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

  let records = 0;
  for (const vol of volumes) {
    const vid = vol.id;

    // snapshot
    try {
      const snapObj = readJSON(path.join(params.publicDir, 'snapshots', `${vid}.json`));
      const snap = (snapObj.snapshots || [])[0];
      if (snap && snap.summary) {
        const r = await index.addCustomRecord({
          url: `${base}/viewer?doc=${vid}&page=${vol.page_first || 1}&snapshot=global`,
          content: snap.summary,
          meta: { title: `${vid} resumo global`, volume: vid },
          filters: { collection: [vol.collection_id], volume: [vid] },
          language: 'pt',
        });
        if (r.errors?.length) { console.error('Erro snapshot', vid, r.errors); process.exit(1); }
        records += 1;
      }
    } catch (e) { /* ignore missing snapshot */ }

    // páginas: ler manifesto do volume para pegar os blocos
    let metaObj = null;
    try {
      metaObj = readJSON(path.join(params.publicDir, vol.meta_url));
    } catch (e) {
      console.error('Manifesto não encontrado para', vid, vol.meta_url);
      process.exit(1);
    }

    for (const pb of metaObj.page_blocks || []) {
      const block = readJSON(path.join(params.publicDir, pb.file));
      for (const p of block.pages) {
        const kwLabels = (p.keyword_ids || []).map(id => kwMap.get(id) || id);
        const kwCatLabels = Object.values(p.keyword_categories || {}).flat().map(id => kwMap.get(id) || id);
        const content = `${p.summary_page || ''}\n${p.summary_global || ''}\n${kwLabels.join(' ')} ${kwCatLabels.join(' ')}`;

        const r = await index.addCustomRecord({
          url: `${base}/viewer?doc=${vid}&page=${p.page}`,
          content,
          meta: { title: `${vid} p.${p.page}`, volume: vid, page: String(p.page) },
          filters: {
            collection: [vol.collection_id],
            volume: [vid],
            keyword: kwLabels.length ? kwLabels : undefined,
            keyword_category: kwCatLabels.length ? kwCatLabels : undefined,
          },
          language: 'pt',
        });
        if (r.errors?.length) { console.error('Erro página', vid, p.page, r.errors); process.exit(1); }
        records += 1;
      }
    }
  }

  if (!records) {
    console.error('Nenhum record adicionado ao índice Pagefind. Abortando.');
    process.exit(1);
  }

  await index.writeFiles({ outputPath: params.outDir });
  await close();
  console.log(`[OK] Pagefind index written to ${params.outDir} (records: ${records})`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
