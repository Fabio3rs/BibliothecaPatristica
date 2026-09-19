// @ts-nocheck
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { scriptureBookLabel } from '../scripts/scripture-index.js';

interface ScriptureManifest {
  routes?: Record<string, { label?: string; labels?: Record<string, string>; url?: string }>;
}

async function readScriptureManifest(): Promise<ScriptureManifest> {
  const path = join(process.cwd(), 'public', 'scripture', 'v3', 'manifest.json');
  const raw = await readFile(path, 'utf8');
  return JSON.parse(raw) as ScriptureManifest;
}

export async function listAlphaScriptureBooks(locale = 'pt-br') {
  const manifest = await readScriptureManifest();
  return Object.entries(manifest.routes || {})
    .map(([bookKey, route]) => ({
      bookKey,
      slug: bookKey.replace(/\s+/g, '-'),
      label: scriptureBookLabel(route, locale, bookKey),
    }));
}
