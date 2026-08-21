// @ts-nocheck
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

interface ScriptureManifest {
  routes?: Record<string, { label?: string; url?: string }>;
}

async function readScriptureManifest(): Promise<ScriptureManifest> {
  const path = join(process.cwd(), 'public', 'scripture', 'v3', 'manifest.json');
  const raw = await readFile(path, 'utf8');
  return JSON.parse(raw) as ScriptureManifest;
}

export async function listAlphaScriptureBooks() {
  const manifest = await readScriptureManifest();
  return Object.entries(manifest.routes || {})
    .map(([bookKey, route]) => ({
      bookKey,
      slug: bookKey.replace(/\s+/g, '-'),
      label: String(route?.label || bookKey),
    }));
}
