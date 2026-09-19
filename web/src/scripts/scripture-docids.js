const MAGIC = Uint8Array.from([0x42, 0x53, 0x44, 0x49, 0x31]); // BSDI1

export function scriptureLocationKey(volumeId, page) {
  return `${String(volumeId || '')}:${Number(page)}`;
}

function pushVarint(output, value) {
  let current = Number(value);
  if (!Number.isSafeInteger(current) || current < 0 || current > 0xffff_ffff) {
    throw new Error(`Unsigned varint is out of range: ${value}`);
  }
  current >>>= 0;
  while (current >= 0x80) {
    output.push((current & 0x7f) | 0x80);
    current >>>= 7;
  }
  output.push(current);
}

function readVarint(bytes, cursor) {
  let value = 0;
  let shift = 0;
  for (let count = 0; count < 5; count += 1) {
    if (cursor.offset >= bytes.length) throw new Error('Truncated Scripture doc_id sidecar.');
    const byte = bytes[cursor.offset++];
    value |= (byte & 0x7f) << shift;
    if ((byte & 0x80) === 0) return value >>> 0;
    shift += 7;
  }
  throw new Error('Invalid Scripture doc_id varint.');
}

function zigZagEncode(value) {
  if (!Number.isSafeInteger(value) || value < -0x7fff_ffff || value > 0x7fff_ffff) {
    throw new Error(`Signed doc_id delta is out of range: ${value}`);
  }
  return ((value << 1) ^ (value >> 31)) >>> 0;
}

function zigZagDecode(value) {
  return (value >>> 1) ^ -(value & 1);
}

export function encodeScriptureDocIdSidecar(volumes, documents) {
  const volumeIndexes = new Map((volumes || []).map((volumeId, index) => [String(volumeId), index]));
  const groups = new Map();
  for (const document of documents || []) {
    const volumeId = String(document?.volume_id || '');
    const volumeIndex = volumeIndexes.get(volumeId);
    if (volumeIndex === undefined) continue;
    const page = Number(document?.page);
    const docId = Number(document?.doc_id);
    if (!Number.isInteger(page) || page < 1 || !Number.isInteger(docId) || docId < 0 || docId > 0xffff_ffff) {
      throw new Error(`Invalid Scripture document mapping: ${volumeId}:${document?.page} -> ${document?.doc_id}`);
    }
    const pages = groups.get(volumeIndex) || new Map();
    const previous = pages.get(page);
    if (previous !== undefined && previous !== docId) {
      throw new Error(`Conflicting doc_id for ${volumeId}:${page}: ${previous} and ${docId}`);
    }
    pages.set(page, docId);
    groups.set(volumeIndex, pages);
  }

  const output = [...MAGIC];
  const orderedGroups = [...groups.entries()].sort(([left], [right]) => left - right);
  pushVarint(output, orderedGroups.length);
  for (const [volumeIndex, pages] of orderedGroups) {
    const orderedPages = [...pages.entries()].sort(([left], [right]) => left - right);
    pushVarint(output, volumeIndex);
    pushVarint(output, orderedPages.length);
    let previousPage = 0;
    let previousDocId = 0;
    for (const [page, docId] of orderedPages) {
      pushVarint(output, page - previousPage);
      pushVarint(output, zigZagEncode(docId - previousDocId));
      previousPage = page;
      previousDocId = docId;
    }
  }
  return Uint8Array.from(output);
}

export function decodeScriptureDocIdSidecar(input, volumes) {
  const bytes = input instanceof Uint8Array ? input : new Uint8Array(input || 0);
  if (bytes.length < MAGIC.length || MAGIC.some((value, index) => bytes[index] !== value)) {
    throw new Error('Unsupported Scripture doc_id sidecar format.');
  }
  const cursor = { offset: MAGIC.length };
  const documents = new Map();
  const groupCount = readVarint(bytes, cursor);
  for (let groupIndex = 0; groupIndex < groupCount; groupIndex += 1) {
    const volumeIndex = readVarint(bytes, cursor);
    const volumeId = volumes?.[volumeIndex];
    if (!volumeId) throw new Error(`Scripture doc_id sidecar references unknown volume index ${volumeIndex}.`);
    const count = readVarint(bytes, cursor);
    let page = 0;
    let docId = 0;
    for (let index = 0; index < count; index += 1) {
      page += readVarint(bytes, cursor);
      docId += zigZagDecode(readVarint(bytes, cursor));
      if (page < 1 || docId < 0 || docId > 0xffff_ffff) {
        throw new Error(`Invalid decoded Scripture mapping for ${volumeId}:${page}.`);
      }
      documents.set(scriptureLocationKey(volumeId, page), docId >>> 0);
    }
  }
  if (cursor.offset !== bytes.length) throw new Error('Scripture doc_id sidecar has trailing bytes.');
  return documents;
}

export function mapScriptureLocationsToDocumentIds(locations, documentMaps) {
  const ids = new Set();
  const missing = new Set();
  for (const location of locations || []) {
    const key = scriptureLocationKey(location?.volume_id, location?.page);
    let found;
    for (const documents of documentMaps || []) {
      if (!documents?.has(key)) continue;
      found = documents.get(key);
      break;
    }
    if (found === undefined) missing.add(key);
    else ids.add(found);
  }
  return { ids: [...ids], missing: [...missing] };
}
