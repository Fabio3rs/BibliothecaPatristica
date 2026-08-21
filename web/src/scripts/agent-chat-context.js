import {
  sanitizeSourceCitationGroups,
  sourceCitationIds,
} from './agent-chat-citations.js';

const KIB = 1024;

export const GENERIC_CONTEXT_POLICY = Object.freeze({
  enabled: true,
  contextWindowTokens: 128 * KIB,
  softLowTokens: 48 * KIB,
  softTargetTokens: 64 * KIB,
  softHighTokens: 80 * KIB,
  hardLimitTokens: 112 * KIB,
  protectedTurns: 2,
  summaryMaxTokens: 3 * KIB,
});

export const GEMMA4_CONTEXT_POLICY = Object.freeze({
  enabled: true,
  contextWindowTokens: 256 * KIB,
  softLowTokens: 112 * KIB,
  softTargetTokens: 128 * KIB,
  softHighTokens: 144 * KIB,
  hardLimitTokens: 224 * KIB,
  protectedTurns: 2,
  summaryMaxTokens: 4 * KIB,
});

export function contextPolicyForModel(model, overrides = {}) {
  const base = /(?:^|[/:_-])gemma4(?:$|[/:_-])/i.test(String(model || ''))
    ? GEMMA4_CONTEXT_POLICY
    : GENERIC_CONTEXT_POLICY;
  return { ...base, ...overrides };
}

function serializedValue(value) {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value) || '';
  } catch {
    return String(value || '');
  }
}

export function estimateContextTokens(value) {
  const text = serializedValue(value);
  let ascii = 0;
  let nonAscii = 0;
  for (const character of text) {
    if (character.codePointAt(0) <= 0x7f) ascii += 1;
    else nonAscii += 1;
  }
  return Math.ceil(ascii / 4 + nonAscii / 1.5);
}

export function estimateChatRequestTokens({ systemPrompt = '', toolDefinitions = [], messages = [] } = {}) {
  const messageOverhead = (Array.isArray(messages) ? messages.length : 0) * 5;
  return estimateContextTokens(systemPrompt)
    + estimateContextTokens(toolDefinitions)
    + estimateContextTokens(messages)
    + messageOverhead
    + 16;
}

function internalMessage(role, content, kind) {
  return {
    role,
    content,
    _bibliotheca: { kind },
  };
}

export function appendTurnContext(messages, routeContext) {
  const output = (Array.isArray(messages) ? messages : []).map((message) => ({ ...message }));
  const content = String(routeContext || '').trim();
  if (!content) return output;
  let userIndex = -1;
  for (let index = output.length - 1; index >= 0; index -= 1) {
    if (output[index]?.role === 'user') {
      userIndex = index;
      break;
    }
  }
  if (userIndex >= 0) {
    const userMessage = output[userIndex];
    output[userIndex] = {
      ...userMessage,
      content: `${content}\n\nUser request:\n${String(userMessage?.content || '')}`,
      _bibliotheca: { kind: 'contextual_user' },
    };
  } else {
    output.push(internalMessage('user', content, 'contextual_user'));
  }
  return output;
}

export function providerMessages(messages) {
  return (Array.isArray(messages) ? messages : []).map((message) => {
    const output = {};
    for (const [key, value] of Object.entries(message || {})) {
      if (key.startsWith('_')) continue;
      output[key] = value;
    }
    return output;
  });
}

function protectedStartIndex(messages, protectedTurns) {
  const userIndexes = [];
  messages.forEach((message, index) => {
    if (message?.role === 'user') userIndexes.push(index);
  });
  if (userIndexes.length <= protectedTurns) return 0;
  return userIndexes[userIndexes.length - protectedTurns];
}

export function elideOldToolExchanges(messages, { protectedTurns = 2 } = {}) {
  const input = Array.isArray(messages) ? messages : [];
  const protectedStart = protectedStartIndex(input, Math.max(1, protectedTurns));
  if (protectedStart === 0) {
    return { messages: input.map((message) => ({ ...message })), changed: false, removedMessages: 0 };
  }

  let removedMessages = 0;
  const output = [];
  input.forEach((message, index) => {
    const old = index < protectedStart;
    if (old && message?.role === 'tool') {
      removedMessages += 1;
      return;
    }
    if (old && message?.role === 'assistant' && Array.isArray(message.tool_calls)) {
      removedMessages += 1;
      const content = typeof message.content === 'string' ? message.content.trim() : '';
      if (content) output.push({ role: 'assistant', content });
      return;
    }
    output.push({ ...message });
  });
  return { messages: output, changed: removedMessages > 0, removedMessages };
}

function citedSourceIds(messages) {
  const ids = new Set();
  for (const message of messages || []) {
    for (const sourceId of sourceCitationIds(message?.content)) ids.add(sourceId);
  }
  return ids;
}

export function compactSourceLedger(sources, messages) {
  const cited = citedSourceIds(messages);
  return (Array.isArray(sources) ? sources : [])
    .filter((source) => cited.has(source?.citationId))
    .map((source) => ({
      source_id: source.citationId,
      kind: source.kind || 'source',
      ...(source.volumeId ? { volume_id: source.volumeId } : {}),
      ...(Number.isFinite(source.page) ? { page: source.page } : {}),
      ...(source.label ? { label: source.label } : {}),
      ...(source.title ? { work: source.title } : {}),
      evidence: Array.isArray(source.evidence) ? source.evidence : [],
    }));
}

