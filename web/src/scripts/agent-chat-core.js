import {
  appendTurnContext,
  compactConversationContext,
  contextPolicyForModel,
  estimateChatRequestTokens,
  providerMessages,
} from './agent-chat-context.js';
import {
  sanitizeSourceCitationGroups,
  sourceCitationIds,
} from './agent-chat-citations.js';

const DEFAULT_MAX_ROUNDS = 8;
const DEFAULT_MAX_TOOL_CALLS = 12;
const DEBUG_MAX_CHARS = 20_000;

export class AgentChatError extends Error {
  constructor(code, message, details = null) {
    super(message);
    this.name = 'AgentChatError';
    this.code = code;
    this.details = details;
  }
}

export function normalizeChatEndpoint(value) {
  const raw = String(value || '').trim();
  if (!raw) throw new AgentChatError('invalid_url', 'API URL is required.');

  let url;
  try {
    url = new URL(raw);
  } catch {
    throw new AgentChatError('invalid_url', 'API URL is invalid.');
  }
  if (!['http:', 'https:'].includes(url.protocol)) {
    throw new AgentChatError('invalid_url', 'API URL must use HTTP or HTTPS.');
  }

  url.hash = '';
  const cleanPath = url.pathname.replace(/\/+$/, '');
  url.pathname = /\/chat\/completions$/i.test(cleanPath)
    ? cleanPath
    : `${cleanPath}/chat/completions`.replace(/^\/\//, '/');
  return url.toString();
}

export function redactSecrets(value) {
  if (Array.isArray(value)) return value.map(redactSecrets);
  if (!value || typeof value !== 'object') return value;

  const out = {};
  for (const [key, entry] of Object.entries(value)) {
    if (/^(authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|secret)$/i.test(key)) {
      out[key] = '[REDACTED]';
    } else {
      out[key] = redactSecrets(entry);
    }
  }
  return out;
}

export function sanitizeDebugPayload(value, maxChars = DEBUG_MAX_CHARS) {
  const sanitized = redactSecrets(value);
  let serialized;
  try {
    serialized = JSON.stringify(sanitized);
  } catch {
    return { truncated: true, preview: String(sanitized).slice(0, maxChars) };
  }
  if (serialized.length <= maxChars) return sanitized;
  const envelope = {};
  if (sanitized && typeof sanitized === 'object' && !Array.isArray(sanitized)) {
    for (const key of ['round', 'tool', 'call_id', 'duration_ms', 'status']) {
      if (['string', 'number', 'boolean'].includes(typeof sanitized[key])) envelope[key] = sanitized[key];
    }
    const result = sanitized.result;
    if (result && typeof result === 'object') {
      const data = result.data && typeof result.data === 'object' ? result.data : {};
      envelope.result = {
        ok: result.ok !== false,
        ...(result.error ? { error: result.error } : {}),
        data: {
          ...(Number.isFinite(data.total) ? { total: data.total } : {}),
          ...(Array.isArray(data.items) ? { result_count: data.items.length } : {}),
          ...(Array.isArray(data.entries) ? { result_count: data.entries.length } : {}),
        },
      };
    }
  }
  return {
    ...envelope,
    truncated: true,
    original_chars: serialized.length,
    preview: serialized.slice(0, maxChars),
  };
}

export function selectCitedSources(text, sources) {
  const available = Array.isArray(sources) ? sources : [];
  const citedIds = sourceCitationIds(text);
  if (!citedIds.size) return [];
  return available.filter((source) => (
    citedIds.has(source?.citationId)
    && !(source?.evidence || []).includes('catalog')
    && !(source?.provenance || []).includes('list_volumes')
  ));
}

export function sanitizeAssistantContent(value, sources = []) {
  const availableIds = new Set(
    (Array.isArray(sources) ? sources : [])
      .filter((source) => (
        typeof source?.citationId === 'string'
        && source.citationId
        && !(source?.evidence || []).includes('catalog')
        && !(source?.provenance || []).includes('list_volumes')
      ))
      .map((source) => source.citationId),
  );
  let content = String(value || '');

  // Links are rendered exclusively from structured tool sources. The model never
  // receives their URLs, so every URL in free-form output is necessarily untrusted.
  content = content
    .replace(/\[([^\]\n]+)\]\(\s*[^)\n]+\s*\)/g, '$1')
    .replace(/<\s*(?:https?:\/\/|www\.)[^>\s]+\s*>/gi, '')
    .replace(/\b(?:https?:\/\/|www\.)[^\s<>()\]]+/gi, '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
  return sanitizeSourceCitationGroups(content, availableIds)
    .replace(/[ \t]+([.,;:!?])/g, '$1')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function filterIndexHitsByScope(hits, { collection = '', volumeId = '' } = {}) {
  return (Array.isArray(hits) ? hits : []).filter((hit) => {
    const url = new URL(String(hit?.url || ''), 'https://bibliotheca.invalid');
    const hitVolume = url.searchParams.get('volume') || '';
    if (volumeId && hitVolume !== volumeId) return false;
    if (collection && !hitVolume.startsWith(collection)) return false;
    return true;
  });
}

