const VERSION = "0.1.0";
const SCHEMA_VERSION = "0.2";
const REDACT_FIELDS = new Set([
  "authorization", "api_key", "apikey", "password", "secret", "token",
  "access_token", "refresh_token", "cookie", "set-cookie", "x-api-key"
]);

const state = {
  config: null,
  installed: false,
  originals: {},
  buffer: [],
  flushTimer: null,
  mutationObserver: null,
  listeners: [],
  traceId: null,
  runId: null,
  events: []
};

function uid(prefix) {
  const c = globalThis.crypto;
  if (c?.randomUUID) return `${prefix}_${c.randomUUID().replaceAll("-", "")}`;
  return `${prefix}_${Date.now().toString(16)}${Math.random().toString(16).slice(2)}`;
}

function stableStringify(value) {
  const seen = new WeakSet();
  const normalize = (v) => {
    if (v === undefined) return null;
    if (v === null || typeof v !== "object") return v;
    if (seen.has(v)) return "[Circular]";
    seen.add(v);
    if (Array.isArray(v)) return v.map(normalize);
    return Object.keys(v).sort().reduce((o, k) => { o[k] = normalize(v[k]); return o; }, {});
  };
  return JSON.stringify(normalize(value));
}

async function sha256(value) {
  const text = typeof value === "string" ? value : stableStringify(value);
  if (globalThis.crypto?.subtle) {
    const data = new TextEncoder().encode(text);
    const digest = await globalThis.crypto.subtle.digest("SHA-256", data);
    return `sha256:${[...new Uint8Array(digest)].map(b => b.toString(16).padStart(2, "0")).join("")}`;
  }
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
  return `fnv1a:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function redactString(s) {
  return String(s)
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, "***REDACTED***")
    .replace(/AKIA[0-9A-Z]{16}/g, "***REDACTED***")
    .replace(/(bearer\s+)[A-Za-z0-9._-]+/gi, "$1***REDACTED***");
}

function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      out[k] = REDACT_FIELDS.has(k.toLowerCase()) ? "***REDACTED***" : redact(v);
    }
    return out;
  }
  return typeof value === "string" ? redactString(value) : value;
}

function normalizeUrl(raw) {
  try {
    const u = new URL(String(raw), globalThis.location?.href);
    return `${u.protocol}//${u.host}${u.pathname.replace(/\/$/, "") || "/"}`;
  } catch { return String(raw || ""); }
}

function defaultConfig(options = {}) {
  return {
    project: "default",
    environment: "dev",
    endpoint: null,
    exporter: "http",
    headers: {},
    batchSize: 10,
    flushIntervalMs: 2000,
    capturePrompt: false,
    captureResponse: false,
    captureArguments: false,
    captureResult: false,
    captureDomText: false,
    captureDomHtml: false,
    captureHash: true,
    redact: true,
    maxBodyBytes: 256000,
    maxDomChars: 20000,
    domDebounceMs: 750,
    actor: {},
    tenantId: null,
    sessionId: null,
    conversationId: null,
    runId: null,
    turnId: null,
    agentId: null,
    purposeId: null,
    agentHint: "browser-agent",
    instrumentFetch: true,
    instrumentXHR: true,
    instrumentWebSocket: true,
    instrumentDOM: true,
    instrumentActions: true,
    instrumentNavigation: true,
    includeUrlPatterns: [],
    excludeUrlPatterns: [],
    debug: false,
    ...options
  };
}

function runtimeMetadata() {
  return {
    language: "javascript",
    sdk: "senda_argus_browser_hooks",
    sdk_version: VERSION,
    user_agent: globalThis.navigator?.userAgent,
    page_url: globalThis.location?.href,
    page_title: globalThis.document?.title
  };
}

async function deriveAgentId(source = {}) {
  if (state.config.agentId) return state.config.agentId;
  const h = await sha256({
    project: state.config.project,
    environment: state.config.environment,
    sdk: source.sdk || "browser",
    agent_hint: state.config.agentHint
  });
  return `agent_${h.split(":")[1].slice(0, 16)}`;
}

