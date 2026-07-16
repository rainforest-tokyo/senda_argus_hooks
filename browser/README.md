# Senda-Argus Browser Hooks

Senda-Argus Browser Hooks is a hook-only JavaScript observability SDK for Browser Agents, browser-side LLM calls, MCP communication, DOM-derived context, and browser action audit events.

It collects normalized execution events by instrumenting browser runtime surfaces such as `fetch`, `XMLHttpRequest`, `WebSocket`, DOM mutation observers, user interaction events, and navigation APIs. Applications do not need to add business-level `audit.event()` calls or rewrite their Browser Agent logic.

The collected events use the same `schema_version: "0.2"` event model as Senda-Argus Hooks for Python and are designed for downstream correlation, trace reconstruction, risk analysis, alerting, and visualization by external systems such as Argus.

> **Patent Notice**
> Certain concepts and techniques related to Senda-Argus, including AI agent execution trace collection, decision trace reconstruction, and runtime audit event correlation, are patent pending in Japan.
> This notice does not change the terms of the Apache License 2.0 applicable to this repository.

## Features

* Hook-only browser event collection
* One-script-tag automatic registration
* No required `audit.event()` calls in application logic
* OpenAI-compatible LLM request and response instrumentation
* Ollama HTTP API instrumentation
* Anthropic Messages API instrumentation
* MCP JSON-RPC request and response instrumentation
* MCP `tools/call` lifecycle event collection
* MCP-over-WebSocket message collection
* DOM-derived context collection as `rag.context.collected`
* Browser action collection for click, input, change, and submit events
* Browser navigation collection for History API, hash, and popstate changes
* PageAgent `execute()` run lifecycle integration
* Privacy-safe defaults using hashes and metadata instead of raw content
* Configurable prompt, response, argument, result, DOM text, and DOM HTML capture
* Redaction of common secrets and authorization values
* HTTP, console, and in-memory exporters
* Stable correlation fields such as `trace_id`, `run_id`, `agent_id`, and `purpose_id`
* Unit tests that do not require external API keys

## v0.1.0 verification summary

The v0.1.0 implementation was locally verified with Node.js test fixtures and mocked browser-compatible request surfaces.

The following checks passed:

* schema version `0.2` event generation
* automatic agent identity generation
* privacy-safe OpenAI-compatible request capture
* prompt hashing when raw prompt capture is disabled
* MCP `tools/call` request metadata collection
* MCP `purpose_id` generation
* LLM and MCP completion event generation
* redaction of common credential and token fields

The test suite completed with:

```text
3 tests passed
0 tests failed
```

No external LLM, MCP server, Ollama instance, or API key is required to run the included tests.

## Hook targets

| Target | Status | Hook approach | Test coverage | Typical events |
|---|---|---|---|---|
| OpenAI-compatible HTTP APIs | Experimental | `fetch` / XHR instrumentation | Mocked fetch test | `llm.request`, `llm.response`, `llm.error` |
| Ollama HTTP API | Experimental | `fetch` / XHR endpoint detection | Shared HTTP classifier coverage | `llm.request`, `llm.response`, `llm.error` |
| Anthropic Messages API | Experimental | `fetch` / XHR endpoint detection | Shared HTTP classifier coverage | `llm.request`, `llm.response`, `llm.error` |
| MCP JSON-RPC over HTTP | Experimental | `fetch` / XHR instrumentation | Mocked MCP request test | `mcp.requested`, `mcp.completed`, `mcp.failed` |
| MCP `tools/call` | Experimental | JSON-RPC method inspection | Mocked MCP tool test | `mcp.tool_call.requested`, `mcp.tool_call.completed`, `mcp.tool_call.failed` |
| MCP over WebSocket | Experimental | Global `WebSocket` wrapper | Implementation smoke coverage | `mcp.websocket.sent`, `mcp.websocket.received` |
| DOM context | Experimental | `MutationObserver` and explicit snapshots | Implementation smoke coverage | `rag.context.collected` |
| Browser actions | Experimental | Capturing DOM event listeners | Implementation smoke coverage | `browser.action.click`, `browser.action.input`, `browser.action.change`, `browser.action.submit` |
| Browser navigation | Experimental | History API and navigation listeners | Implementation smoke coverage | `browser.navigation` |
| PageAgent | Experimental | `agent.execute()` wrapper | Integration helper coverage | `browser.agent.run.started`, `browser.agent.run.completed`, `browser.agent.run.failed` |

