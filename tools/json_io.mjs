import fs from 'fs';
import { gunzip as gunzipCallback } from 'node:zlib';
import { promisify } from 'node:util';

const gunzip = promisify(gunzipCallback);

async function decodeJsonBytes(bytes, sourceLabel = '') {
  let raw = bytes;
  if (raw?.byteLength >= 2 && raw[0] === 0x1f && raw[1] === 0x8b) {
    raw = await gunzip(raw);
  }

  const text = Buffer.from(raw).toString('utf8');
  return JSON.parse(text);
}

export async function readJsonMaybeGz(filePath) {
  try {
    const data = await fs.promises.readFile(filePath);
    return decodeJsonBytes(data, filePath);
  } catch (err) {
    if (err?.code === 'ENOENT' && String(filePath).endsWith('.gz')) {
      const fallbackPath = String(filePath).replace(/\.gz$/, '');
      const data = await fs.promises.readFile(fallbackPath);
      return decodeJsonBytes(data, fallbackPath);
    }
    throw err;
  }
}

export async function readJsonMaybeGzFromBytes(bytes) {
  return decodeJsonBytes(bytes);
}
