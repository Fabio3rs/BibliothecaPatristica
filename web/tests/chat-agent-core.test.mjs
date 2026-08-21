import test from 'node:test';
import assert from 'node:assert/strict';

import {
  AgentChatError,
  filterIndexHitsByScope,
  normalizeChatEndpoint,
  runChatCompletionLoop,
  sanitizeAssistantContent,
  sanitizeDebugPayload,
  selectCitedSources,
} from '../src/scripts/agent-chat-core.js';
import {
  GEMMA4_CONTEXT_POLICY,
  appendTurnContext,
  compactConversationContext,
  contextPolicyForModel,
  elideOldToolExchanges,
  extractiveCheckpoint,
  providerMessages,
} from '../src/scripts/agent-chat-context.js';
import { containsBooleanSyntax, parseQueryDsl } from '../public/indexador/runtime/query-dsl.mjs';

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function completion(message, usage = undefined) {
  return { choices: [{ message }], ...(usage ? { usage } : {}) };
}

function streamResponse(events) {
  const encoder = new TextEncoder();
  const body = new ReadableStream({
    start(controller) {
      for (const event of events) {
        controller.enqueue(encoder.encode(event === '[DONE]'
          ? 'data: [DONE]\n\n'
          : `data: ${JSON.stringify(event)}\n\n`));
      }
      controller.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
}

test('normalizes base and full Chat Completions endpoints', () => {
  assert.equal(
    normalizeChatEndpoint('http://127.0.0.1:11434/v1/'),
    'http://127.0.0.1:11434/v1/chat/completions',
  );
  assert.equal(
    normalizeChatEndpoint('https://provider.example/api/chat/completions'),
    'https://provider.example/api/chat/completions',
  );
  assert.throws(() => normalizeChatEndpoint('file:///tmp/model'), (error) => error.code === 'invalid_url');
});

test('documents the Indexador micro-DSL semantics used by search tools', () => {
  assert.deepEqual(parseQueryDsl('Clemente Romano'), {
    type: 'or',
    left: { type: 'term', value: 'Clemente' },
    right: { type: 'term', value: 'Romano' },
  });
  assert.deepEqual(parseQueryDsl('(Clemente || Clemens) && Corinthios'), {
    type: 'and',
    left: {
      type: 'or',
      left: { type: 'term', value: 'Clemente' },
      right: { type: 'term', value: 'Clemens' },
    },
    right: { type: 'term', value: 'Corinthios' },
  });
  assert.equal(containsBooleanSyntax('Clemente AND Corinthios'), false);
  assert.equal(containsBooleanSyntax('Clemente && Corinthios'), true);
});

test('redacts credentials and truncates oversized debug payloads', () => {
  assert.deepEqual(
    sanitizeDebugPayload({ apiKey: 'top-secret', nested: { authorization: 'Bearer secret', safe: true } }),
    { apiKey: '[REDACTED]', nested: { authorization: '[REDACTED]', safe: true } },
  );
  const truncated = sanitizeDebugPayload({ content: 'x'.repeat(200) }, 40);
  assert.equal(truncated.truncated, true);
  assert.ok(truncated.original_chars > 40);
  assert.equal(truncated.preview.length, 40);
});

test('shows only cited sources when the final answer contains valid source IDs', () => {
  const sources = [
    { citationId: 's1', label: 'candidate' },
    { citationId: 's2', label: 'verified page' },
    { citationId: 's3', label: 'unrelated candidate' },
    { citationId: 's4', label: 'catalog item', provenance: ['list_volumes'], evidence: ['catalog'] },
  ];
  assert.deepEqual(selectCitedSources('Verified in the transcription. [s2]', sources), [sources[1]]);
  assert.deepEqual(selectCitedSources('Compared both passages. [s1, s2]', sources), [sources[0], sources[1]]);
  assert.deepEqual(selectCitedSources('Spacing and separators. [ S1 ; s3 ]', sources), [sources[0], sources[2]]);
  assert.deepEqual(selectCitedSources('No citations were produced.', sources), []);
  assert.deepEqual(selectCitedSources('Unknown source [s99].', sources), []);
});

test('removes model-authored URLs without attaching an uncited source', () => {
  const sources = [{
    citationId: 's1',
    volumeId: 'PG033',
    page: 697,
    evidence: ['metadata'],
    representations: { viewer: { url: '/viewer?doc=PG033&page=697' } },
  }];
  const sanitized = sanitizeAssistantContent(
    'Viewer: [PG033 p.697](https://invented.example/viewer/PG033/697)\nOCR: https://invented.example/ocr/PG033/697',
    sources,
  );
  assert.equal(sanitized.includes('invented.example'), false);
  assert.equal(sanitized.includes('PG033 p.697'), true);
  assert.equal(sanitized.includes('[s1]'), false);
});

test('removes citations that do not exist in the runtime source registry', () => {
  const sanitized = sanitizeAssistantContent(
    'Conhecida [s1]. Inventada [s99].',
    [{ citationId: 's1', evidence: ['ocr_read'] }],
  );
  assert.equal(sanitized, 'Conhecida [s1]. Inventada.');
});

test('normalizes grouped citations and removes unavailable IDs from each group', () => {
  const sanitized = sanitizeAssistantContent(
    'Comparação [ S1,s2, s99 ]; repetida [s2, s2].',
    [
      { citationId: 's1', evidence: ['ocr_read'] },
      { citationId: 's2', evidence: ['metadata'] },
    ],
  );
  assert.equal(sanitized, 'Comparação [s1, s2]; repetida [s2].');
});

test('keeps an existing valid source citation without adding a duplicate footer', () => {
  const sources = [{ citationId: 's1', evidence: ['ocr_read'] }];
  assert.equal(
    sanitizeAssistantContent('Verificado no OCR. [s1]', sources),
    'Verificado no OCR. [s1]',
  );
});

test('filters index hits by URL metadata instead of adding short collection terms to the query', () => {
  const hits = [
    { url: '/indices?volume=PG001&work=w1' },
    { url: '/indices?volume=PL001&work=w2' },
    { url: '/indices?volume=PG002&work=w3' },
  ];
  assert.deepEqual(filterIndexHitsByScope(hits, { collection: 'PG' }), [hits[0], hits[2]]);
  assert.deepEqual(filterIndexHitsByScope(hits, { volumeId: 'PG001' }), [hits[0]]);
});

test('returns a direct assistant answer without executing tools', async () => {
  const events = [];
  let calls = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'mock-model',
    systemPrompt: 'Answer carefully.',
    conversationMessages: [{ role: 'user', content: 'Hello' }],
    toolDefinitions: [],
    executeTool: async () => { calls += 1; },
    fetchImpl: async (_url, request) => {
      const body = JSON.parse(request.body);
      assert.equal(body.stream, true);
      assert.deepEqual(body.stream_options, { include_usage: true });
      assert.equal(body.tool_choice, 'auto');
      return jsonResponse(completion({ role: 'assistant', content: 'Hello from the mock.' }, { total_tokens: 7 }));
    },
    onEvent: (event) => events.push(event),
  });

  assert.equal(result.content, 'Hello from the mock.');
  assert.equal(result.rounds, 1);
  assert.equal(result.toolCalls, 0);
  assert.equal(calls, 0);
  assert.deepEqual(events.map((event) => event.type), ['request', 'response', 'complete']);
});

test('streams text and reassembles fragmented OpenAI tool-call deltas', async () => {
  const events = [];
  let requests = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'streaming-model',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'Find PG001.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'find_page', parameters: { type: 'object' } } }],
    executeTool: async (name, args) => ({ ok: true, data: { name, args } }),
    fetchImpl: async () => {
      requests += 1;
      if (requests === 1) {
        return streamResponse([
          { choices: [{ delta: { role: 'assistant', tool_calls: [{ index: 0, id: 'call-stream', type: 'function', function: { name: 'find_', arguments: '{"volume":' } }] } }] },
          { choices: [{ delta: { tool_calls: [{ index: 0, function: { name: 'page', arguments: '"PG001"}' } }] }, finish_reason: 'tool_calls' }] },
          { choices: [], usage: { prompt_tokens: 10, completion_tokens: 4, total_tokens: 14 } },
          '[DONE]',
        ]);
      }
      return streamResponse([
        { choices: [{ delta: { role: 'assistant', content: 'Encontrei ' } }] },
        { choices: [{ delta: { content: 'PG001.' }, finish_reason: 'stop' }] },
        { choices: [], usage: { prompt_tokens: 20, completion_tokens: 3, total_tokens: 23 } },
        '[DONE]',
      ]);
    },
    onEvent: (event) => events.push(event),
  });

  assert.equal(result.content, 'Encontrei PG001.');
  assert.equal(result.toolCalls, 1);
  assert.deepEqual(result.usage.total, { prompt_tokens: 30, completion_tokens: 7, total_tokens: 37 });
  assert.equal(events.filter((event) => event.type === 'model_delta').map((event) => event.payload.content).join(''), 'Encontrei PG001.');
});

