import test from "node:test";
import assert from "node:assert/strict";
import { createRelayRouter, resourceScope, wrapResource, unwrapResource } from "../app/services/catalog_app_relay_core.js";

const source = "12345678-1234-1234-1234-123456789abc";
const generation = "87654321-4321-4321-4321-cba987654321";
const a = "catapp_" + "a".repeat(48), b = "catapp_" + "b".repeat(48);
const router = value => createRelayRouter({ source, generation, tools: { save: value } });
const call = (name, id = 1) => ({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: { value: 7 }, _meta: { "fixture/unknown": true } } });

test("same raw callback names bind to separate source adapters without mutating input", () => {
  const left = router(a), right = router(b), message = call("save");
  const dispatched = left.toHost(message);
  assert.equal(dispatched.params.name, a);
  assert.equal(right.toHost(message).params.name, b);
  assert.equal(message.params.name, "save");
  const response = left.fromHost({ jsonrpc: "2.0", id: dispatched.id, result: { content: [], structuredContent: { kept: true }, _meta: { unknown: 1 } } });
  assert.deepEqual(response.result._meta, { unknown: 1 });
  assert.equal(response.id, 1);
});

test("unknown/cross-source callbacks are rejected, not guessed", () => {
  const left = router(a);
  assert.throws(() => left.toHost(call("missing")));
  assert.throws(() => left.toHost(call(b)));
  assert.equal(left.toHost(call(a)).params.name, a);
});

test("opaque resource URI encoding preserves unicode/query/fragment", () => {
  const scope = resourceScope(source, generation), uri = "asset://same/中文?q=1#fragment";
  const expected = scope + Buffer.from(uri).toString("base64url");
  assert.equal(wrapResource(scope, uri), expected);
  assert.equal(unwrapResource(scope, expected), uri);
  const result = router(a).toHost({ jsonrpc: "2.0", id: 1, method: "resources/read", params: { uri } });
  assert.equal(result.params.uri, expected);
});

test("reserved sandbox methods never escape to the outer Host", () => {
  const relay = router(a), value = { jsonrpc: "2.0", method: "ui/notifications/sandbox-proxy-ready", params: {} };
  assert.equal(relay.toHost(value), null);
  assert.equal(relay.fromHost(value), null);
});

test("pending requests bounded and id reusable only after response", () => {
  const relay = router(a);
  const dispatched = relay.toHost(call("save"));
  assert.throws(() => relay.toHost(call("save")));
  relay.fromHost({ jsonrpc: "2.0", id: dispatched.id, result: {} });
  relay.toHost(call("save"));
  for (let id = 2; id <= 256; id++) relay.toHost(call("save", id));
  assert.throws(() => relay.toHost(call("save", 257)));
  assert.equal(relay.close().length, 256);
  assert.throws(() => relay.toHost(call("save", 257)));
});

test("sessions rewrite RPC ids and stale responses cannot update a newer View", () => {
  const first = router(a), second = router(a);
  const old = first.toHost(call("save")), current = second.toHost(call("save"));
  assert.notEqual(old.id, current.id);
  assert.equal(second.fromHost({ jsonrpc: "2.0", id: old.id, result: {} }), null);
  assert.equal(second.fromHost({ jsonrpc: "2.0", id: current.id, result: {} }).id, 1);
});

test("cancellation translates only this View's pending request id", () => {
  const relay = router(a), request = relay.toHost(call("save", 9));
  const cancel = id => ({ jsonrpc: "2.0", method: "notifications/cancelled", params: { requestId: id } });
  assert.equal(relay.toHost(cancel(9)).params.requestId, request.id);
  assert.equal(relay.toHost(cancel(10)), null);
});

test("resource replies restore raw URI and own scoped requests are idempotent", () => {
  const relay = router(a), uri = "asset://source/data?q=1#x";
  const scoped = wrapResource(resourceScope(source, generation), uri);
  const request = relay.toHost({ jsonrpc: "2.0", id: 1, method: "resources/read", params: { uri: scoped } });
  assert.equal(request.params.uri, scoped);
  const result = relay.fromHost({ jsonrpc: "2.0", id: request.id, result: { contents: [{ uri: scoped, blob: "AP8=", mimeType: "application/custom", _meta: { kept: true } }] } });
  assert.equal(result.result.contents[0].uri, uri);
  assert.equal(result.result.contents[0].blob, "AP8=");
});

test("unrecognized MCP methods and notification-style tool calls do not dispatch", () => {
  const relay = router(a), notification = call("save"); delete notification.id;
  assert.throws(() => relay.toHost(notification));
  assert.throws(() => relay.toHost({ jsonrpc: "2.0", id: 1, method: "resources/list" }));
});

test("malformed batches, ids and messages are rejected", () => {
  const relay = router(a);
  for (const value of [[], null, {}, { ...call("save"), id: Infinity }, { ...call("save"), result: {} }]) {
    assert.throws(() => relay.toHost(value));
  }
});

test("invalid oversized Host response fails only its correlated call and releases quota", () => {
  const relay = router(a), request = relay.toHost(call("save"));
  const response = { jsonrpc: "2.0", id: request.id, result: { value: "x".repeat(16 * 1024 * 1024) } };
  assert.throws(() => relay.fromHost(response));
  const rejected = relay.rejectHost(response);
  assert.equal(rejected.id, 1);
  assert.equal(rejected.error.code, -32000);
  assert.equal(relay.rejectHost(response), null);
  relay.toHost(call("save"));
});
