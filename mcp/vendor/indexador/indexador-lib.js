// Biblioteca modular para usar com o WASM do indexador.
// Exporta createIndexador(opts) que retorna uma instância com init() e search(q).

import {
    collectNormalizedTerms,
    containsBooleanSyntax,
    evaluateNormalizedDsl,
    normalizeQueryDsl,
} from './query-dsl.mjs';

const sharedModulePromises = new Map();
const UINT32_MAX = 0xffffffff;
const DEFAULT_MAX_BITSET_BYTES = 16 * 1024 * 1024;

export function createStringVector(module, values) {
    const vector = new module.VectorString();
    for (const value of values ?? []) {
        vector.push_back(String(value));
    }
    return vector;
}

function positiveInteger(value, fallback) {
    const parsed = Number(value);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function boundedPositiveInteger(value, fallback, maximum) {
    return Math.min(positiveInteger(value, fallback), maximum);
}

function normalizeDocumentIds(values, label) {
    let normalized;
    try {
        normalized = Array.from(values ?? []);
    } catch {
        throw new TypeError(`${label} deve ser iterável`);
    }

    for (let index = 0; index < normalized.length; index++) {
        const value = normalized[index];
        if (typeof value !== 'number' || !Number.isInteger(value) ||
            value < 0 || value > UINT32_MAX) {
            throw new TypeError(
                `${label} contém doc_id inválido no índice ${index}: ${String(value)}`,
            );
        }
    }
    return normalized;
}

function abortError(reason) {
    const message = reason instanceof Error
        ? reason.message
        : (reason == null ? 'Operação cancelada' : String(reason));
    const error = new Error(message);
    error.name = 'AbortError';
    if (reason instanceof Error) error.cause = reason;
    return error;
}

function throwIfAborted(signal) {
    if (signal?.aborted) throw abortError(signal.reason);
}

function waitWithSignal(promise, signal) {
    if (!signal) return promise;
    throwIfAborted(signal);
    return new Promise((resolve, reject) => {
        const onAbort = () => reject(abortError(signal.reason));
        signal.addEventListener('abort', onAbort, { once: true });
        Promise.resolve(promise).then(
            (value) => {
                signal.removeEventListener('abort', onAbort);
                resolve(value);
            },
            (error) => {
                signal.removeEventListener('abort', onAbort);
                reject(error);
            },
        );
    });
}

function filterNormalizedDocumentIds(
    candidates,
    values,
    algorithm = 'set',
    options = {},
) {
    let includes;

    if (algorithm === 'bitset') {
        const maxBitsetBytes = positiveInteger(
            options?.maxBitsetBytes,
            DEFAULT_MAX_BITSET_BYTES,
        );
        const candidateMaximum = candidates.reduce(
            (current, value) => Math.max(current, value),
            -1,
        );
        const relevantValues = values.filter((value) => value <= candidateMaximum);
        const maximum = relevantValues.reduce(
            (current, value) => Math.max(current, value),
            -1,
        );
        const requiredBytes = maximum < 0 ? 0 : Math.floor(maximum / 8) + 1;
        if (requiredBytes > maxBitsetBytes) {
            const allowed = new Set(relevantValues);
            includes = (value) => allowed.has(value);
        } else {
            const bits = new Uint8Array(requiredBytes);
            for (const value of relevantValues) {
                bits[Math.floor(value / 8)] |= 1 << (value % 8);
            }
            includes = (value) => value <= maximum &&
                Boolean(bits[Math.floor(value / 8)] & (1 << (value % 8)));
        }
    } else if (algorithm === 'sorted') {
        const sorted = Uint32Array.from(new Set(values)).sort();
        includes = (value) => {
            let left = 0;
            let right = sorted.length - 1;
            while (left <= right) {
                const middle = (left + right) >> 1;
                const candidate = sorted[middle];
                if (candidate === value) return true;
                if (candidate < value) left = middle + 1;
                else right = middle - 1;
            }
            return false;
        };
    } else if (algorithm === 'set') {
        const allowed = new Set(values);
        includes = (value) => allowed.has(value);
    } else {
        throw new Error(`Algoritmo de filtro de documentos desconhecido: ${algorithm}`);
    }

    return candidates.filter(includes);
}

export function filterRankedDocumentIds(
    docIds,
    allowedIds,
    algorithm = 'set',
    options = {},
) {
    return filterNormalizedDocumentIds(
        normalizeDocumentIds(docIds, 'docIds'),
        normalizeDocumentIds(allowedIds, 'allowedIds'),
        algorithm,
        options,
    );
}

// Prefix matching is opt-in. The limits bound both vocabulary expansion and
// the number of candidate documents read for each expanded term.
export function createIndexador(opts = {}) {
    const PAGE_SIZE = opts.PAGE_SIZE ?? 25;
    const MAX_PER_TOKEN = opts.MAX_PER_TOKEN ?? 20;
    const MAX_CONCURRENT_FETCHES = opts.MAX_CONCURRENT_FETCHES ?? 6;
    const PREFIX_MATCH = opts.prefixMatch ?? opts.PREFIX_MATCH ?? false;
    const PREFIX_MIN_LENGTH = positiveInteger(
        opts.prefixMinLength ?? opts.PREFIX_MIN_LENGTH,
        3,
    );
    const PREFIX_MAX_TERMS = boundedPositiveInteger(
        opts.prefixMaxTerms ?? opts.PREFIX_MAX_TERMS,
        32,
        256,
    );
    const PREFIX_MAX_PER_TERM = positiveInteger(
        opts.prefixMaxPerTerm ?? opts.PREFIX_MAX_PER_TERM,
        MAX_PER_TOKEN,
    );
    const SUGGESTIONS = opts.suggestions ?? opts.SUGGESTIONS ?? false;
    const SUGGESTION_LIMIT = boundedPositiveInteger(
        opts.suggestionLimit ?? opts.SUGGESTION_LIMIT,
        15,
        64,
    );
    const SUGGESTION_MIN_LENGTH = positiveInteger(
        opts.suggestionMinLength ?? opts.SUGGESTION_MIN_LENGTH,
        3,
    );
    const SUGGESTION_FALLBACK_MIN = Math.min(
        SUGGESTION_LIMIT,
        positiveInteger(
            opts.suggestionFallbackMin ?? opts.SUGGESTION_FALLBACK_MIN,
            5,
        ),
    );
    const SUGGESTION_WORDLISTS =
        opts.suggestionWordlists ?? opts.SUGGESTION_WORDLISTS ?? false;
    const SUGGESTION_WORDLIST_RADIUS = boundedPositiveInteger(
        opts.suggestionWordlistRadius ?? opts.SUGGESTION_WORDLIST_RADIUS,
        1,
        4,
    );
    const MAX_WASM_CACHE_BYTES = positiveInteger(
        opts.maxWasmCacheBytes ?? opts.MAX_WASM_CACHE_BYTES,
        16 * 1024 * 1024,
    );

    const wasmScriptUrl = opts.wasmScriptUrl || 'indexador_wasm.js';
    const indexMapUrl = opts.indexMapUrl || '/build/index.map';
    const fetchRoot = opts.fetchRoot || '/build';
    const fetchImpl = opts.fetchImpl || ((...args) => globalThis.fetch(...args));
    const wasmModuleLoader = typeof opts.wasmModuleLoader === 'function'
        ? opts.wasmModuleLoader
        : null;
    const onProgress = typeof opts.onProgress === 'function' ? opts.onProgress : null;
    const INDEX_BUILD_ID = opts.indexBuildId == null
        ? null
        : String(opts.indexBuildId);
    const REQUIRE_INDEX_BUILD_ID = opts.requireIndexBuildId ?? false;
    const MAX_BITSET_BYTES = positiveInteger(
        opts.maxBitsetBytes,
        DEFAULT_MAX_BITSET_BYTES,
    );
    const wasmCacheKey = wasmModuleLoader || wasmScriptUrl;

    let M = null;
    let engine = null;

    const assetCache = new Map();
    const pending = new Map();
    let cachedBytes = 0;
    let accessClock = 0;
    let destroyed = false;
    let activeFetches = 0;
    const fetchQueue = [];
    let fetchedFiles = 0;
    let fetchedWireBytes = 0;
    let fetchedDecodedBytes = 0;
    let operationSequence = 0;

    function createOperation(options = {}, prefix = 'operation', parent = null) {
        const requestedId = options?.operationId;
        const id = requestedId == null || String(requestedId).trim() === ''
            ? `${prefix}-${++operationSequence}`
            : String(requestedId);
        return {
            id,
            parentId: parent?.id ?? null,
            signal: options?.signal ?? null,
            fetchedFiles: 0,
            fetchedWireBytes: 0,
            fetchedDecodedBytes: 0,
        };
    }

    function errorDetail(error) {
        return {
            name: String(error?.name || 'Error'),
            message: String(error?.message || error),
        };
    }

    function progress(phase, state, detail = {}, operation = null) {
        if (!onProgress) return;
        try {
            onProgress({
                phase,
                state,
                operation_id: operation?.id ?? null,
                parent_operation_id: operation?.parentId ?? null,
                fetched_files: fetchedFiles,
                fetched_wire_bytes: fetchedWireBytes,
                fetched_decoded_bytes: fetchedDecodedBytes,
                operation_fetched_files: operation?.fetchedFiles ?? 0,
                operation_fetched_wire_bytes: operation?.fetchedWireBytes ?? 0,
                operation_fetched_decoded_bytes:
                    operation?.fetchedDecodedBytes ?? 0,
                ...detail,
            });
        } catch {
            // Observability must never break a search.
        }
    }

    function progressFailure(phase, error, detail, operation) {
        progress(
            phase,
            error?.name === 'AbortError' ? 'aborted' : 'error',
            { ...detail, error: errorDetail(error) },
            operation,
        );
    }

    function resolveAssetUrl(path) {
        if (/^[a-z][a-z0-9+.-]*:\/\//i.test(path)) {
            return path;
        }
        return path.startsWith('/')
            ? path
            : (fetchRoot.replace(/\/$/, '') + '/' + path.replace(/^\//, ''));
    }

    function isGzipBytes(bytes) {
        return bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
    }

    async function maybeDecompressGzip(bytes) {
        if (!isGzipBytes(bytes)) return bytes;
        if (typeof DecompressionStream !== 'function') {
            throw new Error('Browser sem suporte a DecompressionStream("gzip")');
        }

        try {
            const stream = new Blob([bytes]).stream()
                .pipeThrough(new DecompressionStream('gzip'));
            return new Uint8Array(await new Response(stream).arrayBuffer());
        } catch (e) {
            throw new Error('Falha ao descomprimir gzip: ' + (e?.message || e));
        }
    }

    function scheduleFetch(task) {
        return new Promise((resolve, reject) => {
            const run = async () => {
                activeFetches++;
                try {
                    resolve(await task());
                } catch (e) {
                    reject(e);
                } finally {
                    activeFetches--;
                    const next = fetchQueue.shift();
                    if (next) next();
                }
            };

            if (activeFetches < MAX_CONCURRENT_FETCHES) run();
            else fetchQueue.push(run);
        });
    }

    async function fetchBytes(path, operation, cancellable = true) {
        throwIfAborted(operation?.signal);
        const url = resolveAssetUrl(path);
        const fetchPromise = scheduleFetch(async () => {
            if (destroyed) throw new Error('Indexador destruído');
            progress('asset', 'start', { asset: path }, operation);
            try {
                const response = await fetchImpl(url, { cache: 'force-cache' });
                if (!response.ok) {
                    throw new Error(`HTTP ${response.status}: ${url}`);
                }

                const rawBytes = new Uint8Array(await response.arrayBuffer());
                let bytes = rawBytes;

                if (url.toLowerCase().endsWith('.gz') && isGzipBytes(rawBytes)) {
                    bytes = await maybeDecompressGzip(rawBytes);
                }

                fetchedFiles++;
                fetchedWireBytes += rawBytes.length;
                fetchedDecodedBytes += bytes.length;
                if (operation) {
                    operation.fetchedFiles++;
                    operation.fetchedWireBytes += rawBytes.length;
                    operation.fetchedDecodedBytes += bytes.length;
                }
                progress('asset', 'done', {
                    asset: path,
                    wire_bytes: rawBytes.length,
                    decoded_bytes: bytes.length,
                }, operation);
                return bytes;
            } catch (error) {
                progressFailure('asset', error, { asset: path }, operation);
                throw error;
            }
        });
        return cancellable
            ? waitWithSignal(fetchPromise, operation?.signal)
            : fetchPromise;
    }

    function evictAssets(requiredBytes = 0) {
        while (cachedBytes + requiredBytes > MAX_WASM_CACHE_BYTES) {
            let victim = null;
            for (const entry of assetCache.values()) {
                if (entry.pins !== 0) continue;
                if (!victim || entry.lastUsed < victim.lastUsed) victim = entry;
            }
            if (!victim) return;
            assetCache.delete(victim.url);
            cachedBytes -= victim.len;
            freeAsset(victim);
        }
    }

    function freeAsset(entry) {
        if (!entry || entry.freed) return;
        entry.freed = true;
        M._free(entry.ptr);
    }

    async function acquireAsset(path, operation) {
        if (destroyed) throw new Error('Indexador destruído');
        throwIfAborted(operation?.signal);
        const url = resolveAssetUrl(path);
        let entry = assetCache.get(url);
        if (!entry) {
            let promise = pending.get(url);
            if (!promise) {
                promise = (async () => {
                    const bytes = await fetchBytes(url, operation, false);
                    if (destroyed) throw new Error('Indexador destruído');
                    evictAssets(bytes.length);
                    const ptr = M._malloc(bytes.length);
                    if (!ptr) throw new Error(`WASM malloc failed: ${url}`);
                    M.HEAPU8.set(bytes, ptr);
                    return {
                        url,
                        ptr,
                        len: bytes.length,
                        pins: 0,
                        lastUsed: ++accessClock,
                        freed: false,
                    };
                })();
                pending.set(url, promise);
                const clearPending = () => {
                    if (pending.get(url) === promise) pending.delete(url);
                };
                promise.then(clearPending, clearPending);
            }
            entry = await waitWithSignal(promise, operation?.signal);
            if (destroyed) {
                freeAsset(entry);
                throw new Error('Indexador destruído');
            }
            if (!assetCache.has(url)) {
                assetCache.set(url, entry);
                cachedBytes += entry.len;
            }
        }
        entry.pins++;
        entry.lastUsed = ++accessClock;
        return entry;
    }

    function releaseAsset(entry) {
        entry.pins = Math.max(0, entry.pins - 1);
        entry.lastUsed = ++accessClock;
        evictAssets();
    }

    async function withCachedAsset(path, fn, operation) {
        const entry = await acquireAsset(path, operation);
        try {
            throwIfAborted(operation?.signal);
            return fn(entry.ptr, entry.len);
        } finally {
            releaseAsset(entry);
        }
    }

    function withWasmBytes(bytes, fn) {
        const ptr = M._malloc(bytes.length);
        if (!ptr) throw new Error('WASM malloc failed');
        try {
            M.HEAPU8.set(bytes, ptr);
            return fn(ptr, bytes.length);
        } finally {
            M._free(ptr);
        }
    }

    function normalizeVector(value) {
        if (!value) {
            return [];
        }
        if (Array.isArray(value)) {
            return value;
        }
        if (typeof value.size === 'function' && typeof value.get === 'function') {
            const out = [];
            for (let i = 0; i < value.size(); i++) {
                out.push(value.get(i));
            }
            return out;
        }
        return Array.from(value);
    }

    function uniqueStrings(values) {
        return Array.from(new Set(Array.from(values ?? [], (value) => String(value).trim())
            .filter(Boolean)));
    }

    function tokenizeToStrings(value) {
        const vector = engine.tokenize(value);
        try {
            return normalizeVector(vector).map(String);
        } finally {
            vector.delete?.();
        }
    }

    function suggestionContext(value) {
        const original = String(value ?? '');
        const termPattern = /[\p{L}\p{N}_]+/gu;
        let match = null;
        for (let candidate = termPattern.exec(original); candidate;
            candidate = termPattern.exec(original)) {
            if (!/^(?:and|or|not)$/i.test(candidate[0])) {
                match = candidate;
            }
        }
        if (!match || match.index == null) return null;
        const normalized = tokenizeToStrings(match[0])[0] ?? '';
        if (Array.from(normalized).length < SUGGESTION_MIN_LENGTH) return null;
        return {
            original,
            normalized,
            start: match.index,
            end: match.index + match[0].length,
        };
    }

    function createQuerySession(context) {
        const length = context ? Array.from(context.normalized).length : 0;
        const maxDistance = length >= 6 ? 2 : length >= 4 ? 1 : 0;
        return {
            wasm: new M.QuerySession(
                context?.normalized ?? '',
                context ? SUGGESTION_LIMIT : 0,
                maxDistance,
            ),
            suggestionAssets: new Set(),
            suggestionsEnabled: Boolean(context),
        };
    }

    function reserveSuggestionAsset(querySession, asset) {
        const key = resolveAssetUrl(asset);
        if (!querySession.suggestionsEnabled ||
            querySession.suggestionAssets.has(key)) {
            return false;
        }
        querySession.suggestionAssets.add(key);
        return true;
    }

    function scanCachedSuggestionShards(querySession) {
        if (!querySession.suggestionsEnabled) return;
        for (const entry of assetCache.values()) {
            if (querySession.wasm.suggestion_count() >=
                SUGGESTION_FALLBACK_MIN) {
                return;
            }
            if (!/(?:^|\/)index_\d+\.gz(?:$|[?#])/i.test(entry.url) ||
                !reserveSuggestionAsset(querySession, entry.url)) {
                continue;
            }
            querySession.wasm.scan_suggestions_raw(entry.ptr, entry.len);
        }
    }

    async function collectSuggestions(querySession, context, operation) {
        if (!context) return [];
        throwIfAborted(operation?.signal);
        scanCachedSuggestionShards(querySession);
        if (SUGGESTION_WORDLISTS &&
            querySession.wasm.suggestion_count() < SUGGESTION_FALLBACK_MIN) {
            const vector = engine.get_neighbor_wordlist_files(
                context.normalized,
                SUGGESTION_WORDLIST_RADIUS,
            );
            let wordlists;
            try {
                wordlists = uniqueStrings(normalizeVector(vector));
            } finally {
                vector.delete?.();
            }
            await Promise.allSettled(wordlists.map((wordlist) =>
                withCachedAsset(wordlist, (ptr, len) =>
                    querySession.wasm.scan_wordlist_raw(ptr, len), operation)));
        }

        throwIfAborted(operation?.signal);
        const vector = querySession.wasm.suggestions();
        try {
            return normalizeVector(vector).map((candidate) => {
                const replacement = String(candidate.replacement);
                return {
                    value: context.original.slice(0, context.start) +
                        replacement + context.original.slice(context.end),
                    term: context.normalized,
                    replacement,
                    kind: candidate.prefix ? 'prefix' : 'correction',
                    distance: Number(candidate.distance) || 0,
                    globalImportance:
                        Number(candidate.global_importance) || 0,
                };
            });
        } finally {
            vector.delete?.();
        }
    }

    function tempUint32Array(ids) {
        const ptr = M._malloc(ids.length * 4);
        if (!ptr) throw new Error('WASM malloc failed for doc ids');
        new Uint32Array(M.HEAPU8.buffer, ptr, ids.length).set(ids);
        return { ptr, free: () => M._free(ptr) };
    }

    async function loadWasm() {
        if (M) return M;
        if (!sharedModulePromises.has(wasmCacheKey)) {
            const promise = (async () => {
                if (wasmModuleLoader) {
                    const module = await wasmModuleLoader({ wasmScriptUrl });
                    if (!module) throw new Error('ModuleInstance não encontrado');
                    return module;
                }
                await new Promise((resolve, reject) => {
                    const s = document.createElement('script');
                    s.src = wasmScriptUrl;
                    s.onload = resolve;
                    s.onerror = () => reject(new Error('Falha ao carregar ' + wasmScriptUrl));
                    document.head.appendChild(s);
                });

                const factory = (typeof createModule === 'function') ? createModule
                    : (typeof Module === 'function') ? Module
                        : null;
                const module = factory ? await factory() : window.Module;
                if (!module) throw new Error('ModuleInstance não encontrado');
                return module;
            })();
            sharedModulePromises.set(wasmCacheKey, promise);
        }
        try {
            M = await sharedModulePromises.get(wasmCacheKey);
        } catch (error) {
            sharedModulePromises.delete(wasmCacheKey);
            throw error;
        }
        if (!M) throw new Error('ModuleInstance não encontrado');
        return M;
    }

    async function init(initOptions = {}) {
        if (destroyed) throw new Error('Indexador destruído');
        if (engine) return engine;
        const operation = createOperation(initOptions, 'init');
        progress('init', 'start', {}, operation);
        try {
            throwIfAborted(operation.signal);
            await waitWithSignal(loadWasm(), operation.signal);
            throwIfAborted(operation.signal);
            engine = new M.QueryEngine();
            const bytes = await fetchBytes(indexMapUrl, operation);
            throwIfAborted(operation.signal);
            if (destroyed) throw new Error('Indexador destruído');
            const res = withWasmBytes(bytes, (ptr, len) =>
                engine.load_index_map_raw(ptr, len));
            if (!res.startsWith('ok:')) {
                throw new Error('load_index_map_raw: ' + res);
            }
            progress('init', 'done', {}, operation);
            return engine;
        } catch (error) {
            engine?.delete?.();
            engine = null;
            progressFailure('init', error, {}, operation);
            throw error;
        }
    }

    async function loadExactWordResults(tokens, maxPerToken, querySession, operation) {
        const uniqueTokens = uniqueStrings(tokens);
        if (uniqueTokens.length === 0) {
            return new Map();
        }

        const tokenVector = createStringVector(M, uniqueTokens);
        let shardNames;
        try {
            const shardVector = engine.get_shard_files(tokenVector);
            try {
                shardNames = normalizeVector(shardVector)
                    .map((s) => String(s).trim())
                    .filter(Boolean);
            } finally {
                shardVector.delete?.();
            }
        } finally {
            tokenVector.delete?.();
        }

        const shardToTokens = new Map();
        for (let i = 0; i < uniqueTokens.length; i++) {
            const shard = shardNames[i];
            if (!shard) continue;
            const bucket = shardToTokens.get(shard) ?? [];
            bucket.push(uniqueTokens[i]);
            shardToTokens.set(shard, bucket);
        }

        const termData = new Map();

        await Promise.all(Array.from(shardToTokens.entries()).map(async ([shard, shardTokens]) => {
            const shardTokenVector = createStringVector(M, shardTokens);
            const scanSuggestions = reserveSuggestionAsset(querySession, shard);
            let wordResultsVector;
            try {
                wordResultsVector = await withCachedAsset(
                    shard,
                    (ptr, len) => querySession.wasm.scan_exact_raw_limited(
                        ptr,
                        len,
                        shardTokenVector,
                        maxPerToken,
                        scanSuggestions,
                    ),
                    operation,
                );
                for (const wr of normalizeVector(wordResultsVector)) {
                    try {
                        const docs = new Map();
                        for (const pair of normalizeVector(wr.docImp)) {
                            if (pair == null) continue;
                            const id = pair[0] >>> 0;
                            const importance = pair[1] ?? {};
                            docs.set(id, importance);
                        }

                        termData.set(String(wr.word), {
                            docs,
                            globalImportance: wr.global_importance,
                        });
                    } finally {
                        wr.docImp?.delete?.();
                    }
                }
            } finally {
                wordResultsVector?.delete?.();
                shardTokenVector.delete?.();
            }
        }));

        return termData;
    }

    function prefixEnabledFor(token) {
        return PREFIX_MATCH && Array.from(token).length >= PREFIX_MIN_LENGTH;
    }

    function weightedImportance(globalImportance, importance) {
        return globalImportance *
            (0.5 * (importance?.local_importance ?? 0) +
                0.5 * Math.log1p(importance?.term_freq ?? 0));
    }

    async function loadPrefixWordResult(token, maxPerToken, querySession, operation) {
        const shardVector = engine.get_prefix_shard_files(token);
        let shardNames;
        try {
            shardNames = uniqueStrings(normalizeVector(shardVector));
        } finally {
            shardVector.delete?.();
        }

        const result = { docs: new Map(), globalImportance: 0 };
        const docsPerExpandedTerm = maxPerToken > 0
            ? Math.min(maxPerToken, PREFIX_MAX_PER_TERM)
            : PREFIX_MAX_PER_TERM;

        await Promise.all(shardNames.map(async (shard) => {
            const scanSuggestions = reserveSuggestionAsset(querySession, shard);
            const wordResultsVector = await withCachedAsset(
                shard,
                (ptr, len) => querySession.wasm.scan_prefix_raw_limited(
                    ptr,
                    len,
                    token,
                    PREFIX_MAX_TERMS,
                    docsPerExpandedTerm,
                    scanSuggestions,
                ),
                operation,
            );
            try {
                for (const wr of normalizeVector(wordResultsVector)) {
                    try {
                        const globalImportance = wr.global_importance ?? 0;
                        result.globalImportance = Math.max(result.globalImportance, globalImportance);
                        for (const pair of normalizeVector(wr.docImp)) {
                            if (pair == null) continue;
                            const id = pair[0] >>> 0;
                            const importance = pair[1] ?? {};
                            const previous = result.docs.get(id);
                            if (!previous ||
                                weightedImportance(globalImportance, importance) >
                                weightedImportance(previous.global_importance, previous)) {
                                result.docs.set(id, {
                                    ...importance,
                                    global_importance: globalImportance,
                                    matched_term: String(wr.word),
                                });
                            }
                        }
                    } finally {
                        wr.docImp?.delete?.();
                    }
                }
            } finally {
                wordResultsVector.delete?.();
            }
        }));

        return result;
    }

    async function loadWordResults(tokens, maxPerToken, querySession, operation) {
        const uniqueTokens = uniqueStrings(tokens);
        const exactTokens = uniqueTokens.filter((token) => !prefixEnabledFor(token));
        const prefixTokens = uniqueTokens.filter(prefixEnabledFor);
        const termData = await loadExactWordResults(
            exactTokens,
            maxPerToken,
            querySession,
            operation,
        );

        await Promise.all(prefixTokens.map(async (token) => {
            termData.set(token, await loadPrefixWordResult(
                token,
                maxPerToken,
                querySession,
                operation,
            ));
        }));
        return termData;
    }

    async function collectDocuments(docsPerShard, pageSlice, operation) {
        const rankOf = new Map(pageSlice.map((id, i) => [id, i]));
        const collected = new Array(pageSlice.length).fill(null);

        const perShard = new Map();
        for (const id of pageSlice) {
            const shard = Math.floor(id / docsPerShard);
            const bucket = perShard.get(shard) ?? [];
            bucket.push(id);
            perShard.set(shard, bucket);
        }

        await Promise.all(Array.from(perShard.entries()).map(async ([shard, ids]) => {
            const tmp = tempUint32Array(ids);
            try {
                const docsVector = await withCachedAsset(
                    `documents_${shard}.gz`,
                    (ptr, len) => engine.get_documents_blob_raw(
                        ptr,
                        len,
                        shard,
                        tmp.ptr,
                        ids.length,
                    ),
                    operation,
                );
                try {
                    const docs = normalizeVector(docsVector);
                    for (let j = 0; j < ids.length; j++) {
                        const rank = rankOf.get(ids[j]);
                        if (rank != null && docs[j]) {
                            collected[rank] = docs[j];
                        }
                    }
                } finally {
                    docsVector.delete?.();
                }
            } finally {
                tmp.free();
            }
        }));

        return collected;
    }

    function createSearchResult(
        docIds,
        docScores,
        querySize,
        docsPerShard,
        parentOperation,
        inheritedMetadata = {},
    ) {
        const total = docIds.length;
        const totalPages = Math.ceil(total / PAGE_SIZE);
        let searchResult;

        function filterDocumentIds(allowedIds, options = {}) {
            const started = globalThis.performance?.now?.() ?? Date.now();
            const algorithm = options.algorithm ?? 'set';
            const operation = createOperation(options, 'scope', parentOperation);
            const scopeBuildId = options.indexBuildId == null
                ? null
                : String(options.indexBuildId);
            progress('scope', 'start', {
                algorithm,
                candidates: docIds.length,
            }, operation);
            try {
                throwIfAborted(operation.signal);
                if (REQUIRE_INDEX_BUILD_ID &&
                    (INDEX_BUILD_ID == null || scopeBuildId == null)) {
                    throw new Error(
                        'indexBuildId é obrigatório no indexador e no escopo de documentos',
                    );
                }
                if (scopeBuildId != null && INDEX_BUILD_ID == null) {
                    throw new Error(
                        'Escopo informa indexBuildId, mas o indexador não foi configurado com indexBuildId',
                    );
                }
                if (scopeBuildId != null && scopeBuildId !== INDEX_BUILD_ID) {
                    throw new Error(
                        `Escopo pertence ao índice ${scopeBuildId}, mas o indexador usa ${INDEX_BUILD_ID}`,
                    );
                }
                const values = normalizeDocumentIds(allowedIds, 'allowedIds');
                const filteredIds = filterNormalizedDocumentIds(
                    docIds,
                    values,
                    algorithm,
                    { maxBitsetBytes: options.maxBitsetBytes ?? MAX_BITSET_BYTES },
                );
                throwIfAborted(operation.signal);
                progress('scope', 'done', {
                    algorithm,
                    allowed_documents: values.length,
                    candidates: docIds.length,
                    matched_documents: filteredIds.length,
                    duration_ms:
                        (globalThis.performance?.now?.() ?? Date.now()) - started,
                }, operation);
                return createSearchResult(
                    filteredIds,
                    docScores,
                    querySize,
                    docsPerShard,
                    operation,
                    {
                        ...inheritedMetadata,
                        suggestions: searchResult.suggestions,
                    },
                );
            } catch (error) {
                progressFailure('scope', error, {
                    algorithm,
                    candidates: docIds.length,
                    duration_ms:
                        (globalThis.performance?.now?.() ?? Date.now()) - started,
                }, operation);
                throw error;
            }
        }

        async function getRange(from, to, rangeOptions = {}) {
            const started = globalThis.performance?.now?.() ?? Date.now();
            const operation = createOperation(
                rangeOptions,
                'documents',
                parentOperation,
            );
            const rangeStart = Math.min(
                Math.max(0, Math.trunc(Number(from) || 0)),
                total,
            );
            const rangeEnd = Math.min(
                Math.max(rangeStart, Math.trunc(Number(to) || 0)),
                total,
            );
            const rangeSlice = docIds.slice(rangeStart, rangeEnd);
            progress('documents', 'start', {
                from: rangeStart,
                to: rangeEnd,
                documents: rangeSlice.length,
            }, operation);
            try {
                throwIfAborted(operation.signal);
                const collected = await collectDocuments(
                    docsPerShard,
                    rangeSlice,
                    operation,
                );
                throwIfAborted(operation.signal);

                const results = [];
                for (let i = 0; i < collected.length; i++) {
                    const document = collected[i];
                    if (!document) continue;
                    const id = rangeSlice[i];
                    const metadata = docScores.get(id);
                    const hits = metadata ? [...metadata.hits] : [];
                    const coverage = metadata && querySize > 0
                        ? `${metadata.hits.size}/${querySize}`
                        : '';
                    results.push({
                        id,
                        name: document.name,
                        url: document.url,
                        score: metadata?.score ?? 0,
                        hits,
                        coverage,
                        rank: rangeStart + i + 1,
                    });
                }

                progress('documents', 'done', {
                    from: rangeStart,
                    to: rangeEnd,
                    documents: results.length,
                    duration_ms:
                        (globalThis.performance?.now?.() ?? Date.now()) - started,
                }, operation);
                return results;
            } catch (error) {
                progressFailure('documents', error, {
                    from: rangeStart,
                    to: rangeEnd,
                    documents: rangeSlice.length,
                    duration_ms:
                        (globalThis.performance?.now?.() ?? Date.now()) - started,
                }, operation);
                throw error;
            }
        }

        function getPage(page, pageOptions = {}) {
            const pageNumber = Math.max(0, Math.trunc(Number(page) || 0));
            const from = pageNumber * PAGE_SIZE;
            return getRange(from, from + PAGE_SIZE, pageOptions);
        }

        searchResult = {
            total,
            totalPages,
            pageSize: PAGE_SIZE,
            operationId: parentOperation?.id ?? null,
            suggestions: inheritedMetadata.suggestions ?? [],
            getPage,
            getRange,
            filterDocumentIds,
        };
        if (INDEX_BUILD_ID != null) {
            searchResult.indexBuildId = INDEX_BUILD_ID;
        }
        return searchResult;
    }

    async function searchLegacy(q, querySession, operation) {
        const tokenVector = engine.tokenize(q);
        const tokens = normalizeVector(tokenVector);
        const querySize = tokens.length;
        const shardVector = engine.get_shard_files(tokenVector);
        const shardNames = uniqueStrings(normalizeVector(shardVector)
            .map((s) => String(s).trim())
            .filter(Boolean));
        shardVector.delete?.();
        const docsPerShard = engine.get_docs_per_shard();

        const ALPHA = 0.5;
        const BETA = 0.5;

        const docScores = new Map();

        try {
            await Promise.all(shardNames.map(async (shard) => {
                const scanSuggestions = reserveSuggestionAsset(querySession, shard);
                const wordResultsVector = await withCachedAsset(
                    shard,
                    (ptr, len) => querySession.wasm.scan_exact_raw_limited(
                        ptr,
                        len,
                        tokenVector,
                        MAX_PER_TOKEN,
                        scanSuggestions,
                    ),
                    operation,
                );
                try {
                    for (const wr of normalizeVector(wordResultsVector)) {
                        try {
                            const gImp = wr.global_importance;
                            const docImp = normalizeVector(wr.docImp);
                            const take = Math.min(MAX_PER_TOKEN, docImp.length);

                            for (let i = 0; i < take; i++) {
                                const pair = docImp[i];
                                if (pair == null) continue;

                                const id = pair[0] >>> 0;
                                const lImp = pair[1]?.local_importance ?? 0;
                                const tf = pair[1]?.term_freq ?? 0;

                                let entry = docScores.get(id);
                                if (!entry) {
                                    entry = { score: 0, hits: new Set() };
                                    docScores.set(id, entry);
                                }
                                entry.score += gImp * (ALPHA * lImp + BETA * Math.log1p(tf));
                                entry.hits.add(wr.word);
                            }
                        } finally {
                            wr.docImp?.delete?.();
                        }
                    }
                } finally {
                    wordResultsVector.delete?.();
                }
            }));
        } finally {
            tokenVector.delete?.();
        }

        for (const entry of docScores.values()) {
            const coverage = querySize > 0 ? entry.hits.size / querySize : 1;
            entry.score *= coverage * coverage;
        }

        const docIds = Array.from(docScores.entries())
            .sort(([, a], [, b]) => b.score - a.score)
            .map(([id]) => id);

        return createSearchResult(
            docIds,
            docScores,
            querySize,
            docsPerShard,
            operation,
        );
    }

    async function searchDsl(q, maxPerToken, querySession, operation) {
        const ast = normalizeQueryDsl(q, tokenizeToStrings);
        const normalizedTerms = collectNormalizedTerms(ast);
        if (normalizedTerms.length === 0) {
            throw new Error('Consulta DSL não produziu termos após normalização');
        }

        const termData = await loadWordResults(
            normalizedTerms,
            maxPerToken,
            querySession,
            operation,
        );
        const docIdSet = evaluateNormalizedDsl(ast, (term) => termData.get(term)?.docs.keys());
        const docsPerShard = engine.get_docs_per_shard();
        const ALPHA = 0.5;
        const BETA = 0.5;

        const docScores = new Map();
        for (const id of docIdSet) {
            let score = 0;
            const hits = new Set();

            for (const token of normalizedTerms) {
                const entry = termData.get(token);
                if (!entry) continue;
                const importance = entry.docs.get(id);
                if (!importance) continue;

                score += (importance.global_importance ?? entry.globalImportance) *
                    (ALPHA * (importance.local_importance ?? 0) +
                        BETA * Math.log1p(importance.term_freq ?? 0));
                hits.add(token);
            }

            if (hits.size > 0) {
                docScores.set(id, { score, hits });
            }
        }

        for (const entry of docScores.values()) {
            const coverage = normalizedTerms.length > 0 ? entry.hits.size / normalizedTerms.length : 1;
            entry.score *= coverage * coverage;
        }

        const docIds = Array.from(docScores.entries())
            .sort(([, a], [, b]) => b.score - a.score)
            .map(([id]) => id);

        return createSearchResult(
            docIds,
            docScores,
            normalizedTerms.length,
            docsPerShard,
            operation,
        );
    }

    // contract: search(q, options) -> result with lazy documents and optional scoping.
    // doc = { id, name, url, score, hits: [token], coverage }
    async function search(q, searchOptions = {}) {
        if (destroyed) throw new Error('Indexador destruído');
        if (!engine) throw new Error('Indexador não inicializado: chame init() antes de search()');
        const started = globalThis.performance?.now?.() ?? Date.now();
        const operation = createOperation(searchOptions, 'search');
        const detail = { query_characters: String(q ?? '').length };
        progress('search', 'start', detail, operation);
        let querySession = null;
        try {
            throwIfAborted(operation.signal);
            const context = SUGGESTIONS
                ? suggestionContext(searchOptions.suggestionsFor ?? q)
                : null;
            querySession = createQuerySession(context);
            let result;
            if (containsBooleanSyntax(q)) {
                result = await searchDsl(q, 0, querySession, operation);
            } else if (PREFIX_MATCH) {
                result = await searchDsl(
                    q,
                    MAX_PER_TOKEN,
                    querySession,
                    operation,
                );
            } else {
                result = await searchLegacy(q, querySession, operation);
            }
            throwIfAborted(operation.signal);
            result.suggestions = await collectSuggestions(
                querySession,
                context,
                operation,
            );
            throwIfAborted(operation.signal);
            progress('search', 'done', {
                ...detail,
                results: result.total,
                duration_ms: (globalThis.performance?.now?.() ?? Date.now()) - started,
            }, operation);
            return result;
        } catch (error) {
            progressFailure('search', error, {
                ...detail,
                duration_ms: (globalThis.performance?.now?.() ?? Date.now()) - started,
            }, operation);
            throw error;
        } finally {
            querySession?.wasm.delete?.();
        }
    }

    function cacheStats() {
        return {
            entries: assetCache.size,
            bytes: cachedBytes,
            maxBytes: MAX_WASM_CACHE_BYTES,
            pending: pending.size,
            fetchedFiles,
            fetchedWireBytes,
            fetchedDecodedBytes,
            indexBuildId: INDEX_BUILD_ID,
            requireIndexBuildId: REQUIRE_INDEX_BUILD_ID,
        };
    }

    function destroy() {
        if (destroyed) return;
        destroyed = true;
        for (const entry of assetCache.values()) freeAsset(entry);
        assetCache.clear();
        cachedBytes = 0;
        engine?.delete?.();
        engine = null;
    }

    return { init, search, cacheStats, destroy };
}
