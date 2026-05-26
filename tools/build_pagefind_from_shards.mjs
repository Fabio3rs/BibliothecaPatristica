#!/usr/bin/env node
// build_pagefind_from_shards.mjs
// Versão simples: lê os metadados, puxa só um prefixo do texto real quando existir,
// extrai apenas um título útil do XML/RAW e indexa serialmente no Pagefind.

import fs from 'fs';
import path from 'path';
import { pathToFileURL } from 'url';

async function readJSON(p) {
  const data = await fs.promises.readFile(p, 'utf-8');
  return JSON.parse(data);
}

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

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

function escapeHtml(s) {
  if (!s && s !== 0) return '';
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normalizeWhitespace(s) {
  return String(s || '').replace(/\s+/g, ' ').trim();
}

function stripNoiseLines(text) {
  return String(text || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .filter((line) => !/^\d+$/.test(line))
    .filter((line) => !/^[.\-_=*·•\s]+$/.test(line))
    .filter((line) => !/^\.{3,}$/.test(line))
    .join('\n');
}

function extractXmlBlocks(text) {
  const blocks = [];
  const blockRe = /<bloco\b[^>]*>([\s\S]*?)<\/bloco>/gi;
  let match;
  while ((match = blockRe.exec(text))) {
    const body = normalizeWhitespace(match[1].replace(/<[^>]+>/g, ' '));
    if (body) blocks.push(body);
  }
  return blocks;
}

function extractXmlHeaderBlock(text) {
  const blockRe = /<bloco\b[^>]*\btipo=(["'])cabecalho\1[^>]*>([\s\S]*?)<\/bloco>/gi;
  const parts = [];
  let match;
  while ((match = blockRe.exec(String(text || '')))) {
    const body = normalizeWhitespace(match[2].replace(/<[^>]+>/g, ' '));
    if (!body) continue;
    if (/^\d+$/.test(body)) continue;
    if (/^[.\-_=*·•\s]+$/.test(body)) continue;
    parts.push(body);
  }
  if (!parts.length) return null;
  return normalizeWhitespace(parts.join(' '));
}

function isCompactHeaderLine(line) {
  if (!line) return false;
  if (/^\d+$/.test(line)) return false;
  if (/^[.\-_=*·•\s]+$/.test(line)) return false;
  const letters = (line.match(/[A-Za-zÀ-ÿΑ-Ωα-ω]/g) || []).length;
  if (letters < 4) return false;
  if (line.length > 140) return false;
  if (/[.!?]{2,}/.test(line)) return false;
  const upperCount = (line.match(/[A-ZÀ-ÝΑ-Ω]/g) || []).length;
  const lowerCount = (line.match(/[a-zà-ÿα-ω]/g) || []).length;
  const upperRatio = upperCount / Math.max(1, letters);
  const lowerRatio = lowerCount / Math.max(1, letters);
  let score = 0;
  score += Math.min(letters, 80);
  score += Math.min(line.length, 120) / 6;
  score += upperRatio * 20;
  if (upperRatio > 0.6) score += 20;
  if (lowerRatio < 0.2 && letters > 8) score += 10;
  return score >= 45;
}

function isShortNoiseLine(line) {
  if (!line) return false;
  const compact = line.trim();
  if (!compact) return true;
  if (/^\d+$/.test(compact)) return true;
  if (/^[a-z]{1,4}$/i.test(compact)) return true;
  if (/^[A-Za-zÀ-ÿΑ-Ωα-ω]{1,3}\.?$/i.test(compact)) return true;
  return false;
}

function extractTitleFromText(rawText) {
  if (!rawText) return null;
  const trimmed = rawText.trimStart();
  const isXml = /^<\s*pagina\b/i.test(trimmed) || /^<\?xml\b/i.test(trimmed);
  if (isXml) {
    const header = extractXmlHeaderBlock(rawText);
    if (header) return header;
    const blocks = extractXmlBlocks(rawText);
    const fallback = blocks.find((block) => {
      const compact = normalizeWhitespace(block);
      return compact && compact.length <= 120 && isCompactHeaderLine(compact);
    });
    return fallback ? normalizeWhitespace(fallback) : null;
  }

  const cleaned = stripNoiseLines(rawText);
  const lines = cleaned.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const headingLines = [];
  let sawUsefulText = false;
  for (const line of lines) {
    if (isShortNoiseLine(line) && !sawUsefulText) continue;
    if (isCompactHeaderLine(line)) {
      headingLines.push(line);
      sawUsefulText = true;
      if (headingLines.length >= 3) break;
      continue;
    }
    if (/^\d+$/.test(line)) {
      if (headingLines.length) break;
      continue;
    }
    if (headingLines.length) break;
    if (sawUsefulText) break;
    if (line.length > 120) break;
    if (line.length >= 8) {
      headingLines.push(line);
      sawUsefulText = true;
      break;
    }
  }
  if (!headingLines.length) {
    return lines[0] && lines[0].length <= 120 ? lines[0] : null;
  }
  return normalizeWhitespace(headingLines.join(' '));
}

async function readRawTextPrefix(publicDir, volId, rawInfo, maxBytes) {
  const rawFile = rawInfo?.file;
  if (!rawFile) return null;
  const txtPath = path.resolve(publicDir, '..', '..', 'teste', volId, 'text', rawFile);
  try {
    const fh = await fs.promises.open(txtPath, 'r');
    try {
      const buffer = Buffer.allocUnsafe(maxBytes);
      const { bytesRead } = await fh.read(buffer, 0, maxBytes, 0);
      return buffer.subarray(0, bytesRead).toString('utf-8');
    } finally {
      await fh.close();
    }
  } catch {
    return null;
  }
}

async function readTitleFromFile(publicDir, volId, rawInfo, maxBytes) {
  const rawFile = rawInfo?.file;
  if (!rawFile) return null;
  const txtPath = path.resolve(publicDir, '..', '..', 'teste', volId, 'text', rawFile);
  try {
    const fh = await fs.promises.open(txtPath, 'r');
    try {
      const buffer = Buffer.allocUnsafe(maxBytes);
      const { bytesRead } = await fh.read(buffer, 0, maxBytes, 0);
      return extractTitleFromText(buffer.subarray(0, bytesRead).toString('utf-8'));
    } finally {
      await fh.close();
    }
  } catch {
    return null;
  }
}

function buildRecordHtml(url, meta, filters, bodyContent) {
  const parts = [];
  parts.push('<!DOCTYPE html>');
  parts.push('<html lang="pt-BR">');
  parts.push('<head>');
  parts.push('  <meta charset="utf-8">');
  if (meta && meta.title) parts.push(`  <title>${escapeHtml(meta.title) + escapeHtml(meta.work ? ' - ' + meta.work : '')}</title>`);
  if (meta) {
    for (const [k, v] of Object.entries(meta)) {
      if (k === 'title') continue;
      parts.push(`  <meta data-pagefind-meta="${escapeHtml(k)}" content="${escapeHtml(v)}">`);
    }
  }
  if (filters) {
    for (const [fk, fv] of Object.entries(filters)) {
      if (Array.isArray(fv)) {
        for (const item of fv) {
          parts.push(`  <meta data-pagefind-meta="filter:${escapeHtml(fk)}" content="${escapeHtml(item)}">`);
        }
      }
    }
  }
  if (meta.author) {
    parts.push(`  <meta data-pagefind-meta="author" content="${escapeHtml(meta.author)}">`);
  }
  if (meta.work) {
    parts.push(`  <meta data-pagefind-meta="title" content="${escapeHtml(meta.work)}">`);
  }
  parts.push(`  <meta data-pagefind-meta="url" content="${escapeHtml(url)}">`);
  parts.push('</head>');
  parts.push('<body>');
  parts.push('  <main data-pagefind-body>');
  parts.push(`    <p>${escapeHtml(bodyContent)}</p>`);
  parts.push('  </main>');
  parts.push('</body>');
  parts.push('</html>');
  return parts.join('\n');
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
  const kwMap = new Map(keywords.map((k) => [k.id, k]));
  const onlyVolume = process.env.BENCH_VOLUME || null;

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

  // Concorrência de leitura de arquivos raw por volume.
  // SSD NVMe local: 64–128. HDD/rede: 8–16.
  const IO_CONCURRENCY = parseInt(process.env.IO_CONCURRENCY || '64', 10);
  const RAW_TITLE_BYTES = parseInt(process.env.RAW_TITLE_BYTES || '65536', 10);

  // Executa `fn` em paralelo sobre `items` com no máximo `concurrency` tarefas simultâneas.
  // Preserva a ordem dos resultados.
  async function parallelMap(items, concurrency, fn) {
    const results = new Array(items.length);
    let nextIdx = 0;
    async function worker() {
      while (true) {
        const i = nextIdx++;
        if (i >= items.length) return;
        results[i] = await fn(items[i], i);
      }
    }
    await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
    return results;
  }

  // Monta o record pronto para indexação a partir de uma página e seu raw text.
  function buildPageRecord(vol, vid, p, extractedTitle) {
    const kwMetas = (p.keyword_ids || [])
      .map((id) => kwMap.get(id))
      .filter((kw) => kw && usable(kw, params.minCount));

    const kwLabels = kwMetas.map((meta) => meta.label);
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
    if (p.author) contentPieces.push(p.author);
    if (p.work) contentPieces.push(p.work);
    if (extractedTitle) contentPieces.push(extractedTitle);
    if (topKeywords.length) contentPieces.push(topKeywords.join(' '));
    if (kwLabels.length) contentPieces.push(kwLabels.join(' '));
    if (bookNames.length) contentPieces.push(bookNames.join(' '));
    const content = normalizeWhitespace(contentPieces.join(' ').replace(/<[^>]*>?/gm, ' '));

    const filters = {
      collection: [vol.collection_id],
      volume: [vid],
    };
    if (bookNames.length) filters.book = bookNames;

    const pageMeta = {
      title: extractedTitle || enrichedTitle,
      volume: vid,
      page: String(p.page),
      collection: vol.collection_id,
      author: p.author,
      work: p.work,
    };
    if (bookNames.length) {
      pageMeta.books = bookNames.join(' • ');
    }

    const url = `${base}/viewer?doc=${vid}&page=${p.page}`;
    return { url, html: buildRecordHtml(url, pageMeta, filters, content) };
  }

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

    var tasks = [];

    for (const pb of metaObj.page_blocks || []) {
      let block;
      try {
        block = await readJSON(path.join(params.publicDir, pb.file));
      } catch {
        continue;
      }
      const pages = block.pages || [];
      if (!pages.length) continue;

      // FASE 1 — I/O paralela por shard: lê só o prefixo necessário do raw text.
      const rawTexts = await parallelMap(pages, IO_CONCURRENCY, (p) =>
        readTitleFromFile(params.publicDir, vid, p.raw, RAW_TITLE_BYTES)
      );

      tasks = tasks.concat(pages.map((p, i) => {
        const rec = buildPageRecord(vol, vid, p, rawTexts[i]);
        return index.addHTMLFile({ url: rec.url, content: rec.html }).then(({ errors }) => {
          if (errors?.length) {
            console.error(`Pagefind addHTMLFile errors em ${vid}:`, errors);
          }
          return p;
        });
      }));
    }

    const indexedPages = await Promise.all(tasks);
    for (const p of indexedPages) {
      totalRecords++;
      if (totalRecords % reportEvery === 0) {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        const rps = (totalRecords / ((Date.now() - startTime) / 1000)).toFixed(1);
        console.log(
          `[${elapsed}s] ${totalRecords} records indexed (${rps} r/s) — last: ${vid} p.${p.page}`,
        );
      }
    }
  }

  for (const vol of volumes) {
    if (onlyVolume && vol.id !== onlyVolume) continue;
    await indexVolume(vol);
  }

  if (!totalRecords) {
    console.error('Nenhum record adicionado ao índice Pagefind. Abortando.');
    process.exit(1);
  }

  console.log('Escrevendo arquivos do índice (aguarde)...');
  await index.writeFiles({ outputPath: params.outDir });
  await close();
  const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
  console.log(`[OK] Pagefind index written to ${params.outDir} (total records: ${totalRecords}, time: ${elapsed}s)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
