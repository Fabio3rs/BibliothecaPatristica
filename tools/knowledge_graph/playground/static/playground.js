"use strict";
const $ = (id) => document.getElementById(id);
const state = {
  csrf: "",
  defaults: {},
  pricing: null,
  pricingRequest: 0,
  pricingPending: false,
  cases: new Map(),
  currentCase: null,
  currentRun: null,
  catalog: [],
  profiles: [],
  busy: false,
  previews: [],
  experiment: null,
  selectedRuns: new Set(),
  historyBefore: null,
  historyLast: null,
  campaignIds: new Set(),
  campaignRefreshing: false,
  graphBox: [0, 0, 1000, 500],
  graphDefault: [0, 0, 1000, 500],
};
const json = (value) => JSON.stringify(value, null, 2);
const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};
const showMessage = (message, error = false) => {
  $("message").textContent = message;
  $("message").classList.toggle("error", error);
};
function tab(name) {
  document
    .querySelectorAll(".tab")
    .forEach((n) => n.classList.toggle("active", n.id === "tab-" + name));
  document
    .querySelectorAll("[data-tab]")
    .forEach((n) => n.classList.toggle("active", n.dataset.tab === name));
  if (innerWidth < 700)
    document.querySelector(".workspace").scrollIntoView({ block: "start" });
}
function evaluation() {
  return $("evaluation").checked;
}
function query(path, params = {}) {
  const q = new URLSearchParams({ ...params, evaluation: evaluation() });
  return path + "?" + q;
}
async function api(path, body) {
  const response = await fetch(
    body === undefined ? query(path.path || path, path.params || {}) : path,
    {
      method: body === undefined ? "GET" : "POST",
      headers:
        body === undefined
          ? {}
          : { "Content-Type": "application/json", "X-KG-CSRF": state.csrf },
      body:
        body === undefined
          ? undefined
          : JSON.stringify({ ...body, evaluation: evaluation() }),
    },
  );
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}
function action(id, fn) {
  $(id).addEventListener("click", async () => {
    const button = $(id);
    const was = button.disabled;
    button.disabled = true;
    try {
      await fn();
    } catch (error) {
      showMessage(error.message, true);
    } finally {
      button.disabled = was;
      if (id === "execute") button.disabled = state.busy || !state.experiment;
    }
  });
}
function invalidate() {
  state.experiment = null;
  $("execute").disabled = true;
}
function profile() {
  if (state.pricingPending)
    throw new Error("Aguarde o preenchimento da tarifa do modelo.");
  const p = {
    provider: $("provider").value,
    model: $("model").value.trim(),
    base_url: $("base-url").value.trim(),
    api_key_env: $("api-key-env").value.trim(),
    vision: $("vision").checked,
    structured_output: $("structured").value,
    reasoning_support: $("reasoning-support").checked,
    temperature_support: $("temperature-support").checked,
    max_output_tokens: Number($("max-tokens").value),
    timeout: Number($("timeout").value),
    retries: Number($("retries").value),
  };
  if ($("reasoning").value) p.reasoning_effort = $("reasoning").value;
  if ($("temperature").value !== "")
    p.temperature = Number($("temperature").value);
  if ($("num-ctx").value) p.num_ctx = Number($("num-ctx").value);
  if ($("prices").value.trim()) p.prices = JSON.parse($("prices").value);
  return p;
}
function providerDefaults() {
  const provider = $("provider").value,
    d = state.defaults[provider];
  $("base-url").value = d.base_url;
  $("api-key-env").value = d.api_key_env;
  $("vision").checked = d.vision;
  $("structured").value = d.structured_output;
  $("reasoning-support").checked = d.reasoning_support;
  $("temperature-support").checked = d.temperature_support;
  $("reasoning").value = "";
  $("temperature").value = "";
  $("num-ctx").value = "";
  const models = {
    ollama: "gemma4:31b",
    meta: "muse-spark-1.3",
    fixture: "starter-demo",
    openai: "",
    compatible: "",
  };
  $("model").value = models[provider];
  updatePrices();
  invalidate();
}
async function updatePrices() {
  invalidate();
  const request = ++state.pricingRequest;
  const params = {
    provider: $("provider").value,
    model: $("model").value.trim(),
    base_url: $("base-url").value.trim(),
  };
  $("priced-models").replaceChildren(
    ...(state.pricing?.models || [])
      .filter((entry) => entry.provider === params.provider)
      .flatMap((entry) =>
        (entry.cloud_models || entry.models).map((model) => {
          const option = el("option", entry.label);
          option.value = model;
          return option;
        }),
      ),
  );
  $("prices").value = "";
  $("price-note").textContent = "Consultando tabela local de tarifas…";
  state.pricingPending = true;
  try {
    const match = await api({ path: "/api/pricing", params });
    if (request !== state.pricingRequest) return;
    $("prices").value = match ? json(match.prices) : "";
    $("price-note").textContent = match
      ? `${match.label} · tabela informada por você em ${match.recorded_on}. Valores editáveis.`
      : "Sem tarifa cadastrada para este modelo/endpoint. Informe valores se desejar estimar o custo.";
  } catch (error) {
    if (request === state.pricingRequest)
      $("price-note").textContent = error.message;
  } finally {
    if (request === state.pricingRequest) state.pricingPending = false;
  }
}
function costNote(run) {
  const cost = run.cost_estimate;
  if (cost?.cost_usd == null) return "";
  if (run.result?.inference_performed === false)
    return "Sem nova inferência: replay de cache ou fixture.";
  const pricing = cost.pricing || {};
  const origin =
    pricing.source === "user_provided"
      ? `Tarifa informada por você em ${pricing.recorded_on}`
      : "Tarifa configurada na execução";
  return `${origin}${cost.basis === "catalog_reestimate" ? " · reestimativa da run antiga" : ""}.`;
}
function renderCases() {
  const box = $("case-list");
  box.replaceChildren();
  for (const c of state.cases.values()) {
    const row = el("div", undefined, "case-row");
    const checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = c.selected !== false;
    checkbox.setAttribute("aria-label", "Incluir " + c.page);
    checkbox.onchange = () => {
      c.selected = checkbox.checked;
      invalidate();
    };
    const open = el("button", "Ver");
    open.className = "secondary";
    open.onclick = () => displaySource(c);
    row.append(
      checkbox,
      el("span", c.page + (c.synthetic ? " · sintética" : "")),
      open,
    );
    box.append(row);
  }
}
function addCase(c, replace = false) {
  if (!c.synthetic && $("provider").value === "fixture") {
    $("provider").value = "ollama";
    providerDefaults();
    state.profiles = [];
    renderProfiles();
  }
  if (replace) state.cases.clear();
  c.selected = true;
  state.cases.set(c.id, c);
  renderCases();
  displaySource(c);
  invalidate();
}
function highlighted(target, text, focus) {
  target.replaceChildren();
  const match = focus?.match;
  if (
    match?.accepted &&
    Number.isInteger(match.start) &&
    Number.isInteger(match.end)
  ) {
    const chars = Array.from(text);
    if (chars.slice(match.start, match.end).join("") === match.matched_quote) {
      target.append(
        document.createTextNode(chars.slice(0, match.start).join("")),
        el("mark", match.matched_quote),
        document.createTextNode(chars.slice(match.end).join("")),
      );
      return;
    }
  }
  const quote = focus?.quote;
  const index = quote ? text.indexOf(quote) : -1;
  if (index < 0) {
    target.textContent = text;
    return;
  }
  target.append(
    document.createTextNode(text.slice(0, index)),
    el("mark", quote),
    document.createTextNode(text.slice(index + quote.length)),
  );
}
function displaySource(c, focus) {
  if (state.currentRun && state.currentRun.case_id !== c.id) {
    state.currentRun = null;
    for (const id of [
      "run-summary",
      "run-cost-note",
      "validation",
      "extraction-list",
      "evidence-detail",
      "object-detail",
      "reviews",
      "result-json",
      "raw-response",
      "graph",
    ])
      $(id).replaceChildren();
    $("validation").textContent =
      "Selecione ou execute um resultado desta página.";
    $("save-review").disabled = true;
    $("revalidate").disabled = true;
  }
  state.currentCase = c;
  selectedBlock = null;
  const snap = c.snapshot,
    s = snap.source;
  $("source-title").textContent = `${c.page} · página física`;
  $("source-scope").textContent =
    (s.scope || "synthetic").toUpperCase() + " · " + (s.status || "EXEMPLO");
  $("source-scope").className = "badge " + s.scope;
  $("source-meta").textContent =
    `Geração ${s.v2_generation_id ?? "—"} · run v2 ${s.run_id ?? "—"} · ${s.pagina_file || "kit sintético"} · ${s.selection_policy || "snapshot"} · OCR SHA256 ${s.ocr_sha256}`;
  $("source-warnings").replaceChildren();
  for (const warning of snap.ocr.warnings || [])
    $("source-warnings").append(el("div", warning, "warning"));
  if (s.scope === "shadow")
    $("source-warnings").append(
      el(
        "div",
        "Geração shadow: ainda não promovida. Proveniência preservada neste experimento.",
        "warning",
      ),
    );
  $("metadata").textContent = json({
    metadata: snap.v2.metadata_context,
    context_only: snap.v2.context_only,
  });
  $("segments").replaceChildren();
  for (const seg of snap.v2.segments) {
    const card = el("article", undefined, "card");
    card.id = "segment-" + seg.id;
    card.append(
      el(
        "div",
        `${seg.id} · ${seg.kind} · ${seg.work_key || "obra não identificada"}`,
        "card-top",
      ),
    );
    const text = el("div", undefined, "source-text");
    highlighted(
      text,
      seg.summary,
      focus?.source === "summary_segment" && focus.locator === seg.id
        ? focus
        : null,
    );
    card.append(text);
    $("segments").append(card);
  }
  $("block-count").textContent = snap.ocr.blocks.length;
  $("blocks").replaceChildren();
  for (const block of snap.ocr.blocks) {
    const card = el("article", undefined, "card ocr-card");
    card.id = "block-" + block.id;
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute(
      "aria-label",
      `Selecionar bloco ${block.id}: ${block.type}`,
    );
    card.append(
      el(
        "div",
        `${block.id} · ${block.type} · ${block.script || "script não indicado"}`,
        "card-top",
      ),
    );
    const text = el("div", undefined, "source-text");
    highlighted(
      text,
      block.text,
      focus?.source === "ocr_block" && focus.locator === block.id
        ? focus
        : null,
    );
    card.append(text);
    card.onclick = () => selectBlock(block);
    card.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        selectBlock(block);
      }
    };
    $("blocks").append(card);
  }
  const image = snap.facsimile;
  $("page-image").hidden = !image.available;
  $("bbox-highlight").hidden = true;
  $("block-detail").textContent =
    "Selecione um bloco para inspecionar o texto e as coordenadas.";
  if (image.available) {
    $("page-image").src = query("/api/image", { case: c.id });
    $("facsimile-status").textContent =
      "Imagem disponível · anexo de inferência depende do perfil e da política escolhidos.";
  } else {
    $("page-image").removeAttribute("src");
    $("facsimile-status").textContent =
      image.warning || "Fac-símile indisponível.";
  }
  if (focus?.source === "ocr_block") {
    const block = snap.ocr.blocks.find((b) => b.id === focus.locator);
    if (block) selectBlock(block);
  }
  if (focus) {
    tab("source");
    setTimeout(
      () =>
        $(
          focus.source === "ocr_block"
            ? "block-" + focus.locator
            : "segment-" + focus.locator,
        )?.scrollIntoView({ block: "center", behavior: "smooth" }),
      50,
    );
  }
}
let selectedBlock = null;
function selectBlock(block) {
  selectedBlock = block;
  document
    .querySelectorAll(".ocr-card")
    .forEach((c) =>
      c.classList.toggle("selected", c.id === "block-" + block.id),
    );
  $("block-detail").replaceChildren(
    el("strong", block.id + " · " + block.type),
    el("p", block.text, "source-text"),
    el(
      "pre",
      json({ script: block.script, bbox: block.bbox, attrs: block.attrs }),
    ),
  );
  drawBox();
}
function drawBox() {
  const b = selectedBlock?.bbox,
    image = state.currentCase?.snapshot.facsimile;
  const node = $("bbox-highlight");
  node.hidden = true;
  if (!b || !image?.available || $("bbox-scale").value === "off") return;
  const dims =
    $("bbox-scale").value === "pixels" ? image.dimensions : [1000, 1000];
  if (!dims || b[2] > dims[0] || b[3] > dims[1]) return;
  node.style.left = (b[0] / dims[0]) * 100 + "%";
  node.style.top = (b[1] / dims[1]) * 100 + "%";
  node.style.width = ((b[2] - b[0]) / dims[0]) * 100 + "%";
  node.style.height = ((b[3] - b[1]) / dims[1]) * 100 + "%";
  node.hidden = false;
}
function populateCatalog() {
  const items = state.catalog.filter(
    (c) =>
      (!$("collection").value || c.collection === $("collection").value) &&
      (!$("category").value || c.categories.includes($("category").value)) &&
      (!$("kind-filter").value || c.page_kind === $("kind-filter").value) &&
      (c.work_key || "")
        .toLowerCase()
        .includes($("work-filter").value.toLowerCase()),
  );
  $("benchmark").replaceChildren(
    ...items.map((c) => {
      const n = el(
        "option",
        `${c.page} · ${c.split} · ${c.work_key || c.page_kind}`,
      );
      n.value = c.benchmark_id;
      return n;
    }),
  );
}
async function loadCatalog() {
  state.catalog = await api("/api/catalog");
  for (const [id, values] of [
    ["category", state.catalog.flatMap((c) => c.categories)],
    ["kind-filter", state.catalog.map((c) => c.page_kind)],
  ]) {
    const first = el("option", "Todos");
    first.value = "";
    $(id).replaceChildren(
      first,
      ...[...new Set(values)].sort().map((v) => el("option", v)),
    );
  }
  populateCatalog();
}
function showPreview(index) {
  const item = state.previews[index];
  if (!item) return;
  const p = item.prepared;
  $("system-prompt").textContent = p.system_prompt;
  $("user-payload").textContent = json(p.user_payload);
  $("request-config").textContent = json({
    config: p.config,
    pricing_reference: p.pricing_reference,
    schema: p.schema,
    request_endpoint: p.request.url,
    image_transport_sha256: p.image_transport_sha256,
  });
  $("prompt-meta").replaceChildren(
    el("div", `Run ${item.run_id} · hash ${p.input_hash}`, "muted"),
    el(
      "p",
      `≈ ${p.estimated_input_tokens.toLocaleString()} tokens textuais (${p.token_estimate_note}). Imagem selecionada: ${p.facsimile_selected ? "sim" : "não"}.`,
    ),
  );
  for (const warning of p.warnings)
    $("prompt-meta").append(el("div", warning, "warning"));
}
async function preview() {
  const cases = [...state.cases.values()]
    .filter((c) => c.selected !== false)
    .map((c) => c.id);
  if (!cases.length) throw new Error("Carregue e selecione uma página.");
  const configs = state.profiles.length ? state.profiles : [profile()];
  const body = {
    case_ids: cases,
    configs,
    variants: $("both-prompts").checked
      ? ["concepts-v1", "concepts-v1-fewshot"]
      : [$("variant").value],
    modes: [$("mode").value],
    facsimiles: [$("facsimile").value],
    repetitions: Number($("repetitions").value),
    name: $("experiment-name").value,
    bypass_cache: $("bypass-cache").checked,
  };
  if ($("custom-system").value.trim())
    body.system_override = $("custom-system").value;
  const result = await api("/api/preview", body);
  state.experiment = result.experiment;
  state.previews = result.previews;
  $("preview-run").replaceChildren(
    ...result.previews.map((r, i) => {
      const o = el(
        "option",
        `Run ${r.run_id} · ${r.prepared.config.model} · ${r.prepared.variant.id}`,
      );
      o.value = i;
      return o;
    }),
  );
  showPreview(0);
  $("execute").disabled = false;
  tab("prompt");
  showMessage(
    `${result.experiment}: ${result.runs.length} execuções preparadas e salvas. Nenhuma chamada de modelo realizada.`,
  );
  await history();
}
async function execute(exp, resume = false) {
  await api("/api/execute", {
    experiment: exp,
    execute: true,
    resume,
    concurrency: Number($("concurrency").value),
  });
  state.busy = true;
  $("execute").disabled = true;
  showMessage("Executando " + exp + "…");
  await poll(exp);
}
async function poll(exp) {
  const p = await api({ path: "/api/progress", params: { experiment: exp } });
  showMessage(
    p.state === "error"
      ? p.error
      : `${exp} · ${p.state} · ${p.completed || 0}/${p.total || "…"}`,
    p.state === "error",
  );
  if (p.state === "running") {
    setTimeout(
      () => poll(exp).catch((e) => showMessage(e.message, true)),
      1000,
    );
    return;
  }
  state.busy = false;
  await history();
  const runs = await api({ path: "/api/runs", params: { experiment: exp } });
  const done = runs.find((r) =>
    ["completed", "invalid", "cached", "error"].includes(r.status),
  );
  if (done) await openRun(done.id);
  state.experiment = null;
  $("execute").disabled = true;
}
function extraction() {
  return state.currentRun?.validation?.parsed;
}
function canInspectGraph() {
  const v = state.currentRun?.validation;
  return v?.graph_inspectable ?? v?.structural_valid;
}
function metric(name, value) {
  const card = el("div", undefined, "metric");
  card.append(el("strong", value ?? "—"), el("span", name));
  return card;
}
async function openRun(id) {
  const run = await api({ path: "/api/run", params: { id } });
  const c = await api({ path: "/api/case", params: { id: run.case_id } });
  state.currentRun = run;
  displaySource(c);
  const v = run.validation,
    r = run.result || {};
  $("run-summary").replaceChildren(
    metric("Página", c.page + (c.synthetic ? " · SINTÉTICA" : "")),
    metric("Run / modelo", `${id} / ${run.config.model}`),
    metric("Contrato", run.prepared.schema_version),
    metric("Status original", run.status),
    metric(
      "Validação atual",
      v ? (v.valid ? "válida" : "inválida") : "pendente",
    ),
    metric(
      "Tokens entrada / saída total",
      `${r.usage?.input_tokens ?? "—"} / ${r.usage?.output_tokens ?? "—"}`,
    ),
    metric("Raciocínio (incluído na saída)", r.usage?.reasoning_tokens),
    metric("Limite de saída", run.config.max_output_tokens),
    metric("Latência (s)", r.latency_seconds?.toFixed(2)),
    metric("Custo estimado USD", run.cost_estimate?.cost_usd?.toFixed(6)),
    metric(
      "Imagem anexada",
      r.facsimile_attached === true
        ? "sim"
        : r.facsimile_attached === false
          ? "não"
          : "desconhecido",
    ),
  );
  $("run-cost-note").textContent = costNote(run);
  $("validation").className = "card";
  $("validation").replaceChildren();
  if (v) {
    $("validation").append(
      el(
        "strong",
        `Estrutura: ${v.structural_valid ? "válida" : "inválida"} · Evidências localizadas: ${v.source_valid === true ? "sim" : v.source_valid === false ? "parcialmente ou não" : "não avaliadas"} · Semântica: pendente`,
      ),
    );
    if (v.evidence_counts) {
      const counts = v.evidence_counts;
      $("validation").append(
        el(
          "p",
          `${counts.exact} exatas · ${counts.normalized + counts.fuzzy} aproximadas · ${counts.ambiguous} ambíguas · ${counts.not_found + counts.unknown_locator} não localizadas. ${v.version}. Localizar uma citação não confirma sua interpretação.`,
          "muted small",
        ),
      );
    }
    if (run.status === "invalid" && v.valid)
      $("validation").append(
        el(
          "p",
          "A revalidação atual passou. O status original registra o resultado obtido na execução e foi preservado.",
          "muted small",
        ),
      );
    if (v.completion?.output_limit_reached) {
      const message = v.diagnostics.find(
        (d) => d.code === "provider_output_limit_reached",
      );
      $("validation").append(
        el(
          "p",
          message?.message || "Resposta interrompida pelo limite de saída.",
          "warning",
        ),
      );
    }
    if (canInspectGraph() && !v.valid)
      $("validation").append(
        el(
          "p",
          "Visualização para inspeção de uma saída inválida. Os IDs e as citações originais foram mantidos; o grafo não confirma a validade das relações.",
          "warning",
        ),
      );
    for (const message of [
      ...v.structural_errors,
      ...v.source_errors,
      ...v.warnings,
    ])
      $("validation").append(el("div", message, "warning"));
    for (const d of v.diagnostics || [])
      if (d.code !== "provider_output_limit_reached")
        $("validation").append(
          el("div", d.question || d.message, "muted small"),
        );
  } else
    $("validation").textContent =
      r.error || "Execução preparada; ainda sem resposta.";
  $("raw-response").textContent = r.raw_response || r.provider_response || "";
  $("result-json").textContent = json(v?.parsed ?? null);
  $("revalidate").disabled = !r.raw_response;
  $("save-review").disabled = !v;
  $("review-target").value = "page";
  $("object-detail").textContent = "Selecione um nó ou uma relação.";
  $("evidence-detail").replaceChildren();
  renderExtraction();
  renderGraph();
  renderReviews();
  tab("extraction");
}
const nameOf = (o) => o.label || o.proposition || o.description || o.id;
function confidenceLabel(obj) {
  const score = obj.model_confidence?.score;
  return typeof score === "number" && Number.isFinite(score)
    ? `Confiança autodeclarada: ${score.toLocaleString("pt-BR", { maximumFractionDigits: 4 })} / 1 · não calibrada`
    : "Confiança autodeclarada: não informada nesta execução";
}
function renderExtraction() {
  const box = $("extraction-list");
  box.replaceChildren();
  const data = extraction();
  if (!canInspectGraph()) {
    box.append(el("p", "Veja a resposta original e os erros de validação."));
    return;
  }
  const names = {
    concepts: "Conceitos",
    entities: "Entidades",
    claims: "Proposições",
    events: "Eventos",
    relations: "Relações",
    warnings: "Avisos",
  };
  for (const group of Object.keys(names)) {
    const section = el("section");
    section.append(el("h3", names[group] + " · " + data[group].length));
    for (const obj of data[group]) {
      if (group === "warnings") {
        section.append(el("div", obj.code + ": " + obj.message, "warning"));
        continue;
      }
      const label =
        group === "relations"
          ? `${nodeName(obj.source_id)} → ${obj.predicate} → ${nodeName(obj.target_id)}`
          : nameOf(obj);
      const btn = el("button", label, "item-button");
      btn.append(
        el(
          "small",
          group === "relations"
            ? obj.id
            : `${obj.id} · ${obj.meaning || obj.type || obj.mode || obj.narrative_status || ""}`,
        ),
      );
      if (obj.model_confidence) btn.append(el("small", confidenceLabel(obj)));
      btn.onclick = () => selectObject(obj);
      section.append(btn);
    }
    box.append(section);
  }
}
function nodeName(id) {
  const d = extraction();
  return (
    ["concepts", "entities", "claims", "events"]
      .flatMap((k) => d[k])
      .find((o) => o.id === id)?.label ||
    ["claims", "events"].flatMap((k) => d[k]).find((o) => o.id === id)
      ?.proposition ||
    id
  );
}
function selectObject(obj) {
  document.querySelectorAll("#graph [data-object-id]").forEach((node) => {
    node.classList.toggle("selected", node.dataset.objectId === obj.id);
  });
  const conflict = state.currentRun.validation.id_conflicts?.some(
    (c) => c.id === obj.id,
  );
  const group = ["concepts", "entities", "claims", "events", "relations"].find(
    (k) => extraction()[k].includes(obj),
  );
  $("review-target").value = conflict && group ? `${group}/${obj.id}` : obj.id;
  $("object-detail").replaceChildren(
    el(
      "strong",
      obj.predicate
        ? `${nodeName(obj.source_id)} → ${obj.predicate} → ${nodeName(obj.target_id)}`
        : nameOf(obj),
    ),
  );
  $("object-detail").append(el("p", confidenceLabel(obj), "small"));
  if (obj.model_confidence) {
    $("object-detail").append(
      el("p", obj.model_confidence.rationale),
      el(
        "p",
        "Estimativa do modelo sobre a fidelidade deste item às fontes. Não mede a verdade histórica da proposição e não substitui a revisão semântica.",
        "muted small",
      ),
    );
  }
  $("object-detail").append(el("pre", json(obj)));
  $("evidence-detail").replaceChildren();
  const data = extraction();
  for (const id of obj.evidence_ids || []) {
    const ev = data.evidence.find((e) => e.id === id);
    if (!ev) continue;
    const card = el("div", undefined, "card evidence-card");
    const source =
      ev.source === "ocr_block"
        ? state.currentCase.snapshot.ocr.blocks.find((b) => b.id === ev.locator)
            ?.text
        : state.currentCase.snapshot.v2.segments.find(
            (s) => s.id === ev.locator,
          )?.summary;
    const exact = Boolean(ev.quote && source?.includes(ev.quote));
    const match = state.currentRun.validation.evidence_matches?.find(
      (m) => m.evidence_id === id,
    );
    const located = match ? match.accepted : exact;
    const label =
      match?.accepted && match.method !== "exact"
        ? match.method === "normalized"
          ? "aproximada · acentos ou tipografia"
          : `aproximada · similaridade textual ${(match.score * 100).toFixed(1)}%`
        : exact
          ? "exata"
          : match?.method === "ambiguous"
            ? "LOCALIZAÇÃO AMBÍGUA"
            : "NÃO LOCALIZADA";
    card.append(
      el("div", `${id} · ${ev.source}:${ev.locator} · ${label}`, "card-top"),
      el("div", "Citação do modelo", "muted small"),
      el("blockquote", ev.quote),
    );
    if (match?.accepted && match.method !== "exact")
      card.append(
        el("div", "Trecho encontrado na fonte", "muted small"),
        el("blockquote", match.matched_quote),
      );
    const button = el(
      "button",
      located ? "Localizar na fonte" : "Inspecionar fonte indicada",
      "secondary",
    );
    button.onclick = () => displaySource(state.currentCase, { ...ev, match });
    card.append(button);
    $("evidence-detail").append(card);
  }
  if (!(obj.evidence_ids || []).length)
    $("evidence-detail").append(
      el("div", "Sem evidência referenciada.", "warning"),
    );
}
function renderReviews() {
  $("reviews").replaceChildren();
  for (const review of state.currentRun?.reviews || [])
    $("reviews").append(
      el(
        "div",
        `${review.object_id} · ${review.decision} · ${review.dimension}\n${review.comment}`,
        "review",
      ),
    );
}
const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}, text) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (text !== undefined) n.textContent = text;
  return n;
}
function updateBox() {
  $("graph").setAttribute("viewBox", state.graphBox.join(" "));
}
function renderGraph() {
  const graph = $("graph");
  graph.replaceChildren();
  if (!canInspectGraph()) return;
  const d = extraction(),
    filter = $("graph-filter").value.toLowerCase();
  const kinds = ["concepts", "entities", "claims", "events"],
    colors = ["#e1ebbc", "#dceaf4", "#f3e1cf", "#e9dff3"];
  const nodes = kinds.flatMap((kind, k) =>
    d[kind]
      .filter(
        (o) =>
          !filter ||
          nameOf(o).toLowerCase().includes(filter) ||
          o.id.includes(filter),
      )
      .map((o, i) => ({ o, k, x: 40 + k * 400, y: 50 + i * 120 })),
  );
  const positions = new Map(nodes.map((n) => [n.o.id, n]));
  const height = Math.max(440, ...nodes.map((n) => n.y + 100));
  state.graphDefault = [
    0,
    0,
    Math.max(700, ...nodes.map((n) => n.x + 270)),
    height,
  ];
  state.graphBox = [...state.graphDefault];
  updateBox();
  const defs = svg("defs"),
    marker = svg("marker", {
      id: "arrow",
      viewBox: "0 0 10 10",
      refX: 9,
      refY: 5,
      markerWidth: 7,
      markerHeight: 7,
      orient: "auto-start-reverse",
    });
  marker.append(svg("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: "#81917f" }));
  defs.append(marker);
  graph.append(defs);
  const edgeLayer = svg("g");
  graph.append(edgeLayer);
  d.relations.forEach((r, i) => {
    const a = positions.get(r.source_id),
      b = positions.get(r.target_id);
    if (!a || !b) return;
    const acx = a.x + 110,
      acy = a.y + 28,
      bcx = b.x + 110,
      bcy = b.y + 28;
    const dx = bcx - acx,
      dy = bcy - acy,
      scale = Math.max(Math.abs(dx) / 110, Math.abs(dy) / 28, 1);
    const sx = acx + dx / scale,
      sy = acy + dy / scale,
      tx = bcx - dx / scale,
      ty = bcy - dy / scale;
    const g = svg("g", {
      class: "graph-edge",
      "data-object-id": r.id,
      tabindex: 0,
      role: "button",
      "aria-label": `${nodeName(r.source_id)} ${r.predicate} ${nodeName(r.target_id)}`,
    });
    let path, lx, ly;
    if (a === b) {
      path = `M ${acx - 60} ${a.y} C ${acx - 120} ${a.y - 65}, ${acx + 120} ${a.y - 65}, ${acx + 60} ${a.y}`;
      lx = acx;
      ly = a.y - 45;
    } else {
      const bend = ((i % 3) - 1) * 60;
      const cx = (sx + tx) / 2,
        cy = (sy + ty) / 2 + bend;
      path = `M ${sx} ${sy} Q ${cx} ${cy} ${tx} ${ty}`;
      lx = (sx + 2 * cx + tx) / 4;
      ly = (sy + 2 * cy + ty) / 4 - 7;
    }
    g.append(
      svg("path", { d: path, "marker-end": "url(#arrow)" }),
      svg("path", {
        d: path,
        style: "stroke:transparent;stroke-width:16;fill:none",
      }),
      svg("text", { x: lx, y: ly, "text-anchor": "middle" }, r.predicate),
    );
    g.onclick = (e) => {
      e.stopPropagation();
      selectObject(r);
    };
    g.onkeydown = (e) => {
      if (e.key === "Enter") selectObject(r);
    };
    edgeLayer.append(g);
  });
  for (const n of nodes) {
    const g = svg("g", {
      class: "graph-node",
      "data-object-id": n.o.id,
      transform: `translate(${n.x},${n.y})`,
      tabindex: 0,
      role: "button",
      "aria-label": nameOf(n.o),
    });
    g.append(
      svg("rect", {
        width: 220,
        height: 56,
        rx: 8,
        fill: colors[n.k],
        stroke: "#c5cdbb",
      }),
    );
    const label = nameOf(n.o);
    g.append(
      svg(
        "text",
        { x: 12, y: 23 },
        label.length > 28 ? label.slice(0, 27) + "…" : label,
      ),
      svg(
        "text",
        { x: 12, y: 43, fill: "#62715d" },
        n.o.id + " · " + kinds[n.k],
      ),
    );
    g.append(svg("title", {}, label));
    g.onclick = () => selectObject(n.o);
    g.onkeydown = (e) => {
      if (e.key === "Enter") selectObject(n.o);
    };
    graph.append(g);
  }
  if (!nodes.length)
    graph.append(
      svg(
        "text",
        { x: 30, y: 55 },
        "Nenhum nó para exibir. Extrações vazias são permitidas.",
      ),
    );
}
function zoom(factor) {
  const [x, y, w, h] = state.graphBox;
  const nw = Math.min(20000, Math.max(150, w * factor)),
    nh = (h * nw) / w;
  state.graphBox = [x + (w - nw) / 2, y + (h - nh) / 2, nw, nh];
  updateBox();
}
let drag = null;
$("graph").addEventListener("pointerdown", (e) => {
  if (e.target.closest(".graph-node,.graph-edge")) return;
  drag = { x: e.clientX, y: e.clientY, box: [...state.graphBox] };
  $("graph").setPointerCapture(e.pointerId);
});
$("graph").addEventListener("pointermove", (e) => {
  if (!drag) return;
  const rect = $("graph").getBoundingClientRect();
  state.graphBox = [
    drag.box[0] - ((e.clientX - drag.x) / rect.width) * drag.box[2],
    drag.box[1] - ((e.clientY - drag.y) / rect.height) * drag.box[3],
    drag.box[2],
    drag.box[3],
  ];
  updateBox();
});
$("graph").addEventListener("pointerup", () => (drag = null));
$("graph").addEventListener("pointercancel", () => (drag = null));
$("graph").addEventListener(
  "wheel",
  (e) => {
    e.preventDefault();
    zoom(e.deltaY > 0 ? 1.15 : 0.87);
  },
  { passive: false },
);
async function history() {
  const selected = $("experiment-filter").value;
  const experiments = await api("/api/experiments");
  const all = el("option", "Todos");
  all.value = "";
  $("experiment-filter").replaceChildren(
    all,
    ...experiments.map((e) => {
      const n = el("option", `${e.name} · ${e.id}`);
      n.value = e.id;
      return n;
    }),
  );
  $("experiment-filter").value = selected;
  const runs = await api({
    path: "/api/runs",
    params: {
      ...(selected ? { experiment: selected } : {}),
      ...(state.historyBefore ? { before: state.historyBefore } : {}),
      limit: 100,
    },
  });
  state.historyLast = runs.length ? runs[runs.length - 1].id : null;
  $("history-older").disabled = runs.length < 100;
  $("history-page-note").textContent =
    `${runs.length} runs nesta página (até 100).`;
  $("history").replaceChildren();
  for (const r of runs) {
    const row = el("tr"),
      cell = el("td"),
      checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedRuns.has(r.id);
    checkbox.setAttribute("aria-label", "Comparar run " + r.id);
    checkbox.onchange = () =>
      checkbox.checked
        ? state.selectedRuns.add(r.id)
        : state.selectedRuns.delete(r.id);
    cell.append(checkbox);
    row.append(cell);
    for (const value of [
      r.id,
      r.page + (r.synthetic ? " (sintética)" : ""),
      r.config.model,
      `${r.variant_id} / ${r.mode}`,
      r.status,
    ])
      row.append(el("td", value));
    const open = el("button", "Abrir", "secondary");
    open.onclick = () =>
      openRun(r.id).catch((e) => showMessage(e.message, true));
    const td = el("td");
    td.append(open);
    row.append(td);
    $("history").append(row);
  }
  exportsFor(selected);
  await refreshCampaigns();
}
async function refreshCampaigns() {
  if (state.campaignRefreshing) return;
  state.campaignRefreshing = true;
  const evalMode = evaluation();
  try {
    const campaigns = await api("/api/campaigns");
    if (evalMode !== evaluation()) return;
    state.campaignIds = new Set(campaigns.map((c) => c.id));
    const selected = $("campaign-filter").value;
    $("campaign-filter").replaceChildren(
      ...campaigns.map((c) => {
        const option = el("option", `${c.name} · ${c.id}`);
        option.value = c.id;
        return option;
      }),
    );
    if (state.campaignIds.has(selected)) $("campaign-filter").value = selected;
    const id = $("campaign-filter").value;
    $("campaign-jobs").replaceChildren();
    $("campaign-metrics").replaceChildren();
    $("campaign-attempts").textContent = "";
    $("campaign-progress").value = 0;
    if (!id) {
      $("campaign-status").textContent =
        "Nenhuma campanha disponível neste modo.";
      return;
    }
    const data = await api({ path: "/api/campaign", params: { id } });
    if (evalMode !== evaluation() || id !== $("campaign-filter").value) return;
    const s = data.states;
    const count = (...keys) =>
      keys.reduce((sum, key) => sum + (s[key] || 0), 0);
    const finished = count(
      "completed",
      "cached",
      "invalid",
      "error",
      "source_error",
    );
    $("campaign-status").textContent =
      `${data.coordinator_active ? "Executor ativo" : "Executor parado"} · ${finished}/${data.total_pages} páginas com tentativa encerrada · SQLite ${data.journal_mode.toUpperCase()} · ${new Date().toLocaleTimeString()}`;
    $("campaign-progress").max = Math.max(1, data.total_pages);
    $("campaign-progress").value = finished;
    for (const [label, value] of [
      ["Pendentes", count("pending", "queued")],
      ["Preparadas sem inferência", count("prepared")],
      ["Em execução", count("preparing", "running")],
      ["Concluídas / cache", count("completed", "cached")],
      ["Validação inválida", count("invalid")],
      ["Erros", count("error", "source_error")],
    ]) {
      const card = el("div", undefined, "metric");
      card.append(el("strong", value), el("span", label));
      $("campaign-metrics").append(card);
    }
    const table = el("table"),
      head = el("thead"),
      titles = el("tr"),
      body = el("tbody");
    for (const title of [
      "Página recente",
      "Geração v2",
      "Status",
      "Tentativas",
      "Run",
    ])
      titles.append(el("th", title));
    head.append(titles);
    for (const job of data.recent_jobs) {
      const row = el("tr");
      for (const value of [
        `${job.documento}:${job.pagina_num}`,
        job.generation_id,
        job.status,
        job.attempts,
      ])
        row.append(el("td", value));
      const cell = el("td");
      if (job.run_id) {
        const open = el("button", `Abrir run ${job.run_id}`, "secondary");
        open.onclick = () =>
          openRun(job.run_id).catch((e) => showMessage(e.message, true));
        cell.append(open);
      }
      row.append(cell);
      body.append(row);
    }
    table.append(head, body);
    $("campaign-jobs").append(table);
    $("campaign-attempts").textContent = json(data.recent_attempts);
    $("resume").disabled = state.campaignIds.has($("experiment-filter").value);
    exportsFor($("experiment-filter").value);
  } finally {
    state.campaignRefreshing = false;
  }
}
function exportsFor(exp) {
  $("exports").replaceChildren();
  if (!exp) return;
  for (const [label, path, format] of [
    ["JSONL + revisões", "/api/export", "jsonl"],
    ["Markdown", "/api/report", "markdown"],
    ["CSV", "/api/report", "csv"],
  ]) {
    if (path === "/api/export" && state.campaignIds.has(exp)) continue;
    const a = el("a", label);
    a.href = query(path, { experiment: exp, format });
    a.download = "";
    $("exports").append(a);
  }
}
async function compare() {
  const result = await api("/api/compare", { runs: [...state.selectedRuns] });
  $("comparison").replaceChildren();
  if (result.source_differences)
    $("comparison").append(
      el(
        "div",
        "Snapshots de fonte diferentes: confira geração, OCR e contexto antes de interpretar diferenças.",
        "warning",
      ),
    );
  const cols = el("div", undefined, "comparison-columns");
  for (const run of result.runs) {
    const card = el("div", undefined, "card");
    const r = run.result || {},
      v = run.validation;
    card.append(
      el("h3", `Run ${run.id} · ${run.config.model}`),
      el("p", `${run.prepared.variant.id} · ${run.prepared.mode}`),
      el(
        "p",
        `Estrutura: ${v?.structural_valid} · evidência: ${v?.source_valid}`,
      ),
      el(
        "p",
        `Tokens entrada / saída total: ${r.usage?.input_tokens ?? "—"} / ${r.usage?.output_tokens ?? "—"} · Raciocínio incluído: ${r.usage?.reasoning_tokens ?? "—"} · Limite: ${run.config.max_output_tokens} · ${r.latency_seconds?.toFixed(2) ?? "—"} s · USD ${run.cost_estimate?.cost_usd?.toFixed(6) ?? "—"}`,
      ),
      el("p", costNote(run), "muted"),
    );
    if (v?.evidence_counts)
      card.append(
        el(
          "p",
          `${v.evidence_counts.exact} citações exatas · ${v.evidence_counts.normalized + v.evidence_counts.fuzzy} aproximadas · ${v.version}`,
          "muted small",
        ),
      );
    const list = el("ul");
    for (const c of v?.parsed?.concepts || []) {
      const item = el("li", c.label + " — " + c.meaning);
      if (c.model_confidence) item.append(el("p", confidenceLabel(c), "small"));
      list.append(item);
    }
    card.append(list);
    for (const warning of [
      ...(v?.warnings || []),
      ...(v?.structural_errors || []),
      ...(v?.source_errors || []),
    ])
      card.append(el("div", warning, "warning"));
    cols.append(card);
  }
  $("comparison").append(cols);
  for (const pair of result.pairs) {
    const section = el("section");
    section.append(el("h3", `Run ${pair.left} × ${pair.right}`));
    if (pair.error) {
      section.append(el("p", pair.error));
    } else {
      for (const [key, label] of [
        ["matched", "Mesma descrição"],
        ["uncertain_correspondences", "Correspondências incertas"],
        ["unique_left", "Somente à esquerda"],
        ["unique_right", "Somente à direita"],
        ["relations_only_left", "Relações somente à esquerda"],
        ["relations_only_right", "Relações somente à direita"],
        ["evidence_differences", "Diferenças de evidência"],
        ["relation_evidence_differences", "Diferenças nas relações"],
        [
          "model_confidence_differences",
          "Confiança autodeclarada · sem calibração entre modelos",
        ],
      ]) {
        const details = el("details");
        details.open = key === "uncertain_correspondences";
        details.append(
          el("summary", `${label} · ${pair[key].length}`),
          el("pre", json(pair[key])),
        );
        section.append(details);
      }
    }
    $("comparison").append(section);
  }
  tab("comparison");
}
// All documentary/model strings are rendered with textContent, never innerHTML.
document
  .querySelectorAll("[data-tab]")
  .forEach((b) => (b.onclick = () => tab(b.dataset.tab)));
document
  .querySelectorAll(".controls input,.controls select,.controls textarea")
  .forEach((n) => n.addEventListener("input", invalidate));
$("provider").addEventListener("change", providerDefaults);
for (const id of ["model", "base-url"])
  $(id).addEventListener("input", updatePrices);
$("prices").addEventListener("input", () => {
  ++state.pricingRequest;
  state.pricingPending = false;
  $("price-note").textContent = $("prices").value.trim()
    ? "Tarifa personalizada para este perfil. Use {} para desativar a estimativa."
    : "Campo vazio: a tarifa cadastrada será usada ao preparar o prompt.";
});
action("reset-prices", updatePrices);
$("preview-run").onchange = () => showPreview(Number($("preview-run").value));
$("bbox-scale").onchange = drawBox;
$("graph-filter").oninput = renderGraph;
for (const id of ["collection", "category", "kind-filter", "work-filter"])
  $(id).addEventListener("input", populateCatalog);
action("find-generations", async () => {
  const rows = await api({
    path: "/api/generations",
    params: { page: $("page").value.trim() },
  });
  const automatic = el("option", "Automática: current → shadow elegível");
  automatic.value = "";
  $("generation").replaceChildren(
    automatic,
    ...rows.map((r) => {
      const option = el(
        "option",
        `${r.id} · run ${r.run_id} · ${r.is_current ? "current" : "shadow"} · ${r.status}`,
      );
      option.value = r.id;
      option.disabled =
        r.tainted_by_page !== null ||
        !["valid", "metadata_pending"].includes(r.status);
      return option;
    }),
  );
  showMessage(`${rows.length} gerações v2 encontradas.`);
});
action("load-page", async () => {
  const body = { page: $("page").value.trim() };
  if ($("generation").value) body.generation_id = Number($("generation").value);
  const c = await api("/api/load", body);
  addCase(c, true);
  tab("source");
  showMessage("Fonte carregada · " + c.page + " · " + c.snapshot.source.scope);
});
action("load-demo", async () => {
  const c = await api("/api/load", { demo: true });
  addCase(c, true);
  $("provider").value = "fixture";
  providerDefaults();
  state.profiles = [];
  renderProfiles();
  tab("source");
  showMessage(
    "Demo sintética carregada. Nenhuma citação histórica autenticada e nenhuma chamada de modelo.",
  );
});
action("load-benchmark", async () => {
  if (!$("benchmark").value)
    throw new Error("Nenhuma amostra corresponde aos filtros.");
  const c = await api("/api/load", { benchmark_id: $("benchmark").value });
  addCase(c, true);
  tab("source");
  showMessage("Snapshot congelado do benchmark carregado.");
});
action("add-pages", async () => {
  for (const page of $("batch-pages")
    .value.split(/[\s,]+/)
    .filter(Boolean)) {
    const c = await api("/api/load", { page });
    addCase(c);
  }
  showMessage("Páginas adicionadas ao lote.");
});
function renderProfiles() {
  $("model-list").replaceChildren();
  state.profiles.forEach((p, i) => {
    const row = el("div", undefined, "case-row");
    const remove = el("button", "Remover", "secondary");
    remove.onclick = () => {
      state.profiles.splice(i, 1);
      renderProfiles();
      invalidate();
    };
    row.append(el("span", p.provider + " / " + p.model), remove);
    $("model-list").append(row);
  });
}
action("add-model", async () => {
  state.profiles.push(profile());
  renderProfiles();
  invalidate();
});
action("preview", preview);
action("execute", async () => {
  if (!state.experiment) throw new Error("Prepare o prompt antes de executar.");
  await execute(state.experiment);
});
action("refresh-history", history);
$("experiment-filter").onchange = () => {
  state.historyBefore = null;
  history().catch((e) => showMessage(e.message, true));
};
action("history-recent", async () => {
  state.historyBefore = null;
  await history();
});
action("history-older", async () => {
  state.historyBefore = state.historyLast;
  await history();
});
action("refresh-campaigns", refreshCampaigns);
$("campaign-filter").onchange = () =>
  refreshCampaigns().catch((e) => showMessage(e.message, true));
setInterval(() => {
  if (!document.hidden && $("tab-history").classList.contains("active"))
    refreshCampaigns().catch((e) => {
      $("campaign-status").textContent = e.message;
    });
}, 5000);
action("compare", compare);
action("compare-history", compare);
action("zoom-in", async () => zoom(0.8));
action("zoom-out", async () => zoom(1.25));
action("graph-reset", async () => {
  state.graphBox = [...state.graphDefault];
  updateBox();
});
action("save-review", async () => {
  if (!state.currentRun) throw new Error("Abra uma execução.");
  await api("/api/review", {
    run_id: state.currentRun.id,
    object_id: $("review-target").value,
    decision: $("review-decision").value,
    dimension: $("review-dimension").value,
    comment: $("review-comment").value,
  });
  state.currentRun = await api({
    path: "/api/run",
    params: { id: state.currentRun.id },
  });
  renderReviews();
  showMessage(
    "Revisão salva separadamente. O resultado original foi preservado.",
  );
});
action("revalidate", async () => {
  await api("/api/validate", { run_id: state.currentRun.id });
  await openRun(state.currentRun.id);
  showMessage("Nova validação anexada ao histórico.");
});
action("show-report", async () => {
  const exp = $("experiment-filter").value;
  if (!exp) throw new Error("Selecione um experimento.");
  $("report").textContent = json(
    await api({ path: "/api/report", params: { experiment: exp } }),
  );
});
action("resume", async () => {
  const exp = $("experiment-filter").value;
  if (!exp) throw new Error("Selecione um experimento.");
  await execute(exp, true);
});
$("evaluation").onchange = async () => {
  invalidate();
  state.cases.clear();
  state.currentCase = null;
  state.currentRun = null;
  state.selectedRuns.clear();
  state.previews = [];
  $("preview-run").replaceChildren();
  $("prompt-meta").replaceChildren();
  renderCases();
  $("segments").replaceChildren();
  $("blocks").replaceChildren();
  $("source-title").textContent = "Selecione uma página";
  $("source-meta").textContent = "";
  $("metadata").textContent = "";
  $("source-warnings").replaceChildren();
  $("page-image").hidden = true;
  $("page-image").removeAttribute("src");
  $("bbox-highlight").hidden = true;
  for (const id of [
    "comparison",
    "extraction-list",
    "evidence-detail",
    "object-detail",
    "result-json",
    "raw-response",
    "graph",
    "system-prompt",
    "user-payload",
    "request-config",
    "report",
    "reviews",
    "run-summary",
    "run-cost-note",
    "validation",
    "block-detail",
    "source-scope",
    "facsimile-status",
    "campaign-status",
    "campaign-metrics",
    "campaign-jobs",
    "campaign-attempts",
  ])
    $(id).replaceChildren();
  $("experiment-filter").value = "";
  $("campaign-filter").replaceChildren();
  $("campaign-progress").value = 0;
  state.historyBefore = null;
  await loadCatalog();
  await history();
  showMessage(
    evaluation()
      ? "Modo avaliação: holdout liberado explicitamente."
      : "Modo calibração: holdout oculto.",
  );
};
(async () => {
  const data = await api("/api/bootstrap");
  state.csrf = data.csrf;
  state.defaults = data.providers;
  state.pricing = data.pricing;
  if (Number.isInteger(data.default_max_output_tokens))
    $("max-tokens").value = data.default_max_output_tokens;
  $("variant").replaceChildren(
    ...data.variants.map((v) => {
      const o = el(
        "option",
        v.id + (v.few_shot ? " · com exemplo" : " · sem exemplo"),
      );
      o.value = v.id;
      return o;
    }),
  );
  await loadCatalog();
  await updatePrices();
  await history();
  showMessage(
    `Laboratório pronto · ${data.summary_db_available ? "banco v2 disponível" : "banco v2 indisponível; demo e snapshots continuam disponíveis"} · nenhuma inferência iniciada.`,
  );
})().catch((error) => showMessage(error.message, true));
