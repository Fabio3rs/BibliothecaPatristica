import {
  AgentChatError,
  normalizeAssistantMessage,
  normalizeChatEndpoint,
  runChatCompletionLoop,
  selectCitedSources,
} from './agent-chat-core.js';
import {
  parseSourceCitationGroup,
  SOURCE_CITATION_GROUP_PATTERN_SOURCE,
} from './agent-chat-citations.js';
import { createAgentTools } from './agent-chat-tools.js';

const STORAGE_KEY = 'bibliotheca:agent-chat:v2';
const LEGACY_STORAGE_KEY = 'bibliotheca:agent-chat:v1';
const PROMPT_VERSION = 6;

function parseJson(value, fallback = {}) {
  try {
    return JSON.parse(value || '');
  } catch {
    return fallback;
  }
}

function loadSettings({ defaultPrompt, locale }) {
  let stored = {};
  try {
    stored = parseJson(localStorage.getItem(STORAGE_KEY), {});
    if (!Object.keys(stored).length) {
      const legacy = parseJson(localStorage.getItem(LEGACY_STORAGE_KEY), {});
      stored = {
        apiUrl: legacy.apiUrl,
        model: legacy.model,
        debug: legacy.debug,
        promptOverrides: legacy.systemPrompt && legacy.systemPrompt !== defaultPrompt
          ? { [locale]: legacy.systemPrompt }
          : {},
      };
    }
  } catch {
    stored = {};
  }
  const storedPromptVersion = Number(stored.promptVersion || 0);
  const promptOverrides = storedPromptVersion === PROMPT_VERSION
    && stored.promptOverrides && typeof stored.promptOverrides === 'object'
    ? stored.promptOverrides
    : {};
  const promptOverride = typeof promptOverrides[locale] === 'string' && promptOverrides[locale].trim()
    ? promptOverrides[locale].trim()
    : '';
  return {
    apiUrl: typeof stored.apiUrl === 'string' ? stored.apiUrl : '',
    model: typeof stored.model === 'string' ? stored.model : '',
    systemPrompt: promptOverride || defaultPrompt,
    promptOverrides,
    debug: stored.debug !== false,
  };
}

function saveSettings(settings, config) {
  const promptOverrides = { ...(settings.promptOverrides || {}) };
  const normalizedPrompt = String(settings.systemPrompt || '').trim();
  if (!normalizedPrompt || normalizedPrompt === String(config.defaultPrompt || '').trim()) {
    delete promptOverrides[config.locale];
  } else {
    promptOverrides[config.locale] = normalizedPrompt;
  }
  localStorage.setItem(STORAGE_KEY, JSON.stringify({
    apiUrl: settings.apiUrl,
    model: settings.model,
    promptOverrides,
    promptVersion: PROMPT_VERSION,
    debug: settings.debug,
  }));
  try { localStorage.removeItem(LEGACY_STORAGE_KEY); } catch { /* optional migration cleanup */ }
  settings.promptOverrides = promptOverrides;
}

function appendInlineContent(container, value, sourceMap, strings, citationScope) {
  const text = String(value || '');
  const pattern = new RegExp(`(${SOURCE_CITATION_GROUP_PATTERN_SOURCE}|\\*\\*[^*\\n]+\\*\\*|\`[^\`\\n]+\`|\\*[^*\\n]+\\*)`, 'g');
  let lastIndex = 0;
  for (const match of text.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > lastIndex) container.appendChild(document.createTextNode(text.slice(lastIndex, index)));
    const token = match[0];
    const citations = parseSourceCitationGroup(token);
    if (citations.length) {
      for (const citation of citations) {
        const source = sourceMap.get(citation);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'agent-chat-citation';
        button.textContent = `[${citation.slice(1)}]`;
        button.setAttribute('aria-label', source
          ? strings.citationLabel.replace('{id}', citation.slice(1))
          : strings.invalidCitation.replace('{id}', citation));
        if (!source) {
          button.disabled = true;
          button.dataset.invalid = 'true';
        } else {
          button.addEventListener('click', () => {
            const card = citationScope?.querySelector(`[data-source-citation="${citation}"]`);
            card?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            card?.focus({ preventScroll: true });
          });
        }
        container.appendChild(button);
      }
    } else if (token.startsWith('**')) {
      const strong = document.createElement('strong');
      strong.textContent = token.slice(2, -2);
      container.appendChild(strong);
    } else if (token.startsWith('`')) {
      const code = document.createElement('code');
      code.textContent = token.slice(1, -1);
      container.appendChild(code);
    } else {
      const emphasis = document.createElement('em');
      emphasis.textContent = token.slice(1, -1);
      container.appendChild(emphasis);
    }
    lastIndex = index + token.length;
  }
  if (lastIndex < text.length) container.appendChild(document.createTextNode(text.slice(lastIndex)));
}