test('preserves provider reasoning only inside an explicit tool-call sequence', async () => {
  const requestBodies = [];
  let requests = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'thinking-model',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'Inspect.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'inspect', parameters: { type: 'object' } } }],
    executeTool: async () => ({ ok: true, data: { inspected: true } }),
    reasoningEffort: 'low',
    fetchImpl: async (_url, request) => {
      const body = JSON.parse(request.body);
      requestBodies.push(body);
      requests += 1;
      if (requests === 1) {
        assert.equal(body.reasoning_effort, 'low');
        return jsonResponse(completion({
          role: 'assistant',
          content: null,
          reasoning: 'I should inspect the corpus.',
          tool_calls: [{ id: 'thinking-call', type: 'function', function: { name: 'inspect', arguments: '{}' } }],
        }));
      }
      assert.equal(body.messages.at(-2).reasoning, 'I should inspect the corpus.');
      return jsonResponse(completion({ role: 'assistant', content: 'Inspection complete.', reasoning: 'Do not persist this.' }));
    },
  });

  assert.equal(result.content, 'Inspection complete.');
  assert.equal(result.messages.at(-1).reasoning, undefined);
  assert.equal(requestBodies.length, 2);
});

test('executes a tool call, returns a URL-free result to the model, and collects structured sources', async () => {
  const requestBodies = [];
  const executed = [];
  const fetchImpl = async (_url, request) => {
    const body = JSON.parse(request.body);
    requestBodies.push(body);
    if (requestBodies.length === 1) {
      return jsonResponse(completion({
        role: 'assistant',
        content: null,
        tool_calls: [{
          id: 'call-volumes',
          type: 'function',
          function: { name: 'list_volumes', arguments: '{"collection":"PG","limit":2}' },
        }],
      }));
    }
    const toolMessage = body.messages.at(-1);
    assert.equal(toolMessage.role, 'tool');
    assert.equal(toolMessage.tool_call_id, 'call-volumes');
    const modelResult = JSON.parse(toolMessage.content);
    assert.equal(modelResult.ok, true);
    assert.equal(modelResult.data.items[0].source_id, 's1');
    assert.equal(JSON.stringify(modelResult).includes('/viewer'), false);
    return jsonResponse(completion(
      { role: 'assistant', content: 'Found PG001 and PG002. [s1]' },
      { prompt_tokens: 9, completion_tokens: 4, total_tokens: 13 },
    ));
  };

  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1/chat/completions',
    apiKey: 'not-visible-in-events',
    model: 'mock-model',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'List two PG volumes.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'list_volumes', parameters: { type: 'object' } } }],
    executeTool: async (name, args) => {
      executed.push({ name, args });
      return {
        ok: true,
        data: { items: [{ id: 'PG001', source_key: 'page:PG001:1', viewer_url: '/viewer?doc=PG001&page=1' }] },
        sources: [{
          id: 'page:PG001:1',
          kind: 'page',
          volumeId: 'PG001',
          page: 1,
          representations: { viewer: { url: '/viewer?doc=PG001&page=1' } },
          provenance: ['list_volumes'],
          evidence: ['catalog'],
        }],
      };
    },
    fetchImpl,
  });

  assert.deepEqual(executed, [{ name: 'list_volumes', args: { collection: 'PG', limit: 2 } }]);
  assert.equal(requestBodies.length, 2);
  assert.equal(result.content, 'Found PG001 and PG002.');
  assert.equal(result.toolCalls, 1);
  assert.equal(result.sources.length, 1);
  assert.equal(result.sources[0].citationId, 's1');
  assert.equal(result.sources[0].representations.viewer.url, '/viewer?doc=PG001&page=1');
  assert.deepEqual(result.usage.total, { prompt_tokens: 9, completion_tokens: 4, total_tokens: 13 });
});

