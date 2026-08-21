const BLOCK_PATTERN = /<bloco\b([^>]*)>([\s\S]*?)<\/bloco>/gi;
const ATTRIBUTE_PATTERN = /([\w:-]+)\s*=\s*(["'])(.*?)\2/g;

function decodeEntities(value) {
  return String(value || '')
    .replace(/&#x([0-9a-f]+);/gi, (_match, hex) => String.fromCodePoint(Number.parseInt(hex, 16)))
    .replace(/&#(\d+);/g, (_match, decimal) => String.fromCodePoint(Number.parseInt(decimal, 10)))
    .replace(/&nbsp;/gi, ' ')
    .replace(/&quot;/gi, '"')
    .replace(/&apos;|&#39;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&');
}

function attributes(value) {
  const output = {};
  for (const match of String(value || '').matchAll(ATTRIBUTE_PATTERN)) output[match[1]] = decodeEntities(match[3]);
  return output;
}

function stripMarkup(value) {
  return decodeEntities(String(value || '')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ')
    .replace(/<[^>]+>/g, ' '));
}

export function repairLineBreakHyphens(value) {
  return String(value || '').replace(
    /(\p{L})-[\t\f\v ]*\r?\n[\t\f\v ]*(?=\p{L})/gu,
    '$1',
  );
}

function paragraphsFromBlock(text, blockIndex) {
  const repaired = repairLineBreakHyphens(text).replace(/\r\n?/g, '\n').trim();
  if (!repaired) return [];
  return repaired
    .split(/\n[\t ]*\n+/)
    .map((paragraph) => paragraph.replace(/[\t ]*\n[\t ]*/g, ' ').replace(/[\t ]{2,}/g, ' ').trim())
    .filter(Boolean)
    .map((textValue, paragraphIndex) => ({
      id: `b${blockIndex + 1}:p${paragraphIndex + 1}`,
      block_index: blockIndex,
      paragraph_index: paragraphIndex,
      text: textValue,
    }));
}

export function parseOcrDocument(value) {
  const raw = String(value || '');
  const blocks = [];
  for (const match of raw.matchAll(BLOCK_PATTERN)) {
    const metadata = attributes(match[1]);
    const text = stripMarkup(match[2]).replace(/\r\n?/g, '\n').trim();
    if (!text) continue;
    const index = blocks.length;
    const bbox = String(metadata.bbox || '').split(',').map(Number);
    blocks.push({
      index,
      type: metadata.tipo || 'outro',
      script: metadata.script || 'desconhecido',
      bbox: bbox.length === 4 && bbox.every(Number.isFinite) ? bbox : null,
      text,
      repaired_text: repairLineBreakHyphens(text),
      paragraphs: paragraphsFromBlock(text, index),
    });
  }

  if (!blocks.length) {
    const text = stripMarkup(raw).replace(/\r\n?/g, '\n').trim();
    if (text) {
      blocks.push({
        index: 0,
        type: 'plain_text',
        script: 'desconhecido',
        bbox: null,
        text,
        repaired_text: repairLineBreakHyphens(text),
        paragraphs: paragraphsFromBlock(text, 0),
      });
    }
  }

  const paragraphs = blocks.flatMap((block) => block.paragraphs.map((paragraph) => ({
    ...paragraph,
    block_type: block.type,
    script: block.script,
    bbox: block.bbox,
  })));
  return {
    format: blocks.length && /<bloco\b/i.test(raw) ? 'structured_ocr_xml' : 'plain_text',
    blocks,
    paragraphs,
  };
}

export function normalizeOcrSearchText(value) {
  return repairLineBreakHyphens(value)
    .normalize('NFKD')
    .replace(/\p{M}+/gu, '')
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function editDistance(left, right) {
  if (left === right) return 0;
  if (!left.length) return right.length;
  if (!right.length) return left.length;
  let previous = Array.from({ length: right.length + 1 }, (_unused, index) => index);
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    const current = [leftIndex];
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      current[rightIndex] = Math.min(
        current[rightIndex - 1] + 1,
        previous[rightIndex] + 1,
        previous[rightIndex - 1] + (left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1),
      );
    }
    previous = current;
  }
  return previous[right.length];
}

function similarToken(queryToken, paragraphToken) {
  if (queryToken === paragraphToken) return 1;
  if (queryToken.length < 5 || paragraphToken.length < 5) return 0;
  return 1 - editDistance(queryToken, paragraphToken) / Math.max(queryToken.length, paragraphToken.length);
}

function scoreParagraph(paragraph, query) {
  const normalizedParagraph = normalizeOcrSearchText(paragraph.text);
  const normalizedQuery = normalizeOcrSearchText(query);
  if (!normalizedQuery || !normalizedParagraph) return null;
  if (normalizedParagraph.includes(normalizedQuery)) {
    return { score: 1, coverage: 1, proximity: 0, match_kind: 'exact_phrase' };
  }

  const queryTokens = normalizedQuery.split(' ').filter(Boolean);
  const paragraphTokens = normalizedParagraph.split(' ').filter(Boolean);
  if (!queryTokens.length || !paragraphTokens.length) return null;
  const positions = [];
  let fuzzy = false;
  for (const queryToken of queryTokens) {
    let best = { similarity: 0, position: -1 };
    paragraphTokens.forEach((paragraphToken, position) => {
      const similarity = similarToken(queryToken, paragraphToken);
      if (similarity > best.similarity) best = { similarity, position };
    });
    const threshold = queryTokens.length === 1 ? 0.86 : 0.78;
    if (best.similarity >= threshold) {
      positions.push(best.position);
      if (best.similarity < 1) fuzzy = true;
    }
  }
  const coverage = positions.length / queryTokens.length;
  if (coverage < (queryTokens.length === 1 ? 1 : 0.75)) return null;
  const proximity = positions.length > 1 ? Math.max(...positions) - Math.min(...positions) : 0;
  if (positions.length > 1 && proximity > Math.max(8, queryTokens.length * 4)) return null;
  return {
    score: coverage - Math.min(0.2, proximity / 200) - (fuzzy ? 0.03 : 0),
    coverage,
    proximity,
    match_kind: fuzzy ? 'fuzzy_tokens' : 'token_proximity',
  };
}

function boundedParagraphText(paragraph, query, maxChars) {
  if (paragraph.length <= maxChars) return { text: paragraph, truncated: false };
  const queryTokens = normalizeOcrSearchText(query).split(' ').filter(Boolean);
  const words = [...paragraph.matchAll(/[\p{L}\p{N}]+/gu)];
  let center = Math.floor(paragraph.length / 2);
  for (const word of words) {
    const normalizedWord = normalizeOcrSearchText(word[0]);
    if (queryTokens.some((token) => similarToken(token, normalizedWord) >= 0.78)) {
      center = word.index ?? center;
      break;
    }
  }
  const start = Math.max(0, Math.min(paragraph.length - maxChars, center - Math.floor(maxChars / 2)));
  return { text: paragraph.slice(start, start + maxChars), truncated: true };
}

export function findOcrParagraph(document, queries, { maxChars = 6_000 } = {}) {
  const variants = (Array.isArray(queries) ? queries : [queries])
    .map((item) => String(item || '').trim())
    .filter(Boolean);
  let best = null;
  for (const query of variants) {
    for (const paragraph of document?.paragraphs || []) {
      const match = scoreParagraph(paragraph, query);
      if (!match || (best && match.score <= best.score)) continue;
      best = { ...match, query, paragraph };
    }
  }
  if (!best) {
    return {
      matched: false,
      matched_query: null,
      match_kind: 'none',
      coverage: 0,
      paragraph: null,
      text: null,
      truncated: false,
    };
  }
  const bounded = boundedParagraphText(best.paragraph.text, best.query, maxChars);
  return {
    matched: true,
    matched_query: best.query,
    match_kind: best.match_kind,
    coverage: Number(best.coverage.toFixed(3)),
    proximity: best.proximity,
    paragraph: best.paragraph,
    text: bounded.text,
    truncated: bounded.truncated,
  };
}

export function readOcrWindow(document, { cursor = 0, maxChars = 6_000 } = {}) {
  const text = (document?.paragraphs || []).map((paragraph) => paragraph.text).join('\n\n');
  const start = Math.max(0, Math.min(Number(cursor) || 0, text.length));
  const end = Math.min(text.length, start + maxChars);
  return {
    text: text.slice(start, end),
    start,
    end,
    total_chars: text.length,
    truncated: end < text.length,
    next_cursor: end < text.length ? end : null,
  };
}