function emit(onEvent, type, payload = {}) {
  onEvent?.({ type, at: new Date().toISOString(), payload: sanitizeDebugPayload(payload) });
}

function providerMessage(payload) {
  const message = payload?.error?.message;
  if (typeof message === 'string' && message.trim()) return message.trim();
  if (typeof payload?.message === 'string' && payload.message.trim()) return payload.message.trim();
  return '';
}

function normalizeToolCall(call, id) {
  if (!call || typeof call !== 'object') return null;
  const fn = call.function && typeof call.function === 'object' ? call.function : call;
  const name = String(fn.name || '').trim();
  if (!name) return null;
  let args = fn.arguments ?? call.arguments ?? {};
  if (typeof args !== 'string') {
    try {
      args = JSON.stringify(args);
    } catch {
      args = '{}';
    }
  }
  return {
    id: String(call.id || id),
    type: 'function',
    function: { name, arguments: args },
  };
}

function decodeXmlText(value) {
  return String(value || '')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&amp;/gi, '&');
}

function parseLooseValue(value) {
  const text = decodeXmlText(value).trim();
  if (!text) return '';
  try {
    return JSON.parse(text);
  } catch {
    if (/^-?(?:\d+\.?\d*|\.\d+)$/.test(text)) return Number(text);
    if (/^true$/i.test(text)) return true;
    if (/^false$/i.test(text)) return false;
    if (/^null$/i.test(text)) return null;
    return text;
  }
}

function callsFromJsonEnvelope(value, idPrefix) {
  const cleaned = String(value || '').trim()
    .replace(/^```(?:json)?\s*/i, '')
    .replace(/\s*```$/i, '');
  if (!cleaned) return [];
  let parsed;
  try {
    parsed = JSON.parse(cleaned);
  } catch {
    return [];
  }
  const candidates = Array.isArray(parsed)
    ? parsed
    : Array.isArray(parsed?.tool_calls)
      ? parsed.tool_calls
      : [parsed];
  return candidates
    .map((call, index) => normalizeToolCall(call, `${idPrefix}-${index + 1}`))
    .filter(Boolean);
}