function markdownTableCells(line) {
  let value = String(line || '').trim();
  if (value.startsWith('|')) value = value.slice(1);
  if (value.endsWith('|')) value = value.slice(0, -1);
  return value.split('|').map((cell) => cell.trim());
}

function isMarkdownTableSeparator(line) {
  const cells = markdownTableCells(line);
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function appendSafeMarkdown(container, value, sources, strings, citationScope) {
  const sourceMap = new Map((sources || []).map((source) => [source.citationId, source]));
  const lines = String(value || '').replace(/\r\n?/g, '\n').split('\n');
  let list = null;
  let listKind = '';
  let code = null;

  const closeList = () => { list = null; listKind = ''; };
  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    if (/^```/.test(line.trim())) {
      closeList();
      if (code) {
        code = null;
      } else {
        const pre = document.createElement('pre');
        code = document.createElement('code');
        pre.appendChild(code);
        container.appendChild(pre);
      }
      continue;
    }
    if (code) {
      code.textContent += `${code.textContent ? '\n' : ''}${line}`;
      continue;
    }
    const headerCells = markdownTableCells(line);
    if (headerCells.length > 1 && isMarkdownTableSeparator(lines[lineIndex + 1])) {
      closeList();
      const wrapper = document.createElement('div');
      wrapper.className = 'agent-chat-table-scroll';
      const table = document.createElement('table');
      const head = document.createElement('thead');
      const headRow = document.createElement('tr');
      for (const cell of headerCells) {
        const headingCell = document.createElement('th');
        headingCell.scope = 'col';
        appendInlineContent(headingCell, cell, sourceMap, strings, citationScope);
        headRow.appendChild(headingCell);
      }
      head.appendChild(headRow);
      table.appendChild(head);
      const body = document.createElement('tbody');
      lineIndex += 2;
      while (lineIndex < lines.length && lines[lineIndex].includes('|') && lines[lineIndex].trim()) {
        const row = document.createElement('tr');
        for (const cell of markdownTableCells(lines[lineIndex])) {
          const dataCell = document.createElement('td');
          appendInlineContent(dataCell, cell, sourceMap, strings, citationScope);
          row.appendChild(dataCell);
        }
        body.appendChild(row);
        lineIndex += 1;
      }
      table.appendChild(body);
      wrapper.appendChild(table);
      container.appendChild(wrapper);
      lineIndex -= 1;
      continue;
    }
    if (/^\s*---+\s*$/.test(line)) {
      closeList();
      container.appendChild(document.createElement('hr'));
      continue;
    }
    const quote = line.match(/^\s*>\s?(.*)$/);
    if (quote) {
      closeList();
      const blockquote = document.createElement('blockquote');
      appendInlineContent(blockquote, quote[1], sourceMap, strings, citationScope);
      container.appendChild(blockquote);
      continue;
    }
    const heading = line.match(/^\s*#{1,3}\s+(.+)$/);
    if (heading) {
      closeList();
      const element = document.createElement('h3');
      appendInlineContent(element, heading[1], sourceMap, strings, citationScope);
      container.appendChild(element);
      continue;
    }
    const unordered = line.match(/^\s*[-*]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      const kind = ordered ? 'ol' : 'ul';
      if (!list || listKind !== kind) {
        list = document.createElement(kind);
        listKind = kind;
        container.appendChild(list);
      }
      const item = document.createElement('li');
      appendInlineContent(item, (ordered || unordered)[1], sourceMap, strings, citationScope);
      list.appendChild(item);
      continue;
    }
    closeList();
    if (!line.trim()) continue;
    const paragraph = document.createElement('p');
    appendInlineContent(paragraph, line.trim(), sourceMap, strings, citationScope);
    container.appendChild(paragraph);
  }
}

function eventTitle(event, strings) {
  const tool = event?.payload?.tool ? ` · ${event.payload.tool}` : '';
  const round = event?.payload?.round ? ` #${event.payload.round}` : '';
  const labels = {
    request: strings.debugRequest,
    response: strings.debugResponse,
    tool_start: strings.debugToolStart,
    tool_end: strings.debugToolEnd,
    tool_error: strings.debugToolError,
    response_repair: strings.debugResponseRepair,
    capability_fallback: strings.debugCapabilityFallback,
    context_pressure: strings.debugContextPressure,
    context_compaction_start: strings.debugContextCompactionStart,
    context_compaction_end: strings.debugContextCompactionEnd,
    complete: strings.debugComplete,
  };
  return `${labels[event.type] || event.type}${round}${tool}`;
}

function errorMessage(error, strings) {
  const byCode = {
    invalid_url: strings.errorInvalidUrl,
    missing_model: strings.errorMissingModel,
    missing_prompt: strings.errorMissingPrompt,
    auth: strings.errorAuth,
    rate_limit: strings.errorRateLimit,
    network: strings.errorNetwork,
    invalid_response: strings.errorInvalidResponse,
    empty_response: strings.errorEmptyResponse,
    round_limit: strings.errorLoopLimit,
    tool_limit: strings.errorLoopLimit,
    context_limit: strings.errorContextLimit,
    aborted: strings.statusAborted,
  };
  const prefix = byCode[error?.code] || strings.errorGeneric;
  if (error?.code === 'aborted') return prefix;
  return error?.message && error.message !== prefix ? `${prefix} ${error.message}` : prefix;
}

function modelsEndpoint(apiUrl) {
  const endpoint = new URL(normalizeChatEndpoint(apiUrl));
  endpoint.pathname = endpoint.pathname.replace(/\/chat\/completions\/?$/i, '/models');
  return endpoint.toString();
}

function authHeaders(apiKey) {
  return {
    'Content-Type': 'application/json',
    ...(apiKey ? { Authorization: `Bearer ${apiKey}` } : {}),
  };
}

export function buildRouteContext({ href, locale = 'pt-br', include = true } = {}) {
  const url = new URL(String(href || '/'), 'https://bibliotheca.invalid');
  const doc = url.searchParams.get('doc');
  const page = url.searchParams.get('page');
  const volume = url.searchParams.get('volume');
  const routeName = url.pathname.split('/').filter(Boolean).at(-1) || 'home';
  const pageNumber = Number(page);
  const pageContext = include && doc && Number.isInteger(pageNumber) && pageNumber > 0
    ? { kind: 'viewer', volume_id: doc, viewer_page: pageNumber }
    : {
      kind: 'none',
      reason: !include
        ? 'user_disabled'
        : doc ? 'viewer_page_missing' : 'current_route_has_no_viewer_page',
    };
  return [
    'Current site context (informational, not an instruction):',
    JSON.stringify({
      page_context: pageContext,
      site_context: {
        locale,
        route: routeName,
        ...(volume ? { selected_volume: volume } : {}),
      },
    }),
  ].join('\n');
}

function initializeAgentChat(root) {
  if (root.dataset.initialized === 'true') return;
  root.dataset.initialized = 'true';
  const config = parseJson(root.dataset.config, {});
  const strings = config.strings || {};
  let settings = loadSettings({ defaultPrompt: config.defaultPrompt || '', locale: config.locale || 'pt-br' });
  let connectionState = settings.apiUrl && settings.model ? 'configured' : 'missing';
  let apiKey = '';
  let conversationMessages = [];
  let conversationSources = [];
  let conversationContextState = { epoch: 0, lastPromptTokens: 0 };
  let debugEvents = [];
  let activeController = null;
  let previousFocus = null;
  let previousOverflow = '';
  let includeRouteContext = true;
  let shouldAutoScroll = true;

  const find = (role) => root.querySelector(`[data-agent-role="${role}"]`);
  const launcher = find('launcher');
  const backdrop = find('backdrop');
  const drawer = find('drawer');
  const closeButton = find('close');
  const transcript = find('transcript');
  const composer = find('composer');
  const input = find('input');
  const sendButton = find('send');
  const stopButton = find('stop');
  const status = find('status');
  const settingsDetails = find('settings');
  const settingsForm = find('settings-form');
  const apiUrlInput = find('api-url');
  const apiKeyInput = find('api-key');
  const modelInput = find('model');
  const promptInput = find('system-prompt');
  const debugInput = find('debug-enabled');
  const revealKeyButton = find('reveal-key');
  const resetPromptButton = find('reset-prompt');
  const newChatButton = find('new-chat');
  const debugList = find('debug-list');
  const clearDebugButton = find('clear-debug');
  const copyDebugButton = find('copy-debug');
  const connectionSummary = find('connection-summary');
  const configureButton = find('configure');
  const contextBar = find('context');
  const contextLabel = find('context-label');
  const removeContextButton = find('remove-context');
  const newResponseButton = find('new-response');
  const testConnectionButton = find('test-connection');
  const connectionTest = find('connection-test');
  const tabButtons = [...root.querySelectorAll('[data-agent-tab]')];
  const panels = [...root.querySelectorAll('[data-agent-panel]')];

  const tools = createAgentTools({
    assetBase: config.assetBase,
    locale: config.locale,
    indicesVersion: config.indicesVersion,
    searchIndexVersion: config.searchIndexVersion,
  });

  function syncSettingsForm() {
    apiUrlInput.value = settings.apiUrl;
    modelInput.value = settings.model;
    promptInput.value = settings.systemPrompt;
    debugInput.checked = settings.debug;
    apiKeyInput.value = apiKey;
    syncConnectionSummary();
  }

  function syncConnectionSummary() {
    if (!connectionSummary) return;
    const labels = {
      validated: strings.connectionValidated,
      failed: strings.connectionFailedSummary,
      configured: strings.connectionConfigured,
      missing: strings.connectionMissing,
    };
    connectionSummary.textContent = (labels[connectionState] || strings.connectionMissing)
      .replace('{model}', settings.model || '');
    connectionSummary.dataset.state = connectionState;
  }

  function syncRouteContext() {
    const url = new URL(window.location.href);
    const doc = url.searchParams.get('doc');
    const page = url.searchParams.get('page');
    const volume = url.searchParams.get('volume');
    const label = doc
      ? `${doc}${page ? ` · ${strings.pageAbbreviation}${page}` : ''}`
      : volume
        ? volume
        : '';
    if (!contextBar || !contextLabel || !label) {
      if (contextBar) contextBar.hidden = true;
      return;
    }
    contextLabel.textContent = includeRouteContext
      ? `${strings.contextLabel}: ${label}`
      : strings.contextDisabled.replace('{context}', label);
    removeContextButton.textContent = includeRouteContext ? strings.removeContext : strings.restoreContext;
    removeContextButton.setAttribute('aria-pressed', includeRouteContext ? 'false' : 'true');
    contextBar.hidden = false;
  }

  function setStatus(message, tone = '') {
    status.textContent = message || '';
    status.dataset.tone = tone;
  }

  function setBusy(busy) {
    root.dataset.busy = busy ? 'true' : 'false';
    sendButton.hidden = busy;
    stopButton.hidden = !busy;
    input.disabled = busy;
    settingsForm.querySelectorAll('input, textarea, button').forEach((element) => {
      if (element !== stopButton) element.disabled = busy;
    });
    drawer.setAttribute('aria-busy', busy ? 'true' : 'false');
  }

  function scrollTranscript(force = false) {
    requestAnimationFrame(() => {
      if (force || shouldAutoScroll) {
        transcript.scrollTo({ top: transcript.scrollHeight, behavior: 'smooth' });
        newResponseButton.hidden = true;
      } else {
        newResponseButton.hidden = false;
      }
    });
  }

  function renderSources(container, sources) {
    if (!sources?.length) return;
    const section = document.createElement('section');
    section.className = 'agent-chat-sources';
    const label = document.createElement('h3');
    label.textContent = `${strings.sourcesLabel} (${sources.length})`;
    section.appendChild(label);
    const list = document.createElement('div');
    list.className = 'agent-chat-source-list';
    for (const source of sources) {
      const card = document.createElement('article');
      card.className = 'agent-chat-source-card';
      card.dataset.sourceCitation = source.citationId;
      card.tabIndex = -1;

      const heading = document.createElement('div');
      heading.className = 'agent-chat-source-heading';
      const title = document.createElement('strong');
      title.textContent = source.kind === 'page'
        ? `${source.volumeId} · ${strings.pageAbbreviation}${source.page}`
        : source.label || source.volumeId || strings.indexSource;
      const citation = document.createElement('span');
      citation.textContent = `[${source.citationId.slice(1)}]`;
      heading.append(title, citation);
      card.appendChild(heading);

      const description = [source.author, source.title].filter(Boolean).join(' — ');
      if (description) {
        const paragraph = document.createElement('p');
        paragraph.textContent = description;
        card.appendChild(paragraph);
      }

      const evidence = document.createElement('p');
      evidence.className = 'agent-chat-source-evidence';
      evidence.textContent = (source.evidence || []).includes('ocr_read')
        ? strings.evidenceOcr
        : (source.evidence || []).includes('metadata')
          ? strings.evidenceMetadata
          : strings.evidenceIndex;
      card.appendChild(evidence);

      const actions = document.createElement('div');
      actions.className = 'agent-chat-source-actions';
      const actionDefinitions = [
        ['viewer', strings.openViewer],
        ['ocr', strings.openOcr],
        ['index', strings.openIndex],
        ['primary', strings.openSource],
      ];
      for (const [kind, actionLabel] of actionDefinitions) {
        const representation = source.representations?.[kind];
        if (!representation?.url) continue;
        const link = document.createElement('a');
        link.href = representation.url;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.textContent = actionLabel;
        actions.appendChild(link);
      }
      if (actions.children.length) card.appendChild(actions);

      const metadataEntries = [
        [strings.sourceAuthor, source.author],
        [strings.sourceWork, source.title],
        [strings.sourceSummary, source.metadata?.summary || source.metadata?.excerpt],
        [strings.sourceKeywords, Array.isArray(source.metadata?.keywords) ? source.metadata.keywords.join(', ') : ''],
        [strings.sourceProvenance, (source.provenance || []).map((item) => strings.toolLabels?.[item] || item).join(', ')],
      ].filter(([, value]) => typeof value === 'string' && value.trim());
      if (metadataEntries.length) {
        const details = document.createElement('details');
        const summary = document.createElement('summary');
        summary.textContent = strings.metadataLabel;
        details.appendChild(summary);
        const metadataList = document.createElement('dl');
        for (const [term, value] of metadataEntries) {
          const dt = document.createElement('dt');
          dt.textContent = term;
          const dd = document.createElement('dd');
          dd.textContent = value;
          metadataList.append(dt, dd);
        }
        details.appendChild(metadataList);
        card.appendChild(details);
      }
      list.appendChild(card);
    }
    section.appendChild(list);
    container.appendChild(section);
  }

  function renderUsage(container, usage) {
    const total = usage?.total;
    if (!total || !Object.keys(total).length) return;
    const details = document.createElement('details');
    details.className = 'agent-chat-usage';
    const summary = document.createElement('summary');
    const rounds = usage.rounds?.length || 0;
    const tokens = total.total_tokens ?? ((total.prompt_tokens || 0) + (total.completion_tokens || 0));
    summary.textContent = strings.usageSummary
      .replace('{rounds}', String(rounds))
      .replace('{tokens}', Number(tokens || 0).toLocaleString(config.locale));
    const pre = document.createElement('pre');
    pre.textContent = JSON.stringify(usage, null, 2);
    details.append(summary, pre);
    container.appendChild(details);
  }

  function appendMessage(role, text, sources = [], options = {}) {
    const displayedSources = role === 'assistant' ? selectCitedSources(text, sources) : sources;
    const article = document.createElement('article');
    article.className = `agent-chat-message is-${role}`;
    if (options.error) article.dataset.error = 'true';
    const label = document.createElement('div');
    label.className = 'agent-chat-message-label';
    label.textContent = role === 'user' ? strings.youLabel : strings.assistantLabel;
    const body = document.createElement('div');
    body.className = 'agent-chat-message-body';
    appendSafeMarkdown(body, text, displayedSources, strings, article);
    article.append(label, body);
    renderSources(article, displayedSources);
    renderUsage(article, options.usage);
    transcript.appendChild(article);
    scrollTranscript();
    return article;
  }

  function createStreamingMessage() {
    const article = document.createElement('article');
    article.className = 'agent-chat-message is-assistant is-streaming';
    article.dataset.streaming = 'true';
    const label = document.createElement('div');
    label.className = 'agent-chat-message-label';
    label.textContent = strings.assistantLabel;
    const body = document.createElement('div');
    body.className = 'agent-chat-message-body';
    // Do not use a live region here: announcing every token makes streamed
    // answers unusable with screen readers. The completion status is announced.
    body.setAttribute('aria-live', 'off');
    article.append(label, body);
    transcript.appendChild(article);
    scrollTranscript();
    return { article, body, text: '', round: 0 };
  }

  function createActivityGroup() {
    const group = document.createElement('div');
    group.className = 'agent-chat-activities';
    group.setAttribute('aria-label', strings.activityLabel);
    transcript.appendChild(group);
    return group;
  }

  function updateActivity(group, event) {
    if (!event.type.startsWith('tool_')) return;
    const callId = String(event.payload?.call_id || event.payload?.tool || 'tool');
    let chip = [...group.children].find((element) => element.dataset.callId === callId);
    if (!chip) {
      chip = document.createElement('span');
      chip.className = 'agent-chat-activity';
      chip.dataset.callId = callId;
      group.appendChild(chip);
    }
    const tool = String(event.payload?.tool || 'tool');
    const publicLabel = strings.toolLabels?.[tool] || tool;
    if (event.type === 'tool_start') {
      chip.dataset.state = 'busy';
      chip.textContent = strings.usingTool.replace('{tool}', publicLabel);
    } else if (event.type === 'tool_end') {
      chip.dataset.state = 'done';
      const resultCount = event.payload?.result?.data?.total
        ?? event.payload?.result?.data?.items?.length
        ?? event.payload?.result?.data?.entries?.length;
      chip.textContent = strings.toolDone.replace('{tool}', publicLabel)
        + (Number.isFinite(resultCount) ? ` · ${resultCount}` : '');
    } else {
      chip.dataset.state = 'error';
      chip.textContent = strings.toolFailed.replace('{tool}', publicLabel);
    }
    scrollTranscript();
  }

  function renderDebug() {
    debugList.replaceChildren();
    if (!debugEvents.length) {
      const empty = document.createElement('p');
      empty.className = 'agent-chat-debug-empty';
      empty.textContent = strings.debugEmpty;
      debugList.appendChild(empty);
      return;
    }
    debugEvents.forEach((event, index) => {
      const details = document.createElement('details');
      details.className = 'agent-chat-debug-event';
      if (index === debugEvents.length - 1) details.open = true;
      const summary = document.createElement('summary');
      summary.textContent = `${new Date(event.at).toLocaleTimeString()} · ${eventTitle(event, strings)}`;
      const pre = document.createElement('pre');
      pre.textContent = JSON.stringify(event.payload, null, 2);
      details.append(summary, pre);
      debugList.appendChild(details);
    });
  }

  function recordEvent(event, activityGroup) {
    updateActivity(activityGroup, event);
    if (!settings.debug) return;
    debugEvents.push(event);
    if (debugEvents.length > 120) debugEvents = debugEvents.slice(-120);
    renderDebug();
  }

  function routeContext() {
    return buildRouteContext({ href: window.location.href, locale: config.locale, include: includeRouteContext });
  }

  function openDrawer() {
    previousFocus = document.activeElement;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    backdrop.hidden = false;
    drawer.hidden = false;
    drawer.setAttribute('aria-hidden', 'false');
    launcher.setAttribute('aria-expanded', 'true');
    if (!settings.apiUrl || !settings.model) activateTab('debug');
    requestAnimationFrame(() => {
      root.dataset.open = 'true';
      (settings.apiUrl && settings.model ? input : apiUrlInput).focus();
    });
  }

  function closeDrawer() {
    root.dataset.open = 'false';
    drawer.setAttribute('aria-hidden', 'true');
    launcher.setAttribute('aria-expanded', 'false');
    window.setTimeout(() => {
      drawer.hidden = true;
      backdrop.hidden = true;
      document.body.style.overflow = previousOverflow;
      previousFocus?.focus?.();
    }, 180);
  }

  function activateTab(name) {
    tabButtons.forEach((button) => {
      const selected = button.dataset.agentTab === name;
      button.setAttribute('aria-selected', selected ? 'true' : 'false');
      button.tabIndex = selected ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.agentPanel !== name;
    });
    if (name === 'debug') renderDebug();
  }

  function resetConversation() {
    conversationMessages = [];
    conversationSources = [];
    conversationContextState = { epoch: 0, lastPromptTokens: 0 };
    includeRouteContext = true;
    syncRouteContext();
    transcript.querySelectorAll('.agent-chat-message:not([data-welcome]), .agent-chat-activities').forEach((element) => element.remove());
    setStatus(strings.statusReady);
    input.value = '';
    input.focus();
  }

  async function testConnection() {
    const apiUrl = apiUrlInput.value.trim();
    const model = modelInput.value.trim();
    apiKey = apiKeyInput.value;
    if (!apiUrl || !model) {
      connectionTest.textContent = strings.statusConfigMissing;
      (apiUrl ? modelInput : apiUrlInput).focus();
      return;
    }

    testConnectionButton.disabled = true;
    connectionTest.textContent = strings.testingConnection;
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 45_000);
    const checks = [];
    try {
      let availableModels = null;
      try {
        const modelsResponse = await fetch(modelsEndpoint(apiUrl), {
          headers: authHeaders(apiKey),
          signal: controller.signal,
        });
        if (modelsResponse.status === 401 || modelsResponse.status === 403) {
          throw new AgentChatError('auth', strings.errorAuth);
        }
        if (modelsResponse.ok) {
          const modelsPayload = await modelsResponse.json();
          availableModels = (modelsPayload?.data || []).map((item) => String(item?.id || '')).filter(Boolean);
          checks.push(
            { ok: true, text: strings.connectionEndpointOk },
            { ok: true, text: strings.connectionAuthOk },
            availableModels.includes(model)
              ? { ok: true, text: strings.connectionModelOk.replace('{model}', model) }
              : { ok: false, text: strings.connectionToolsMissing.replace('{detail}', `${strings.errorMissingModel} ${model}`) },
          );
        }
      } catch (error) {
        if (error instanceof AgentChatError) throw error;
        if (error?.name === 'AbortError') throw error;
        // /models is optional in OpenAI-compatible providers; the completion probe below is authoritative.
      }

      const echoTool = {
        type: 'function',
        function: {
          name: 'echo_test',
          description: 'Connection capability test.',
          parameters: {
            type: 'object',
            properties: { value: { type: 'string' } },
            required: ['value'],
            additionalProperties: false,
          },
        },
      };
      const completionResponse = await fetch(normalizeChatEndpoint(apiUrl), {
        method: 'POST',
        headers: authHeaders(apiKey),
        signal: controller.signal,
        body: JSON.stringify({
          model,
          messages: [
            { role: 'system', content: 'This is a tool-calling capability test. You must call echo_test exactly once and must not answer with normal text.' },
            { role: 'user', content: 'Call echo_test with value "ok".' },
          ],
          tools: [echoTool],
          tool_choice: 'auto',
          stream: false,
        }),
      });
      const completionPayload = await completionResponse.json().catch(() => null);
      if (!completionResponse.ok) {
        const code = completionResponse.status === 401 || completionResponse.status === 403 ? 'auth' : 'provider';
        throw new AgentChatError(code, completionPayload?.error?.message || `HTTP ${completionResponse.status}`);
      }
      if (!availableModels) {
        checks.push(
          { ok: true, text: strings.connectionEndpointOk },
          { ok: true, text: strings.connectionAuthOk },
          { ok: true, text: strings.connectionModelOk.replace('{model}', model) },
        );
      }
      const message = completionPayload?.choices?.[0]?.message;
      const normalizedProbe = normalizeAssistantMessage(message, [echoTool], 1).assistant;
      const hasToolCall = Array.isArray(normalizedProbe.tool_calls)
        && normalizedProbe.tool_calls.some((call) => call?.function?.name === 'echo_test');
      checks.push({
        ok: hasToolCall,
        text: hasToolCall ? strings.connectionToolsOk : strings.connectionToolsMissing.replace('{detail}', 'tool calling'),
      });
      connectionTest.textContent = checks
        .filter((check, index, all) => all.findIndex((item) => item.text === check.text) === index)
        .map((check) => `${check.ok ? '✓' : '△'} ${check.text}`)
        .join('\n');
      connectionState = hasToolCall ? 'validated' : 'failed';
      syncConnectionSummary();
    } catch (error) {
      const detail = error?.name === 'AbortError' ? strings.errorNetwork : errorMessage(error, strings);
      connectionTest.textContent = `× ${strings.connectionFailed}\n${detail}`;
      connectionState = 'failed';
      syncConnectionSummary();
    } finally {
      window.clearTimeout(timeoutId);
      testConnectionButton.disabled = false;
    }
  }

  async function submitMessage(event) {
    event.preventDefault();
    if (activeController) return;
    const userText = input.value.trim();
    if (!userText) return;
    const previousConnection = `${settings.apiUrl}\n${settings.model}`;
    settings = {
      apiUrl: apiUrlInput.value.trim(),
      model: modelInput.value.trim(),
      systemPrompt: promptInput.value.trim(),
      debug: debugInput.checked,
      promptOverrides: settings.promptOverrides,
    };
    if (`${settings.apiUrl}\n${settings.model}` !== previousConnection) {
      connectionState = settings.apiUrl && settings.model ? 'configured' : 'missing';
      syncConnectionSummary();
    }
    apiKey = apiKeyInput.value;
    try {
      saveSettings(settings, config);
    } catch {
      // A blocked localStorage must not prevent a local PoC from running.
    }
    if (!settings.apiUrl || !settings.model || !settings.systemPrompt) {
      settingsDetails.open = true;
      setStatus(strings.statusConfigMissing, 'error');
      (settings.apiUrl ? (settings.model ? promptInput : modelInput) : apiUrlInput).focus();
      return;
    }

    const messagesBeforeTurn = conversationMessages.length;
    conversationMessages.push({ role: 'user', content: userText });
    appendMessage('user', userText);
    input.value = '';
    const activityGroup = createActivityGroup();
    let streamingMessage = null;
    activeController = new AbortController();
    setBusy(true);
    setStatus(strings.statusWorking, 'busy');
    try {
      const result = await runChatCompletionLoop({
        apiUrl: settings.apiUrl,
        apiKey,
        model: settings.model,
        systemPrompt: settings.systemPrompt,
        routeContext: routeContext(),
        conversationMessages,
        sourceRegistry: conversationSources,
        contextState: conversationContextState,
        toolDefinitions: tools.definitions,
        executeTool: tools.execute,
        signal: activeController.signal,
        onEvent: (agentEvent) => {
          if (agentEvent.type === 'request') {
            const nextRound = Number(agentEvent.payload?.round || 0);
            if (streamingMessage && streamingMessage.round !== nextRound) {
              streamingMessage.article.remove();
              streamingMessage = null;
            }
          } else if (agentEvent.type === 'model_delta') {
            if (!streamingMessage) streamingMessage = createStreamingMessage();
            streamingMessage.round = Number(agentEvent.payload?.round || streamingMessage.round || 0);
            streamingMessage.text += String(agentEvent.payload?.content || '');
            streamingMessage.body.textContent = streamingMessage.text;
            scrollTranscript();
            return;
          }
          recordEvent(agentEvent, activityGroup);
        },
      });
      conversationMessages = result.messages;
      conversationSources = result.sourceRegistry;
      conversationContextState = result.contextState;
      streamingMessage?.article.remove();
      appendMessage('assistant', result.content, result.sources, { usage: result.usage });
      setStatus(strings.statusReady);
    } catch (error) {
      streamingMessage?.article.remove();
      if (error?.compactionRecovery) {
        conversationMessages = error.compactionRecovery.messages;
        conversationContextState = error.compactionRecovery.contextState;
      } else {
        conversationMessages = conversationMessages.slice(0, messagesBeforeTurn);
      }
      const normalized = error instanceof AgentChatError ? error : new AgentChatError('generic', error?.message || strings.errorGeneric);
      appendMessage('assistant', errorMessage(normalized, strings), [], { error: true });
      setStatus(normalized.code === 'aborted' ? strings.statusAborted : strings.statusError, normalized.code === 'aborted' ? '' : 'error');
      if (normalized.code !== 'aborted' && !input.value) input.value = userText;
    } finally {
      activeController = null;
      setBusy(false);
      input.focus();
    }
  }

  launcher.addEventListener('click', openDrawer);
  closeButton.addEventListener('click', closeDrawer);
  backdrop.addEventListener('click', closeDrawer);
  composer.addEventListener('submit', submitMessage);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      composer.requestSubmit();
    }
  });
  stopButton.addEventListener('click', () => activeController?.abort());
  newChatButton.addEventListener('click', resetConversation);
  configureButton.addEventListener('click', () => {
    activateTab('debug');
    settingsDetails.open = true;
    requestAnimationFrame(() => apiUrlInput.focus());
  });
  removeContextButton.addEventListener('click', () => {
    includeRouteContext = !includeRouteContext;
    syncRouteContext();
  });
  newResponseButton.addEventListener('click', () => {
    shouldAutoScroll = true;
    scrollTranscript(true);
  });
  transcript.addEventListener('scroll', () => {
    const distanceFromBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight;
    shouldAutoScroll = distanceFromBottom < 72;
    if (shouldAutoScroll) newResponseButton.hidden = true;
  });
  settingsForm.addEventListener('submit', (event) => {
    event.preventDefault();
    settings = {
      apiUrl: apiUrlInput.value.trim(),
      model: modelInput.value.trim(),
      systemPrompt: promptInput.value.trim() || config.defaultPrompt,
      debug: debugInput.checked,
      promptOverrides: settings.promptOverrides,
    };
    apiKey = apiKeyInput.value;
    connectionState = settings.apiUrl && settings.model ? 'configured' : 'missing';
    promptInput.value = settings.systemPrompt;
    try {
      saveSettings(settings, config);
      syncConnectionSummary();
      setStatus(strings.settingsSaved);
    } catch {
      setStatus(strings.settingsNotPersisted, 'error');
    }
  });
  revealKeyButton.addEventListener('click', () => {
    const reveal = apiKeyInput.type === 'password';
    apiKeyInput.type = reveal ? 'text' : 'password';
    revealKeyButton.setAttribute('aria-pressed', reveal ? 'true' : 'false');
    revealKeyButton.textContent = reveal ? strings.hideKey : strings.showKey;
  });
  resetPromptButton.addEventListener('click', () => {
    promptInput.value = config.defaultPrompt;
  });
  testConnectionButton.addEventListener('click', testConnection);
  clearDebugButton.addEventListener('click', () => {
    debugEvents = [];
    renderDebug();
  });
  copyDebugButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(debugEvents, null, 2));
      copyDebugButton.textContent = strings.debugCopied;
      window.setTimeout(() => { copyDebugButton.textContent = strings.debugCopy; }, 1200);
    } catch {
      setStatus(strings.copyFailed, 'error');
    }
  });
  tabButtons.forEach((button, index) => {
    button.addEventListener('click', () => activateTab(button.dataset.agentTab));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const nextIndex = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? tabButtons.length - 1
          : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabButtons.length) % tabButtons.length;
      const next = tabButtons[nextIndex];
      activateTab(next.dataset.agentTab);
      next.focus();
    });
  });
  drawer.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      closeDrawer();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = [...drawer.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [href], summary')]
      .filter((element) => !element.hidden && element.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  syncSettingsForm();
  syncRouteContext();
  renderDebug();
  setBusy(false);
  setStatus(settings.apiUrl && settings.model ? strings.statusReady : strings.statusConfigMissing);
  settingsDetails.open = !settings.apiUrl || !settings.model;
  activateTab('conversation');
}

export function initAgentChats() {
  document.querySelectorAll('[data-agent-chat]').forEach(initializeAgentChat);
}
