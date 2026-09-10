export function foldScriptureText(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase()
    .replace(/[.]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

export function scriptureBookSlug(bookKey) {
  return String(bookKey || '').trim().replace(/\s+/g, '-');
}

export function joinScriptureRoute(baseRoute, segment) {
  const base = String(baseRoute || '').replace(/\/+$/, '');
  const child = String(segment || '').replace(/^\/+|\/+$/g, '');
  return `${base}/${encodeURIComponent(child)}/`;
}

function scriptureSegmentSlug(segment) {
  const [startChapter, startVerse, endChapter, endVerse] = segment;
  if (!startVerse && !endVerse) {
    return startChapter === endChapter
      ? `chapter-${startChapter}`
      : `chapter-${startChapter}-to-chapter-${endChapter}`;
  }
  const start = `chapter-${startChapter}-verse-${startVerse}`;
  if (startChapter === endChapter && startVerse === endVerse) return start;
  return `${start}-to-chapter-${endChapter}-verse-${endVerse}`;
}

export function scriptureReferenceSlug(segments) {
  if (!Array.isArray(segments) || !segments.length) return '';
  return segments.map(scriptureSegmentSlug).join('--and--');
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function bookAliases(bookKey, route) {
  const aliases = new Set([
    foldScriptureText(bookKey),
    foldScriptureText(scriptureBookSlug(bookKey).replaceAll('-', ' ')),
    foldScriptureText(route?.label),
    foldScriptureText(route?.label).replace(/^sao\s+/, ''),
    ...(route?.aliases || []).map(foldScriptureText),
  ]);
  return [...aliases].filter(Boolean);
}

export function findScriptureBook(manifest, reference, explicitBook = '') {
  const entries = Object.entries(manifest?.routes || {});
  const explicit = foldScriptureText(String(explicitBook || '').replaceAll('-', ' '));
  if (explicit) {
    const selected = entries.find(([bookKey, route]) => bookAliases(bookKey, route).includes(explicit));
    if (selected) return selected[0];
  }

  const normalizedReference = foldScriptureText(reference);
  const candidates = entries.flatMap(([bookKey, route]) => bookAliases(bookKey, route)
    .map((alias) => ({ bookKey, alias })))
    .sort((left, right) => right.alias.length - left.alias.length);
  return candidates.find(({ alias }) => new RegExp(`(^|\\s)${escapeRegExp(alias)}(?=\\s|$)`).test(normalizedReference))?.bookKey || '';
}

export function decodeScriptureSegments(flatSegments) {
  const decoded = [];
  for (let index = 0; index < (flatSegments || []).length; index += 4) {
    decoded.push(flatSegments.slice(index, index + 4));
  }
  return decoded;
}

function formatSegment(segment) {
  const [startChapter, startVerse, endChapter, endVerse] = segment;
  if (!startVerse && !endVerse) {
    return startChapter === endChapter ? `${startChapter}` : `${startChapter}–${endChapter}`;
  }
  const start = `${startChapter}:${startVerse}`;
  if (startChapter === endChapter && startVerse === endVerse) return start;
  return startChapter === endChapter
    ? `${start}–${endVerse}`
    : `${start}–${endChapter}:${endVerse}`;
}

export function formatScriptureLocator(segments) {
  return segments.map((segment, index) => {
    if (index === 0) return formatSegment(segment);
    const previous = segments[index - 1];
    const sameChapter = segment[0] === previous[0] && segment[2] === previous[2];
    if (sameChapter && segment[1]) {
      return segment[1] === segment[3] ? `${segment[1]}` : `${segment[1]}–${segment[3]}`;
    }
    return formatSegment(segment);
  }).join('; ');
}

export function parseScriptureLocator(value) {
  return parseScriptureLocators(value)?.[0] || null;
}

function romanToInteger(value) {
  const token = String(value || '').toUpperCase();
  if (!/^[IVXLCDM]+$/.test(token)) return null;
  const values = { I: 1, V: 5, X: 10, L: 50, C: 100, D: 500, M: 1000 };
  let total = 0;
  let previous = 0;
  for (const character of [...token].reverse()) {
    const current = values[character];
    total += current < previous ? -current : current;
    previous = Math.max(previous, current);
  }
  return total > 0 ? total : null;
}

function locatorTail(value) {
  const text = String(value || '');
  let start = 0;
  for (const match of text.matchAll(/\p{L}+/gu)) {
    if (
      !/^[ivxlcdm]+$/i.test(match[0])
      && !/^(?:e|et|and|ss|sqq|ff)$/i.test(match[0])
    ) start = match.index + match[0].length;
  }
  return text.slice(start).replace(/^[\s.]+/, '');
}

function skipSpaces(text, cursor) {
  while (cursor < text.length && /\s/u.test(text[cursor])) cursor += 1;
  return cursor;
}

function readNumber(text, cursor) {
  const start = skipSpaces(text, cursor);
  const match = text.slice(start).match(/^(\d+|[ivxlcdm]+)(?!\p{L})/iu);
  if (!match) return null;
  const value = /^\d+$/.test(match[1]) ? Number(match[1]) : romanToInteger(match[1]);
  if (!Number.isInteger(value) || value <= 0) return null;
  return { value, end: start + match[0].length };
}

function readPattern(text, cursor, pattern) {
  const start = skipSpaces(text, cursor);
  const match = text.slice(start).match(pattern);
  return match ? { token: match[0], end: start + match[0].length } : null;
}

function readVerseEndpoint(text, cursor, chapter, startVerse) {
  const first = readNumber(text, cursor);
  if (!first) return null;
  const separator = readPattern(text, first.end, /^[:,.]/u);
  if (separator) {
    const second = readNumber(text, separator.end);
    if (second && (
      separator.token === ':'
      || (
        first.value >= chapter
        && first.value <= chapter + 3
        && (first.value < startVerse || chapter >= startVerse)
      )
    )) {
      return [first.value, second.value, second.end];
    }
  }
  return [chapter, first.value, first.end];
}

export function parseScriptureLocators(value) {
  const text = locatorTail(value);
  const firstChapter = readNumber(text, 0);
  if (!firstChapter) return null;
  let cursor = firstChapter.end;
  const separator = readPattern(text, cursor, /^[:,.]/u);
  const segments = [];

  if (!separator) {
    const range = readPattern(text, cursor, /^[-‐‑‒–—]/u);
    if (range) {
      const endChapter = readNumber(text, range.end);
      if (endChapter) {
        segments.push([firstChapter.value, 0, endChapter.value, 0]);
        cursor = endChapter.end;
      }
    }
    if (!segments.length) segments.push([firstChapter.value, 0, firstChapter.value, 0]);
    return segments;
  }

  const firstVerse = readNumber(text, separator.end);
  if (!firstVerse) return null;
  cursor = firstVerse.end;
  let endChapter = firstChapter.value;
  let endVerse = firstVerse.value;
  const initialRange = readPattern(text, cursor, /^[-‐‑‒–—]/u);
  if (initialRange) {
    const endpoint = readVerseEndpoint(
      text,
      initialRange.end,
      firstChapter.value,
      firstVerse.value,
    );
    if (endpoint) {
      [endChapter, endVerse, cursor] = endpoint;
    }
  }
  segments.push([firstChapter.value, firstVerse.value, endChapter, endVerse]);

  while (true) {
    const openEnd = readPattern(text, cursor, /^(?:ss|sqq|ff)\.?/iu);
    if (openEnd) cursor = openEnd.end;
    const connector = readPattern(text, cursor, /^(?:[,;.]|\be\b|\bet\b|\band\b)/iu);
    if (!connector) break;
    const number = readNumber(text, connector.end);
    if (!number) break;
    const chapterSeparator = readPattern(text, number.end, /^[:,.]/u);
    const previous = segments[segments.length - 1];
    const connectorToken = connector.token.toLocaleLowerCase();
    const explicitChapter = Boolean(chapterSeparator) && (
      chapterSeparator.token === ':'
      || connector.token === ';'
      || number.value === previous[0]
      || number.value < previous[1]
      || (['e', 'et', 'and'].includes(connectorToken) && number.value === previous[0] + 1)
    );
    let currentChapter = previous[0];
    let currentVerse = number.value;
    let nextCursor = number.end;
    if (explicitChapter) {
      const verse = readNumber(text, chapterSeparator.end);
      if (!verse) break;
      currentChapter = number.value;
      currentVerse = verse.value;
      nextCursor = verse.end;
    }
    let currentEndChapter = currentChapter;
    let currentEndVerse = currentVerse;
    const range = readPattern(text, nextCursor, /^[-‐‑‒–—]/u);
    if (range) {
      const endpoint = readVerseEndpoint(text, range.end, currentChapter, currentVerse);
      if (!endpoint) break;
      [currentEndChapter, currentEndVerse, nextCursor] = endpoint;
    }
    segments.push([currentChapter, currentVerse, currentEndChapter, currentEndVerse]);
    cursor = nextCursor;
  }
  return segments;
}

function position(chapter, verse, end = false) {
  return chapter * 10000 + (verse || (end ? 9999 : 0));
}

function sameSegments(left, right) {
  return left.length === right.length && left.every((segment, index) => (
    segment.length === right[index].length
    && segment.every((value, valueIndex) => value === right[index][valueIndex])
  ));
}

function segmentContains(container, contained) {
  const containerStart = position(container[0], container[1]);
  const containerEnd = position(container[2], container[3], !container[3]);
  const containedStart = position(contained[0], contained[1]);
  const containedEnd = position(contained[2], contained[3], !contained[3]);
  return containerStart <= containedStart && containerEnd >= containedEnd;
}

function segmentsOverlap(left, right) {
  const leftStart = position(left[0], left[1]);
  const leftEnd = position(left[2], left[3], !left[3]);
  const rightStart = position(right[0], right[1]);
  const rightEnd = position(right[2], right[3], !right[3]);
  return leftStart <= rightEnd && leftEnd >= rightStart;
}

export function relationForScriptureSegments(referenceSegments, querySegments) {
  if (!querySegments?.length) return { rank: 10, relation: 'browse' };
  if (sameSegments(referenceSegments, querySegments)) return { rank: 0, relation: 'exact' };
  if (querySegments.every((query) => referenceSegments.some((reference) => segmentContains(reference, query)))) {
    return { rank: 1, relation: 'contains_query' };
  }
  if (referenceSegments.every((reference) => querySegments.some((query) => segmentContains(query, reference)))) {
    return { rank: 2, relation: 'contained_by_query' };
  }
  if (referenceSegments.some((reference) => querySegments.some((query) => segmentsOverlap(reference, query)))) {
    return { rank: 3, relation: 'overlap' };
  }
  return { rank: 99, relation: 'none' };
}

export function decodeScriptureLocations(shard, reference) {
  const locations = [];
  for (const volumePosting of reference?.[2] || []) {
    const volumeId = shard.volumes[volumePosting[0]];
    const postings = volumePosting[1] || [];
    let page = 0;
    for (let index = 0; index < postings.length; index += 2) {
      page += postings[index];
      const mask = postings[index + 1] || 0;
      locations.push({
        volume_id: volumeId,
        page,
        source_mask: mask,
        source_types: [
          ...(mask & 1 ? ['direct'] : []),
          ...(mask & 2 ? ['keyword_association'] : []),
          ...(mask & 4 ? ['ocr'] : []),
        ],
      });
    }
  }
  return locations;
}

function sourceMatches(mask, source) {
  if (source === 'direct') return Boolean(mask & 1);
  if (source === 'associated') return Boolean(mask & 2);
  if (source === 'ocr') return Boolean(mask & 4);
  return true;
}

function optionInteger(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(parsed)));
}

function compareSegments(left, right) {
  const a = left.segments.flat();
  const b = right.segments.flat();
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    if ((a[index] ?? -1) !== (b[index] ?? -1)) return (a[index] ?? -1) - (b[index] ?? -1);
  }
  return 0;
}