function callsFromXmlEnvelope(value, idPrefix) {
  const calls = [];
  const functionPattern = /<function\s*=\s*["']?([A-Za-z0-9_-]+)["']?\s*>([\s\S]*?)<\/function>/gi;
  for (const match of String(value || '').matchAll(functionPattern)) {
    const args = {};
    const parameterPattern = /<parameter\s*=\s*["']?([A-Za-z0-9_-]+)["']?\s*>([\s\S]*?)<\/parameter>/gi;
    for (const parameter of match[2].matchAll(parameterPattern)) {
      args[parameter[1]] = parseLooseValue(parameter[2]);
    }
    const call = normalizeToolCall(
      { function: { name: match[1], arguments: args } },
      `${idPrefix}-${calls.length + 1}`,
    );
    if (call) calls.push(call);
  }
  return calls;
}

function recoverTaggedToolCalls(message, allowedNames, round) {
  const sources = [
    ['reasoning', message?.reasoning],
    ['content', message?.content],
  ];
  for (const [source, raw] of sources) {
    if (typeof raw !== 'string' || !/<tool_call(?:\s[^>]*)?>/i.test(raw)) continue;
    const calls = [];
    const envelopePattern = /<tool_call(?:\s[^>]*)?>([\s\S]*?)<\/tool_call>/gi;
    for (const [index, envelope] of [...raw.matchAll(envelopePattern)].entries()) {
      const body = envelope[1];
      const parsed = callsFromJsonEnvelope(body, `compat-${round}-${index + 1}`);
      calls.push(...(parsed.length ? parsed : callsFromXmlEnvelope(body, `compat-${round}-${index + 1}`)));
    }
    const registered = calls.filter((call) => allowedNames.has(call.function.name));
    if (registered.length) {
      return {
        calls: registered,
        source,
        cleanedContent: source === 'content'
          ? raw.replace(envelopePattern, '').trim() || null
          : typeof message.content === 'string' && message.content.trim() ? message.content : null,
      };
    }
  }
  return null;
}

function appendStreamToolCall(target, rawCall, fallbackIndex) {
  if (!rawCall || typeof rawCall !== 'object') return;
  const index = Number.isInteger(rawCall.index) ? rawCall.index : fallbackIndex;
  const current = target.get(index) || {
    id: '',
    type: 'function',
    function: { name: '', arguments: '' },
  };
  if (rawCall.id) current.id = String(rawCall.id);
  if (rawCall.type) current.type = String(rawCall.type);
  const fn = rawCall.function || {};
  if (fn.name) current.function.name += String(fn.name);
  if (fn.arguments != null) {
    current.function.arguments += typeof fn.arguments === 'string'
      ? fn.arguments
      : JSON.stringify(fn.arguments);
  }
  target.set(index, current);
}

async function readCompletionStream(response, onEvent, round) {
  const reader = response.body?.getReader?.();
  if (!reader) throw new AgentChatError('invalid_response', 'Provider returned an unreadable stream.');

  const decoder = new TextDecoder();
  const toolCalls = new Map();
  let buffer = '';
  let content = '';
  let reasoning = '';
  let role = 'assistant';
  let finishReason = null;
  let usage;
  let providerError = null;

  function consumeData(rawData) {
    const data = rawData.trim();
    if (!data || data === '[DONE]') return;
    let chunk;
    try {
      chunk = JSON.parse(data);
    } catch {
      throw new AgentChatError('invalid_response', 'Provider returned an invalid streaming event.', {
        preview: data.slice(0, 600),
      });
    }
    if (chunk?.error) {
      providerError = chunk;
      return;
    }
    if (chunk?.usage) usage = chunk.usage;
    const choice = chunk?.choices?.[0];
    if (!choice) return;
    const delta = choice.delta || choice.message || {};
    if (delta.role) role = delta.role;
    if (typeof delta.content === 'string' && delta.content) {
      content += delta.content;
      emit(onEvent, 'model_delta', { round, content: delta.content });
    }
    if (typeof delta.reasoning === 'string') reasoning += delta.reasoning;
    if (typeof delta.reasoning_content === 'string') reasoning += delta.reasoning_content;
    if (Array.isArray(delta.tool_calls)) {
      delta.tool_calls.forEach((call, index) => appendStreamToolCall(toolCalls, call, index));
    }
    if (delta.function_call) appendStreamToolCall(toolCalls, { index: 0, function: delta.function_call }, 0);
    if (choice.finish_reason != null) finishReason = choice.finish_reason;
  }

  function consumeLines(final = false) {
    const lines = buffer.split(/\r?\n/);
    buffer = final ? '' : lines.pop() || '';
    for (const line of lines) {
      if (!line.startsWith('data:')) continue;
      consumeData(line.slice(5));
    }
    if (final && buffer.startsWith('data:')) consumeData(buffer.slice(5));
  }

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    consumeLines();
  }
  buffer += decoder.decode();
  consumeLines(true);

  if (providerError) {
    throw new AgentChatError('provider', providerMessage(providerError) || 'Provider returned an error inside the stream.');
  }
  const normalizedCalls = [...toolCalls.entries()]
    .sort(([left], [right]) => left - right)
    .map(([index, call]) => normalizeToolCall(call, `call-${round}-${index + 1}`))
    .filter(Boolean);
  return {
    choices: [{
      message: {
        role,
        content: content || null,
        ...(reasoning ? { reasoning } : {}),
        ...(normalizedCalls.length ? { tool_calls: normalizedCalls } : {}),
      },
      finish_reason: finishReason,
    }],
    ...(usage ? { usage } : {}),
  };
}

async function requestCompletion({ endpoint, apiKey, body, fetchImpl, signal, onEvent, round }) {
  emit(onEvent, 'request', { round, endpoint, body });
  const startedAt = performance.now();
  let response;
  try {
    response = await fetchImpl(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error?.name === 'AbortError') throw new AgentChatError('aborted', 'Request aborted.');
    throw new AgentChatError('network', error?.message || 'Network request failed.');
  }

  const contentType = response.headers.get('content-type') || '';
  if (response.ok && contentType.includes('text/event-stream')) {
    let payload;
    try {
      payload = await readCompletionStream(response, onEvent, round);
    } catch (error) {
      if (error?.name === 'AbortError') throw new AgentChatError('aborted', 'Request aborted.');
      throw error;
    }
    emit(onEvent, 'response', {
      round,
      status: response.status,
      duration_ms: Math.round(performance.now() - startedAt),
      body: payload,
    });
    return payload;
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      throw new AgentChatError('invalid_response', 'Provider returned invalid JSON.', {
        status: response.status,
        preview: text.slice(0, 600),
      });
    }
  }

  emit(onEvent, 'response', {
    round,
    status: response.status,
    duration_ms: Math.round(performance.now() - startedAt),
    body: payload,
  });

  if (!response.ok) {
    const message = providerMessage(payload);
    if (
      body.reasoning_effort
      && (response.status === 400 || response.status === 422)
      && /reasoning|unknown (?:field|parameter)|unsupported (?:field|parameter)/i.test(message)
    ) {
      const fallbackBody = { ...body };
      delete fallbackBody.reasoning_effort;
      emit(onEvent, 'capability_fallback', { round, capability: 'reasoning_effort', message });
      return requestCompletion({ endpoint, apiKey, body: fallbackBody, fetchImpl, signal, onEvent, round });
    }
    const code = response.status === 401 || response.status === 403
      ? 'auth'
      : response.status === 429
        ? 'rate_limit'
        : 'provider';
    throw new AgentChatError(code, message || `Provider returned HTTP ${response.status}.`, {
      status: response.status,
    });
  }
  return payload;
}