Browser APIs and third-party agent libraries may change between releases. Integrations are marked experimental until compatibility has been verified across a wider set of browser, bundler, framework, and PageAgent versions.

## Architecture

Senda-Argus Browser Hooks observes Browser Agent activity at multiple runtime layers.

```text
Browser Agent / PageAgent
        |
        +-- DOM context and mutations
        +-- Browser actions
        +-- LLM HTTP requests
        +-- MCP HTTP or WebSocket messages
        +-- Navigation changes
        |
        v
Senda-Argus Browser Hooks
        |
        +-- normalize to schema 0.2
        +-- hash or capture configured content
        +-- redact sensitive values
        +-- batch events
        |
        v
Argus Collector / HTTP endpoint / console / memory
```

The SDK is designed to complement the Python version of Senda-Argus Hooks.

```text
Server-side and Python agents
  -> senda_argus_hooks

Browser-side and JavaScript agents
  -> senda_argus_browser_hooks
```

## Installation

### Script tag

Host the distribution file on your application server or CDN and add it to the page.

```html
<script
  src="https://your-cdn.example/senda-argus-browser-hooks.js"
  data-project="my-browser-agent"
  data-environment="production"
  data-endpoint="https://argus.example/v1/events">
</script>
```

The script automatically registers itself and instruments supported browser runtime surfaces.

### npm

```bash
npm install @senda/argus-browser-hooks
```

```javascript
import SendaArgus from "@senda/argus-browser-hooks";

SendaArgus.register({
  project: "my-browser-agent",
  environment: "development",
  endpoint: "https://argus.example/v1/events",
});
```

### Local development

```bash
git clone <repository-url>
cd senda_argus_browser_hooks
npm install
npm test
npm run build
```

Node.js 18 or later is required for the included development scripts and tests.

## One-line browser integration

The minimal integration is a single script tag.

```html
<script src="/senda-argus-browser-hooks.js"></script>
```

Without an endpoint, the automatic configuration uses the console exporter.

A production-style example is:

```html
<script
  src="/assets/senda-argus-browser-hooks.js"
  data-project="page-agent-console"
  data-environment="production"
  data-endpoint="https://collector.example.com/v1/events">
</script>
```

Automatic registration can be disabled when manual initialization is preferred.

```html
<script
  src="/assets/senda-argus-browser-hooks.js"
  data-auto="false">
</script>
```

```javascript
SendaArgus.register({
  project: "page-agent-console",
  environment: "production",
  endpoint: "https://collector.example.com/v1/events",
});
```

## Script tag configuration

The automatic loader reads `data-*` attributes from the script element.

| Attribute | Default | Description |
|---|---:|---|
| `data-auto` | `true` | Set to `false` to disable automatic registration |
| `data-project` | `default` | Project identifier |
| `data-environment` | `prod` | Environment identifier |
| `data-endpoint` | none | HTTP collector endpoint |
| `data-exporter` | `http` when endpoint exists, otherwise `console` | Export mode |
| `data-capture-prompt` | `false` | Capture raw LLM and agent prompt content |
| `data-capture-response` | `false` | Capture raw LLM response content |
| `data-capture-arguments` | `false` | Capture raw MCP arguments, headers, and interactive DOM descriptors |
| `data-capture-result` | `false` | Capture raw MCP and PageAgent results |
| `data-capture-dom-text` | `false` | Capture normalized visible DOM text |
| `data-capture-dom-html` | `false` | Capture DOM HTML up to the configured limit |
| `data-redact` | `true` | Apply redaction to emitted events |
| `data-debug` | `false` | Print emitted events to the browser console |

Example with explicit content capture:

```html
<script
  src="/senda-argus-browser-hooks.js"
  data-project="browser-agent-lab"
  data-environment="development"
  data-endpoint="https://argus.example/v1/events"
  data-capture-prompt="true"
  data-capture-response="true"
  data-capture-arguments="true"
  data-capture-result="true"
  data-capture-dom-text="true"
  data-redact="true">
</script>
```