test('turns invalid tool arguments into a tool error the provider can recover from', async () => {
  let requests = 0;
  let executions = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'mock-model',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'Try a tool.' }],
    toolDefinitions: [],
    executeTool: async () => { executions += 1; },
    fetchImpl: async (_url, request) => {
      requests += 1;
      if (requests === 1) {
        return jsonResponse(completion({
          role: 'assistant',
          content: null,
          tool_calls: [{ id: 'bad-call', type: 'function', function: { name: 'broken', arguments: '{bad json' } }],
        }));
      }
      const toolResult = JSON.parse(JSON.parse(request.body).messages.at(-1).content);
      assert.equal(toolResult.ok, false);
      assert.equal(toolResult.error.code, 'invalid_tool_arguments');
      return jsonResponse(completion({ role: 'assistant', content: 'I could not use that tool.' }));
    },
  });

  assert.equal(executions, 0);
  assert.equal(result.content, 'I could not use that tool.');
});

test('reuses an identical tool call result inside one agent turn', async () => {
  let requests = 0;
  let executions = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'mock-model',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'Read twice.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'read', parameters: { type: 'object' } } }],
    executeTool: async () => {
      executions += 1;
      return { ok: true, data: { value: 1 } };
    },
    fetchImpl: async (_url, options) => {
      requests += 1;
      if (requests <= 2) {
        if (requests === 2) {
          const toolPayload = JSON.parse(JSON.parse(options.body).messages.at(-1).content);
          assert.equal(toolPayload.reused, undefined);
        }
        return jsonResponse(completion({
          role: 'assistant',
          content: null,
          tool_calls: [{ id: `call-${requests}`, type: 'function', function: { name: 'read', arguments: '{"page":1,"volume":"PG001"}' } }],
        }));
      }
      const toolPayload = JSON.parse(JSON.parse(options.body).messages.at(-1).content);
      assert.equal(toolPayload.reused, true);
      return jsonResponse(completion({ role: 'assistant', content: 'Done.' }));
    },
  });

  assert.equal(result.content, 'Done.');
  assert.equal(executions, 1);
});