export function normalizeAssistantMessage(message, toolDefinitions, round) {
  if (!message || typeof message !== 'object') {
    throw new AgentChatError('invalid_response', 'Provider response has no assistant message.');
  }
  const explicitCalls = Array.isArray(message.tool_calls)
    ? message.tool_calls.map((call, index) => normalizeToolCall(call, `call-${round}-${index + 1}`)).filter(Boolean)
    : [];
  const legacyCall = !explicitCalls.length && message.function_call
    ? normalizeToolCall(message.function_call, `legacy-${round}-1`)
    : null;
  const allowedNames = new Set(toolDefinitions
    .map((definition) => definition?.function?.name)
    .filter((name) => typeof name === 'string' && name));
  const recovered = !explicitCalls.length && !legacyCall
    ? recoverTaggedToolCalls(message, allowedNames, round)
    : null;
  const calls = explicitCalls.length ? explicitCalls : legacyCall ? [legacyCall] : recovered?.calls || [];
  const reasoning = typeof message.reasoning === 'string' && message.reasoning
    ? message.reasoning
    : typeof message.reasoning_content === 'string' && message.reasoning_content
      ? message.reasoning_content
      : '';
  return {
    assistant: {
      role: 'assistant',
      content: recovered
        ? recovered.cleanedContent
        : typeof message.content === 'string' ? message.content : null,
      ...(calls.length && explicitCalls.length && reasoning ? { reasoning } : {}),
      ...(calls.length ? { tool_calls: calls } : {}),
    },
    recovery: recovered ? { source: recovered.source, tools: calls.map((call) => call.function.name) } : null,
  };
}