async function emit(eventType, {data = {}, source = {}, status = null, latencyMs = null, error = null, purposeId = null, parentSpanId = null} = {}) {
  if (!state.config) return null;
  state.traceId ||= uid("trace");
  state.runId ||= state.config.runId || uid("run");
  let event = {
    schema_version: SCHEMA_VERSION,
    event_id: uid("evt"),
    trace_id: state.traceId,
    span_id: uid("span"),
    parent_span_id: parentSpanId,
    timestamp: new Date().toISOString(),
    project: state.config.project,
    environment: state.config.environment,
    event_type: eventType,
    tenant_id: state.config.tenantId,
    session_id: state.config.sessionId,
    conversation_id: state.config.conversationId,
    run_id: state.runId,
    turn_id: state.config.turnId,
    agent_id: await deriveAgentId(source),
    purpose_id: purposeId || state.config.purposeId,
    source,
    actor: state.config.actor || {},
    data,
    security: {redacted: false},
    status,
    latency_ms: latencyMs,
    error,
    runtime: runtimeMetadata()
  };
  if (state.config.redact) {
    event = redact(event);
    event.security.redacted = true;
  }
  state.events.push(event);
  state.buffer.push(event);
  if (state.config.debug) console.debug("[SendaArgus]", eventType, event);
  if (state.buffer.length >= state.config.batchSize) void flush();
  return event;
}

async function flush() {
  if (!state.config || !state.buffer.length) return;
  const events = state.buffer.splice(0, state.buffer.length);
  try {
    if (state.config.exporter === "console") {
      console.log("[SendaArgus events]", events);
      return;
    }
    if (state.config.exporter === "memory") return;
    if (!state.config.endpoint) return;
    const body = JSON.stringify({events});
    if (globalThis.navigator?.sendBeacon && state.config.useBeacon !== false) {
      const ok = globalThis.navigator.sendBeacon(state.config.endpoint, new Blob([body], {type: "application/json"}));
      if (ok) return;
    }
    const rawFetch = state.originals.fetch || globalThis.fetch;
    await rawFetch(state.config.endpoint, {
      method: "POST",
      headers: {"content-type": "application/json", ...state.config.headers, "x-senda-argus-internal": "1"},
      body,
      keepalive: true
    });
  } catch {
    state.buffer.unshift(...events.slice(-100));
  }
}

function shouldObserveUrl(url) {
  const s = String(url || "");
  if (state.config.excludeUrlPatterns.some(p => new RegExp(p).test(s))) return false;
  if (!state.config.includeUrlPatterns.length) return true;
  return state.config.includeUrlPatterns.some(p => new RegExp(p).test(s));
}