test('recovers a Qwen/Ollama XML tool call emitted inside reasoning', async () => {
  const events = [];
  const executions = [];
  let requests = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'qwen-compatible',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'Inspect PG001.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'get_volume_index', parameters: { type: 'object' } } }],
    executeTool: async (name, args) => {
      executions.push({ name, args });
      return {
        ok: true,
        data: { volume: { volume_id: 'PG001' }, source_key: 'index:PG001:overview', indices_url: '/indices?volume=PG001' },
        sources: [{
          id: 'index:PG001:overview',
          kind: 'index',
          label: 'PG001 index',
          representations: { index: { url: '/indices?volume=PG001' } },
          provenance: ['get_volume_index'],
          evidence: ['index'],
        }],
      };
    },
    fetchImpl: async (_url, request) => {
      requests += 1;
      if (requests === 1) {
        return jsonResponse(completion({
          role: 'assistant',
          content: '',
          reasoning: '<tool_call>\n<function=get_volume_index>\n<parameter=volume_id>\nPG001\n</parameter>\n<parameter=limit>\n20\n</parameter>\n</function>\n</tool_call>',
        }));
      }
      const messages = JSON.parse(request.body).messages;
      const assistant = messages.at(-2);
      const tool = messages.at(-1);
      assert.equal(assistant.role, 'assistant');
      assert.equal(assistant.content, null);
      assert.equal(assistant.tool_calls[0].function.name, 'get_volume_index');
      assert.equal(tool.role, 'tool');
      const toolPayload = JSON.parse(tool.content);
      assert.equal(toolPayload.sources, undefined);
      assert.equal(toolPayload.data.indices_url, undefined);
      assert.equal(toolPayload.data.source_id, 's1');
      return jsonResponse(completion({ role: 'assistant', content: 'PG001 index recovered.' }));
    },
    onEvent: (event) => events.push(event),
  });

  assert.deepEqual(executions, [{ name: 'get_volume_index', args: { volume_id: 'PG001', limit: 20 } }]);
  assert.equal(result.content, 'PG001 index recovered.');
  assert.equal(result.sources[0].citationId, 's1');
  assert.equal(result.sources[0].representations.index.url, '/indices?volume=PG001');
  assert.equal(events.find((event) => event.type === 'response_repair')?.payload.source, 'reasoning');
});