function modelSafeValue(value, sourceIds) {
  if (Array.isArray(value)) return value.map((entry) => modelSafeValue(entry, sourceIds));
  if (!value || typeof value !== 'object') return value;

  const output = {};
  for (const [key, entry] of Object.entries(value)) {
    if (/^(?:url|href|viewer_url|raw_ocr_url|indices_url|representations|links|sources|ui)$/i.test(key)) continue;
    if (key === 'source_key') {
      const sourceId = sourceIds.get(String(entry || ''));
      if (sourceId) output.source_id = sourceId;
      continue;
    }
    output[key] = modelSafeValue(entry, sourceIds);
  }
  return output;
}

function toolResultForModel(result, sourceIds) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) return result;
  const data = result.modelContext ?? result.data ?? null;
  return {
    ok: result.ok !== false,
    data: modelSafeValue(data, sourceIds),
    ...(result.reused ? { reused: true } : {}),
    ...(result.error ? { error: modelSafeValue(result.error, sourceIds) } : {}),
  };
}

function parseToolArguments(call) {
  const raw = call?.function?.arguments;
  if (typeof raw !== 'string') {
    throw new AgentChatError('invalid_tool_arguments', 'Tool arguments must be a JSON string.');
  }
  try {
    const args = JSON.parse(raw);
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('not an object');
    return args;
  } catch {
    throw new AgentChatError('invalid_tool_arguments', 'Tool arguments are not a valid JSON object.');
  }
}

function uniqueStrings(...values) {
  return [...new Set(values.flat().filter((value) => typeof value === 'string' && value.trim()).map((value) => value.trim()))];
}

function mergeSource(current, incoming) {
  return {
    ...current,
    ...incoming,
    citationId: current.citationId,
    representations: {
      ...(current.representations || {}),
      ...(incoming.representations || {}),
    },
    metadata: {
      ...(current.metadata || {}),
      ...(incoming.metadata || {}),
    },
    provenance: uniqueStrings(current.provenance || [], incoming.provenance || []),
    evidence: uniqueStrings(current.evidence || [], incoming.evidence || []),
  };
}

function nextCitationId(target) {
  let next = 1;
  for (const source of target.values()) {
    const match = /^s(\d+)$/.exec(String(source?.citationId || ''));
    if (match) next = Math.max(next, Number(match[1]) + 1);
  }
  return `s${next}`;
}

function collectSources(result, target, sourceIds) {
  const structured = Array.isArray(result?.sources) ? result.sources : [];
  for (const source of structured) {
    if (!source || typeof source.id !== 'string' || !source.id.trim()) continue;
    const id = source.id.trim();
    const current = target.get(id);
    if (current) {
      target.set(id, mergeSource(current, source));
      continue;
    }
    const citationId = nextCitationId(target);
    sourceIds.set(id, citationId);
    target.set(id, {
      ...source,
      id,
      citationId,
      provenance: uniqueStrings(source.provenance || []),
      evidence: uniqueStrings(source.evidence || []),
    });
  }

  // Temporary compatibility for integrations that still return the original flat links.
  const links = Array.isArray(result?.links) ? result.links : [];
  for (const link of links) {
    if (!link || typeof link.url !== 'string' || !/^(?:https?:|\/)/i.test(link.url)) continue;
    const id = `link:${link.kind || 'source'}:${link.url}`;
    if (target.has(id)) continue;
    const citationId = nextCitationId(target);
    sourceIds.set(id, citationId);
    target.set(id, {
      id,
      citationId,
      kind: String(link.kind || 'link'),
      label: String(link.label || link.url),
      representations: { primary: { url: link.url } },
      provenance: ['legacy_link'],
      evidence: [],
    });
  }
}

function aggregateUsage(target, rawUsage, round) {
  if (!rawUsage || typeof rawUsage !== 'object') return;
  const normalized = {};
  for (const [key, value] of Object.entries(rawUsage)) {
    if (typeof value === 'number' && Number.isFinite(value)) normalized[key] = value;
  }
  const cachedTokens = rawUsage?.prompt_tokens_details?.cached_tokens
    ?? rawUsage?.input_tokens_details?.cached_tokens;
  if (typeof cachedTokens === 'number' && Number.isFinite(cachedTokens)) {
    normalized.cached_tokens = cachedTokens;
  }
  if (!Object.keys(normalized).length) return;
  target.rounds.push({ round, ...normalized });
  for (const [key, value] of Object.entries(normalized)) {
    target.total[key] = (target.total[key] || 0) + value;
  }
}