function classifyRequest(url, body, headers = {}) {
  const u = String(url || "").toLowerCase();
  const obj = body && typeof body === "object" ? body : null;
  if (obj?.jsonrpc === "2.0" || /\/mcp(?:\/|$)|message\?sessionid|\/sse(?:\/|$)/.test(u)) return "mcp";
  if (/\/api\/(chat|generate|embed|embeddings|show|pull)/.test(u) || /:11434\//.test(u)) return "ollama";
  if (/\/(v1\/)?(chat\/completions|responses|embeddings|completions)(?:\?|$)/.test(u)) return "openai_compatible";
  if (/anthropic|\/v1\/messages/.test(u) && (headers["anthropic-version"] || obj?.messages)) return "anthropic";
  return null;
}

function parseMaybeJson(body) {
  if (body == null) return null;
  if (typeof body === "object" && !(body instanceof ArrayBuffer) && !(body instanceof Blob) && !(body instanceof FormData)) return body;
  if (typeof body !== "string") return null;
  try { return JSON.parse(body); } catch { return null; }
}

function headersObject(headers) {
  const out = {};
  try { new Headers(headers || {}).forEach((v, k) => out[k] = v); } catch {}
  return out;
}

async function llmRequestData(kind, url, body, headers) {
  const messages = body?.messages || body?.input || body?.prompt;
  const data = {
    llm: {
      provider: kind,
      operation: body?.stream ? "stream" : "request",
      model: body?.model,
      endpoint: normalizeUrl(url),
      stream: Boolean(body?.stream),
      messages_count: Array.isArray(body?.messages) ? body.messages.length : undefined,
      input_hash: state.config.captureHash ? await sha256(body) : undefined
    }
  };
  if (state.config.capturePrompt) data.llm.input = body;
  else if (state.config.captureHash && messages != null) data.llm.messages_hash = await sha256(messages);
  if (state.config.captureArguments) data.llm.headers = headers;
  return data;
}

async function mcpRequestData(url, body) {
  const method = body?.method;
  const params = body?.params || {};
  const tool = method === "tools/call" ? params.name : undefined;
  const args = method === "tools/call" ? params.arguments : params;
  const profile = {source_type: "mcp", mcp_server_url: normalizeUrl(url), tool_name: tool || method || "unknown"};
  const purposeHash = await sha256(profile);
  const purposeId = `purpose_${purposeHash.split(":")[1].slice(0, 16)}`;
  const mcp = {
    operation: method,
    server_url: normalizeUrl(url),
    tool,
    request_id: body?.id,
    purpose_id: purposeId,
    purpose_source: "mcp_data_source_hash",
    purpose_profile: profile,
    data_source_hash: await sha256(profile),
    arguments_hash: await sha256(args)
  };
  if (state.config.captureArguments) mcp.arguments = args;
  return {data: {mcp}, purposeId};
}

async function instrumentedFetch(input, init = {}) {
  const rawFetch = state.originals.fetch;
  const request = input instanceof Request ? input : null;
  const url = request?.url || input;
  const headers = headersObject(init.headers || request?.headers);
  if (headers["x-senda-argus-internal"] === "1" || !shouldObserveUrl(url)) return rawFetch(input, init);
  let rawBody = init.body;
  if (rawBody == null && request) {
    try { rawBody = await request.clone().text(); } catch {}
  }
  const body = parseMaybeJson(rawBody);
  const kind = classifyRequest(url, body, headers);
  if (!kind) return rawFetch(input, init);
  const start = performance.now();
  let requested;
  let purposeId = null;
  if (kind === "mcp") {
    const meta = await mcpRequestData(url, body);
    purposeId = meta.purposeId;
    requested = await emit(body?.method === "tools/call" ? "mcp.tool_call.requested" : "mcp.requested", {
      source: {component: "instrumentor", sdk: "browser_fetch", operation: body?.method}, data: meta.data, status: "start", purposeId
    });
  } else {
    requested = await emit("llm.request", {
      source: {component: "instrumentor", sdk: "browser_fetch", provider: kind},
      data: await llmRequestData(kind, url, body, headers), status: "start"
    });
  }
  try {
    const response = await rawFetch(input, init);
    const latencyMs = Math.round(performance.now() - start);
    let responsePayload = null;
    try {
      const text = await response.clone().text();
      if (text.length <= state.config.maxBodyBytes) responsePayload = parseMaybeJson(text) ?? text;
    } catch {}
    if (kind === "mcp") {
      const resultHash = state.config.captureHash ? await sha256(responsePayload) : undefined;
      const data = (await mcpRequestData(url, body)).data;
      data.mcp.result_hash = resultHash;
      if (state.config.captureResult) data.mcp.result = responsePayload;
      await emit(body?.method === "tools/call" ? "mcp.tool_call.completed" : "mcp.completed", {
        source: {component: "instrumentor", sdk: "browser_fetch", operation: body?.method}, data,
        status: response.ok ? "success" : "error", latencyMs, purposeId, parentSpanId: requested?.span_id,
        error: response.ok ? null : {type: "HTTPError", message: `HTTP ${response.status}`}
      });
    } else {
      const out = {llm: {provider: kind, endpoint: normalizeUrl(url), status_code: response.status}};
      if (state.config.captureResponse) out.llm.output = responsePayload;
      if (state.config.captureHash) out.llm.output_hash = await sha256(responsePayload);
      await emit(response.ok ? "llm.response" : "llm.error", {
        source: {component: "instrumentor", sdk: "browser_fetch", provider: kind}, data: out,
        status: response.ok ? "success" : "error", latencyMs, parentSpanId: requested?.span_id,
        error: response.ok ? null : {type: "HTTPError", message: `HTTP ${response.status}`}
      });
    }
    return response;
  } catch (e) {
    const latencyMs = Math.round(performance.now() - start);
    await emit(kind === "mcp" ? "mcp.failed" : "llm.error", {
      source: {component: "instrumentor", sdk: "browser_fetch", provider: kind},
      data: kind === "mcp" ? (await mcpRequestData(url, body)).data : await llmRequestData(kind, url, body, headers),
      status: "error", latencyMs, purposeId, parentSpanId: requested?.span_id,
      error: {type: e?.name || "Error", message: e?.message || String(e)}
    });
    throw e;
  }
}

function patchFetch() {
  if (!globalThis.fetch || state.originals.fetch) return;
  state.originals.fetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = instrumentedFetch;
}

function patchXHR() {
  if (!globalThis.XMLHttpRequest || state.originals.xhrOpen) return;
  const p = XMLHttpRequest.prototype;
  state.originals.xhrOpen = p.open;
  state.originals.xhrSend = p.send;
  state.originals.xhrSetRequestHeader = p.setRequestHeader;
  p.open = function(method, url, ...rest) { this.__senda = {method, url, headers: {}}; return state.originals.xhrOpen.call(this, method, url, ...rest); };
  p.setRequestHeader = function(k, v) { if (this.__senda) this.__senda.headers[String(k).toLowerCase()] = v; return state.originals.xhrSetRequestHeader.call(this, k, v); };
  p.send = function(body) {
    const meta = this.__senda;
    const obj = parseMaybeJson(body);
    const kind = classifyRequest(meta?.url, obj, meta?.headers || {});
    const start = performance.now();
    if (kind) {
      void (async () => {
        const req = kind === "mcp" ? await mcpRequestData(meta.url, obj) : {data: await llmRequestData(kind, meta.url, obj, meta.headers), purposeId: null};
        const startEvent = await emit(kind === "mcp" ? "mcp.requested" : "llm.request", {source: {component: "instrumentor", sdk: "browser_xhr", provider: kind}, data: req.data, status: "start", purposeId: req.purposeId});
        this.addEventListener("loadend", async () => {
          const responsePayload = parseMaybeJson(this.responseText) ?? this.responseText;
          const data = kind === "mcp" ? (await mcpRequestData(meta.url, obj)).data : {llm: {provider: kind, endpoint: normalizeUrl(meta.url)}};
          if (kind === "mcp") data.mcp.result_hash = await sha256(responsePayload);
          else data.llm.output_hash = await sha256(responsePayload);
          await emit(kind === "mcp" ? "mcp.completed" : (this.status < 400 ? "llm.response" : "llm.error"), {source: {component: "instrumentor", sdk: "browser_xhr", provider: kind}, data, status: this.status < 400 ? "success" : "error", latencyMs: Math.round(performance.now() - start), parentSpanId: startEvent?.span_id, purposeId: req.purposeId});
        }, {once: true});
      })();
    }
    return state.originals.xhrSend.call(this, body);
  };
}

function patchWebSocket() {
  if (!globalThis.WebSocket || state.originals.WebSocket) return;
  const Native = globalThis.WebSocket;
  state.originals.WebSocket = Native;
  globalThis.WebSocket = class SendaWebSocket extends Native {
    constructor(url, protocols) {
      super(url, protocols);
      this.__sendaUrl = String(url);
      this.addEventListener("message", e => observeSocketMessage("received", this.__sendaUrl, e.data));
    }
    send(data) { void observeSocketMessage("sent", this.__sendaUrl, data); return super.send(data); }
  };
}

async function observeSocketMessage(direction, url, raw) {
  const body = parseMaybeJson(raw);
  if (body?.jsonrpc !== "2.0" && !/mcp/i.test(url)) return;
  const meta = await mcpRequestData(url, body);
  await emit(`mcp.websocket.${direction}`, {source: {component: "instrumentor", sdk: "browser_websocket"}, data: meta.data, status: "success", purposeId: meta.purposeId});
}

function elementDescriptor(el) {
  if (!(el instanceof Element)) return {};
  return {
    tag: el.tagName.toLowerCase(),
    role: el.getAttribute("role"),
    type: el.getAttribute("type"),
    name: el.getAttribute("name"),
    id: el.id || undefined,
    aria_label: el.getAttribute("aria-label"),
    text: (el.innerText || el.textContent || "").trim().slice(0, 300),
    href: el instanceof HTMLAnchorElement ? normalizeUrl(el.href) : undefined,
    selector_hint: selectorHint(el)
  };
}

function selectorHint(el) {
  if (el.id) return `#${CSS.escape(el.id)}`;
  const parts = [];
  let cur = el;
  while (cur && parts.length < 4 && cur.nodeType === 1) {
    let p = cur.tagName.toLowerCase();
    if (cur.classList.length) p += `.${[...cur.classList].slice(0, 2).map(c => CSS.escape(c)).join(".")}`;
    parts.unshift(p); cur = cur.parentElement;
  }
  return parts.join(" > ");
}

async function captureDom(reason = "snapshot") {
  if (!globalThis.document?.documentElement) return;
  const root = document.body || document.documentElement;
  const text = (root.innerText || "").replace(/\s+/g, " ").trim().slice(0, state.config.maxDomChars);
  const interactive = [...document.querySelectorAll("a,button,input,select,textarea,[role=button],[role=link],[contenteditable=true]")]
    .slice(0, 500).map(elementDescriptor);
  const snapshot = {
    reason,
    page: {url: location.href, title: document.title, lang: document.documentElement.lang},
    interactive_count: interactive.length,
    interactive_hash: state.config.captureHash ? await sha256(interactive) : undefined,
    text_hash: state.config.captureHash ? await sha256(text) : undefined
  };
  if (state.config.captureDomText) snapshot.text = text;
  if (state.config.captureDomHtml) snapshot.html = root.outerHTML.slice(0, state.config.maxDomChars);
  if (state.config.captureArguments) snapshot.interactive_elements = interactive;
  await emit("rag.context.collected", {source: {component: "instrumentor", sdk: "browser_dom", framework: "dom"}, data: {rag: snapshot}, status: "success"});
}

function patchDOM() {
  if (!globalThis.MutationObserver || state.mutationObserver) return;
  let timer;
  state.mutationObserver = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => void captureDom("mutation"), state.config.domDebounceMs);
  });
  state.mutationObserver.observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: false});
  void captureDom("initial");
}