Raw content capture should be enabled only after reviewing the privacy and security implications.

## JavaScript API

### Register hooks

```javascript
import SendaArgus from "@senda/argus-browser-hooks";

SendaArgus.register({
  project: "browser-agent-app",
  environment: "dev",
  endpoint: "https://argus.example/v1/events",
  exporter: "http",

  capturePrompt: false,
  captureResponse: false,
  captureArguments: false,
  captureResult: false,
  captureDomText: false,
  captureDomHtml: false,
  captureHash: true,
  redact: true,

  instrumentFetch: true,
  instrumentXHR: true,
  instrumentWebSocket: true,
  instrumentDOM: true,
  instrumentActions: true,
  instrumentNavigation: true,
});
```

### Unregister hooks

```javascript
SendaArgus.unregister();
```

This restores the original browser methods where supported, disconnects the DOM observer, removes registered event listeners, and stops the periodic flush timer.

### Flush buffered events

```javascript
await SendaArgus.flush();
```

The SDK also attempts to flush events on `pagehide` and `beforeunload`.

### Capture an explicit DOM snapshot

```javascript
await SendaArgus.captureDom("before-agent-run");
```

### Update runtime context

```javascript
SendaArgus.setContext({
  sessionId: "session-123",
  conversationId: "conversation-456",
  turnId: "turn-7",
  actor: {
    type: "user",
    id: "analyst-001",
  },
});
```

### Inspect in-memory events

```javascript
const events = SendaArgus.getEvents();
console.log(events);

SendaArgus.clearEvents();
```

### Emit a custom event

Hook-only collection is the recommended default. A custom event API is available for application-specific cases.

```javascript
await SendaArgus.emit("custom.browser.event", {
  source: {
    component: "application",
    sdk: "custom",
  },
  data: {
    message: "Example custom event",
  },
  status: "success",
});
```

## PageAgent integration

HTTP instrumentation can observe PageAgent LLM traffic when it passes through supported `fetch` or XHR endpoints. To also capture the PageAgent run lifecycle, wrap the agent instance with `observePageAgent()`.

```javascript
import { PageAgent } from "page-agent";
import SendaArgus from "@senda/argus-browser-hooks";

SendaArgus.register({
  project: "page-agent-demo",
  environment: "dev",
  endpoint: "https://argus.example/v1/events",
  capturePrompt: false,
  captureResult: false,
  redact: true,
});

const agent = new PageAgent({
  model: "qwen3.5-plus",
  baseURL: "http://127.0.0.1:11434/v1",
  apiKey: "ollama",
});

SendaArgus.observePageAgent(agent);

await agent.execute("Show unresolved security alerts.");
```

Typical PageAgent events include:

* `browser.agent.instrumented`
* `browser.agent.run.started`
* `browser.agent.run.completed`
* `browser.agent.run.failed`

The current PageAgent integration wraps `agent.execute()`. It does not depend on undocumented PageAgent internal classes. This improves compatibility but means that internal planning steps may only be visible through LLM, MCP, DOM, and browser action events.

## Browser-side LLM instrumentation

The SDK classifies supported browser requests by URL, request body, and selected headers.

### OpenAI-compatible APIs

Recognized endpoint patterns include:

* `/v1/chat/completions`
* `/v1/responses`
* `/v1/embeddings`
* `/v1/completions`

This includes OpenAI-compatible gateways and local servers when they expose compatible paths.

Typical events:

* `llm.request`
* `llm.response`
* `llm.error`

Example:

```javascript
await fetch("http://127.0.0.1:11434/v1/chat/completions", {
  method: "POST",
  headers: {"content-type": "application/json"},
  body: JSON.stringify({
    model: "qwen3:8b",
    messages: [
      {role: "user", content: "Summarize this page."},
    ],
  }),
});
```

### Ollama HTTP APIs

Recognized Ollama patterns include:

* `/api/chat`
* `/api/generate`
* `/api/embed`
* `/api/embeddings`
* `/api/show`
* `/api/pull`
* endpoints using port `11434`

