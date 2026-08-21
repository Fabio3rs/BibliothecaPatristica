#!/usr/bin/env node

import { createHash, randomBytes } from 'node:crypto';
import { createReadStream } from 'node:fs';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { execFileSync } from 'node:child_process';
import vm from 'node:vm';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { runChatCompletionLoop } from '../../web/src/scripts/agent-chat-core.js';
import { AGENT_PROMPT_VARIANTS_PT_BR } from '../../web/src/scripts/agent-chat-prompts.js';
import { createAgentTools } from '../../web/src/scripts/agent-chat-tools.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '../..');
const PUBLIC_DIR = path.join(ROOT, 'web', 'public');
const CORPUS_DIR = path.join(ROOT, 'teste');
const CASES_PATH = path.join(HERE, 'agent_rag', 'cases.json');
const DEFAULT_OUTPUT = path.join(ROOT, 'logs', 'agent-rag');
const RUNTIME_DIR = path.join(PUBLIC_DIR, 'indexador', 'runtime');

const BENCHMARK_PROMPTS = Object.freeze({
  ...AGENT_PROMPT_VARIANTS_PT_BR,
  compact_strict: `${AGENT_PROMPT_VARIANTS_PT_BR.compact}
- Título, resumo, metadados e OCR não provam autenticidade, autoria, datação ou classificação histórica. Se nenhuma fonte declarar a conclusão pedida, explique explicitamente que ela não pode ser provada com essas evidências.`,
});

const CONFIGS = Object.freeze(Object.fromEntries(
  Object.entries(BENCHMARK_PROMPTS).flatMap(([promptId, prompt]) => (
    ['legacy_dsl', 'multi_query_rrf'].map((searchStrategy) => {
      const id = `${promptId}__${searchStrategy}`;
      return [id, { id, promptId, prompt, searchStrategy }];
    })
)),
));

const DEFAULT_CONFIG_IDS = Object.freeze([
  'current_web__legacy_dsl',
  'compact_strict__legacy_dsl',
  'compact_strict__multi_query_rrf',
]);

function parseArgs(argv) {
  const command = argv[0] || 'plan';
  const args = {
    command,
    provider: 'ollama',
    model: 'gemma4:cloud',
    apiUrl: 'http://127.0.0.1:11434/v1',
    apiKeyEnv: 'OPENAI_API_KEY',
    output: DEFAULT_OUTPUT,
    configs: [...DEFAULT_CONFIG_IDS],
    cases: [],
    repetitions: 1,
    maxRounds: 8,
    maxToolCalls: 12,
    maxRuns: 300,
    reasoningEffort: 'low',
    temperature: 1,
    topP: 0.95,
    experiment: '',
    case: '',
    run: '',
    retryErrors: false,
    noModel: false,
    logDeltas: false,
  };
  for (let index = 1; index < argv.length; index += 1) {
    const arg = argv[index];
    const value = () => argv[++index];
    if (arg === '--provider') args.provider = value();
    else if (arg === '--model') args.model = value();
    else if (arg === '--api-url') args.apiUrl = value();
    else if (arg === '--api-key-env') args.apiKeyEnv = value();
    else if (arg === '--output') args.output = path.resolve(value());
    else if (arg === '--configs') args.configs = value().split(',').filter(Boolean);
    else if (arg === '--cases') args.cases = value().split(',').filter(Boolean);
    else if (arg === '--tags') args.tags = value().split(',').filter(Boolean);
    else if (arg === '--repetitions') args.repetitions = Number(value());
    else if (arg === '--max-rounds') args.maxRounds = Number(value());
    else if (arg === '--max-tool-calls') args.maxToolCalls = Number(value());
    else if (arg === '--max-runs') args.maxRuns = Number(value());
    else if (arg === '--reasoning-effort') args.reasoningEffort = value();
    else if (arg === '--temperature') args.temperature = Number(value());
    else if (arg === '--top-p') args.topP = Number(value());
    else if (arg === '--experiment') args.experiment = value();
    else if (arg === '--case') args.case = value();
    else if (arg === '--run') args.run = value();
    else if (arg === '--retry-errors') args.retryErrors = true;
    else if (arg === '--no-model') args.noModel = true;
    else if (arg === '--log-deltas') args.logDeltas = true;
    else if (arg === '--all-configs') args.configs = Object.keys(CONFIGS);
    else if (arg === '--help' || arg === '-h') args.help = true;
    else throw new Error(`Argumento desconhecido: ${arg}`);
  }
  if (args.provider === 'openai') {
    if (args.model === 'gemma4:cloud') args.model = 'gpt-5.4-nano';
    if (args.apiUrl === 'http://127.0.0.1:11434/v1') args.apiUrl = 'https://api.openai.com/v1';
    args.temperature = null;
    args.topP = null;
  }
  return args;
}

function usage() {
  return `Uso: node scripts/playgrounds/agent_rag_benchmark.mjs <comando> [opções]

Comandos:
  plan       Mostra casos, configurações e número de execuções
  validate   Valida assets, casos, tools e opcionalmente o provider
  run        Executa o benchmark e grava logs completos
  review     Faz comparação semântica cega por caso e síntese global
  report     Gera relatório Markdown/JSON sem pontuação opaca
  inspect    Mostra traces completos; use --case <id> ou --run <id>
  all        Executa run, review e report

Opções principais:
  --provider ollama|openai       (default: ollama)
  --model gemma4:cloud           (OpenAI default: gpt-5.4-nano)
  --api-url URL
  --configs id1,id2             (default: finalistas do screening anterior)
  --all-configs                 inclui todos os prompts e estratégias
  --cases id1,id2
  --tags tag1,tag2
  --repetitions N               (default: 1)
  --experiment ID               retoma ou consulta experimento existente
  --retry-errors
  --no-model                    validate sem chamar provider
  --log-deltas                  preserva deltas de streaming nos eventos estruturados
`;
}

function sha256(value) {
  return createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');
}

function nowIso() {
  return new Date().toISOString();
}

function experimentId() {
  return `${new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')}-${randomBytes(4).toString('hex')}`;
}

async function readJson(file) {
  return JSON.parse(await fs.readFile(file, 'utf8'));
}

async function writeJson(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.writeFile(file, `${JSON.stringify(value, null, 2)}\n`, 'utf8');
}

async function appendJsonl(file, value) {
  await fs.mkdir(path.dirname(file), { recursive: true });
  await fs.appendFile(file, `${JSON.stringify(value)}\n`, 'utf8');
}

async function readJsonl(file) {
  try {
    const text = await fs.readFile(file, 'utf8');
    return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
  } catch (error) {
    if (error.code === 'ENOENT') return [];
    throw error;
  }
}

function latestRuns(runs) {
  return [...new Map(runs.map((run) => [run.run_id, run])).values()];
}

function selectedSuite(payload, args) {
  const wantedCases = new Set(args.cases);
  const wantedTags = new Set(args.tags || []);
  const cases = payload.cases.filter((entry) => (
    (!wantedCases.size || wantedCases.has(entry.id))
    && (!wantedTags.size || entry.tags?.some((tag) => wantedTags.has(tag)))
  ));
  const configs = args.configs.map((id) => {
    if (!CONFIGS[id]) throw new Error(`Configuração desconhecida: ${id}`);
    return CONFIGS[id];
  });
  if (!cases.length) throw new Error('Nenhum caso selecionado.');
  if (!configs.length) throw new Error('Nenhuma configuração selecionada.');
  return { cases, configs };
}