export function searchScriptureShard(shard, reference, options = {}) {
  const source = ['direct', 'associated', 'ocr'].includes(options.source) ? options.source : 'all';
  const matchMode = options.matchMode === 'exact' ? 'exact' : 'overlap';
  const offset = optionInteger(options.offset, 0, 0, 10_000);
  const limit = optionInteger(options.limit, 5, 1, 20);
  const locationOffset = optionInteger(options.locationOffset, 0, 0, 100_000);
  const locationsPerReference = optionInteger(options.locationsPerReference, 10, 1, 50);
  const querySegments = parseScriptureLocators(reference);
  const label = shard?.book?.[1] || shard?.book?.[0] || '';
  const normalizedReference = foldScriptureText(reference);

  const matches = (shard?.references || []).map((row) => {
    const segments = decodeScriptureSegments(row[1]);
    const locator = formatScriptureLocator(segments);
    const relation = relationForScriptureSegments(segments, querySegments);
    const textMatches = !querySegments && foldScriptureText(`${label} ${locator}`).includes(normalizedReference);
    const locations = decodeScriptureLocations(shard, row).filter((location) => sourceMatches(location.source_mask, source));
    return {
      segments,
      locator,
      relation: textMatches ? 'text_match' : relation.relation,
      rank: textMatches ? 4 : relation.rank,
      locations,
    };
  }).filter((item) => item.locations.length
      && item.rank < 99
      && (matchMode !== 'exact' || item.relation === 'exact'))
    .sort((left, right) => left.rank - right.rank || compareSegments(left, right));

  return {
    total: matches.length,
    offset,
    limit,
    next_offset: offset + limit < matches.length ? offset + limit : null,
    items: matches.slice(offset, offset + limit).map((item) => ({
      reference: `${label} ${item.locator}`.trim(),
      locator: item.locator,
      relation: item.relation,
      page_count: item.locations.length,
      volume_count: new Set(item.locations.map((location) => location.volume_id)).size,
      location_offset: locationOffset,
      locations_limit: locationsPerReference,
      next_location_offset: locationOffset + locationsPerReference < item.locations.length
        ? locationOffset + locationsPerReference
        : null,
      locations: item.locations.slice(locationOffset, locationOffset + locationsPerReference),
      locations_truncated: locationOffset > 0 || locationOffset + locationsPerReference < item.locations.length,
    })),
  };
}
