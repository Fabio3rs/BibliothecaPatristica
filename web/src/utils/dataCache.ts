const cache = new Map<string, Promise<unknown>>();
const navigationCacheIndexKey = 'pdfocr:navigation-json:v1:index';
const navigationCachePrefix = 'pdfocr:navigation-json:v1:';
const navigationCacheMaxEntries = 3;
const navigationCacheMaxChars = 900_000;
const navigationCacheTtlMs = 10 * 60 * 1000;

export interface JsonBatchResult<T = unknown> {
  url: string;
  ok: boolean;
  value: T | null;
  error?: unknown;
}

export interface JsonBatchOptions {
  batchSize?: number;
  delayMs?: number;
  signal?: AbortSignal;
}

/**
 * Fetches and memoizes JSON responses per-browser-tab. Reuses the same
 * in-flight promise to prevent duplicate network requests.
 */
export function getJsonOnce<T = unknown>(url: string): Promise<T> {
  if (!cache.has(url)) {
    const navigationValue = readNavigationJson<T>(url);
    const p = navigationValue !== undefined
      ? Promise.resolve(navigationValue)
      : fetch(url).then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status} ao buscar ${url}`);
        const bytes = await res.arrayBuffer();
        return decodeJsonBytes<T>(bytes);
      }).catch((err) => {
        // remove from cache so a subsequent call can retry
        cache.delete(url);
        throw err;
      });
    cache.set(url, p);
  }
  return cache.get(url) as Promise<T>;
}

function navigationKey(url: string): string {
  let hash = 2166136261;
  for (let index = 0; index < url.length; index += 1) {
    hash ^= url.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `${navigationCachePrefix}${(hash >>> 0).toString(36)}`;
}

function readNavigationIndex(storage: Storage): string[] {
  try {
    const value = JSON.parse(storage.getItem(navigationCacheIndexKey) || '[]');
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
  } catch {
    return [];
  }
}

function removeNavigationEntry(storage: Storage, key: string): void {
  storage.removeItem(key);
  const keys = readNavigationIndex(storage).filter((item) => item !== key);
  storage.setItem(navigationCacheIndexKey, JSON.stringify(keys));
}

function readNavigationJson<T>(url: string): T | undefined {
  if (typeof sessionStorage === 'undefined') return undefined;
  try {
    const key = navigationKey(url);
    const raw = sessionStorage.getItem(key);
    if (!raw) return undefined;
    const entry = JSON.parse(raw) as { url?: string; savedAt?: number; value?: T };
    if (entry.url !== url || !entry.savedAt || Date.now() - entry.savedAt > navigationCacheTtlMs) {
      removeNavigationEntry(sessionStorage, key);
      return undefined;
    }
    return entry.value;
  } catch {
    return undefined;
  }
}

/**
 * Preserves a small, explicitly selected JSON payload across a full navigation
 * in the same tab. This is intentionally opt-in so a search page does not put
 * every result shard into sessionStorage.
 */
export function rememberJsonForNavigation<T = unknown>(url: string, value: T): boolean {
  if (typeof sessionStorage === 'undefined' || !url || value === undefined) return false;
  try {
    const key = navigationKey(url);
    const serialized = JSON.stringify({ url, savedAt: Date.now(), value });
    if (serialized.length > navigationCacheMaxChars) return false;

    let keys = readNavigationIndex(sessionStorage).filter((item) => item !== key);
    while (keys.length >= navigationCacheMaxEntries) {
      const oldest = keys.shift();
      if (oldest) sessionStorage.removeItem(oldest);
    }
    sessionStorage.setItem(key, serialized);
    keys.push(key);
    sessionStorage.setItem(navigationCacheIndexKey, JSON.stringify(keys));
    cache.set(url, Promise.resolve(value));
    return true;
  } catch {
    return false;
  }
}

async function decodeJsonBytes<T>(bytes: ArrayBuffer): Promise<T> {
  const view = new Uint8Array(bytes);
  const isGzipped = view.length >= 2 && view[0] === 0x1f && view[1] === 0x8b;
  const payload = isGzipped ? await decompressGzip(view) : view;
  const text = new TextDecoder('utf-8').decode(payload);
  return JSON.parse(text) as T;
}

async function decompressGzip(bytes: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream !== 'undefined') {
    const blobBytes = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    const stream = new Blob([new Uint8Array(blobBytes)]).stream().pipeThrough(new DecompressionStream('gzip'));
    const buffer = await new Response(stream).arrayBuffer();
    return new Uint8Array(buffer);
  }

  throw new Error('Gzip não suportado neste navegador');
}

export function clearJsonCache() {
  cache.clear();
}

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function loadJsonInBatches<T = unknown>(
  urls: string[],
  options: JsonBatchOptions = {},
): Promise<JsonBatchResult<T>[]> {
  const batchSize = Math.max(1, Math.floor(options.batchSize || 4));
  const delayMs = Math.max(0, Math.floor(options.delayMs || 0));
  const results: JsonBatchResult<T>[] = [];

  for (let i = 0; i < urls.length; i += batchSize) {
    if (options.signal?.aborted) {
      throw new DOMException('Aborted', 'AbortError');
    }

    const batch = urls.slice(i, i + batchSize);
    const settled = await Promise.allSettled(batch.map((url) => getJsonOnce<T>(url)));

    settled.forEach((entry, idx) => {
      const url = batch[idx]!;
      if (entry.status === 'fulfilled') {
        results.push({ url, ok: true, value: entry.value });
        return;
      }
      results.push({ url, ok: false, value: null, error: entry.reason });
    });

    if (delayMs > 0 && i + batchSize < urls.length) {
      await sleep(delayMs);
    }
  }

  return results;
}

declare global {
  interface Window {
    pdfocrDataCache?: {
      getJsonOnce: typeof getJsonOnce;
      loadJsonInBatches: typeof loadJsonInBatches;
      clearJsonCache: typeof clearJsonCache;
      rememberJsonForNavigation: typeof rememberJsonForNavigation;
    };
  }
}

if (typeof window !== 'undefined') {
  window.pdfocrDataCache = {
    getJsonOnce,
    loadJsonInBatches,
    clearJsonCache,
    rememberJsonForNavigation,
  };
}