Calls are recorded as browser-side LLM events with `provider: "ollama"`.

### Anthropic Messages API

Requests using Anthropic-style `/v1/messages` endpoints and compatible request metadata are recorded with `provider: "anthropic"`.

### Streaming limitation

A request with `stream: true` is identified as a streaming operation. The current release records request and final HTTP-level response metadata. It does not expand Server-Sent Events or streamed token chunks into individual per-chunk LLM events.

## MCP instrumentation

Senda-Argus Browser Hooks recognizes MCP-style JSON-RPC 2.0 messages sent through HTTP, XHR, or WebSocket connections.

### MCP tool calls

For a JSON-RPC request such as:

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "search_vulnerabilities",
    "arguments": {
      "query": "CVE-2024-3094"
    }
  }
}
```

The SDK emits lifecycle events such as:

* `mcp.tool_call.requested`
* `mcp.tool_call.completed`
* `mcp.tool_call.failed`

Other MCP methods may emit:

* `mcp.requested`
* `mcp.completed`
* `mcp.failed`

### MCP WebSocket messages

MCP-like JSON-RPC messages sent or received through WebSocket connections emit:

* `mcp.websocket.sent`
* `mcp.websocket.received`

### MCP purpose identity

For MCP requests, the SDK derives a stable `purpose_id` from normalized capability metadata such as:

* MCP server URL
* tool name
* JSON-RPC method
* source type

This helps correlate the same external capability across different browser sessions or agent runs without storing raw arguments.

## DOM context as RAG evidence

Browser Agents frequently use DOM content as their working context. Senda-Argus Browser Hooks records this context as a RAG-style event:

```text
rag.context.collected
```

A DOM snapshot may contain:

* snapshot reason
* page URL
* page title
* document language
* interactive element count
* visible text hash
* interactive element hash
* visible DOM text, when enabled
* DOM HTML, when enabled
* interactive element descriptors, when argument capture is enabled

Default behavior stores hashes and metadata rather than raw page content.

```json
{
  "event_type": "rag.context.collected",
  "data": {
    "rag": {
      "reason": "initial",
      "page": {
        "url": "https://example.com/console",
        "title": "Security Console",
        "lang": "en"
      },
      "interactive_count": 18,
      "interactive_hash": "sha256:...",
      "text_hash": "sha256:..."
    }
  }
}
```

DOM snapshots are captured:

* once during initial registration
* after debounced DOM mutations
* when `captureDom()` is called explicitly

The default DOM text limit is 20,000 characters. The default mutation debounce interval is 750 milliseconds.

## Browser action events

The SDK uses capturing event listeners to observe browser interactions.

Typical events include:

* `browser.action.click`
* `browser.action.input`
* `browser.action.change`
* `browser.action.submit`

Action metadata may include:

* element tag
* role
* input type
* name
* element ID
* ARIA label
* visible text excerpt
* normalized link URL
* selector hint
* page URL

Raw input values are not stored. Non-password input values are represented by a SHA-256 hash. Password values are always replaced with a redacted marker.

## Navigation events

The SDK observes:

* `history.pushState()`
* `history.replaceState()`
* `popstate`
* `hashchange`

These produce `browser.navigation` events with the navigation kind, current URL, and current page title.

## What is collected

### Common event fields

Each event is normalized with common fields such as:

* `schema_version`
* `event_id`
* `trace_id`
* `span_id`
* `parent_span_id`
* `timestamp`
* `project`
* `environment`
* `event_type`
* `tenant_id`
* `session_id`
* `conversation_id`
* `run_id`
* `turn_id`
* `agent_id`
* `purpose_id`
* `source`
* `actor`
* `data`
* `security`
* `status`
* `latency_ms`
* `error`
* `runtime`

Runtime metadata may include:

* JavaScript language identifier
* SDK name and version
* browser user agent
* page URL
* page title

### LLM events

Typical LLM data includes:

* provider
* operation
* model
* normalized endpoint
* streaming flag
* message count
* full input hash
* message or prompt hash
* raw input, when enabled
* raw output, when enabled
* output hash
* HTTP status
* latency
* error details

### MCP events

Typical MCP data includes:

* JSON-RPC operation
* normalized server URL
* tool name
* request ID
* purpose profile
* `purpose_id`
* data source hash
* argument hash
* result hash
* raw arguments, when enabled
* raw result, when enabled
* latency
* error details

### Browser Agent events

Typical Browser Agent data includes:

* framework name
* prompt hash
* result hash
* raw prompt, when enabled
* raw result, when enabled
* run latency
* failure details

## Identity model

### `agent_id`

`agent_id` identifies the browser-side execution origin.

It is derived from stable runtime metadata such as:

* project
* environment
* source SDK
* configured agent hint

This is intended to answer:

> Which browser agent or runtime produced this event?

### `purpose_id`

`purpose_id` identifies the external capability or purpose represented by an event.

For MCP communication it is derived from stable MCP profile metadata such as:

* server URL
* tool name
* JSON-RPC method
* source type

This is intended to answer:

> Which external tool or capability did the browser agent use?

### Trace and run identifiers

Events generated during the active registration share trace and run context where applicable. Parent span identifiers connect request and completion events, such as:

```text
llm.request
  -> llm.response