test('normalizes legacy function_call and object arguments', async () => {
  let requests = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'legacy-compatible',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'List volumes.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'list_volumes', parameters: { type: 'object' } } }],
    executeTool: async (name, args) => ({ ok: true, data: { name, args } }),
    fetchImpl: async () => {
      requests += 1;
      return requests === 1
        ? jsonResponse(completion({ role: 'assistant', content: null, function_call: { name: 'list_volumes', arguments: { collection: 'PG', limit: 2 } } }))
        : jsonResponse(completion({ role: 'assistant', content: 'Legacy call worked.' }));
    },
  });
  assert.equal(result.content, 'Legacy call worked.');
  assert.equal(result.toolCalls, 1);
});

test('recovers JSON tool-call envelopes emitted inside content', async () => {
  let requests = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'tagged-json-compatible',
    systemPrompt: 'Use tools.',
    conversationMessages: [{ role: 'user', content: 'List volumes.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'list_volumes', parameters: { type: 'object' } } }],
    executeTool: async (_name, args) => ({ ok: true, data: args }),
    fetchImpl: async (_url, request) => {
      requests += 1;
      if (requests === 1) {
        return jsonResponse(completion({
          role: 'assistant',
          content: 'Vou consultar.\n<tool_call>{"name":"list_volumes","arguments":{"collection":"PL","limit":3}}</tool_call>',
        }));
      }
      const assistant = JSON.parse(request.body).messages.at(-2);
      assert.equal(assistant.content, 'Vou consultar.');
      assert.equal(assistant.tool_calls[0].function.arguments, '{"collection":"PL","limit":3}');
      return jsonResponse(completion({ role: 'assistant', content: 'JSON envelope worked.' }));
    },
  });
  assert.equal(result.content, 'JSON envelope worked.');
  assert.equal(result.toolCalls, 1);
});

test('enforces the tool-call safety limit', async () => {
  await assert.rejects(
    runChatCompletionLoop({
      apiUrl: 'http://provider.test/v1',
      model: 'mock-model',
      systemPrompt: 'Use tools.',
      conversationMessages: [{ role: 'user', content: 'Loop.' }],
      toolDefinitions: [],
      executeTool: async () => ({ ok: true }),
      maxToolCalls: 0,
      fetchImpl: async () => jsonResponse(completion({
        role: 'assistant',
        content: null,
        tool_calls: [{ id: 'call-loop', type: 'function', function: { name: 'loop', arguments: '{}' } }],
      })),
    }),
    (error) => error instanceof AgentChatError && error.code === 'tool_limit',
  );
});

