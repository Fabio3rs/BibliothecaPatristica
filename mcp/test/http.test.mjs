import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createRemoteFetch, httpError } from '../dist/http.js';
import { download } from '../dist/cache.js';
import { fetchOcrWithRetry } from '../dist/shared/agent-chat-resilient-fetch.js';

const commit = 'a'.repeat(40);
const raw = `https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/${commit}/teste/PG001/text/page.txt`;
const api = `https://api.github.com/repos/Fabio3rs/BibliothecaPatristica/contents/teste/PG001/text/page.txt?ref=${commit}`;
const token = 'test-only-not-a-real-token';

test('optional token only authenticates pinned repository OCR; raw stays the default', async () => {
  const requests = [];
  const fetchImpl = async (url, options) => { requests.push({ url, options }); return new Response('OCR'); };
  await createRemoteFetch({ fetchImpl })(raw);
  assert.equal(requests[0].url, raw);
  assert.equal(requests[0].options.headers, undefined);
  const remote = createRemoteFetch({ fetchImpl, githubToken: token });
  await remote(raw);
  assert.equal(requests[1].url, api);
  assert.equal(requests[1].options.headers.Authorization, `Bearer ${token}`);
  assert.equal(requests[1].options.headers.Accept, 'application/vnd.github.raw+json');
  for (const url of ['https://fabio3rs.github.io/BibliothecaPatristica/compat.json', 'http://localhost:1234/compat.json',
    raw.replace(commit, 'codex'), raw.replace('/teste/', '/other/'), raw.replace('/Fabio3rs/', '/other/'),
    raw.replace('raw.githubusercontent.com', 'raw.githubusercontent.com.evil.test'), api]) {
    await remote(url);
    assert.equal(requests.at(-1).options.headers, undefined, url);
  }
  assert.ok(requests.every(r => r.options.redirect === 'error'));
  assert.throws(() => createRemoteFetch({ githubToken: 'bad\ntoken' }), /without whitespace/);
});

test('quota diagnostics preserve Retry-After/reset and distinguish credentials from rate limits', async () => {
  const now = 1_700_000_000_000;
  const cases = [
    [429, { 'retry-after': '120' }, '', now + 120_000],
    [429, { 'retry-after': new Date(now + 90_000).toUTCString() }, '', now + 90_000],
    [403, { 'x-ratelimit-remaining': '0', 'x-ratelimit-reset': String(now / 1000 + 300), 'retry-after': '60' }, '', now + 300_000],
    [403, {}, '{"message":"You have exceeded a secondary rate limit."}', now + 60_000],
    [429, { 'retry-after': 'invalid' }, '', now + 60_000],
    [401, {}, token, null],
    [403, {}, 'Resource not accessible by personal access token', null],
    [503, {}, '', null],
  ];
  for (const [status, headers, body, expected] of cases) {
    const error = await httpError(new Response(body, { status, headers }), api, now);
    assert.equal(error.status, status);
    assert.equal(error.retryAt, expected);
    assert.equal(error.retryable, status === 503);
    assert.ok(!error.message.includes(token));
    if (expected) assert.ok(error.message.includes(new Date(expected).toISOString()));
  }
});

test('rate limit stops OCR retries and suppresses other requests to the origin until reset', async () => {
  let now = 1_700_000_000_000;
  let requests = 0;
  const remote = createRemoteFetch({ githubToken: token, now: () => now, fetchImpl: async () => {
    requests++;
    return requests === 1 ? new Response('', { status: 429, headers: { 'retry-after': '60' } }) : new Response('OCR');
  } });
  await assert.rejects(fetchOcrWithRetry(remote, raw), { code: 'rate_limited' });
  assert.equal(requests, 1);
  await assert.rejects(remote(raw.replace('page.txt', 'another.txt')), { code: 'rate_limited' });
  assert.equal(requests, 1);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(remote(raw, { signal: controller.signal }), { name: 'AbortError' });
  now += 60_000;
  assert.equal(await (await remote(raw)).text(), 'OCR');
  assert.equal(requests, 2);
});

test('authenticated failures redact transport details and never retry invalid credentials', async () => {
  let requests = 0;
  const denied = createRemoteFetch({ githubToken: token, fetchImpl: async () => {
    requests++; return new Response(token, { status: 401 });
  } });
  await assert.rejects(fetchOcrWithRetry(denied, raw), error => error.status === 401 && !error.message.includes(token));
  assert.equal(requests, 1);
  const failing = createRemoteFetch({ githubToken: token, fetchImpl: async () => { throw new Error(`Authorization: ${token}`); } });
  await assert.rejects(failing(raw), error => !error.message.includes(token) && /GitHub Contents/.test(error.message));
});

test('actual HTTP transport rejects redirects and preserves timeout/cancellation with a token', async t => {
  let mode = 'redirect';
  let leaked = 0;
  let arrived;
  const server = createServer((request, response) => {
    if (request.url === '/leak') { leaked++; response.end('leaked'); return; }
    assert.equal(request.headers.authorization, `Bearer ${token}`);
    if (mode === 'redirect') { response.writeHead(302, { location: '/leak' }); response.end(); }
    else arrived?.();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.close(resolve); server.closeAllConnections(); }));
  // Map the fixed API host to this offline HTTP fixture; production cannot override it.
  const remote = createRemoteFetch({ githubToken: token, fetchImpl: (url, options) => {
    assert.equal(url, api);
    return fetch(`http://127.0.0.1:${server.address().port}/contents`, options);
  } });
  await assert.rejects(download(remote, raw), /GitHub Contents request failed/);
  assert.equal(leaked, 0);
  mode = 'stall';
  await assert.rejects(download(remote, raw, { timeoutMs: 50 }), { name: 'TimeoutError' });
  const started = new Promise(resolve => { arrived = resolve; });
  const controller = new AbortController();
  const pending = assert.rejects(download(remote, raw, { signal: controller.signal }), { name: 'AbortError' });
  await started;
  controller.abort();
  await pending;
});