mcp.tool_call.requested
  -> mcp.tool_call.completed

browser.agent.run.started
  -> browser.agent.run.completed
```

## Exporters

### HTTP exporter

```javascript
SendaArgus.register({
  project: "browser-agent",
  endpoint: "https://argus.example/v1/events",
  exporter: "http",
});
```

Events are batched and sent as:

```json
{
  "events": [
    {
      "schema_version": "0.2",
      "event_type": "llm.request"
    }
  ]
}
```

The exporter uses `navigator.sendBeacon()` when available and enabled. It falls back to the original uninstrumented `fetch` implementation.

The internal request includes:

```text
x-senda-argus-internal: 1
```

This prevents the collector upload from recursively generating another instrumented event.

### Console exporter

```javascript
SendaArgus.register({
  project: "browser-agent",
  exporter: "console",
});
```

### Memory exporter

```javascript
SendaArgus.register({
  project: "browser-agent-test",
  exporter: "memory",
});

const events = SendaArgus.getEvents();
```

## Configuration reference

| Option | Default | Description |
|---|---:|---|
| `project` | `default` | Project identifier |
| `environment` | `dev` | Environment identifier for manual registration |
| `endpoint` | `null` | HTTP collector endpoint |
| `exporter` | `http` | `http`, `console`, or `memory` |
| `headers` | `{}` | Additional HTTP exporter headers |
| `batchSize` | `10` | Flush when the buffer reaches this event count |
| `flushIntervalMs` | `2000` | Periodic flush interval |
| `capturePrompt` | `false` | Capture raw LLM and PageAgent prompt content |
| `captureResponse` | `false` | Capture raw LLM response content |
| `captureArguments` | `false` | Capture raw LLM headers, MCP arguments, and DOM element descriptors |
| `captureResult` | `false` | Capture raw MCP and PageAgent results |
| `captureDomText` | `false` | Capture normalized visible DOM text |
| `captureDomHtml` | `false` | Capture DOM HTML |
| `captureHash` | `true` | Emit hashes for correlation |
| `redact` | `true` | Redact configured sensitive fields and patterns |
| `maxBodyBytes` | `256000` | Maximum response body size inspected |
| `maxDomChars` | `20000` | Maximum captured DOM text or HTML length |
| `domDebounceMs` | `750` | Delay before mutation-triggered DOM capture |
| `actor` | `{}` | Actor metadata |
| `tenantId` | `null` | Tenant identifier |
| `sessionId` | `null` | Session identifier |
| `conversationId` | `null` | Conversation identifier |
| `runId` | generated | Run identifier |
| `turnId` | `null` | Turn identifier |
| `agentId` | generated | Explicit agent identifier override |
| `purposeId` | `null` | Explicit purpose identifier override |
| `agentHint` | `browser-agent` | Input used for generated agent identity |
| `instrumentFetch` | `true` | Instrument global `fetch` |
| `instrumentXHR` | `true` | Instrument `XMLHttpRequest` |
| `instrumentWebSocket` | `true` | Instrument global `WebSocket` |
| `instrumentDOM` | `true` | Enable DOM snapshot collection |
| `instrumentActions` | `true` | Enable browser action collection |
| `instrumentNavigation` | `true` | Enable navigation collection |
| `includeUrlPatterns` | `[]` | Observe only matching URL regular expressions |
| `excludeUrlPatterns` | `[]` | Ignore matching URL regular expressions |
| `debug` | `false` | Print individual emitted events |

## URL filtering

URL filters can limit which LLM or MCP endpoints are observed.

```javascript
SendaArgus.register({
  project: "browser-agent",
  includeUrlPatterns: [
    "^https://llm-gateway\\.example\\.com/",
    "^https://mcp\\.example\\.com/",
  ],
  excludeUrlPatterns: [
    "/health$",
    "/metrics$",
  ],
});
```

Patterns are interpreted as JavaScript regular expressions.

## Capture and redaction controls

The default configuration is privacy-oriented:

```javascript
SendaArgus.register({
  capturePrompt: false,
  captureResponse: false,
  captureArguments: false,
  captureResult: false,
  captureDomText: false,
  captureDomHtml: false,
  captureHash: true,
  redact: true,
});
```

When raw capture is disabled, hashes and metadata remain available for trace correlation.

The redactor masks common sensitive data such as:

* authorization headers
* bearer tokens
* API keys
* access tokens
* secrets
* passwords
* cookies
* session values
* private key material

Redaction is best effort and does not replace application-specific data classification or DLP controls.

## Browser security considerations

Browser-side instrumentation has additional security constraints compared with server-side SDK hooks.

For production use:

* do not embed long-lived provider API keys in frontend JavaScript
* use short-lived credentials or an authenticated LLM gateway
* configure CORS deliberately
* avoid HTTPS-to-HTTP mixed-content connections
* review Content Security Policy requirements
* restrict collector endpoints with authentication and origin controls
* disable raw DOM capture unless required
* disable raw prompt and response capture unless required
* treat event exports as security-sensitive audit data
* review whether third-party scripts can access the global `SendaArgus` object
* test compatibility with application frameworks before broad deployment

The SDK observes browser activity but does not provide a security boundary against malicious code running in the same page origin.

## Content Security Policy

Applications with a restrictive Content Security Policy may need to allow:

* loading the Senda-Argus script from the selected origin
* sending event batches to the collector endpoint
* connecting to local or remote LLM and MCP endpoints

Example directives depend on the deployment architecture:

```text
script-src 'self' https://your-cdn.example
connect-src 'self' https://argus.example http://127.0.0.1:11434
```

Use the narrowest policy suitable for the application.

## Testing

Run the included tests:

```bash
npm test
```

Expected result for v0.1.0:

```text
3 tests passed
0 tests failed
```

The test suite covers:

* common schema generation
* registration and in-memory export
* automatic redaction
* OpenAI-compatible fetch detection
* privacy-safe prompt handling
* LLM response event generation
* MCP `tools/call` request detection
* MCP capability identity generation
* MCP completion event generation

## Build

Build the distribution file:

```bash
npm run build
```

The generated browser file is:

```text
dist/senda-argus-browser-hooks.js
```

## Smoke test

A minimal browser smoke test can use the memory exporter.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Senda-Argus Browser Hooks Smoke Test</title>
</head>
<body>
  <button id="test-button">Test</button>

  <script src="./dist/senda-argus-browser-hooks.js" data-auto="false"></script>
  <script>
    SendaArgus.register({
      project: "browser-smoke-test",
      environment: "local",
      exporter: "memory",
      debug: true
    });

    document.getElementById("test-button").click();

    setTimeout(() => {
      console.log(SendaArgus.getEvents());
    }, 1000);
  </script>
</body>
</html>
```

