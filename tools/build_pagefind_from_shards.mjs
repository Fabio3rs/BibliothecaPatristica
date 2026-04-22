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

        const extractBookName = (label) => {
          if (!label || typeof label !== 'string') return null;
          // Remove conteúdo parentético e notas, ex: "Apocalipse (Ap 3, 7-12)" -> "Apocalipse"
          let s = label.replace(/\s*\([^)]*\)\s*$/g, '');
          // Remove número de verso/capítulo residual e trims
          s = s.replace(/[:;,-]+\s*$/g, '').trim();
          // Em alguns labels compostos com barra ou hífen, ficar com a primeira parte
          s = s.split(/\/|-|—/)[0].trim();
          return s || null;
        };

        const pagePromises = block.pages.map(p => {
          const usable = (meta) => {
            if (!meta) return false;
            // Se for citação (iscit), consideramos útil mesmo com baixa contagem
            if (meta.iscit) return !!meta.label;
            if (meta.count !== undefined && meta.count < params.minCount) return false;
            return !!meta.label;
          };

          const kwMetas = (p.keyword_ids || [])
            .map(id => kwMap.get(id))
            .filter(kw => !!kw && usable(kw));

          const kwLabels = kwMetas.map(meta => meta.label);

          // extraia nomes de livro limpos para melhorar buscas por book name
          const bookNames = Array.from(new Set(kwMetas
            .filter(m => m.iscit)
            .map(m => extractBookName(m.label))
            .filter(Boolean)));

          // monte título enriquecido com até 3 keywords principais
          const topKeywords = kwLabels.slice(0, 3);
          const enrichedTitle = topKeywords.length ? `${vid} p.${p.page} — ${topKeywords.join(' • ')}` : `${vid} p.${p.page}`;

          // Conteúdo limpo para o Pagefind (remove qualquer tag HTML residual)
          const contentPieces = [p.summary_page || ''];
          if (kwLabels.length) contentPieces.push(kwLabels.join(' '));
          if (bookNames.length) contentPieces.push(bookNames.join(' '));
          const content = contentPieces.join(' ').replace(/<[^>]*>?/gm, '').replace(/\s+/g, ' ').trim();

          const filters = {
            collection: [vol.collection_id],
            volume: [vid],
          };
          if (bookNames.length) filters.book = bookNames;

          // monte meta objetct condicionalmente para evitar enviar arrays onde
          // Pagefind espera strings (erro: invalid type: sequence, expected a string)
          const metaObj = {
            title: enrichedTitle,
            volume: vid,
            page: String(p.page),
            collection: vol.collection_id,
          };
          if (bookNames.length) {
            // envie nomes de livro como uma string única (mais seguro para o parser)
            metaObj.books = bookNames.join(' • ');
          }

          return index.addCustomRecord({
            url: `${base}/viewer?doc=${vid}&page=${p.page}`,
            content: content,
            meta: metaObj,
            filters: filters,
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