test('merges repeated page sources and aggregates usage across every model round', async () => {
  let requests = 0;
  const result = await runChatCompletionLoop({
    apiUrl: 'http://provider.test/v1',
    model: 'mock-model',
    systemPrompt: 'Use tools and source IDs.',
    conversationMessages: [{ role: 'user', content: 'Inspect PG001 p.18.' }],
    toolDefinitions: [{ type: 'function', function: { name: 'inspect', parameters: { type: 'object' } } }],
    executeTool: async () => ({
      ok: true,
      data: { source_key: 'page:PG001:18', raw_ocr_url: 'https://example.invalid/ocr.txt' },
      sources: [
        {
          id: 'page:PG001:18',
          kind: 'page',
          volumeId: 'PG001',
          page: 18,
          representations: { viewer: { url: '/viewer?doc=PG001&page=18' } },
          provenance: ['search_corpus'],
          evidence: ['metadata'],
        },
        {
          id: 'page:PG001:18',
          kind: 'page',
          volumeId: 'PG001',
          page: 18,
          representations: { ocr: { url: 'https://example.invalid/ocr.txt' } },
          metadata: { ocrExcerpt: 'Clemens' },
          provenance: ['get_page_ocr'],
          evidence: ['ocr_read'],
        },
      ],
    }),
    fetchImpl: async (_url, request) => {
      requests += 1;
      if (requests === 1) {
        return jsonResponse(completion({
          role: 'assistant',
          content: null,
          tool_calls: [{ id: 'inspect-1', type: 'function', function: { name: 'inspect', arguments: '{}' } }],
        }, { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 }));
      }
      const modelResult = JSON.parse(JSON.parse(request.body).messages.at(-1).content);
      assert.equal(modelResult.data.source_id, 's1');
      assert.equal(JSON.stringify(modelResult).includes('example.invalid'), false);
      return jsonResponse(completion(
        { role: 'assistant', content: 'Confirmed in OCR. [s1]' },
        { prompt_tokens: 20, completion_tokens: 5, total_tokens: 25 },
      ));
    },
  });

  assert.equal(result.sources.length, 1);
  assert.deepEqual(result.sources[0].provenance, ['search_corpus', 'get_page_ocr']);
  assert.deepEqual(result.sources[0].evidence, ['metadata', 'ocr_read']);
  assert.equal(result.sources[0].representations.viewer.url, '/viewer?doc=PG001&page=18');
  assert.equal(result.sources[0].representations.ocr.url, 'https://example.invalid/ocr.txt');
  assert.deepEqual(result.usage.total, { prompt_tokens: 30, completion_tokens: 7, total_tokens: 37 });
  assert.equal(result.usage.rounds.length, 2);
});

test('uses a 256K Gemma 4 window with hysteresis around the 128K target', () => {
  const policy = contextPolicyForModel('gemma4:cloud');
  assert.deepEqual(policy, GEMMA4_CONTEXT_POLICY);
  assert.equal(policy.contextWindowTokens, 256 * 1024);
  assert.equal(policy.softLowTokens, 112 * 1024);
  assert.equal(policy.softTargetTokens, 128 * 1024);
  assert.equal(policy.softHighTokens, 144 * 1024);
  assert.equal(policy.hardLimitTokens, 224 * 1024);
});

test('keeps route context append-only for the turn and strips internal metadata for providers', () => {
  const messages = [
    { role: 'user', content: 'Earlier question' },
    { role: 'assistant', content: 'Earlier answer' },
    { role: 'user', content: 'What does this page say?' },
  ];
  const contextual = appendTurnContext(messages, 'kind=viewer\nvolume=PL016.03\npage=100');
  assert.deepEqual(contextual.slice(0, 2), messages.slice(0, 2));
  assert.equal(contextual.at(-1).role, 'user');
  assert.equal(contextual.at(-1)._bibliotheca.kind, 'contextual_user');
  assert.match(contextual.at(-1).content, /volume=PL016\.03/);
  assert.match(contextual.at(-1).content, /User request:\nWhat does this page say\?/);
  assert.equal(providerMessages(contextual).at(-1)._bibliotheca, undefined);
});

test('elides only old complete tool exchanges and preserves the newest turns verbatim', () => {
  const oldAssistant = {
    role: 'assistant',
    content: null,
    tool_calls: [{ id: 'old-call', type: 'function', function: { name: 'search', arguments: '{}' } }],
  };
  const messages = [
    { role: 'user', content: 'Old question' },
    oldAssistant,
    { role: 'tool', tool_call_id: 'old-call', content: '{"ok":true}' },
    { role: 'assistant', content: 'Old answer' },
    { role: 'user', content: 'Recent question' },
    { role: 'assistant', content: 'Recent answer' },
    { role: 'user', content: 'Current question' },
  ];
  const result = elideOldToolExchanges(messages, { protectedTurns: 2 });
  assert.equal(result.changed, true);
  assert.equal(result.removedMessages, 2);
  assert.equal(result.messages.some((message) => message.role === 'tool'), false);
  assert.deepEqual(result.messages.slice(-3), messages.slice(-3));
});

