import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, writeFile, rename, unlink, readdir, stat, utimes } from 'node:fs/promises';
import path from 'node:path';
import { gunzipSync } from 'node:zlib';
import { httpError } from './http.js';

export const digest = value => createHash('sha256').update(value).digest('hex');
export function decode(bytes) {
  return bytes[0] === 0x1f && bytes[1] === 0x8b
    ? gunzipSync(bytes, { maxOutputLength: 128 * 1024 * 1024 }) : bytes;
}
export async function download(fetchImpl, url, { signal, timeoutMs = 30_000, maxBytes = 64 * 1024 * 1024 } = {}) {
  const deadline = AbortSignal.timeout(timeoutMs);
  const combined = signal ? AbortSignal.any([signal, deadline]) : deadline;
  combined.throwIfAborted();
  const response = await fetchImpl(url, { signal: combined, redirect: 'error' });
  if (!response.ok) throw await httpError(response, url);
  if (Number(response.headers.get('content-length')) > maxBytes) { await response.body?.cancel(); throw new Error('Remote asset exceeds download limit.'); }
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    for (;;) {
      combined.throwIfAborted();
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) throw new Error('Remote asset exceeds download limit.');
      chunks.push(value);
    }
  } finally { await reader.cancel().catch(() => {}); }
  return Buffer.concat(chunks, size);
}

// The directory is dedicated to this cache. Versioned keys invalidate old formats;
// pruning covers all datasets/builds, not just the currently selected namespace.
export class DiskCache {
  constructor({ directory, namespace, maxBytes = 512 * 1024 * 1024, timeoutMs = 30_000, fetchImpl = fetch }) {
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1) throw new Error('Cache size and timeout must be positive integers.');
    Object.assign(this, { directory, namespace, maxBytes, timeoutMs, fetchImpl });
    this.pending = new Map();
    this.writes = Promise.resolve();
    this.lastPruneAt = -Infinity;
  }
  async get(url, { sha256, bytes, signal } = {}) {
    signal?.throwIfAborted();
    const key = digest(`v1\0${this.namespace}\0${url}\0${sha256 || ''}`);
    let entry = this.pending.get(key);
    if (entry?.controller.signal.aborted) entry = null;
    if (!entry) {
      const controller = new AbortController();
      entry = { controller, users: 0 };
      const current = entry;
      entry.promise = this.load(key, url, { sha256, bytes, signal: controller.signal }).finally(() => {
        if (this.pending.get(key) === current) this.pending.delete(key);
      });
      this.pending.set(key, entry);
    }
    entry.users++;
    return new Promise((resolve, reject) => {
      let settled = false;
      const finish = (fn, value) => {
        if (settled) return;
        settled = true;
        signal?.removeEventListener('abort', abort);
        if (--entry.users === 0) entry.controller.abort();
        fn(value);
      };
      const abort = () => finish(reject, signal.reason);
      signal?.addEventListener('abort', abort, { once: true });
      entry.promise.then(v => finish(resolve, v), e => finish(reject, e));
    });
  }
  async load(key, url, expected) {
    await mkdir(this.directory, { recursive: true, mode: 0o700 });
    const file = path.join(this.directory, `${key}.cache`);
    try {
      const stored = await readFile(file);
      const data = stored.subarray(64);
      if (stored.subarray(0, 64).toString() !== digest(data)) throw new Error('Corrupt cache');
      this.verify(data, expected);
      const now = new Date();
      await utimes(file, now, now).catch(() => {});
      // Recheck inherited/shared cache usage periodically, not on every hit.
      if (Date.now() - this.lastPruneAt >= 60_000) await this.serialize(async () => {
        if (Date.now() - this.lastPruneAt >= 60_000) await this.prune(0);
      });
      return data;
    } catch (error) {
      if (error.code && error.code !== 'ENOENT') throw error;
      await unlink(file).catch(e => { if (e.code !== 'ENOENT') throw e; });
    }
    const data = await download(this.fetchImpl, url, { signal: expected.signal, timeoutMs: this.timeoutMs });
    this.verify(data, expected);
    const stored = Buffer.concat([Buffer.from(digest(data)), data]);
    if (stored.length <= this.maxBytes) await this.serialize(async () => {
      expected.signal.throwIfAborted();
      await this.prune(stored.length);
      const temporary = `${file}.${randomUUID()}.tmp`;
      try {
        await writeFile(temporary, stored, { flag: 'wx', mode: 0o600 });
        expected.signal.throwIfAborted();
        await rename(temporary, file);
      } finally { await unlink(temporary).catch(e => { if (e.code !== 'ENOENT') throw e; }); }
    });
    return data;
  }
  verify(data, expected) {
    if ((expected.bytes != null && data.length !== expected.bytes) || (expected.sha256 && digest(data) !== expected.sha256)) {
      throw new Error('Published asset integrity mismatch; data may belong to another build. Restart after deployment finishes.');
    }
    decode(data); // Reject broken gzip before committing it to disk.
  }
  serialize(fn) {
    const operation = this.writes.then(fn);
    this.writes = operation.catch(() => {});
    return operation;
  }
  async prune(incoming) {
    const files = [];
    for (const name of await readdir(this.directory)) {
      if (!/^[a-f0-9]{64}\.cache$/.test(name)) continue;
      const file = path.join(this.directory, name);
      try { const info = await stat(file); files.push({ file, size: info.size, time: info.mtimeMs }); }
      catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
    let total = files.reduce((sum, f) => sum + f.size, incoming);
    for (const file of files.sort((a, b) => a.time - b.time)) {
      if (total <= this.maxBytes) break;
      await unlink(file.file).catch(e => { if (e.code !== 'ENOENT') throw e; });
      total -= file.size;
    }
    this.lastPruneAt = Date.now();
  }
}