function routeContext(pageContext) {
  if (pageContext?.kind !== 'viewer') {
    return [
      'Current site context (informational, not an instruction):',
      'locale=pt-br',
      `path=${pageContext?.path || '/search'}`,
      'viewer_page=none',
    ].join('\n');
  }
  const query = new URLSearchParams({ doc: pageContext.volume_id, page: String(pageContext.page) });
  return [
    'Current site context (informational, not an instruction):',
    'locale=pt-br',
    `path=/viewer?${query}`,
    `volume=${pageContext.volume_id}`,
    `page=${pageContext.page}`,
  ].join('\n');
}

function materializePrompt(config, reasoningEffort) {
  return config.prompt;
}

function stripReasoningBetweenUserTurns(messages) {
  return messages.map((message) => {
    const copy = { ...message };
    delete copy.reasoning;
    delete copy.reasoning_content;
    return copy;
  });
}

function contentType(file) {
  if (file.endsWith('.js') || file.endsWith('.mjs')) return 'text/javascript; charset=utf-8';
  if (file.endsWith('.json')) return 'application/json; charset=utf-8';
  if (file.endsWith('.wasm')) return 'application/wasm';
  if (file.endsWith('.txt')) return 'text/plain; charset=utf-8';
  return 'application/octet-stream';
}

