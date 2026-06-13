const cache = new Map<string, Promise<unknown>>();

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
    const p = fetch(url).then(async (res) => {
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
    };
  }
}

if (typeof window !== 'undefined') {
  window.pdfocrDataCache = {
    getJsonOnce,
    loadJsonInBatches,
    clearJsonCache,
  };
}
