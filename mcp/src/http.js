// Keep response diagnostics independent of bodies and credentials.
export class HttpError extends Error {
  constructor(status, url, retryAt = null) {
    super(`HTTP ${status} downloading ${url}${retryAt === null ? '' : `; rate limited, retry after ${new Date(retryAt).toISOString()}`}`);
    this.name = 'HttpError';
    this.status = status;
    this.retryAt = retryAt;
    this.code = retryAt === null ? 'http_error' : 'rate_limited';
    // The shared OCR retry must not retry a quota error or invalid credentials.
    this.retryable = retryAt === null && ([404, 408, 425].includes(status) || status >= 500);
  }
}

export async function httpError(response, url, now = Date.now()) {
  const retry = response.headers.get('retry-after');
  const reset = response.headers.get('x-ratelimit-reset');
  const exhausted = response.headers.get('x-ratelimit-remaining') === '0';
  let secondary = false;
  // GitHub can return a secondary limit as 403 with no quota headers.
  // Inspect only a bounded prefix, never expose the body in diagnostics.
  if (response.status === 403 && new URL(url).hostname === 'api.github.com' && response.body) {
    const reader = response.body.getReader();
    let prefix = '';
    try {
      while (prefix.length < 4096) {
        const { value, done } = await reader.read();
        if (done) break;
        prefix += Buffer.from(value.subarray(0, 4096 - prefix.length)).toString('utf8');
      }
      secondary = /rate limit|abuse detection/i.test(prefix);
    } finally { await reader.cancel(); }
  } else await response.body?.cancel();
  const limited = response.status === 429 || (response.status === 403 && (retry !== null || exhausted || secondary));
  let retryAt = null;
  if (limited) {
    const dates = [];
    if (retry !== null) dates.push(/^\d+(?:\.\d+)?$/.test(retry) ? now + Number(retry) * 1000 : Date.parse(retry));
    if (exhausted && reset !== null && /^\d+$/.test(reset)) dates.push(Number(reset) * 1000);
    const valid = dates.filter(value => Number.isFinite(value) && value > now && value <= 8.64e15);
    retryAt = valid.length ? Math.max(...valid) : now + 60_000;
  }
  return new HttpError(response.status, url, retryAt);
}

export function createRemoteFetch({ fetchImpl = fetch, githubToken = '', now = Date.now } = {}) {
  if (typeof githubToken !== 'string' || (githubToken && !/^[\x21-\x7e]+$/.test(githubToken))) {
    throw new Error('BIBLIOTHECA_GITHUB_TOKEN must be a token without whitespace.');
  }
  const cooldowns = new Map();
  return async (input, { signal } = {}) => {
    signal?.throwIfAborted();
    const url = new URL(input);
    // Only immutable OCR URLs already accepted by the runtime can use a token.
    const match = url.href.match(/^https:\/\/raw\.githubusercontent\.com\/Fabio3rs\/BibliothecaPatristica\/([a-f0-9]{40})\/(teste\/[A-Z0-9.-]+\/text\/[A-Za-z0-9._-]+\.txt)$/);
    const authenticated = Boolean(githubToken && match);
    const target = authenticated
      ? new URL(`https://api.github.com/repos/Fabio3rs/BibliothecaPatristica/contents/${match[2]}?ref=${match[1]}`)
      : url;
    const cooling = cooldowns.get(target.origin);
    if (cooling && cooling.retryAt > now()) throw new HttpError(cooling.status, url.href, cooling.retryAt);
    cooldowns.delete(target.origin);
    let response;
    try {
      response = await fetchImpl(target.href, {
        signal, redirect: 'error',
        ...(authenticated ? { headers: {
          Authorization: `Bearer ${githubToken}`,
          Accept: 'application/vnd.github.raw+json',
          'X-GitHub-Api-Version': '2022-11-28',
        } } : {}),
      });
    } catch (error) {
      signal?.throwIfAborted();
      // An injected transport may include request headers in its errors.
      if (authenticated) throw new Error('GitHub Contents request failed. Check connectivity and token configuration.');
      throw error;
    }
    if (!response.ok) {
      const error = await httpError(response, target.href, now());
      if (error.retryAt !== null) cooldowns.set(target.origin, { status: error.status, retryAt: error.retryAt });
      throw error;
    }
    return response;
  };
}