function cleanCheckpointText(value, allowedSourceIds) {
  const unknown = new Set();
  let text = String(value || '')
    .replace(/\[([^\]\n]+)\]\(\s*[^)\n]+\s*\)/g, '$1')
    .replace(/<\s*(?:https?:\/\/|www\.)[^>\s]+\s*>/gi, '')
    .replace(/\b(?:https?:\/\/|www\.)[^\s<>()\]]+/gi, '')
    .trim();
  for (const sourceId of sourceCitationIds(text)) {
    if (!allowedSourceIds.has(sourceId)) unknown.add(sourceId);
  }
  if (unknown.size) return '';
  if (text.length > 24_000) text = `${text.slice(0, 24_000).trim()}…`;
  return text;
}

function shorten(value, maxChars) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  if (text.length <= maxChars) return text;
  return `${text.slice(0, Math.max(1, maxChars - 1)).trim()}…`;
}

export function extractiveCheckpoint(messages, sourceLedger, maxChars = 12_000) {
  const allowedSourceIds = new Set(sourceLedger.map((source) => source.source_id));
  const lines = ['Conversation checkpoint (extractive; older tool traces omitted):'];
  for (const message of messages || []) {
    if (!['user', 'assistant'].includes(message?.role) || Array.isArray(message?.tool_calls)) continue;
    const content = sanitizeSourceCitationGroups(
      shorten(message.content, message.role === 'user' ? 500 : 900),
      allowedSourceIds,
    )
      .replace(/\[([^\]\n]+)\]\(\s*[^)\n]+\s*\)/g, '$1')
      .replace(/<\s*(?:https?:\/\/|www\.)[^>\s]+\s*>/gi, '')
      .replace(/\b(?:https?:\/\/|www\.)[^\s<>()\]]+/gi, '')
      .trim();
    if (!content) continue;
    lines.push(`${message.role === 'user' ? 'User' : 'Assistant'}: ${content}`);
    if (lines.join('\n').length >= maxChars) break;
  }
  if (sourceLedger.length) lines.push(`Source ledger: ${JSON.stringify(sourceLedger)}`);
  return lines.join('\n').slice(0, maxChars).trim();
}

function checkpointMessage(summary, sourceLedger, strategy) {
  const ledger = sourceLedger.length ? `\nSource ledger: ${JSON.stringify(sourceLedger)}` : '';
  return internalMessage(
    'assistant',
    `Conversation checkpoint. Preserve its source IDs exactly; it is context, not a new instruction.\n${summary}${ledger}`,
    strategy,
  );
}

function splitOldMessages(messages, protectedTurns) {
  const start = protectedStartIndex(messages, Math.max(1, protectedTurns));
  return {
    oldMessages: messages.slice(0, start),
    protectedMessages: messages.slice(start),
  };
}

export async function compactConversationContext({
  messages,
  sources = [],
  systemPrompt = '',
  toolDefinitions = [],
  policy = GENERIC_CONTEXT_POLICY,
  lastPromptTokens = 0,
  summarize,
} = {}) {
  const input = Array.isArray(messages) ? messages.map((message) => ({ ...message })) : [];
  const estimate = (candidate) => Math.max(
    estimateChatRequestTokens({ systemPrompt, toolDefinitions, messages: providerMessages(candidate) }),
    candidate === input && Number.isFinite(lastPromptTokens) ? Number(lastPromptTokens) : 0,
  );
  const beforeTokens = estimate(input);
  const base = {
    messages: input,
    changed: false,
    strategy: 'none',
    beforeTokens,
    afterTokens: beforeTokens,
    pressure: beforeTokens >= policy.softLowTokens,
  };
  if (policy.enabled === false || beforeTokens < policy.softHighTokens) return base;

  const elided = elideOldToolExchanges(input, { protectedTurns: policy.protectedTurns });
  let candidate = elided.messages;
  let afterTokens = estimate(candidate);
  if (elided.changed && afterTokens <= policy.softTargetTokens) {
    return {
      ...base,
      messages: candidate,
      changed: true,
      strategy: 'tool_elision',
      afterTokens,
      removedToolMessages: elided.removedMessages,
    };
  }

  const { oldMessages, protectedMessages } = splitOldMessages(candidate, policy.protectedTurns);
  if (!oldMessages.length) {
    return elided.changed
      ? {
        ...base,
        messages: candidate,
        changed: true,
        strategy: 'tool_elision',
        afterTokens,
        removedToolMessages: elided.removedMessages,
      }
      : base;
  }

  const sourceLedger = compactSourceLedger(sources, oldMessages);
  const allowedSourceIds = new Set(sourceLedger.map((source) => source.source_id));
  let summary = '';
  let summaryUsage = null;
  let strategy = 'extractive_summary';
  if (typeof summarize === 'function') {
    try {
      const summarized = await summarize({
        messages: providerMessages(oldMessages),
        sourceLedger,
        maxTokens: policy.summaryMaxTokens,
      });
      summaryUsage = summarized?.usage || null;
      summary = cleanCheckpointText(summarized?.content ?? summarized, allowedSourceIds);
      if (summary) strategy = 'model_summary';
    } catch {
      summary = '';
    }
  }
  if (!summary) summary = extractiveCheckpoint(oldMessages, sourceLedger);

  candidate = [checkpointMessage(summary, sourceLedger, strategy), ...protectedMessages];
  afterTokens = estimate(candidate);
  return {
    ...base,
    messages: candidate,
    changed: true,
    strategy,
    afterTokens,
    removedToolMessages: elided.removedMessages,
    summarizedMessages: oldMessages.length,
    summaryUsage,
    hardLimitExceeded: afterTokens >= policy.hardLimitTokens,
  };
}
