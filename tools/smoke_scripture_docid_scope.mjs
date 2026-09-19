#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { createAgentTools } from '../web/src/scripts/agent-chat-tools.js';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function parseArgs(argv) {
  const args = {
    publicDir: path.join(ROOT, 'web', 'public'),
    searchManifest: '',
    query: 'PG',
    reference: 'João 3:16',
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === '--public') args.publicDir = path.resolve(argv[++index]);
    else if (value === '--search-manifest') args.searchManifest = path.resolve(argv[++index]);
    else if (value === '--query') args.query = String(argv[++index] || '').trim();
    else if (value === '--reference') args.reference = String(argv[++index] || '').trim();
    else if (value === '--help' || value === '-h') args.help = true;
    else throw new Error(`Argumento desconhecido: ${value}`);
  }
  args.searchManifest ||= path.join(args.publicDir, 'indexador', 'search', 'manifest.json');
  if (!args.query || !args.reference) throw new Error('--query e --reference não podem ser vazios.');
  return args;
}

function usage() {
  return `Uso: node tools/smoke_scripture_docid_scope.mjs [opções]

Executa a busca ampla real e confirma o filtro por doc_id sobre os artefatos publicados.

  --public web/public
  --search-manifest web/public/indexador/search/<versão>/manifest.json
  --query PG
  --reference "João 3:16"`;
}

function readMaybeGzipJson(filePath) {
  const input = fs.readFileSync(filePath);
  const bytes = input[0] === 0x1f && input[1] === 0x8b ? zlib.gunzipSync(input) : input;
  return JSON.parse(bytes.toString('utf8'));
}

function loadWasmFactory(runtimeDir) {
  const source = fs.readFileSync(path.join(runtimeDir, 'indexador_wasm.js'), 'utf8');
  return Function(`${source}\nreturn Module;`)();
}

function searchVersion(publicDir, searchManifest) {
  const searchRoot = path.join(publicDir, 'indexador', 'search');
  const relative = path.relative(searchRoot, path.dirname(searchManifest));
  if (!relative) return '';
  if (relative.startsWith('..') || path.isAbsolute(relative) || relative.includes(path.sep)) {
    throw new Error('O manifesto de busca deve estar diretamente sob public/indexador/search/<versão>.');
  }
  return relative;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(usage());
    return;
  }
  const runtimeDir = path.join(args.publicDir, 'indexador', 'runtime');
  const wasmFactory = loadWasmFactory(runtimeDir);
  const wasmBytes = fs.readFileSync(path.join(runtimeDir, 'indexador_wasm.wasm'));
  const assetBase = pathToFileURL(`${args.publicDir}${path.sep}`).href;
  const localFetch = async (input) => {
    const url = String(input);
    if (!url.startsWith('file:')) throw new Error(`Smoke test recusou URL não local: ${url}`);
    return new Response(fs.readFileSync(fileURLToPath(url)), { status: 200 });
  };
  globalThis.fetch = localFetch;
  const progress = [];
  const tools = createAgentTools({
    assetBase,
    origin: 'https://bibliotheca.invalid',
    searchIndexVersion: searchVersion(args.publicDir, args.searchManifest),
    fetchImpl: localFetch,
    getJsonImpl: async (url) => readMaybeGzipJson(fileURLToPath(url)),
    loadIndexadorModule: () => import(pathToFileURL(path.join(runtimeDir, 'indexador-pagefind.js')).href),
    indexadorOptions: {
      wasmModuleLoader: () => wasmFactory({ wasmBinary: wasmBytes }),
    },
  });
  const started = performance.now();
  const result = await tools.execute('search_corpus', {
    query: args.query,
    scripture_reference: args.reference,
    limit: 3,
  }, {
    operationId: 'scripture-docid-smoke',
    onProgress: (event) => progress.push(event),
  });
  if (!result?.ok || result.data?.total < 1 || !result.data?.items?.length) {
    throw new Error('A busca integrada não retornou documentos dentro do escopo bíblico.');
  }
  for (const phase of ['init', 'search', 'scope', 'documents']) {
    if (!progress.some((event) => event.phase === phase && event.state === 'done')) {
      throw new Error(`A fase ${phase} não emitiu conclusão no smoke test.`);
    }
  }
  if (!progress.some((event) => event.phase === 'scope_mapping' && event.state === 'done')) {
    throw new Error('O mapeamento dos sidecars não emitiu conclusão no smoke test.');
  }
  if (result.sources?.some((source) => !source.provenance?.includes('search_scripture_scope'))) {
    throw new Error('Resultado sem proveniência do escopo bíblico.');
  }
  const scopeEvent = progress.find((event) => event.phase === 'scope' && event.state === 'done');
  console.log(JSON.stringify({
    elapsed_ms: Math.round(performance.now() - started),
    reference: args.reference,
    associated_pages: result.data.scripture_scope.pages,
    search_candidates: scopeEvent?.candidates,
    matched_documents: scopeEvent?.matched_documents,
    returned_documents: result.data.items.length,
    operation_ids: [...new Set(progress.map((event) => event.operation_id).filter(Boolean))],
  }, null, 2));
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}