test('compacts once at the high watermark and replaces old history with a sourced checkpoint', async () => {
  let summaryCalls = 0;
  const messages = [
    { role: 'user', content: `Old research question ${'x'.repeat(900)}` },
    { role: 'assistant', content: `Old finding ${'y'.repeat(900)} [s1]` },
    { role: 'user', content: 'Current follow-up' },
  ];
  const result = await compactConversationContext({
    messages,
    sources: [{ id: 'page:PL016.03:100', citationId: 's1', kind: 'page', volumeId: 'PL016.03', page: 100 }],
    systemPrompt: 'Research.',
    policy: {
      enabled: true,
      softLowTokens: 100,
      softTargetTokens: 140,
      softHighTokens: 180,
      hardLimitTokens: 1_000,
      protectedTurns: 1,
      summaryMaxTokens: 80,
    },
    summarize: async ({ sourceLedger }) => {
      summaryCalls += 1;
      assert.equal(sourceLedger[0].source_id, 's1');
      return { content: 'The older turn established the relevant locator [s1].', usage: { total_tokens: 25 } };
    },
  });
  assert.equal(summaryCalls, 1);
  assert.equal(result.changed, true);
  assert.equal(result.strategy, 'model_summary');
  assert.equal(result.messages[0].role, 'assistant');
  assert.equal(result.messages[0]._bibliotheca.kind, 'model_summary');
  assert.match(result.messages[0].content, /\[s1\]/);
  assert.deepEqual(result.messages.slice(1), [{ role: 'user', content: 'Current follow-up' }]);
});

test('exposes a completed checkpoint for persistence when the main provider request fails', async () => {
  let requests = 0;
  await assert.rejects(
    runChatCompletionLoop({
      apiUrl: 'http://provider.test/v1',
      model: 'mock-model',
      systemPrompt: 'Research.',
      conversationMessages: [
        { role: 'user', content: `Old question ${'x'.repeat(1_000)}` },
        { role: 'assistant', content: `Old answer ${'y'.repeat(1_000)}` },
        { role: 'user', content: 'Current question' },
      ],
      toolDefinitions: [],
      executeTool: async () => ({ ok: true }),
      contextPolicy: {
        enabled: true,
        softLowTokens: 100,
        softTargetTokens: 140,
        softHighTokens: 180,
        hardLimitTokens: 1_000,
        protectedTurns: 1,
        summaryMaxTokens: 80,
      },
      fetchImpl: async () => {
        requests += 1;
        if (requests === 1) return jsonResponse(completion({ role: 'assistant', content: 'Old conclusion retained.' }, { total_tokens: 25 }));
        return jsonResponse({ error: { message: 'Provider unavailable.' } }, 503);
      },
    }),
    (error) => {
      assert.equal(error.code, 'provider');
      assert.equal(error.compactionRecovery.messages.length, 1);
      assert.match(error.compactionRecovery.messages[0].content, /Old conclusion retained/);
      assert.equal(error.compactionRecovery.contextState.epoch, 1);
      return true;
    },
  );
});

test('extractive checkpoints keep only registered source IDs and no raw URLs', () => {
  const checkpoint = extractiveCheckpoint(
    [{ role: 'assistant', content: 'Known [s1], unknown [s99], URL https://invented.example/page.' }],
    [{ source_id: 's1', kind: 'page' }],
  );
  assert.match(checkpoint, /\[s1\]/);
  assert.equal(checkpoint.includes('[s99]'), false);
  assert.equal(checkpoint.includes('invented.example'), false);
});

test('extractive checkpoints preserve valid grouped source IDs', () => {
  const checkpoint = extractiveCheckpoint(
    [{ role: 'assistant', content: 'Compared passages [s1, s2, s99].' }],
    [{ source_id: 's1', kind: 'page' }, { source_id: 's2', kind: 'page' }],
  );
  assert.match(checkpoint, /\[s1, s2\]/);
  assert.equal(checkpoint.includes('s99'), false);
});

