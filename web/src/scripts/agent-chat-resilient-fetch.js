const RETRYABLE_OCR_STATUSES = new Set([404, 408, 425, 429]);

function isRetryableOcrStatus(status) {
  return RETRYABLE_OCR_STATUSES.has(status) || status >= 500;
}

function abortableDelay(milliseconds, signal) {
  if (!milliseconds) return Promise.resolve();
  if (signal?.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'));

  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort);
      resolve();
    }, milliseconds);
    const onAbort = () => {
      clearTimeout(timeout);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

export async function fetchOcrWithRetry(fetchImpl, url, options = {}) {
  const {
    signal,
    attempts = 3,
    delays = [250, 750],
  } = options;
  let lastError = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await fetchImpl(url, { signal });
      if (response.ok || !isRetryableOcrStatus(response.status) || attempt === attempts - 1) {
        return response;
      }
    } catch (error) {
      if (error?.name === 'AbortError') throw error;
      lastError = error;
      if (attempt === attempts - 1) throw error;
    }

    await abortableDelay(delays[Math.min(attempt, delays.length - 1)] || 0, signal);
  }

  throw lastError || new Error('OCR request failed.');
}
