import http from 'node:http';

const port = Number(process.env.CHAT_MOCK_PORT || 4789);

function send(response, status, payload) {
  response.writeHead(status, {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Authorization, Content-Type',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Content-Type': 'application/json; charset=utf-8',
  });
  response.end(JSON.stringify(payload));
}

const server = http.createServer((request, response) => {
  if (request.method === 'OPTIONS') {
    send(response, 204, {});
    return;
  }
  if (request.method === 'GET' && request.url?.endsWith('/models')) {
    send(response, 200, { object: 'list', data: [{ id: 'mock-model', object: 'model' }] });
    return;
  }
  if (request.method !== 'POST' || !request.url?.endsWith('/chat/completions')) {
    send(response, 404, { error: { message: 'Not found' } });
    return;
  }

  let raw = '';
  request.setEncoding('utf8');
  request.on('data', (chunk) => {
    raw += chunk;
    if (raw.length > 1_000_000) request.destroy();
  });
  request.on('end', () => {
    let body;
    try {
      body = JSON.parse(raw);
    } catch {
      send(response, 400, { error: { message: 'Invalid JSON' } });
      return;
    }

    const messages = Array.isArray(body.messages) ? body.messages : [];
    const lastMessage = messages.at(-1);
    const lastUserIndex = messages.findLastIndex((message) => message.role === 'user');
    const hasToolResult = messages
      .slice(lastUserIndex + 1)
      .some((message) => message.role === 'tool');
    const isConnectionProbe = Array.isArray(body.tools)
      && body.tools.some((tool) => tool?.function?.name === 'echo_test');
    if (isConnectionProbe) {
      send(response, 200, {
        id: 'chatcmpl-local-echo',
        object: 'chat.completion',
        choices: [{
          index: 0,
          finish_reason: 'tool_calls',
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [{
              id: 'call_echo_test',
              type: 'function',
              function: { name: 'echo_test', arguments: '{"value":"ok"}' },
            }],
          },
        }],
        usage: { prompt_tokens: 12, completion_tokens: 4, total_tokens: 16 },
      });
      return;
    }
    const requestsBrokenFormat = messages.some((message) => message.role === 'user' && /formato quebrado/i.test(String(message.content || '')));
    const requestsOcr = messages.some((message) => message.role === 'user' && /OCR\s+PG001\s+p\.?\s*18/i.test(String(message.content || '')));
    const requestsScripture = messages.some((message) => message.role === 'user' && /João\s+3\s*[:,]\s*16/i.test(String(message.content || '')));
    if (!hasToolResult && requestsScripture) {
      send(response, 200, {
        id: 'chatcmpl-local-scripture-tool',
        object: 'chat.completion',
        choices: [{
          index: 0,
          finish_reason: 'tool_calls',
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [{
              id: 'call_search_scripture',
              type: 'function',
              function: {
                name: 'search_scripture',
                arguments: JSON.stringify({ reference: 'João 3,16', match_mode: 'exact', limit: 3, locations_per_reference: 4 }),
              },
            }],
          },
        }],
        usage: { prompt_tokens: 30, completion_tokens: 12, total_tokens: 42 },
      });
      return;
    }
    if (!hasToolResult && requestsOcr) {
      send(response, 200, {
        id: 'chatcmpl-local-ocr-tool',
        object: 'chat.completion',
        choices: [{
          index: 0,
          finish_reason: 'tool_calls',
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [{
              id: 'call_page_ocr',
              type: 'function',
              function: {
                name: 'get_page_ocr',
                arguments: JSON.stringify({ volume_id: 'PG001', page: 18, query: 'Clemens', max_chars: 1200 }),
              },
            }],
          },
        }],
        usage: { prompt_tokens: 28, completion_tokens: 11, total_tokens: 39 },
      });
      return;
    }
    if (!hasToolResult && requestsBrokenFormat) {
      send(response, 200, {
        id: 'chatcmpl-local-reasoning-tool',
        object: 'chat.completion',
        choices: [{
          index: 0,
          finish_reason: 'stop',
          message: {
            role: 'assistant',
            content: '',
            reasoning: '<tool_call>\n<function=get_volume_index>\n<parameter=volume_id>\nPG001\n</parameter>\n<parameter=limit>\n20\n</parameter>\n</function>\n</tool_call>',
          },
        }],
      });
      return;
    }
    if (!hasToolResult && /ferrament|tools?|volumes?/i.test(String(lastMessage?.content || ''))) {
      send(response, 200, {
        id: 'chatcmpl-local-tool',
        object: 'chat.completion',
        choices: [{
          index: 0,
          finish_reason: 'tool_calls',
          message: {
            role: 'assistant',
            content: null,
            tool_calls: [{
              id: 'call_list_volumes',
              type: 'function',
              function: {
                name: 'list_volumes',
                arguments: JSON.stringify({ collection: 'PG', limit: 2 }),
              },
            }],
          },
        }],
        usage: { prompt_tokens: 25, completion_tokens: 10, total_tokens: 35 },
      });
      return;
    }

    if (lastMessage?.role === 'tool') {
      let result = {};
      try { result = JSON.parse(lastMessage.content); } catch { /* mock fallback */ }
      const items = result?.data?.items || [];
      const labels = items.map((item) => item.id).filter(Boolean).join(' e ');
      const sourceIds = items.map((item) => item.source_id).filter(Boolean);
      const indexedVolume = result?.data?.volume?.volume_id;
      const exactPage = result?.data?.volume_id && result?.data?.page
        ? `${result.data.volume_id} p.${result.data.page}`
        : '';
      const exactSourceId = result?.data?.source_id;
      const scriptureItem = result?.data?.items?.[0];
      const scriptureLocations = scriptureItem?.locations || [];
      const scripturePages = scriptureLocations
        .map((item) => `${item.volume_id} p.${item.page}${item.source_id ? ` [${item.source_id}]` : ''}`)
        .join(', ');
      send(response, 200, {
        id: 'chatcmpl-local-final',
        object: 'chat.completion',
        choices: [{
          index: 0,
          finish_reason: 'stop',
          message: {
            role: 'assistant',
            content: scriptureItem
              ? `Encontrei **${scriptureItem.reference}** em ${scriptureItem.page_count} páginas associadas. Primeiras localizações: ${scripturePages}. O índice vem das keywords dos resumos; o OCR ainda não foi verificado.`
              : exactPage
              ? `### OCR verificado\n\n> “Oceanus intransmeabilis est hominibus.” ${exactSourceId ? `[${exactSourceId}]` : ''}\n\n| Página | Evidência |\n| --- | --- |\n| ${exactPage} | OCR lido |`
              : indexedVolume
              ? `Recuperei a chamada de ferramenta e abri o índice de ${indexedVolume}.`
              : labels
              ? `Encontrei ${labels}. ${sourceIds.map((id) => `[${id}]`).join(' ')}`
              : 'A ferramenta respondeu, mas não encontrei volumes.',
          },
        }],
        usage: { prompt_tokens: 45, completion_tokens: 18, total_tokens: 63 },
      });
      return;
    }

    send(response, 200, {
      id: 'chatcmpl-local-direct',
      object: 'chat.completion',
      choices: [{ index: 0, finish_reason: 'stop', message: { role: 'assistant', content: 'Resposta direta do provedor mock local.' } }],
      usage: { prompt_tokens: 15, completion_tokens: 8, total_tokens: 23 },
    });
  });
});

server.listen(port, '127.0.0.1', () => {
  process.stdout.write(`Mock Chat Completions ready at http://127.0.0.1:${port}/v1/chat/completions\n`);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => server.close(() => process.exit(0)));
}