function initializeSourceRegistry(sourceRegistry) {
  const sources = new Map();
  const sourceIds = new Map();
  for (const source of Array.isArray(sourceRegistry) ? sourceRegistry : []) {
    const id = typeof source?.id === 'string' ? source.id.trim() : '';
    const citationId = typeof source?.citationId === 'string' ? source.citationId.trim() : '';
    if (!id || !/^s\d+$/.test(citationId) || sources.has(id)) continue;
    sources.set(id, {
      ...source,
      id,
      citationId,
      provenance: uniqueStrings(source.provenance || []),
      evidence: uniqueStrings(source.evidence || []),
    });
    sourceIds.set(id, citationId);
  }
  return { sources, sourceIds };
}

function latestPromptTokens(usage, fallback = 0) {
  const latest = usage?.rounds?.at?.(-1) || {};
  const value = latest.prompt_tokens ?? latest.input_tokens;
  return Number.isFinite(value) ? value : fallback;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}

async function executeOneTool(call, executeTool, onEvent, signal, toolCache) {
  const name = String(call?.function?.name || 'unknown_tool');
  const startedAt = performance.now();
  let args;
  try {
    args = parseToolArguments(call);
  } catch (error) {
    const result = { ok: false, data: null, error: { code: error.code, message: error.message } };
    emit(onEvent, 'tool_error', { tool: name, call_id: call?.id, result });
    return result;
  }

  const cacheKey = `${name}:${canonicalJson(args)}`;
  if (toolCache?.has(cacheKey)) {
    emit(onEvent, 'tool_start', { tool: name, call_id: call?.id, arguments: args, reused: true });
    const cached = await toolCache.get(cacheKey);
    const reused = { ...cached, reused: true };
    emit(onEvent, 'tool_end', {
      tool: name,
      call_id: call?.id,
      duration_ms: Math.round(performance.now() - startedAt),
      reused: true,
      result: reused,
    });
    return reused;
  }

  emit(onEvent, 'tool_start', { tool: name, call_id: call?.id, arguments: args });
  try {
    if (signal?.aborted) throw new AgentChatError('aborted', 'Request aborted.');
    const pending = Promise.resolve(executeTool(name, args, { signal })).then((result) => (
      result && typeof result === 'object' ? result : { ok: true, data: result, error: null }
    ));
    toolCache?.set(cacheKey, pending);
    const normalized = await pending;
    emit(onEvent, 'tool_end', {
      tool: name,
      call_id: call?.id,
      duration_ms: Math.round(performance.now() - startedAt),
      result: normalized,
    });
    return normalized;
  } catch (error) {
    toolCache?.delete(cacheKey);
    const result = {
      ok: false,
      data: null,
      error: {
        code: error?.code || 'tool_failed',
        message: error?.message || 'Tool execution failed.',
      },
    };
    emit(onEvent, 'tool_error', {
      tool: name,
      call_id: call?.id,
      duration_ms: Math.round(performance.now() - startedAt),
      result,
    });
    return result;
  }
}