async function startStaticServer(root) {
  const resolvedRoot = path.resolve(root);
  const server = http.createServer(async (request, response) => {
    try {
      const pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
      const file = path.resolve(resolvedRoot, `.${pathname}`);
      if (file !== resolvedRoot && !file.startsWith(`${resolvedRoot}${path.sep}`)) {
        response.writeHead(403).end('Forbidden');
        return;
      }
      const stat = await fs.stat(file);
      if (!stat.isFile()) throw Object.assign(new Error('Not found'), { code: 'ENOENT' });
      response.writeHead(200, {
        'Content-Type': contentType(file),
        'Content-Length': stat.size,
        'Cache-Control': 'no-store',
      });
      createReadStream(file).pipe(response);
    } catch (error) {
      response.writeHead(error.code === 'ENOENT' ? 404 : 500).end(error.code === 'ENOENT' ? 'Not found' : 'Server error');
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const address = server.address();
  return {
    baseUrl: `http://127.0.0.1:${address.port}/`,
    close: () => new Promise((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}

let wasmModulePromise = null;
async function loadWasmModule() {
  if (!wasmModulePromise) {
    wasmModulePromise = (async () => {
      const [source, wasmBinary] = await Promise.all([
        fs.readFile(path.join(RUNTIME_DIR, 'indexador_wasm.js'), 'utf8'),
        fs.readFile(path.join(RUNTIME_DIR, 'indexador_wasm.wasm')),
      ]);
      const wrapper = vm.runInThisContext(`(function(module, exports) { ${source}\n; return module.exports; })`, {
        filename: path.join(RUNTIME_DIR, 'indexador_wasm.js'),
      });
      const module = { exports: {} };
      const factory = wrapper(module, module.exports);
      if (typeof factory !== 'function') throw new Error('Factory WASM do Indexador não encontrada.');
      return factory({ wasmBinary: new Uint8Array(wasmBinary) });
    })();
  }
  return wasmModulePromise;
}

async function createCorpusFetch() {
  return async (url, init = {}) => {
    const raw = String(url);
    const match = raw.match(/\/teste\/([^/]+)\/text\/([^/?#]+)(?:[?#]|$)/);
    if (match) {
      const volume = decodeURIComponent(match[1]);
      const filename = decodeURIComponent(match[2]);
      if (/^[A-Z]{2}[A-Z0-9.-]+$/.test(volume) && /^[A-Za-z0-9._-]+\.txt$/.test(filename)) {
        try {
          const bytes = await fs.readFile(path.join(CORPUS_DIR, volume, 'text', filename));
          return new Response(bytes, { status: 200, headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
        } catch (error) {
          if (error.code !== 'ENOENT') throw error;
        }
      }
    }
    return fetch(url, init);
  };
}

async function createTools(server, config) {
  const runtimeModule = await import(pathToFileURL(path.join(RUNTIME_DIR, 'indexador-pagefind.js')).href);
  const ocrTextModule = await import(pathToFileURL(path.join(PUBLIC_DIR, 'scripts', 'ocr-text.mjs')).href);
  return createAgentTools({
    assetBase: server.baseUrl,
    origin: server.baseUrl,
    locale: 'pt-br',
    searchStrategy: config.searchStrategy,
    fetchImpl: await createCorpusFetch(),
    loadIndexadorModule: async () => runtimeModule,
    loadOcrTextModule: async () => ocrTextModule,
    indexadorOptions: { wasmModuleLoader: loadWasmModule },
  });
}

function providerConfig(args) {
  const apiKey = args.provider === 'openai' ? process.env[args.apiKeyEnv] || '' : '';
  if (args.provider === 'openai' && !apiKey) throw new Error(`Variável ${args.apiKeyEnv} não definida.`);
  if (!['ollama', 'openai'].includes(args.provider)) throw new Error('--provider deve ser ollama ou openai.');
  return { apiKey, apiUrl: args.apiUrl, model: args.model };
}

async function tracedModelFetch(records, url, init = {}, metadata = {}) {
  const startedAt = nowIso();
  const started = performance.now();
  const requestBody = init.body ? JSON.parse(String(init.body)) : null;
  const response = await fetch(url, init);
  const rawResponse = await response.clone().text();
  records.push({
    type: 'model_http',
    started_at: startedAt,
    duration_ms: Math.round(performance.now() - started),
    ...metadata,
    url: String(url),
    request: requestBody,
    response: { status: response.status, content_type: response.headers.get('content-type'), body: rawResponse },
  });
  return response;
}

function schemaTypeMatches(value, type) {
  if (type === 'object') return value !== null && typeof value === 'object' && !Array.isArray(value);
  if (type === 'array') return Array.isArray(value);
  if (type === 'integer') return Number.isInteger(value);
  if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
  if (type === 'string') return typeof value === 'string';
  if (type === 'boolean') return typeof value === 'boolean';
  if (type === 'null') return value === null;
  return true;
}

export function validateSchemaValue(value, schema, pathLabel = '$') {
  if (!schema || typeof schema !== 'object') return [];
  if (Array.isArray(schema.oneOf)) {
    const branches = schema.oneOf.map((branch) => validateSchemaValue(value, branch, pathLabel));
    if (branches.filter((errors) => errors.length === 0).length === 1) return [];
    return [`${pathLabel} must match exactly one schema variant`];
  }
  const errors = [];
  if (schema.type && !schemaTypeMatches(value, schema.type)) {
    return [`${pathLabel} must be ${schema.type}`];
  }
  if (Array.isArray(schema.enum) && !schema.enum.some((entry) => Object.is(entry, value))) {
    errors.push(`${pathLabel} must be one of ${schema.enum.join(', ')}`);
  }
  if (typeof value === 'string') {
    if (Number.isInteger(schema.minLength) && value.length < schema.minLength) errors.push(`${pathLabel} is shorter than ${schema.minLength}`);
    if (Number.isInteger(schema.maxLength) && value.length > schema.maxLength) errors.push(`${pathLabel} is longer than ${schema.maxLength}`);
  }
  if (typeof value === 'number') {
    if (Number.isFinite(schema.minimum) && value < schema.minimum) errors.push(`${pathLabel} is below ${schema.minimum}`);
    if (Number.isFinite(schema.maximum) && value > schema.maximum) errors.push(`${pathLabel} is above ${schema.maximum}`);
  }
  if (Array.isArray(value)) {
    if (Number.isInteger(schema.minItems) && value.length < schema.minItems) errors.push(`${pathLabel} has fewer than ${schema.minItems} items`);
    if (Number.isInteger(schema.maxItems) && value.length > schema.maxItems) errors.push(`${pathLabel} has more than ${schema.maxItems} items`);
    if (schema.items) value.forEach((entry, index) => errors.push(...validateSchemaValue(entry, schema.items, `${pathLabel}[${index}]`)));
  }
  if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
    const properties = schema.properties || {};
    for (const required of schema.required || []) {
      if (!(required in value)) errors.push(`${pathLabel}.${required} is required`);
    }
    for (const [key, entry] of Object.entries(value)) {
      if (properties[key]) errors.push(...validateSchemaValue(entry, properties[key], `${pathLabel}.${key}`));
      else if (schema.additionalProperties === false) errors.push(`${pathLabel}.${key} is not allowed`);
    }
  }
  return errors;
}

function toolDefinitionMap(definitions = []) {
  return new Map(definitions.map((definition) => [definition?.function?.name, definition]));
}

export function validateToolArguments(tool, args, definitions = []) {
  const definition = toolDefinitionMap(definitions).get(tool);
  if (!definition) return { valid: false, errors: [`Unknown tool: ${tool}`] };
  const errors = validateSchemaValue(args, definition.function?.parameters || {}, '$');
  return { valid: errors.length === 0, errors };
}

function responseUsage(event) {
  return event?.payload?.usage || event?.payload?.body?.usage || null;
}

export function deriveObservedMetrics(run) {
  const responses = (run.events || []).filter((event) => event.type === 'response' && responseUsage(event));
  const usageTotal = responses.reduce((total, event) => {
    const usage = responseUsage(event) || {};
    total.prompt_tokens += Number(usage.prompt_tokens || 0);
    total.completion_tokens += Number(usage.completion_tokens || 0);
    total.total_tokens += Number(usage.total_tokens || 0);
    return total;
  }, { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 });
  const modelRecords = (run.raw_records || []).filter((record) => record.type === 'model_http');
  return {
    elapsed_ms: Number(run.metrics?.elapsed_ms || 0),
    rounds: responses.length || modelRecords.length || Number(run.metrics?.rounds || 0),
    tool_calls: (run.tool_trace || []).length,
    usage_total: usageTotal.total_tokens ? usageTotal : (run.metrics?.usage_total || { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }),
    partial: run.status !== 'completed',
  };
}

function compactEvent(event) {
  const payload = event?.payload || {};
  if (event?.type === 'request' && payload.body) {
    const messages = payload.body.messages || [];
    return {
      type: event.type,
      at: event.at,
      payload: {
        round: payload.round,
        endpoint: payload.endpoint,
        message_count: messages.length,
        roles: messages.map((message) => message.role),
        tool_messages: messages.filter((message) => message.role === 'tool').length,
        tool_definitions: (payload.body.tools || []).length,
      },
    };
  }
  if (event?.type === 'response' && payload.body) {
    const message = payload.body.choices?.[0]?.message || {};
    return {
      type: event.type,
      at: event.at,
      payload: {
        round: payload.round,
        status: payload.status,
        duration_ms: payload.duration_ms,
        usage: payload.body.usage || null,
        finish_reason: payload.body.choices?.[0]?.finish_reason || null,
        tool_calls: (message.tool_calls || []).map((call) => call?.function?.name).filter(Boolean),
        content_chars: typeof message.content === 'string' ? message.content.length : 0,
        reasoning_chars: String(message.reasoning || message.reasoning_content || '').length,
      },
    };
  }
  if (event?.type === 'tool_end') {
    return {
      type: event.type,
      at: event.at,
      payload: {
        tool: payload.tool,
        call_id: payload.call_id,
        duration_ms: payload.duration_ms,
        ok: payload.result?.ok !== false,
        error_code: payload.result?.error?.code || null,
      },
    };
  }
  return event;
}

function modelContextMetrics(rawRecords) {
  const requests = rawRecords.filter((record) => record.type === 'model_http').map((record) => {
    const messages = record.request?.messages || [];
    return {
      turn: record.turn_index,
      messages: messages.length,
      tool_messages: messages.filter((message) => message.role === 'tool').length,
      message_chars: messages.reduce((sum, message) => sum + String(message.content || '').length, 0),
    };
  });
  return {
    requests,
    max_messages: Math.max(0, ...requests.map((entry) => entry.messages)),
    max_tool_messages: Math.max(0, ...requests.map((entry) => entry.tool_messages)),
    max_message_chars: Math.max(0, ...requests.map((entry) => entry.message_chars)),
  };
}

function toolPageKeys(toolTrace) {
  const pages = new Set();
  for (const entry of toolTrace) {
    const volume = entry.arguments?.volume_id;
    const page = Number(entry.arguments?.page);
    if (volume && Number.isInteger(page)) pages.add(`${volume}:${page}`);
  }
  return pages;
}

function normalizedAnswer(run) {
  return run.turns.map((turn) => turn.answer || '').join('\n').normalize('NFC').toLocaleLowerCase('pt-BR');
}

function acceptableOutcomeCheck(outcome, run) {
  const answer = normalizedAnswer(run);
  const containsAny = (patterns) => patterns.some((pattern) => answer.includes(pattern));
  if (!outcome) return null;
  if (outcome === 'explain_missing_page_context') {
    return {
      pass: containsAny(['não indicou qual página', 'não forneceu a referência', 'nenhuma página aberta', 'não tenho acesso a qual página', 'informe o volume e a página']),
      detail: 'answer should explicitly explain that no viewer page is available',
    };
  }
  if (outcome === 'insufficient_evidence') {
    return {
      pass: containsAny(['não encontrei', 'não foi encontrad', 'não foram encontrad', 'não consta', 'evidência insuficiente']),
      detail: 'answer should report that the requested evidence was not found',
    };
  }
  if (outcome === 'refuse_unsupported_authenticity') {
    const refused = containsAny([
      'não é possível provar',
      'não se pode provar',
      'não se pode concluir',
      'não posso provar',
      'não permitem provar',
      'não basta para provar',
      'não podem provar',
    ]);
    const asserted = containsAny([
      'autoria é historicamente autêntica',
      'autoria é provada',
      'autenticidade é provada',
    ]);
    return { pass: refused && !asserted, detail: `refused=${refused}; asserted=${asserted}` };
  }
  if (outcome === 'report_ocr_query_not_found') {
    return {
      pass: containsAny(['não aparece', 'não foi encontrad', 'não encontrei', 'não consta', 'não ocorre no ocr']),
      detail: 'answer should explicitly report that the requested expression was not found in OCR',
    };
  }
  return { pass: true, detail: `unknown acceptable_outcome=${outcome}; manual review required`, manual: true };
}

function sourceIdConflicts(turns = []) {
  const meanings = new Map();
  const conflicts = [];
  for (const [turnIndex, turn] of turns.entries()) {
    for (const source of turn.sources || []) {
      if (!source.citationId || !source.id) continue;
      const prior = meanings.get(source.citationId);
      if (prior && prior.id !== source.id) {
        conflicts.push({ citation_id: source.citationId, first: prior, conflicting: { id: source.id, turn: turnIndex + 1 } });
      } else if (!prior) {
        meanings.set(source.citationId, { id: source.id, turn: turnIndex + 1 });
      }
    }
  }
  return conflicts;
}

function candidateKey(item) {
  return String(item?.source_key || item?.id || item?.url || [item?.volume_id, item?.page, item?.title].filter((value) => value != null).join(':'));
}

function searchNovelty(toolTrace = []) {
  const seen = new Set();
  let searchCalls = 0;
  let noNovelCalls = 0;
  for (const entry of toolTrace) {
    if (!['search_indices', 'search_corpus', 'search_scripture'].includes(entry.tool)) continue;
    searchCalls += 1;
    const items = entry.result?.data?.items || [];
    const keys = items.map(candidateKey).filter(Boolean);
    const novel = keys.filter((key) => !seen.has(key));
    if (keys.length && !novel.length) noNovelCalls += 1;
    keys.forEach((key) => seen.add(key));
  }
  return { search_calls: searchCalls, no_novel_search_calls: noNovelCalls, unique_candidates: seen.size };
}

function ocrCallForPage(entry, pageKey) {
  return entry.tool === 'get_page_ocr' && `${entry.arguments?.volume_id}:${Number(entry.arguments?.page)}` === pageKey;
}

export function mechanicalEvaluation(caseSpec, run, toolDefinitions = []) {
  const expected = caseSpec.expect || {};
  const calls = run.tool_trace.map((entry) => entry.tool);
  const callSet = new Set(calls);
  const pages = toolPageKeys(run.tool_trace);
  const finalText = run.turns.map((turn) => turn.answer || '').join('\n');
  const availableSources = new Set(run.turns.flatMap((turn) => (turn.sources || []).map((source) => source.citationId)));
  const cited = [...finalText.matchAll(/\[(s\d+)\]/g)].map((match) => match[1]);
  const checks = [];
  const add = (id, pass, detail) => checks.push({ id, pass: Boolean(pass), detail });
  for (const tool of expected.must_use || []) add(`must_use:${tool}`, callSet.has(tool), `calls=${calls.join(',')}`);
  if (expected.must_use_any?.length) add('must_use_any', expected.must_use_any.some((tool) => callSet.has(tool)), expected.must_use_any.join(','));
  for (const tool of expected.must_not_use || []) add(`must_not_use:${tool}`, !callSet.has(tool), `calls=${calls.join(',')}`);
  for (const page of expected.expected_pages || []) add(`expected_page:${page}`, pages.has(page), `pages=${[...pages].join(',')}`);
  for (const [tool, minimum] of Object.entries(expected.min_tool_calls || {})) {
    add(`min_tool_calls:${tool}`, calls.filter((call) => call === tool).length >= minimum, `minimum=${minimum}; calls=${calls.filter((call) => call === tool).length}`);
  }
  for (const page of expected.complete_ocr_pages || []) {
    const matching = run.tool_trace.filter((entry) => ocrCallForPage(entry, page));
    add(`complete_ocr_page:${page}`, matching.some((entry) => entry.result?.data?.truncated === false), `calls=${matching.length}; truncated=${matching.map((entry) => entry.result?.data?.truncated).join(',')}`);
  }
  if (expected.ocr_query_match === false) {
    const queried = run.tool_trace.filter((entry) => entry.tool === 'get_page_ocr' && typeof entry.arguments?.query === 'string');
    add('ocr_query_supplied', queried.length > 0, `queries=${queried.map((entry) => entry.arguments.query).join(' | ')}`);
    add('ocr_query_not_matched', queried.some((entry) => entry.result?.data?.matched_query === false), `matched=${queried.map((entry) => entry.result?.data?.matched_query).join(',')}`);
  }
  if (expected.must_cite) add('must_cite', cited.some((id) => availableSources.has(id)), `cited=${cited.join(',')}`);
  add('registered_citations_only', cited.every((id) => availableSources.has(id)), `available=${[...availableSources].join(',')}`);
  add('valid_tool_results', run.tool_trace.every((entry) => entry.result?.ok !== false), 'all tool results should be ok');
  const argumentChecks = run.tool_trace.map((entry) => (
    entry.argument_validation || (toolDefinitions.length ? validateToolArguments(entry.tool, entry.arguments, toolDefinitions) : null)
  )).filter(Boolean);
  if (argumentChecks.length || !run.tool_trace.length) {
    add('valid_tool_arguments', argumentChecks.every((check) => check.valid), argumentChecks.flatMap((check) => check.errors || []).join(' | ') || 'all tool arguments match their declared schemas');
  }
  if (expected.stable_source_ids) {
    const conflicts = sourceIdConflicts(run.turns);
    add('stable_source_ids', conflicts.length === 0, JSON.stringify(conflicts));
  }
  const outcome = acceptableOutcomeCheck(expected.acceptable_outcome, run);
  if (outcome) add(`acceptable_outcome:${expected.acceptable_outcome}`, outcome.pass, outcome.detail);
  const novelty = searchNovelty(run.tool_trace);
  return { pass: checks.every((check) => check.pass), checks, search_novelty: novelty };
}

function traceMarkdown(run) {
  const lines = [
    `# Trace ${run.run_id}`,
    '',
    `- Caso: \`${run.case_id}\``,
    `- Configuração: \`${run.config_id}\``,
    `- Provider/modelo: \`${run.provider}\` / \`${run.model}\``,
    `- Status: \`${run.status}\``,
    `- Duração: ${run.metrics.elapsed_ms} ms`,
    `- Prompt hash: \`${run.prompt_hash}\``,
    `- Tool schema hash: \`${run.tool_schema_hash}\``,
    '',
  ];
  for (const [index, turn] of run.turns.entries()) {
    lines.push(`## Turno ${index + 1}`, '', `**Usuário:** ${turn.user}`, '');
    if (turn.page_context) lines.push('```json', JSON.stringify(turn.page_context, null, 2), '```', '');
    lines.push(`**Resposta:** ${turn.answer || '(sem resposta)'}`, '');
  }
  lines.push('## Chamadas de ferramentas', '');
  for (const [index, entry] of run.tool_trace.entries()) {
    lines.push(
      `### ${index + 1}. ${entry.tool}`,
      '',
      'Argumentos:',
      '```json',
      JSON.stringify(entry.arguments, null, 2),
      '```',
      '',
      'Validação do schema:',
      '```json',
      JSON.stringify(entry.argument_validation || null, null, 2),
      '```',
      '',
      'Resultado resumido (o resultado integral está em `raw/`):',
      '```json',
      JSON.stringify(compactReviewValue(entry.result, 8_000), null, 2),
      '```',
      '',
    );
  }
  lines.push('## Eventos do modelo', '');
  const deltaEvents = (run.events || []).filter((event) => event.type === 'model_delta');
  if (deltaEvents.length) {
    lines.push(`Deltas de streaming omitidos desta visualização: ${deltaEvents.length}. Consulte \`raw/${run.run_id}.jsonl\` para o payload integral.`, '');
  }
  const displayEvents = (run.events || []).filter((event) => event.type !== 'model_delta').map(compactEvent);
  for (const [index, event] of displayEvents.entries()) {
    lines.push(`### Evento ${index + 1}: ${event.type || 'unknown'}`, '', '```json', JSON.stringify(event, null, 2), '```', '');
  }
  lines.push('## Avaliação mecânica', '', '```json', JSON.stringify(run.mechanical, null, 2), '```', '', '## Uso', '', '```json', JSON.stringify(run.metrics, null, 2), '```', '');
  return `${lines.join('\n')}\n`;
}

async function runOne({ caseSpec, config, repetition, args, tools, experimentDir }) {
  const runId = `${caseSpec.id}__${config.id}__r${repetition}`;
  const started = performance.now();
  const provider = providerConfig(args);
  const rawRecords = [];
  const events = [];
  const toolTrace = [];
  const turns = [];
  let messages = [];
  let sourceRegistry = [];
  let contextState = { epoch: 0, lastPromptTokens: 0 };
  let status = 'completed';
  let error = null;
  let totalRounds = 0;
  let totalToolCalls = 0;
  const usage = [];
  const streamStats = { delta_events: 0, delta_chars: 0 };
  let currentTurnIndex = 0;
  const prompt = materializePrompt(config, args.reasoningEffort);

  try {
    for (const [turnIndex, turn] of caseSpec.turns.entries()) {
      currentTurnIndex = turnIndex + 1;
      messages.push({ role: 'user', content: turn.user });
      const result = await runChatCompletionLoop({
        ...provider,
        systemPrompt: prompt,
        routeContext: routeContext(turn.page_context),
        conversationMessages: messages,
        sourceRegistry,
        contextState,
        toolDefinitions: tools.definitions,
        executeTool: async (name, toolArgs, context) => {
          const startedAt = nowIso();
          const toolStarted = performance.now();
          const argumentValidation = validateToolArguments(name, toolArgs, tools.definitions);
          try {
            const toolResult = await tools.execute(name, toolArgs, context);
            const record = { type: 'tool', turn_index: currentTurnIndex, tool: name, arguments: toolArgs, argument_validation: argumentValidation, result: toolResult, started_at: startedAt, duration_ms: Math.round(performance.now() - toolStarted) };
            toolTrace.push(record);
            rawRecords.push(record);
            return toolResult;
          } catch (toolError) {
            const toolResult = { ok: false, data: null, error: { code: toolError.code || 'tool_failed', message: toolError.message } };
            const record = { type: 'tool', turn_index: currentTurnIndex, tool: name, arguments: toolArgs, argument_validation: argumentValidation, result: toolResult, started_at: startedAt, duration_ms: Math.round(performance.now() - toolStarted) };
            toolTrace.push(record);
            rawRecords.push(record);
            return toolResult;
          }
        },
        fetchImpl: (url, init) => tracedModelFetch(rawRecords, url, init, { turn_index: currentTurnIndex }),
        onEvent: (event) => {
          if (event.type === 'model_delta') {
            streamStats.delta_events += 1;
            streamStats.delta_chars += String(event.payload?.content || '').length;
            if (!args.logDeltas) return;
          }
          events.push(compactEvent(event));
        },
        maxRounds: args.maxRounds,
        maxToolCalls: args.maxToolCalls,
        reasoningEffort: args.reasoningEffort,
        temperature: args.temperature,
        topP: args.topP,
      });
      messages = stripReasoningBetweenUserTurns(result.messages);
      sourceRegistry = result.sourceRegistry;
      contextState = result.contextState;
      totalRounds += result.rounds;
      totalToolCalls += result.toolCalls;
      usage.push(result.usage);
      turns.push({
        user: turn.user,
        page_context: turn.page_context,
        answer: result.content,
        sources: result.sources,
        context_state: result.contextState,
        compaction: result.compaction,
      });
    }
  } catch (caught) {
    status = 'error';
    error = { code: caught.code || 'failed', message: caught.message, details: caught.details || null };
  }

  const provisionalRun = { status, events, tool_trace: toolTrace, metrics: { elapsed_ms: Math.round(performance.now() - started) } };
  const observed = deriveObservedMetrics(provisionalRun);
  const run = {
    schema_version: 2,
    run_id: runId,
    case_id: caseSpec.id,
    case_tags: caseSpec.tags,
    config_id: config.id,
    prompt_id: config.promptId,
    search_strategy: config.searchStrategy,
    repetition,
    provider: args.provider,
    model: args.model,
    reasoning_effort: args.reasoningEffort,
    prompt_hash: sha256(prompt),
    tool_schema_hash: sha256(tools.definitions),
    status,
    error,
    turns,
    tool_trace: toolTrace,
    events,
    metrics: {
      elapsed_ms: observed.elapsed_ms,
      rounds: observed.rounds || totalRounds,
      tool_calls: observed.tool_calls || totalToolCalls,
      usage,
      usage_total: observed.usage_total,
      partial: observed.partial,
      stream: streamStats,
      context: modelContextMetrics(rawRecords),
      search_novelty: searchNovelty(toolTrace),
    },
    created_at: nowIso(),
  };
  run.mechanical = mechanicalEvaluation(caseSpec, run, tools.definitions);
  for (const record of rawRecords) await appendJsonl(path.join(experimentDir, 'raw', `${runId}.jsonl`), record);
  await fs.mkdir(path.join(experimentDir, 'traces'), { recursive: true });
  await fs.writeFile(path.join(experimentDir, 'traces', `${runId}.md`), traceMarkdown(run), 'utf8');
  return run;
}

function gitSnapshot() {
  try {
    const commit = execFileSync('git', ['rev-parse', 'HEAD'], { cwd: ROOT, encoding: 'utf8' }).trim();
    const status = execFileSync('git', ['status', '--porcelain', '--untracked-files=no'], { cwd: ROOT, encoding: 'utf8', maxBuffer: 20_000_000 });
    return { commit, dirty: Boolean(status.trim()), tracked_status_hash: sha256(status) };
  } catch {
    return { commit: '', dirty: null, tracked_status_hash: '' };
  }
}

async function prepareExperiment(args, suite) {
  const id = args.experiment || experimentId();
  const directory = path.join(args.output, id);
  await fs.mkdir(directory, { recursive: true });
  const manifestPath = path.join(directory, 'manifest.json');
  let manifest;
  try {
    manifest = await readJson(manifestPath);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    manifest = {
      schema_version: 2,
      experiment_id: id,
      created_at: nowIso(),
      provider: args.provider,
      model: args.model,
      api_url: args.apiUrl,
      reasoning_effort: args.reasoningEffort,
      sampling: { temperature: args.temperature, top_p: args.topP },
      repetitions: args.repetitions,
      max_rounds: args.maxRounds,
      max_tool_calls: args.maxToolCalls,
      structured_event_deltas: args.logDeltas,
      cases: suite.cases.map((entry) => entry.id),
      configs: suite.configs.map((entry) => ({
        id: entry.id,
        prompt_id: entry.promptId,
        search_strategy: entry.searchStrategy,
        system_prompt: materializePrompt(entry, args.reasoningEffort),
        prompt_hash: sha256(materializePrompt(entry, args.reasoningEffort)),
      })),
      cases_hash: sha256(suite.cases),
      git: gitSnapshot(),
      node: process.version,
    };
    await writeJson(manifestPath, manifest);
  }
  return { id, directory, manifest };
}

async function validateAssets(args, suite) {
  for (const file of [
    path.join(PUBLIC_DIR, 'volumes.json'),
    path.join(PUBLIC_DIR, 'indexador', 'search', 'manifest.json'),
    path.join(PUBLIC_DIR, 'indexador', 'indices', 'index.map'),
    path.join(RUNTIME_DIR, 'indexador_wasm.js'),
    path.join(RUNTIME_DIR, 'indexador_wasm.wasm'),
  ]) await fs.access(file);
  const ids = new Set();
  for (const entry of suite.cases) {
    if (ids.has(entry.id)) throw new Error(`Caso duplicado: ${entry.id}`);
    ids.add(entry.id);
    if (!entry.turns?.length) throw new Error(`Caso sem turnos: ${entry.id}`);
  }
  const server = await startStaticServer(PUBLIC_DIR);
  const toolChecks = [];
  try {
    const configsByStrategy = [...new Map(suite.configs.map((config) => [config.searchStrategy, config])).values()];
    for (const [index, config] of configsByStrategy.entries()) {
      const tools = await createTools(server, config);
      const smokeQuery = config.searchStrategy === 'multi_query_rrf'
        ? ['Clemente', 'Clemens']
        : 'Clemente';
      const [corpusResult, indicesResult] = await Promise.all([
        tools.execute('search_corpus', { query: smokeQuery, limit: 1 }),
        tools.execute('search_indices', { query: smokeQuery, limit: 1 }),
      ]);
      if (!corpusResult?.ok || !Array.isArray(corpusResult.data?.items)) {
        throw new Error(`Smoke da search_corpus falhou para ${config.searchStrategy}.`);
      }
      if (!indicesResult?.ok || !Array.isArray(indicesResult.data?.items)) {
        throw new Error(`Smoke da search_indices falhou para ${config.searchStrategy}.`);
      }
      const check = {
        strategy: config.searchStrategy,
        search_corpus_items: corpusResult.data.items.length,
        search_indices_items: indicesResult.data.items.length,
      };
      if (index === 0) {
        const metadataResult = await tools.execute('get_page_metadata', { volume_id: 'PG001', page: 18 });
        const ocrResult = await tools.execute('get_page_ocr', { volume_id: 'PG001', page: 18, max_chars: 500 });
        if (!metadataResult?.ok || !ocrResult?.ok || !ocrResult.data?.text) {
          throw new Error(`Smoke de metadados/OCR falhou para PG001:18 (${metadataResult?.error?.code || 'metadata'}, ${ocrResult?.error?.code || 'ocr'}).`);
        }
        check.page_metadata = true;
        check.page_ocr_chars = ocrResult.data.text.length;
      }
      toolChecks.push(check);
    }
  } finally {
    await server.close();
  }
  if (!args.noModel) {
    const provider = providerConfig(args);
    const endpoint = new URL('models', args.apiUrl.endsWith('/') ? args.apiUrl : `${args.apiUrl}/`);
    const response = await fetch(endpoint, { headers: provider.apiKey ? { Authorization: `Bearer ${provider.apiKey}` } : {} });
    if (!response.ok) throw new Error(`Provider models retornou HTTP ${response.status}.`);
    const payload = await response.json();
    const available = (payload.data || payload.models || []).map((entry) => entry.id || entry.name || entry.model);
    if (available.length && !available.includes(args.model)) {
      throw new Error(`Modelo ${args.model} não apareceu em /models.`);
    }
  }
  return { cases: suite.cases.length, configs: suite.configs.length, tool_checks: toolChecks };
}

async function runCommand(args, casesPayload) {
  const suite = selectedSuite(casesPayload, args);
  const planned = suite.cases.length * suite.configs.length * args.repetitions;
  if (planned > args.maxRuns) throw new Error(`${planned} runs excedem --max-runs=${args.maxRuns}.`);
  await validateAssets({ ...args, noModel: false }, suite);
  const experiment = await prepareExperiment(args, suite);
  const existing = latestRuns(await readJsonl(path.join(experiment.directory, 'runs.jsonl')));
  const completed = new Map(existing.map((run) => [run.run_id, run]));
  const server = await startStaticServer(PUBLIC_DIR);
  const toolsByStrategy = new Map();
  const getTools = async (config) => {
    if (!toolsByStrategy.has(config.searchStrategy)) {
      toolsByStrategy.set(config.searchStrategy, await createTools(server, config));
    }
    return toolsByStrategy.get(config.searchStrategy);
  };
  let done = 0;
  try {
    const configurationSnapshots = [];
    for (const config of suite.configs) {
      const tools = await getTools(config);
      configurationSnapshots.push({
        id: config.id,
        prompt_id: config.promptId,
        search_strategy: config.searchStrategy,
        system_prompt: materializePrompt(config, args.reasoningEffort),
        tool_definitions: tools.definitions,
        prompt_hash: sha256(materializePrompt(config, args.reasoningEffort)),
        tool_schema_hash: sha256(tools.definitions),
      });
    }
    await writeJson(path.join(experiment.directory, 'configurations.json'), configurationSnapshots);
    for (const caseSpec of suite.cases) {
      for (const config of suite.configs) {
        for (let repetition = 1; repetition <= args.repetitions; repetition += 1) {
          const runId = `${caseSpec.id}__${config.id}__r${repetition}`;
          const prior = completed.get(runId);
          if (prior && (prior.status === 'completed' || !args.retryErrors)) {
            done += 1;
            process.stderr.write(`[skip ${done}/${planned}] ${runId}\n`);
            continue;
          }
          const run = await runOne({
            caseSpec,
            config,
            repetition,
            args,
            tools: await getTools(config),
            experimentDir: experiment.directory,
          });
          await appendJsonl(path.join(experiment.directory, 'runs.jsonl'), run);
          await appendJsonl(path.join(experiment.directory, 'mechanical.jsonl'), { run_id: run.run_id, case_id: run.case_id, config_id: run.config_id, ...run.mechanical });
          done += 1;
          completed.set(runId, run);
          process.stderr.write(`[${done}/${planned}] ${runId} ${run.status} ${run.metrics.elapsed_ms}ms\n`);
        }
      }
    }
  } finally {
    await server.close();
  }
  process.stdout.write(`${experiment.id}\n`);
  return experiment.id;
}

function parseJsonResponse(text) {
  const clean = String(text || '').trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  return JSON.parse(clean);
}

async function judgeCall(args, systemPrompt, userPayload) {
  const provider = providerConfig(args);
  const result = await runChatCompletionLoop({
    ...provider,
    systemPrompt: materializePrompt({ prompt: systemPrompt }, args.reasoningEffort),
    conversationMessages: [{ role: 'user', content: JSON.stringify(userPayload) }],
    toolDefinitions: [],
    executeTool: async () => ({ ok: false }),
    maxRounds: 1,
    maxToolCalls: 0,
    reasoningEffort: args.reasoningEffort,
    temperature: args.temperature,
    topP: args.topP,
  });
  return { parsed: parseJsonResponse(result.content), raw: result.content, usage: result.usage };
}

const CASE_REVIEW_PROMPT = `Você é o avaliador cego de um benchmark de agente RAG patrístico. Receberá uma pergunta, expectativas e várias execuções identificadas apenas por letras. Avalie toda a sequência de tools, seus resultados e a resposta. Não favoreça respostas longas. Resumos localizam candidatos; somente OCR sustenta leitura ou citação textual. Não presuma que uma chamada bem-sucedida encontrou conteúdo relevante. Retorne JSON puro: {"evaluations":{"A":{"task_success":1,"retrieval_decision":1,"evidence_fidelity":1,"answer_quality":1,"efficiency":1,"findings":[""]}},"ranking":["A"],"case_findings":[""]}. Use notas inteiras 1–5 e inclua todas as letras.`;

const SYNTHESIS_PROMPT = `Você revisa um benchmark completo de agente RAG. Receberá avaliações cegas por caso, métricas mecânicas e o mapa final de letras para configurações. Sintetize padrões, falhas epistemológicas, estratégias fortes por família de pergunta e recomendações de experimentos seguintes. Não transforme métricas mecânicas em verdade sem considerar os traces semânticos. Retorne JSON puro: {"summary":"","strengths":{},"weaknesses":{},"recommendations":[],"manual_review_priorities":[]}.`;

async function definitionsByConfig(directory) {
  try {
    const snapshots = await readJson(path.join(directory, 'configurations.json'));
    return new Map(snapshots.map((snapshot) => [snapshot.id, snapshot.tool_definitions || []]));
  } catch (error) {
    if (error.code === 'ENOENT') return new Map();
    throw error;
  }
}

function reassessRuns(runs, casesPayload, definitionsMap = new Map()) {
  const cases = new Map(casesPayload.cases.map((entry) => [entry.id, entry]));
  return runs.map((run) => {
    const definitions = definitionsMap.get(run.config_id) || [];
    const toolTrace = (run.tool_trace || []).map((entry) => ({
      ...entry,
      argument_validation: entry.argument_validation || (definitions.length ? validateToolArguments(entry.tool, entry.arguments, definitions) : null),
    }));
    const assessed = { ...run, tool_trace: toolTrace };
    const observed = deriveObservedMetrics(assessed);
    assessed.metrics = {
      ...run.metrics,
      rounds: observed.rounds,
      tool_calls: observed.tool_calls,
      usage_total: observed.usage_total.total_tokens ? observed.usage_total : (run.metrics?.usage_total || observed.usage_total),
      partial: observed.partial,
      search_novelty: run.metrics?.search_novelty || searchNovelty(toolTrace),
    };
    const caseSpec = cases.get(run.case_id);
    if (caseSpec) assessed.mechanical = mechanicalEvaluation(caseSpec, assessed, definitions);
    return assessed;
  });
}

function compactReviewValue(value, maxChars = 3_000) {
  if (typeof value === 'string') return value.length > maxChars ? `${value.slice(0, maxChars)}…` : value;
  if (Array.isArray(value)) return value.slice(0, 12).map((entry) => compactReviewValue(entry, maxChars));
  if (!value || typeof value !== 'object') return value;
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => {
    if (['representations', 'url', 'viewer_url', 'ocr_url'].includes(key)) return [key, undefined];
    return [key, compactReviewValue(entry, maxChars)];
  }).filter(([, entry]) => entry !== undefined));
}

async function reviewCommand(args, casesPayload) {
  if (!args.experiment) throw new Error('review exige --experiment.');
  const directory = path.join(args.output, args.experiment);
  const definitionsMap = await definitionsByConfig(directory);
  const runs = reassessRuns(latestRuns(await readJsonl(path.join(directory, 'runs.jsonl'))), casesPayload, definitionsMap);
  if (!runs.length) throw new Error('Experimento sem runs.');
  const caseMap = new Map(casesPayload.cases.map((entry) => [entry.id, entry]));
  const configIds = [...new Set(runs.map((run) => run.config_id))].sort();
  const labelMap = Object.fromEntries(configIds.map((id, index) => [id, String.fromCharCode(65 + index)]));
  const reviewFile = path.join(directory, 'reviews.jsonl');
  const existing = await readJsonl(reviewFile);
  const reviewedCases = new Set(existing.filter((entry) => entry.type === 'case_review').map((entry) => entry.case_id));

  for (const caseId of [...new Set(runs.map((run) => run.case_id))]) {
    if (reviewedCases.has(caseId)) continue;
    const caseRuns = runs.filter((run) => run.case_id === caseId).map((run) => ({
      label: `${labelMap[run.config_id]}${run.repetition > 1 ? `-r${run.repetition}` : ''}`,
      repetition: run.repetition,
      status: run.status,
      turns: compactReviewValue(run.turns, 4_000),
      error: run.error,
      tools: run.tool_trace.map((entry) => ({
        tool: entry.tool,
        arguments: entry.arguments,
        argument_validation: entry.argument_validation,
        result: compactReviewValue(entry.result),
      })),
      mechanical: run.mechanical,
      metrics: run.metrics,
    }));
    try {
      const judged = await judgeCall(args, CASE_REVIEW_PROMPT, { case: caseMap.get(caseId), executions: caseRuns });
      await appendJsonl(reviewFile, { type: 'case_review', case_id: caseId, review: judged.parsed, raw: judged.raw, usage: judged.usage, created_at: nowIso() });
      process.stderr.write(`[review] ${caseId}\n`);
    } catch (error) {
      await appendJsonl(reviewFile, { type: 'case_review_error', case_id: caseId, error: error.message, created_at: nowIso() });
      process.stderr.write(`[review:error] ${caseId}: ${error.message}\n`);
    }
  }

  const reviews = await readJsonl(reviewFile);
  const mechanical = aggregateRuns(runs);
  const synthesis = await judgeCall(args, SYNTHESIS_PROMPT, {
    label_map: Object.fromEntries(Object.entries(labelMap).map(([config, label]) => [label, config])),
    case_reviews: reviews.filter((entry) => entry.type === 'case_review').map((entry) => ({ case_id: entry.case_id, review: entry.review })),
    mechanical,
  });
  await writeJson(path.join(directory, 'review.json'), { label_map: labelMap, synthesis: synthesis.parsed, raw: synthesis.raw, created_at: nowIso() });
  return args.experiment;
}

function usageTotal(run) {
  if (Number.isFinite(run.metrics?.usage_total?.total_tokens)) return Number(run.metrics.usage_total.total_tokens);
  const totals = run.metrics?.usage || [];
  return totals.reduce((sum, entry) => sum + Number(entry?.total?.total_tokens || entry?.total?.total || 0), 0);
}

function aggregateRuns(runs) {
  const buckets = new Map();
  for (const run of runs) {
    const bucket = buckets.get(run.config_id) || { config_id: run.config_id, runs: 0, completed: 0, mechanical_pass: 0, elapsed_ms: 0, rounds: 0, tool_calls: 0, tokens: 0, invalid_argument_calls: 0, no_novel_search_calls: 0 };
    bucket.runs += 1;
    bucket.completed += run.status === 'completed' ? 1 : 0;
    bucket.mechanical_pass += run.mechanical?.pass ? 1 : 0;
    bucket.elapsed_ms += Number(run.metrics?.elapsed_ms || 0);
    bucket.rounds += Number(run.metrics?.rounds || 0);
    bucket.tool_calls += Number(run.metrics?.tool_calls || 0);
    bucket.tokens += usageTotal(run);
    bucket.invalid_argument_calls += (run.tool_trace || []).filter((entry) => entry.argument_validation?.valid === false).length;
    bucket.no_novel_search_calls += Number(run.metrics?.search_novelty?.no_novel_search_calls || run.mechanical?.search_novelty?.no_novel_search_calls || 0);
    buckets.set(run.config_id, bucket);
  }
  return [...buckets.values()].map((bucket) => ({
    ...bucket,
    mechanical_pass_rate: bucket.mechanical_pass / bucket.runs,
    average_elapsed_ms: Math.round(bucket.elapsed_ms / bucket.runs),
    average_rounds: bucket.rounds / bucket.runs,
    average_tool_calls: bucket.tool_calls / bucket.runs,
    average_tokens: bucket.tokens / bucket.runs,
  }));
}

function reportMarkdown(experiment, aggregate, failures, review) {
  const lines = [
    `# Benchmark agentic RAG — ${experiment.experiment_id}`,
    '',
    `Provider/modelo: \`${experiment.provider}\` / \`${experiment.model}\`  `,
    `Thinking: \`${experiment.reasoning_effort}\`  `,
    `Casos: ${experiment.cases.length}; configurações: ${experiment.configs.length}`,
    '',
    '> As verificações mecânicas e a revisão LLM são sinais distintos. Consulte os traces antes de escolher uma estratégia.',
    '',
    '## Métricas mecânicas',
    '',
    '| Configuração | Runs | Concluídos | Pass mecânico | Latência média | Rodadas/run | Tools/run | Tokens/run | Args inválidos | Buscas sem novidade |',
    '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|',
  ];
  for (const row of aggregate) {
    lines.push(`| ${row.config_id} | ${row.runs} | ${row.completed} | ${(100 * row.mechanical_pass_rate).toFixed(1)}% | ${row.average_elapsed_ms} ms | ${row.average_rounds.toFixed(2)} | ${row.average_tool_calls.toFixed(2)} | ${row.average_tokens.toFixed(0)} | ${row.invalid_argument_calls} | ${row.no_novel_search_calls} |`);
  }
  if (failures.length) {
    lines.push('', '## Execuções que exigem inspeção', '');
    for (const failure of failures) {
      const reasons = [
        failure.error?.code,
        ...failure.failed_checks.map((check) => check.id),
      ].filter(Boolean).join(', ');
      lines.push(`- \`${failure.run_id}\`: ${reasons || failure.status}`);
    }
  }
  if (review?.synthesis) {
    lines.push('', '## Síntese do revisor', '', review.synthesis.summary || '', '');
    for (const recommendation of review.synthesis.recommendations || []) lines.push(`- ${recommendation}`);
    if (review.synthesis.manual_review_priorities?.length) {
      lines.push('', '## Prioridades para inspeção manual', '');
      for (const item of review.synthesis.manual_review_priorities) lines.push(`- ${item}`);
    }
  }
  lines.push('', '## Inspeção', '', 'Os traces completos estão em `traces/`; os payloads integrais estão em `raw/`.', '');
  return `${lines.join('\n')}\n`;
}

async function reportCommand(args, casesPayload) {
  if (!args.experiment) throw new Error('report exige --experiment.');
  const directory = path.join(args.output, args.experiment);
  const [manifest, storedRuns, definitionsMap] = await Promise.all([
    readJson(path.join(directory, 'manifest.json')),
    readJsonl(path.join(directory, 'runs.jsonl')).then(latestRuns),
    definitionsByConfig(directory),
  ]);
  const runs = reassessRuns(storedRuns, casesPayload, definitionsMap);
  let review = null;
  try { review = await readJson(path.join(directory, 'review.json')); } catch (error) { if (error.code !== 'ENOENT') throw error; }
  const aggregate = aggregateRuns(runs);
  const failures = runs.filter((run) => run.status !== 'completed' || !run.mechanical?.pass).map((run) => ({
    run_id: run.run_id,
    case_id: run.case_id,
    config_id: run.config_id,
    status: run.status,
    error: run.error,
    failed_checks: (run.mechanical?.checks || []).filter((check) => !check.pass),
    metrics: run.metrics,
  }));
  const report = { manifest, aggregate, failures, review, generated_at: nowIso() };
  await writeJson(path.join(directory, 'report.json'), report);
  await writeJson(path.join(directory, 'audit.json'), {
    experiment_id: manifest.experiment_id,
    generated_at: nowIso(),
    runs: runs.map((run) => ({
      run_id: run.run_id,
      case_id: run.case_id,
      config_id: run.config_id,
      status: run.status,
      error: run.error,
      metrics: run.metrics,
      mechanical: run.mechanical,
    })),
  });
  await fs.writeFile(path.join(directory, 'report.md'), reportMarkdown(manifest, aggregate, failures, review), 'utf8');
  process.stdout.write(`${path.join(directory, 'report.md')}\n`);
}

async function inspectCommand(args, casesPayload) {
  if (!args.experiment) throw new Error('inspect exige --experiment.');
  const directory = path.join(args.output, args.experiment);
  const definitionsMap = await definitionsByConfig(directory);
  const runs = reassessRuns(latestRuns(await readJsonl(path.join(directory, 'runs.jsonl'))), casesPayload, definitionsMap);
  if (args.run) {
    const run = runs.find((entry) => entry.run_id === args.run);
    if (!run) throw new Error(`Run não encontrado: ${args.run}`);
    process.stdout.write(traceMarkdown(run));
    return;
  }
  const selected = args.case ? runs.filter((run) => run.case_id === args.case) : runs;
  for (const run of selected) process.stdout.write(traceMarkdown(run));
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) return process.stdout.write(usage());
  const casesPayload = await readJson(CASES_PATH);
  const suite = selectedSuite(casesPayload, args);
  const planned = suite.cases.length * suite.configs.length * args.repetitions;
  if (args.command === 'plan') {
    process.stdout.write(`${JSON.stringify({ suite: casesPayload.suite, cases: suite.cases.map((entry) => entry.id), configs: suite.configs.map((entry) => entry.id), repetitions: args.repetitions, planned_runs: planned }, null, 2)}\n`);
    return;
  }
  if (args.command === 'validate') {
    process.stdout.write(`${JSON.stringify(await validateAssets(args, suite), null, 2)}\n`);
    return;
  }
  if (args.command === 'run') return runCommand(args, casesPayload);
  if (args.command === 'review') return reviewCommand(args, casesPayload);
  if (args.command === 'report') return reportCommand(args, casesPayload);
  if (args.command === 'inspect') return inspectCommand(args, casesPayload);
  if (args.command === 'all') {
    const id = await runCommand(args, casesPayload);
    args.experiment = id;
    await reviewCommand(args, casesPayload);
    await reportCommand(args, casesPayload);
    return;
  }
  throw new Error(`Comando desconhecido: ${args.command}`);
}

const isMainModule = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMainModule) {
  main().catch((error) => {
    console.error(error.stack || error.message || error);
    process.exitCode = 1;
  });
}