test('debounces compaction inside the low/high watermark band', async () => {
  let summaryCalls = 0;
  const messages = [
    { role: 'user', content: 'Earlier' },
    { role: 'assistant', content: 'Answer' },
    { role: 'user', content: 'Current' },
  ];
  const result = await compactConversationContext({
    messages,
    lastPromptTokens: 150,
    policy: {
      enabled: true,
      softLowTokens: 100,
      softTargetTokens: 150,
      softHighTokens: 200,
      hardLimitTokens: 300,
      protectedTurns: 1,
      summaryMaxTokens: 20,
    },
    summarize: async () => { summaryCalls += 1; return 'unused'; },
  });
  assert.equal(result.pressure, true);
  assert.equal(result.changed, false);
  assert.equal(result.strategy, 'none');
  assert.equal(summaryCalls, 0);
  assert.deepEqual(result.messages, messages);
});

test('uses old tool elision before paying for a model checkpoint', async () => {
  let summaryCalls = 0;
  const messages = [
    { role: 'user', content: 'Old lookup' },
    {
      role: 'assistant',
      content: null,
      tool_calls: [{ id: 'large-call', type: 'function', function: { name: 'search', arguments: '{}' } }],
    },
    { role: 'tool', tool_call_id: 'large-call', content: JSON.stringify({ text: 'x'.repeat(2_000) }) },
    { role: 'assistant', content: 'Result retained in the answer.' },
    { role: 'user', content: 'Current lookup' },
  ];
  const result = await compactConversationContext({
    messages,
    policy: {
      enabled: true,
      softLowTokens: 100,
      softTargetTokens: 180,
      softHighTokens: 200,
      hardLimitTokens: 800,
      protectedTurns: 1,
      summaryMaxTokens: 20,
    },
    summarize: async () => { summaryCalls += 1; return 'unused'; },
  });
  assert.equal(result.strategy, 'tool_elision');
  assert.equal(result.removedToolMessages, 2);
  assert.equal(summaryCalls, 0);
  assert.equal(result.afterTokens <= 180, true);
});

test('keeps source IDs stable across user turns', async () => {
  const definitions = [{ type: 'function', function: { name: 'read', parameters: { type: 'object' } } }];

  async function runTurn({ messages, sourceRegistry = [], sourceKey, answer }) {
    let request = 0;
    return runChatCompletionLoop({
      apiUrl: 'http://provider.test/v1',
      model: 'mock-model',
      systemPrompt: 'Use the source registry.',
      conversationMessages: messages,
      sourceRegistry,
      toolDefinitions: definitions,
      executeTool: async () => ({
        ok: true,
        data: { source_key: sourceKey },
        sources: [{ id: sourceKey, kind: 'page', evidence: ['ocr_read'] }],
      }),
      fetchImpl: async (_url, options) => {
        request += 1;
        if (request === 1) {
          return jsonResponse(completion({
            role: 'assistant',
            content: null,
            tool_calls: [{ id: `call-${request}`, type: 'function', function: { name: 'read', arguments: '{}' } }],
          }));
        }
        const toolPayload = JSON.parse(JSON.parse(options.body).messages.at(-1).content);
        const expectedId = sourceRegistry.length ? 's2' : 's1';
        assert.equal(toolPayload.data.source_id, expectedId);
        return jsonResponse(completion({ role: 'assistant', content: `${answer} [${expectedId}]` }));
      },
    });
  }

  const first = await runTurn({
    messages: [{ role: 'user', content: 'Read the first page.' }],
    sourceKey: 'page:PG001:1',
    answer: 'First page',
  });
  const secondMessages = [...first.messages, { role: 'user', content: 'Now read another page.' }];
  const second = await runTurn({
    messages: secondMessages,
    sourceRegistry: first.sourceRegistry,
    sourceKey: 'page:PL001:2',
    answer: 'Second page',
  });

  assert.deepEqual(
    second.sourceRegistry.map(({ id, citationId }) => ({ id, citationId })),
    [
      { id: 'page:PG001:1', citationId: 's1' },
      { id: 'page:PL001:2', citationId: 's2' },
    ],
  );
});