function addListener(target, type, fn, options) {
  if (!target || typeof target.addEventListener !== "function") return;
  target.addEventListener(type, fn, options); state.listeners.push([target, type, fn, options]);
}

function patchActions() {
  const action = async (type, e) => {
    const el = e.target instanceof Element ? e.target.closest("a,button,input,select,textarea,[role=button],[role=link],[contenteditable=true]") || e.target : null;
    if (!el) return;
    const d = elementDescriptor(el);
    if (type === "input" && /password/i.test(d.type || "")) d.value = "***REDACTED***";
    else if (type === "input") d.value_hash = await sha256(el.value || el.textContent || "");
    await emit(`browser.action.${type}`, {source: {component: "instrumentor", sdk: "browser_dom"}, data: {browser: {action: type, element: d, page_url: location.href}}, status: "success"});
  };
  addListener(document, "click", e => void action("click", e), true);
  addListener(document, "input", e => void action("input", e), true);
  addListener(document, "change", e => void action("change", e), true);
  addListener(document, "submit", e => void action("submit", e), true);
}

function patchNavigation() {
  const observe = kind => void emit("browser.navigation", {source: {component: "instrumentor", sdk: "browser_navigation"}, data: {browser: {kind, url: location.href, title: document.title}}, status: "success"});
  for (const name of ["pushState", "replaceState"]) {
    state.originals[name] = history[name];
    history[name] = function(...args) { const r = state.originals[name].apply(this, args); observe(name); return r; };
  }
  addListener(globalThis, "popstate", () => observe("popstate"));
  addListener(globalThis, "hashchange", () => observe("hashchange"));
}

