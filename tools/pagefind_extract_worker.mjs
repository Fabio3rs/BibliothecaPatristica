import { parentPort } from 'worker_threads';
import fs from 'fs/promises';
import path from 'path';

function normalizeWhitespace(s) {
  return String(s || '').replace(/\s+/g, ' ').trim();
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
  const candidates = [];
  let match;
  while ((match = blockRe.exec(String(text || '')))) {
    const body = String(match[2] || '')
      .split(/\r?\n/)
      .map((line) => normalizeWhitespace(line.replace(/<[^>]+>/g, ' ')))
      .filter(Boolean);
    for (const line of body) {
      if (/^\d+$/.test(line)) continue;
      if (/^[.\-_=*·•\s]+$/.test(line)) continue;
      if (line.length < 4) continue;
      candidates.push(line);
    }
  }
  if (!candidates.length) return null;

  const cleaned = candidates
    .map((line) => line.replace(/^\d+\s+/, '').replace(/\s+\d+$/, '').trim())
    .map((line) => line.replace(/\s*[-–—]\s*(Patrologia|Patrologie|Patrologiae|edi[cç][aã]o|ed\.|editio|Paris|Migne|Tomus|TOMUS|vol\.|volume|fasciculo|fascículo).*/i, '').trim())
    .map((line) => line.replace(/\s{2,}/g, ' '))
    .filter(Boolean);

  if (!cleaned.length) return null;
  const score = (s) => {
    const letters = (s.match(/[A-Za-zÀ-ÿΑ-Ωα-ω]/g) || []).length;
    const digits = (s.match(/\d/g) || []).length;
    let v = letters * 2 - digits * 3;
    if (s.length > 120) v -= 20;
    if (s.length < 12) v -= 10;
    if (/[.!?]$/.test(s)) v += 2;
    return v;
  };
  return cleaned.slice().sort((a, b) => score(b) - score(a))[0] || null;
}

function isHeaderLike(line) {
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

function classifyPageKind({ rawText = '', xmlText = '', title = '', body = '', summaryPage = '' }) {
  const probe = normalizeWhitespace([title, body, summaryPage, rawText].filter(Boolean).join(' ')).toLowerCase();
  const xmlProbe = String(xmlText || rawText || '').toLowerCase();

  if (!probe) return 'vazio';
  if (/^(a\s*)?(pagina\s+)?(vazia|em branco|sem texto)/i.test(probe)) return 'vazio';
  if (/(capa|contracapa|guarda|frontisp[ií]cio|folha de rosto|title page|page de titre)/i.test(probe)) return 'rosto';
  if (/(sum[aá]rio|índice|indice|elenchus|ordo rerum|contents|table of contents|toc)/i.test(probe)) return 'indice';
  if (/(conte[úu]do administrativo|administrativ|cota|carimbo|selo|etiqueta de biblioteca|classifica[cç][aã]o|marca de biblioteca)/i.test(probe)) return 'administrativa';
  if (/(gravura|illustratio|imagem|figura|estampa|plate|mapa)/i.test(probe)) return 'gravura';
  if (/tipo\s*=\s*["']texto["']/.test(xmlProbe)) return 'texto';
  if (/tipo\s*=\s*["']capa_ou_guarda["']/.test(xmlProbe)) return 'rosto';
  return 'texto';
}

function extractIndexableContent(rawText) {
  if (!rawText) return { title: null, body: '', kind: 'vazio' };
  const trimmed = rawText.trimStart();
  const isXml = /^<\s*pagina\b/i.test(trimmed) || /^<\?xml\b/i.test(trimmed);
  if (isXml) {
    const title = extractXmlHeaderBlock(rawText);
    const body = normalizeWhitespace(title || stripNoiseLines(rawText));
    return { title: title || null, body, kind: classifyPageKind({ rawText, xmlText: rawText, title, body }) };
  }
  const cleaned = stripNoiseLines(rawText);
  const lines = cleaned.split(/\n+/).map((line) => line.trim()).filter(Boolean);
  const headingLines = [];
  let sawUsefulText = false;
  for (const line of lines) {
    if (isShortNoiseLine(line) && !sawUsefulText) continue;
    if (isHeaderLike(line)) {
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
  const heading = headingLines.length ? normalizeWhitespace(headingLines.join(' ')) : null;
  const body = normalizeWhitespace(stripNoiseLines(rawText));
  return { title: heading, body, kind: classifyPageKind({ rawText, title: heading, body }) };
}

async function readRawText(publicDir, volId, rawInfo) {
  const rawFile = rawInfo?.file;
  if (!rawFile) return null;
  const txtPath = path.resolve(publicDir, '..', '..', 'teste', volId, 'text', rawFile);
  try {
    return await fs.readFile(txtPath, 'utf-8');
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
          parts.push(`  <meta data-pagefind-filter="${escapeHtml(fk)}[content]" content="${escapeHtml(item)}">`);
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

parentPort.on('message', async (job) => {
  const startedAt = Date.now();
  try {
    const readStartedAt = Date.now();
    const rawText = await readRawText(job.publicDir, job.volId, job.raw);
    const readMs = Date.now() - readStartedAt;

    const extractStartedAt = Date.now();
    const extracted = extractIndexableContent(rawText);
    const extractMs = Date.now() - extractStartedAt;

    const htmlStartedAt = Date.now();
    const bodyContent = normalizeWhitespace([
      job.summaryPage || '',
      job.content || '',
      extracted.title || '',
      // O `body` bruto foi removido do conteúdo indexado porque ele
      // inflava bastante o payload para o Pagefind e derrubava o throughput.
      // Para reativar depois, basta adicionar `extracted.body || ''` aqui,
      // mas o efeito esperado é aumentar bastante o custo de `addHTMLFile`.
      job.author || '',
      job.work || '',
      job.enrichedTitle || '',
    ].join(' '));

    const meta = {
      ...job.meta,
      page_kind: extracted.kind || 'texto',
    };
    if (extracted.title && (!meta.title || extracted.kind === 'rosto' || extracted.kind === 'indice')) {
      meta.title = extracted.title;
    } else if (extracted.title) {
      meta.source_title = extracted.title;
    }

    const html = buildRecordHtml(job.url, meta, job.filters, bodyContent);
    const htmlMs = Date.now() - htmlStartedAt;
    parentPort.postMessage({
      ok: true,
      id: job.id,
      result: {
        html,
        meta,
        bodyContent,
        timings: {
          totalMs: Date.now() - startedAt,
          readMs,
          extractMs,
          htmlMs,
        },
      },
    });
  } catch (err) {
    parentPort.postMessage({ ok: false, id: job.id, error: err?.message || String(err) });
  }
});
