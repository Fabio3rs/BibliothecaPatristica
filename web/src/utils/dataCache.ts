const cache = new Map<string, Promise<unknown>>();

/**
 * Fetches and memoizes JSON responses per-browser-tab. Reuses the same
 * in-flight promise to prevent duplicate network requests.
 */
export function getJsonOnce<T = unknown>(url: string): Promise<T> {
  if (!cache.has(url)) {
    const p = fetch(url).then(async (res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status} ao buscar ${url}`);
      return res.json() as Promise<T>;
    }).catch((err) => {
      // remove from cache so a subsequent call can retry
      cache.delete(url);
      throw err;
    });
    cache.set(url, p);
  }
  return cache.get(url) as Promise<T>;
}

export function clearJsonCache() {
  cache.clear();
}
