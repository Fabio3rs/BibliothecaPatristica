import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { baseUrl, relativePath, validateCompatibility, validateIntegrity } from './compat.js';
import { DiskCache, download, decode } from './cache.js';
import { createRemoteFetch } from './http.js';
import { createAgentTools } from './shared/agent-chat-tools.js';
import * as ocr from './shared/ocr-text.mjs';
import { createIndexadorPagefind } from '../vendor/indexador/indexador-pagefind.js';
import wasmFactory from '../vendor/indexador/indexador_wasm.cjs';

let wasm;
export const loadWasm = () => wasm ||= readFile(new URL('../vendor/indexador/indexador_wasm.wasm', import.meta.url))
  .then(wasmBinary => wasmFactory({ wasmBinary, print: (...args) => console.error(...args), printErr: (...args) => console.error(...args) }))
  .catch(error => { wasm = undefined; throw error; });

export function definitions() { return createAgentTools({}).definitions; }

export class BibliothecaRuntime {
  constructor(options = {}) {
    this.base = baseUrl(options.baseUrl || process.env.BIBLIOTHECA_BASE_URL);
    this.fetchImpl = createRemoteFetch({
      fetchImpl: options.fetchImpl || fetch,
      githubToken: options.githubToken ?? process.env.BIBLIOTHECA_GITHUB_TOKEN ?? '',
    });
    this.timeoutMs = Number(options.timeoutMs ?? process.env.BIBLIOTHECA_TIMEOUT_MS ?? 30_000);
    this.cacheOptions = {
      directory: options.cacheDir || process.env.BIBLIOTHECA_CACHE_DIR || path.join(process.env.XDG_CACHE_HOME || path.join(homedir(), '.cache'), 'bibliotheca-patristica-mcp'),
      maxBytes: Number(options.cacheMaxBytes ?? process.env.BIBLIOTHECA_CACHE_MAX_BYTES ?? 512 * 1024 * 1024),
      timeoutMs: this.timeoutMs, fetchImpl: this.fetchImpl,
    };
    this.queue = Promise.resolve();
  }
  async discover(signal) {
    if (this.compat) return this.compat;
    const bytes = await download(this.fetchImpl, new URL('compat.json', this.base).href, { signal, timeoutMs: this.timeoutMs, maxBytes: 1024 * 1024 });
    const compat = validateCompatibility(JSON.parse(bytes));
    const cache = new DiskCache({ ...this.cacheOptions, namespace: `${this.base.href}:${compat.dataset}:${compat.build}:contract1` });
    const inventoryBytes = await cache.get(new URL(compat.integrity.path, this.base).href, { ...compat.integrity, signal });
    const inventory = validateIntegrity(JSON.parse(inventoryBytes), compat);
    this.compat = compat;
    this.inventory = inventory;
    this.cache = cache;
    return compat;
  }
  // Accept only catalogued same-origin data and immutable OCR in this repository.
  resolve(input) {
    const raw = String(input);
    if (/[\\%\x00-\x20]/.test(raw) || raw.split('/').includes('..')) throw new Error('Unsafe remote URL.');
    const url = new URL(raw, this.base);
    if (url.username || url.password || url.hash || url.search) throw new Error('Unsafe remote URL credentials/query.');
    if (url.origin === this.base.origin && url.pathname.startsWith(this.base.pathname)) {
      const name = relativePath(url.pathname.slice(this.base.pathname.length));
      if (!Object.hasOwn(this.inventory.assets, name)) throw new Error(`Asset is not part of publication ${this.compat.build}: ${name}`);
      const expected = this.inventory.assets[name];
      return { url: url.href, expected, name };
    }
    const match = url.href.match(/^https:\/\/raw\.githubusercontent\.com\/Fabio3rs\/BibliothecaPatristica\/(?:refs\/(?:heads|tags)\/)?([^/]+)\/(teste\/[A-Z0-9.-]+\/text\/[A-Za-z0-9._-]+\.txt)$/i);
    if (match) {
      relativePath(match[2]);
      return { url: `https://raw.githubusercontent.com/Fabio3rs/BibliothecaPatristica/${this.compat.commit}/${match[2]}`, expected: {} };
    }
    throw new Error('Remote asset outside the dataset allowlist.');
  }
  async fetchData(input, signal) {
    const { url, expected } = this.resolve(input);
    const bytes = await this.cache.get(url, { ...expected, signal });
    return new Response(bytes);
  }
  async getJson(input, signal) {
    const resolved = this.resolve(input);
    const bytes = await this.cache.get(resolved.url, { ...resolved.expected, signal });
    const data = JSON.parse(decode(bytes));
    const name = resolved.name;
    let version;
    if (name === 'volumes.json') version = 1;
    else if (name?.startsWith('meta/')) version = data.block_type === 'pages' ? 2 : 1;
    else if (name?.startsWith('indices/')) version = 3;
    else if (name === this.compat.search.path) version = 2;
    else if (name === this.compat.indices.path) version = 1;
    if (version && data.schema_version !== version) throw new Error(`Unsupported schema in ${name}: ${data.schema_version}`);
    if (name === this.compat.search.path) {
      for (const index of this.compat.search.indexes) {
        const actual = data.indexes?.find(item => item.id === index.id);
        if (actual?.build_id !== index.build_id || actual.path !== index.path) throw new Error('Search build_id mismatch.');
      }
    }
    if (name === this.compat.sidecars.path) {
      if (data.schema !== this.compat.schemas.sidecars) throw new Error('Unsupported sidecar schema.');
      for (const [id, buildId] of Object.entries(this.compat.sidecars.indexes)) if (data.indexes?.[id]?.build_id !== buildId) throw new Error('Sidecar build_id mismatch.');
    }
    if (name === 'scripture/v3/manifest.json' && data.schema !== this.compat.schemas.scripture) throw new Error('Unsupported Scripture schema.');
    // Pin mutable raw GitHub references before both fetching and exposing source URLs.
    const pin = value => {
      if (Array.isArray(value)) return value.map(pin);
      if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, pin(v)]));
      if (typeof value === 'string' && /^https:\/\/raw\.githubusercontent\.com\/Fabio3rs\/BibliothecaPatristica\//i.test(value)) {
        return value.replace(/(githubusercontent\.com\/Fabio3rs\/BibliothecaPatristica\/)(?:refs\/(?:heads|tags)\/)?[^/]+\/(?=teste\/)/i, `$1${this.compat.commit}/`);
      }
      return value;
    };
    return pin(data);
  }
  execute(name, args, context = {}) {
    // QueryEngine has mutable search sessions. Serialize tools, release engines
    // per request, and let the bounded disk cache retain reusable downloads.
    const operation = this.queue.then(() => this.run(name, args, context));
    this.queue = operation.catch(() => {});
    return operation;
  }
  async run(name, args, context) {
    const deadline = AbortSignal.timeout(this.timeoutMs);
    const signal = context.signal ? AbortSignal.any([context.signal, deadline]) : deadline;
    signal.throwIfAborted();
    await this.discover(signal);
    const engines = [];
    const fetchImpl = input => this.fetchData(input, signal);
    const agent = createAgentTools({
      assetBase: this.base.href, origin: this.base.origin,
      searchIndexVersion: this.compat.build, indicesVersion: this.compat.build,
      fetchImpl, getJsonImpl: url => this.getJson(url, signal),
      getBinaryImpl: async url => decode(Buffer.from(await (await fetchImpl(url)).arrayBuffer())),
      loadOcrTextModule: async () => ocr,
      loadIndexadorModule: async () => ({ createIndexadorPagefind: options => {
        const engine = createIndexadorPagefind({ ...options, fetchImpl, wasmModuleLoader: loadWasm });
        engines.push(engine);
        return engine;
      } }),
    });
    try {
      const result = await agent.execute(name, args, { ...context, signal });
      signal.throwIfAborted();
      return { ...result, dataset: { build: this.compat.build, contract_version: 1 }, content_trust: 'untrusted_remote_data' };
    } finally { for (const engine of engines) engine.destroy(); }
  }
}
