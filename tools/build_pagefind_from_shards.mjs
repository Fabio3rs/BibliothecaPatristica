#!/usr/bin/env node
// build_pagefind_from_shards.mjs
// Gera índice Pagefind usando createIndex/addCustomRecord com paralelismo
// Uso: node tools/build_pagefind_from_shards.mjs \\
//         --public web/public --out web/public/pagefind --base /BibliothecaPatristica \\
//         [--min-count 1]

import fs from 'fs';
import path from 'path';
import os from 'os';
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
    concurrency: Math.max(1, Math.min(4, (os.cpus()?.length || 2))), // bound default to avoid overloading
  };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--public') params.publicDir = args[++i];
    else if (a === '--out') params.outDir = args[++i];
    else if (a === '--base') params.base = args[++i];
    else if (a === '--min-count') params.minCount = parseInt(args[++i], 10) || 1;
    else if (a === '--concurrency') params.concurrency = Math.max(1, parseInt(args[++i], 10) || params.concurrency);
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

  const volumesData = await readJSON(path.join(params.publicDir, 'volumes.json'));
  const volumes = volumesData.volumes || [];
  const keywordsData = await readJSON(path.join(params.publicDir, 'dict', 'keywords.json'));
  const keywords = keywordsData.items || [];
  const kwMap = new Map(keywords.map(k => [k.id, k])); // meta: id, label, group_id, count, iscit

  // Limpa a pasta de saída para garantir que não haja resíduos do índice anterior
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
  const BATCH_SIZE = 50; // Quantidade de blocos processados em paralelo por volume

  async function indexVolume(vol) {
    const vid = vol.id;
    process.stdout.write(`Indexando volume: ${vid}... `);

    // snapshot
    try {
      /*const snapObj = await readJSON(path.join(params.publicDir, 'snapshots', `${vid}.json`));
      const snap = (snapObj.snapshots || [])[0];
      if (snap && snap.summary) {
        await index.addCustomRecord({
          url: `${base}/viewer?doc=${vid}&page=${vol.page_first || 1}&snapshot=global`,
          content: snap.summary.replace(/<[^>]*>?/gm, ''),
          meta: { title: `${vid} resumo global`, volume: vid, collection: vol.collection_id },
          filters: { collection: [vol.collection_id], volume: [vid] },
          language: 'pt',
        });
        totalRecords++;
      }*/
    } catch (e) { /* ignore missing snapshot */ }

    // páginas
    let metaObj = await readJSON(path.join(params.publicDir, vol.meta_url));
    const blocks = metaObj.page_blocks || [];

    for (let i = 0; i < blocks.length; i += BATCH_SIZE) {
      const batch = blocks.slice(i, i + BATCH_SIZE);

      await Promise.all(batch.map(async (pb) => {
        const block = await readJSON(path.join(params.publicDir, pb.file));
        const pagePromises = block.pages.map(p => {
          const usable = (meta) => {
            if (!meta) return false;
            if (meta.count !== undefined && meta.count < params.minCount) return false;
            return !!meta.label;
          };

          const kwLabels = (p.keyword_ids || [])
            .map(id => kwMap.get(id))
            .filter(usable)
            .map(meta => meta.label);

          // Conteúdo limpo para o Pagefind (remove qualquer tag HTML residual)
          const content = `
            ${p.summary_page || ''}
            ${kwLabels.join(' ')}
          `.replace(/<[^>]*>?/gm, '').replace(/\s+/g, ' ').trim();

          return index.addCustomRecord({
            url: `${base}/viewer?doc=${vid}&page=${p.page}`,
            content: content,
            meta: {
              title: `${vid} p.${p.page}`,
              volume: vid,
              page: String(p.page),
              collection: vol.collection_id
            },
            filters: {
              collection: [vol.collection_id],
              volume: [vid],
            },
            language: 'pt',
          });
        });

        const results = await Promise.all(pagePromises);
        totalRecords += results.length;
      }));
    }
    process.stdout.write(`OK (${totalRecords} total)\n`);
  }

  // Processa volumes em paralelo respeitando a concorrência configurada
  const queue = [...volumes];
  const workers = Array.from({ length: params.concurrency }, async () => {
    while (queue.length) {
      const vol = queue.shift();
      if (vol) await indexVolume(vol);
    }
  });
  await Promise.all(workers);

  if (!totalRecords) {
    console.error('Nenhum record adicionado ao índice Pagefind. Abortando.');
    process.exit(1);
  }

  console.log("Escrevendo arquivos do índice (aguarde)...");
  await index.writeFiles({ outputPath: params.outDir });
  await close();
  console.log(`[OK] Pagefind index written to ${params.outDir} (total records: ${totalRecords})`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
