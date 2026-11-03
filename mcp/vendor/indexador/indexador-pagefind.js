import { createIndexador } from './indexador-lib.js';

function trimTrailingSlash(value) {
    return String(value ?? '').replace(/\/+$/, '');
}

function joinUrl(base, path) {
    const cleanBase = trimTrailingSlash(base);
    const cleanPath = String(path ?? '').replace(/^\/+/, '');
    if (!cleanBase) {
        return cleanPath;
    }
    return `${cleanBase}/${cleanPath}`;
}

export function resolveIndexadorAssets(options = {}) {
    const assetBaseUrl = trimTrailingSlash(options.assetBaseUrl ?? options.baseUrl ?? './indexador');
    const indexBaseUrl = trimTrailingSlash(options.indexBaseUrl ?? options.baseUrl ?? assetBaseUrl);

    return {
        assetBaseUrl,
        indexBaseUrl,
        wasmScriptUrl: options.wasmScriptUrl ?? joinUrl(assetBaseUrl, 'indexador_wasm.js'),
        indexMapUrl: options.indexMapUrl ?? joinUrl(indexBaseUrl, 'index.map'),
        fetchRoot: options.fetchRoot ?? indexBaseUrl,
    };
}

export function createIndexadorPagefind(options = {}) {
    const resolved = resolveIndexadorAssets(options);
    return createIndexador({
        ...options,
        wasmScriptUrl: resolved.wasmScriptUrl,
        indexMapUrl: resolved.indexMapUrl,
        fetchRoot: resolved.fetchRoot,
    });
}