export async function runChatCompletionLoop(options) {
  const {
    apiUrl,
    apiKey = '',
    model,
    systemPrompt,
    routeContext = '',
    conversationMessages = [],
    toolDefinitions = [],
    executeTool,
    fetchImpl = fetch,
    signal,
    onEvent,
    maxRounds = DEFAULT_MAX_ROUNDS,
    maxToolCalls = DEFAULT_MAX_TOOL_CALLS,
    reasoningEffort = 'none',
    temperature,
    topP,
    sourceRegistry = [],
    contextState = {},
    contextPolicy: contextPolicyOverrides = {},
  } = options;

  if (!String(model || '').trim()) throw new AgentChatError('missing_model', 'Model is required.');
  if (!String(systemPrompt || '').trim()) throw new AgentChatError('missing_prompt', 'System prompt is required.');
  if (typeof executeTool !== 'function') throw new AgentChatError('configuration', 'Tool executor is required.');

  const endpoint = normalizeChatEndpoint(apiUrl);
  const registry = initializeSourceRegistry(sourceRegistry);
  const { sources, sourceIds } = registry;
  const toolCache = new Map();
  const usage = { rounds: [], total: {} };
  const policy = contextPolicyForModel(model, contextPolicyOverrides);
  const contextualMessages = appendTurnContext(conversationMessages, routeContext);
  if (signal?.aborted) throw new AgentChatError('aborted', 'Request aborted.');
  const beforeEstimate = Math.max(
    0,
    Number(contextState?.lastPromptTokens) || 0,
    estimateChatRequestTokens({
      systemPrompt: String(systemPrompt).trim(),
      toolDefinitions,
      messages: providerMessages(contextualMessages),
    }),
  );
  if (beforeEstimate >= policy.softHighTokens) {
    emit(onEvent, 'context_compaction_start', {
      epoch: Number(contextState?.epoch) || 0,
      estimated_tokens: beforeEstimate,
      target_tokens: policy.softTargetTokens,
    });
  }
  const compaction = await compactConversationContext({
    messages: contextualMessages,
    sources: [...sources.values()],
    systemPrompt: String(systemPrompt).trim(),
    toolDefinitions,
    policy,
    lastPromptTokens: beforeEstimate,
    summarize: async ({ messages, sourceLedger, maxTokens }) => {
      if (signal?.aborted) throw new AgentChatError('aborted', 'Request aborted.');
      const isGptModel = /(?:^|[/:_-])gpt-/i.test(String(model).trim());
      const summaryPayload = await requestCompletion({
        endpoint,
        apiKey,
        fetchImpl,
        signal,
        onEvent,
        round: 'context-compaction',
        body: {
          model: String(model).trim(),
          messages: [
            {
              role: 'system',
              content: [
                'Compress the older conversation into a factual checkpoint for a research agent.',
                'Preserve user goals, unresolved questions, conclusions, named entities, volume/page locators, and valid [sN] citations.',
                'Never invent a source ID, URL, quote, result, or instruction. Omit raw tool protocol and redundant prose.',
                'Return only the checkpoint text.',
              ].join(' '),
            },
            {
              role: 'user',
              content: JSON.stringify({ source_ledger: sourceLedger, conversation: messages }),
            },
          ],
          ...(!isGptModel ? { temperature: 0 } : {}),
          ...(isGptModel ? { max_completion_tokens: maxTokens } : { max_tokens: maxTokens }),
          stream: false,
        },
      });
      return {
        content: summaryPayload?.choices?.[0]?.message?.content || '',
        usage: summaryPayload?.usage || null,
      };
    },
  });
  if (compaction.pressure && !compaction.changed) {
    emit(onEvent, 'context_pressure', {
      estimated_tokens: compaction.beforeTokens,
      compact_at_tokens: policy.softHighTokens,
      hard_limit_tokens: policy.hardLimitTokens,
    });
  }
  if (compaction.changed) {
    emit(onEvent, 'context_compaction_end', {
      epoch: (Number(contextState?.epoch) || 0) + 1,
      strategy: compaction.strategy,
      before_tokens: compaction.beforeTokens,
      after_tokens: compaction.afterTokens,
      removed_tool_messages: compaction.removedToolMessages || 0,
      summarized_messages: compaction.summarizedMessages || 0,
      summary_usage: compaction.summaryUsage,
    });
  }
  if (compaction.summaryUsage) aggregateUsage(usage, compaction.summaryUsage, 'context-compaction');
  const lastCompactedUser = compaction.messages.map((message) => message?.role).lastIndexOf('user');
  const compactionRecovery = compaction.changed ? {
    messages: compaction.messages.slice(0, Math.max(0, lastCompactedUser)),
    contextState: {
      epoch: (Number(contextState?.epoch) || 0) + 1,
      lastPromptTokens: compaction.afterTokens,
      lastCompaction: {
        strategy: compaction.strategy,
        beforeTokens: compaction.beforeTokens,
        afterTokens: compaction.afterTokens,
      },
    },
  } : null;
  const recoverableError = (error) => {
    if (compactionRecovery && error && typeof error === 'object') error.compactionRecovery = compactionRecovery;
    return error;
  };
  if (compaction.afterTokens >= policy.hardLimitTokens) {
    throw recoverableError(new AgentChatError(
      'context_limit',
      `Conversation context exceeds the safe limit (${policy.hardLimitTokens} tokens).`,
      { estimated_tokens: compaction.afterTokens, context_window_tokens: policy.contextWindowTokens },
    ));
  }
  const workingMessages = compaction.messages;
  const { messages: _compactedMessages, ...compactionReport } = compaction;
  let totalToolCalls = 0;

  for (let round = 1; round <= maxRounds; round += 1) {
    if (signal?.aborted) throw new AgentChatError('aborted', 'Request aborted.');
    const requestMessages = [
      { role: 'system', content: String(systemPrompt).trim() },
      ...providerMessages(workingMessages),
    ];
    const roundEstimate = estimateChatRequestTokens({
      systemPrompt: String(systemPrompt).trim(),
      toolDefinitions,
      messages: providerMessages(workingMessages),
    });
    if (roundEstimate >= policy.hardLimitTokens) {
      throw recoverableError(new AgentChatError(
        'context_limit',
        `Current agent turn exceeds the safe context limit (${policy.hardLimitTokens} tokens).`,
        { estimated_tokens: roundEstimate, context_window_tokens: policy.contextWindowTokens },
      ));
    }
    const body = {
      model: String(model).trim(),
      messages: requestMessages,
      tools: toolDefinitions,
      tool_choice: 'auto',
      ...(reasoningEffort ? { reasoning_effort: reasoningEffort } : {}),
      ...(Number.isFinite(temperature) ? { temperature: Number(temperature) } : {}),
      ...(Number.isFinite(topP) ? { top_p: Number(topP) } : {}),
      stream: true,
      stream_options: { include_usage: true },
    };
    let payload;
    try {
      payload = await requestCompletion({
        endpoint,
        apiKey,
        body,
        fetchImpl,
        signal,
        onEvent,
        round,
      });
    } catch (error) {
      throw recoverableError(error);
    }
    aggregateUsage(usage, payload?.usage, round);
    const choice = payload?.choices?.[0];
    const normalizedAssistant = normalizeAssistantMessage(choice?.message, toolDefinitions, round);
    const assistant = normalizedAssistant.assistant;
    if (normalizedAssistant.recovery) {
      emit(onEvent, 'response_repair', { round, ...normalizedAssistant.recovery });
    }
    workingMessages.push(assistant);

    const calls = Array.isArray(assistant.tool_calls) ? assistant.tool_calls : [];
    if (!calls.length) {
      const rawContent = typeof assistant.content === 'string' ? assistant.content.trim() : '';
      const allSources = [...sources.values()];
      const content = sanitizeAssistantContent(rawContent, allSources);
      if (!content) throw recoverableError(new AgentChatError('empty_response', 'Provider returned an empty answer.'));
      assistant.content = content;
      emit(onEvent, 'complete', { rounds: round, tool_calls: totalToolCalls, usage });
      return {
        content,
        messages: workingMessages,
        sources: allSources,
        sourceRegistry: allSources,
        contextState: {
          epoch: (Number(contextState?.epoch) || 0) + (compaction.changed ? 1 : 0),
          lastPromptTokens: latestPromptTokens(usage, compaction.afterTokens),
          lastCompaction: compaction.changed
            ? {
              strategy: compaction.strategy,
              beforeTokens: compaction.beforeTokens,
              afterTokens: compaction.afterTokens,
            }
            : contextState?.lastCompaction || null,
        },
        compaction: compactionReport,
        usage,
        rounds: round,
        toolCalls: totalToolCalls,
      };
    }

    totalToolCalls += calls.length;
    if (totalToolCalls > maxToolCalls) {
      throw recoverableError(new AgentChatError('tool_limit', `Tool call limit exceeded (${maxToolCalls}).`));
    }

    const results = await Promise.all(calls.map((call) => executeOneTool(call, executeTool, onEvent, signal, toolCache)));
    calls.forEach((call, index) => {
      const result = results[index];
      collectSources(result, sources, sourceIds);
      workingMessages.push({
        role: 'tool',
        tool_call_id: String(call?.id || `tool-${round}-${index}`),
        content: JSON.stringify(toolResultForModel(result, sourceIds)),
      });
    });
  }

  throw recoverableError(new AgentChatError('round_limit', `Agent round limit exceeded (${maxRounds}).`));
}