Expected event types include:

```text
browser.agent.instrumented
rag.context.collected
browser.action.click
```

## Known limitations

* Streaming LLM response chunks are not emitted as individual events.
* Server-Sent Events are not currently instrumented as a dedicated transport.
* PageAgent internal planning steps are not directly monkey patched.
* DOM mutation snapshots are page-level context snapshots, not a semantic vector-store retrieval trace.
* Closed shadow roots cannot be inspected.
* Cross-origin iframe content cannot be inspected unless browser same-origin rules permit access.
* Service Worker, Shared Worker, and Web Worker network activity is not automatically covered by page-level hooks.
* Requests issued before registration are not observed.
* Request classification is endpoint- and payload-based and may require future adapters for custom protocols.
* The SDK does not currently provide persistent local storage or IndexedDB exporters.
* The SDK does not currently provide a CLI; downstream validation can use compatible Senda-Argus tooling or collector-side validation.

## What this project does not do

Senda-Argus Browser Hooks does not provide:

* risk scoring
* alerting
* policy enforcement
* request blocking
* prompt injection prevention
* browser sandboxing
* dashboard functionality
* SIEM integration
* vector embedding generation
* DOM semantic ranking
* automatic Argus backend deployment

These functions are expected to be implemented by downstream analysis, policy, and visualization systems.

