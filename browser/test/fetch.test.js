import test from "node:test";
import assert from "node:assert/strict";
import SendaArgus from "../src/index.js";

test("captures OpenAI-compatible fetch without raw prompt by default", async () => {
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({choices:[{message:{content:"ok"}}]}), {status: 200, headers:{"content-type":"application/json"}});
  SendaArgus.clearEvents();
  SendaArgus.register({project:"fetch-test", exporter:"memory", instrumentXHR:false, instrumentWebSocket:false, instrumentDOM:false, instrumentActions:false, instrumentNavigation:false});
  await fetch("https://llm.example/v1/chat/completions", {method:"POST", headers:{"content-type":"application/json", authorization:"Bearer secret"}, body:JSON.stringify({model:"test-model", messages:[{role:"user",content:"private prompt"}]})});
  const events = SendaArgus.getEvents();
  const req = events.find(e => e.event_type === "llm.request");
  const res = events.find(e => e.event_type === "llm.response");
  assert.equal(req.data.llm.model, "test-model");
  assert.equal(req.data.llm.input, undefined);
  assert.match(req.data.llm.messages_hash, /^(sha256|fnv1a):/);
  assert.equal(res.status, "success");
  SendaArgus.unregister();
  globalThis.fetch = nativeFetch;
});

test("captures MCP tools/call metadata", async () => {
  const nativeFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({jsonrpc:"2.0",id:1,result:{content:[]}}), {status:200});
  SendaArgus.clearEvents();
  SendaArgus.register({project:"mcp-test", exporter:"memory", instrumentXHR:false, instrumentWebSocket:false, instrumentDOM:false, instrumentActions:false, instrumentNavigation:false});
  await fetch("https://mcp.example/mcp", {method:"POST", body:JSON.stringify({jsonrpc:"2.0",id:1,method:"tools/call",params:{name:"search",arguments:{q:"secret"}}})});
  const events = SendaArgus.getEvents();
  const req = events.find(e => e.event_type === "mcp.tool_call.requested");
  const done = events.find(e => e.event_type === "mcp.tool_call.completed");
  assert.equal(req.data.mcp.tool, "search");
  assert.equal(req.data.mcp.arguments, undefined);
  assert.match(req.purpose_id, /^purpose_/);
  assert.equal(done.status, "success");
  SendaArgus.unregister();
  globalThis.fetch = nativeFetch;
});