function register(options = {}) {
  if (state.installed) return api;
  state.config = defaultConfig(options);
  state.installed = true;
  if (state.config.instrumentFetch) patchFetch();
  if (state.config.instrumentXHR) patchXHR();
  if (state.config.instrumentWebSocket) patchWebSocket();
  if (state.config.instrumentDOM && globalThis.document) patchDOM();
  if (state.config.instrumentActions && globalThis.document) patchActions();
  if (state.config.instrumentNavigation && globalThis.history) patchNavigation();
  state.flushTimer = setInterval(() => void flush(), state.config.flushIntervalMs);
  state.flushTimer?.unref?.();
  addListener(globalThis, "pagehide", () => void flush());
  addListener(globalThis, "beforeunload", () => void flush());
  void emit("browser.agent.instrumented", {source: {component: "runtime", sdk: "senda_argus_browser_hooks"}, data: {instrumentors: {fetch: !!state.originals.fetch, xhr: !!state.originals.xhrOpen, websocket: !!state.originals.WebSocket, dom: !!state.mutationObserver}}, status: "success"});
  return api;
}

function unregister() {
  if (!state.installed) return;
  if (state.originals.fetch) globalThis.fetch = state.originals.fetch;
  if (state.originals.xhrOpen) {
    XMLHttpRequest.prototype.open = state.originals.xhrOpen;
    XMLHttpRequest.prototype.send = state.originals.xhrSend;
    XMLHttpRequest.prototype.setRequestHeader = state.originals.xhrSetRequestHeader;
  }
  if (state.originals.WebSocket) globalThis.WebSocket = state.originals.WebSocket;
  for (const n of ["pushState", "replaceState"]) if (state.originals[n]) history[n] = state.originals[n];
  state.mutationObserver?.disconnect();
  for (const [t, type, fn, opt] of state.listeners) t.removeEventListener(type, fn, opt);
  state.listeners = [];
  clearInterval(state.flushTimer);
  state.installed = false;
  state.originals = {};
  state.mutationObserver = null;
  state.flushTimer = null;
}