## Troubleshooting

### No events are generated

Check that:

* the script loaded successfully
* automatic registration was not disabled unintentionally
* `register()` was called before the Browser Agent request
* the relevant instrumentor is enabled
* the request URL and body match a supported LLM or MCP pattern
* URL filters are not excluding the request
* the page is not replacing `fetch`, XHR, or WebSocket after registration

### Events appear in memory but are not uploaded

Check that:

* `endpoint` is configured
* `exporter` is set to `http`
* the collector accepts `POST` requests with JSON bodies
* CORS permits requests from the application origin
* CSP `connect-src` permits the collector endpoint
* collector authentication headers are configured with `headers`
* browser developer tools do not show mixed-content or network errors

### Ollama requests are blocked

When an HTTPS application attempts to call `http://127.0.0.1:11434`, the browser may block the connection as mixed content. Use an HTTPS-compatible local gateway, a trusted reverse proxy, or an application architecture that avoids insecure cross-scheme requests.

Ollama or the gateway must also permit the browser origin through its CORS configuration.

### PageAgent run events are missing

Check that:

* `SendaArgus.observePageAgent(agent)` was called
* the supplied object exposes an `execute()` function
* the wrapper was installed before calling `agent.execute()`
* another library did not replace `agent.execute()` after instrumentation

LLM request events may still appear even when the explicit PageAgent run wrapper is not installed.

### DOM context events are too frequent

Increase the mutation debounce interval or disable automatic DOM instrumentation.

```javascript
SendaArgus.register({
  domDebounceMs: 2000,
  instrumentDOM: true,
});
```

Or disable DOM instrumentation and capture snapshots only when needed.

```javascript
SendaArgus.register({
  instrumentDOM: false,
});

await SendaArgus.captureDom("manual-checkpoint");
```

### Duplicate events are generated

Possible causes include:

* the SDK was bundled and loaded more than once
* more than one page-level instrumentation script is active
* both XHR and fetch wrappers observe different layers of a custom client
* the application retries the same request

Call `register()` once per page runtime and verify the application bundle does not include duplicate copies.

## Relationship to Senda-Argus Hooks for Python

Senda-Argus Browser Hooks is the browser-side companion to the Python SDK.

| Runtime | Package | Primary coverage |
|---|---|---|
| Python / server-side agents | `senda_argus_hooks` | Python LLM SDKs, MCP Python SDK, agent frameworks, RAG components |
| Browser / client-side agents | `senda_argus_browser_hooks` | Browser HTTP, MCP JSON-RPC, DOM context, browser actions, PageAgent runs |

Both implementations use normalized Senda-Argus event concepts so downstream systems can correlate activity across frontend and backend agent components.

## Security and privacy notes

Senda-Argus Browser Hooks may observe sensitive browser runtime data such as prompts, model responses, MCP arguments, MCP results, DOM text, page URLs, element labels, and user interactions.

For production use:

* keep all raw capture settings disabled unless required
* enable redaction
* use endpoint allowlists
* review collector access controls
* avoid committing captured event data to public repositories
* document user and administrator monitoring expectations
* comply with applicable privacy, employment, and data protection requirements
* validate that collected URLs and page titles do not disclose sensitive information
* test redaction against application-specific secret formats

## License

Apache License 2.0. See [LICENSE](./LICENSE).
