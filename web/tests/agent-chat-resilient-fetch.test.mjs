import test from 'node:test';
import assert from 'node:assert/strict';

import { fetchOcrWithRetry } from '../src/scripts/agent-chat-resilient-fetch.js';

function response(status) {
  return { ok: status >= 200 && status < 300, status };
}

test('retries a transient OCR response and returns the successful response', async () => {
  const statuses = [503, 200];
  const calls = [];
  const result = await fetchOcrWithRetry(async (url) => {
    calls.push(url);
    return response(statuses.shift());
  }, 'https://example.test/page.txt', { delays: [0, 0] });

  assert.equal(result.status, 200);
  assert.equal(calls.length, 2);
});

test('retries a temporary not-found response for a file declared by metadata', async () => {
  const statuses = [404, 200];
  let calls = 0;
  const result = await fetchOcrWithRetry(async () => {
    calls += 1;
    return response(statuses.shift());
  }, 'https://example.test/page.txt', { delays: [0, 0] });

  assert.equal(result.status, 200);
  assert.equal(calls, 2);
});

test('retries a transient network error', async () => {
  let calls = 0;
  const result = await fetchOcrWithRetry(async () => {
    calls += 1;
    if (calls === 1) throw new TypeError('temporary network failure');
    return response(200);
  }, 'https://example.test/page.txt', { delays: [0, 0] });

  assert.equal(result.status, 200);
  assert.equal(calls, 2);
});

test('does not retry a permanent client error', async () => {
  let calls = 0;
  const result = await fetchOcrWithRetry(async () => {
    calls += 1;
    return response(400);
  }, 'https://example.test/page.txt', { delays: [0, 0] });

  assert.equal(result.status, 400);
  assert.equal(calls, 1);
});

test('stops retrying when the request is aborted', async () => {
  const controller = new AbortController();
  let calls = 0;
  const pending = fetchOcrWithRetry(async () => {
    calls += 1;
    return response(503);
  }, 'https://example.test/page.txt', {
    signal: controller.signal,
    delays: [10_000, 10_000],
  });

  controller.abort();
  await assert.rejects(pending, { name: 'AbortError' });
  assert.equal(calls, 1);
});