function getEvents() { return [...state.events]; }
function clearEvents() { state.events.length = 0; }
function setContext(patch) { Object.assign(state.config, patch || {}); }
function observePageAgent(agent) {
  if (!agent || typeof agent.execute !== "function" || agent.execute.__senda_patched__) return false;
  const original = agent.execute.bind(agent);
  const wrapped = async (...args) => {
    const start = performance.now();
    const prompt = args[0];
    const ev = await emit("browser.agent.run.started", {source: {component: "integration", sdk: "page-agent"}, data: {agent: {framework: "page-agent", prompt_hash: await sha256(prompt), ...(state.config.capturePrompt ? {prompt} : {})}}, status: "start"});
    try {
      const result = await original(...args);
      await emit("browser.agent.run.completed", {source: {component: "integration", sdk: "page-agent"}, data: {agent: {framework: "page-agent", result_hash: await sha256(result), ...(state.config.captureResult ? {result} : {})}}, status: "success", latencyMs: Math.round(performance.now() - start), parentSpanId: ev?.span_id});
      return result;
    } catch (e) {
      await emit("browser.agent.run.failed", {source: {component: "integration", sdk: "page-agent"}, data: {agent: {framework: "page-agent"}}, status: "error", latencyMs: Math.round(performance.now() - start), parentSpanId: ev?.span_id, error: {type: e?.name || "Error", message: e?.message || String(e)}});
      throw e;
    }
  };
  wrapped.__senda_patched__ = true;
  agent.execute = wrapped;
  return true;
}

function autoRegisterFromScript() {
  if (!globalThis.document) return;
  const script = document.currentScript || [...document.scripts].find(s => /senda-argus-browser-hooks/.test(s.src));
  if (!script || script.dataset.auto === "false") return;
  const bool = (name, def) => script.dataset[name] == null ? def : script.dataset[name] !== "false";
  register({
    project: script.dataset.project || "default",
    environment: script.dataset.environment || "prod",
    endpoint: script.dataset.endpoint || null,
    exporter: script.dataset.exporter || (script.dataset.endpoint ? "http" : "console"),
    capturePrompt: bool("capturePrompt", false),
    captureResponse: bool("captureResponse", false),
    captureArguments: bool("captureArguments", false),
    captureResult: bool("captureResult", false),
    captureDomText: bool("captureDomText", false),
    captureDomHtml: bool("captureDomHtml", false),
    redact: bool("redact", true),
    debug: bool("debug", false)
  });
}

const api = {register, unregister, flush, emit, captureDom, observePageAgent, getEvents, clearEvents, setContext, version: VERSION};
if (typeof globalThis !== "undefined") globalThis.SendaArgus = api;
export {register, unregister, flush, emit, captureDom, observePageAgent, getEvents, clearEvents, setContext};
export default api;

if (typeof document !== "undefined") queueMicrotask(autoRegisterFromScript);
