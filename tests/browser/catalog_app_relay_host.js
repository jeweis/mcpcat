// 可信 Host 模拟；没有 Key、真实业务工具或外部网络。
const configurations = JSON.parse(document.getElementById("fixture-config").textContent);
const states = [];
let foreign = 0, mapped = 0, cancellations = 0, teardowns = 0;
function summary() {
  if (states.length !== 2 || states.some(state => !state.checks)) return;
  const passed = states.every(state => Object.values(state.checks).every(value => value === true));
  document.getElementById("status").textContent =
    "checks=" + passed + "; mapped=" + mapped + "; foreign=" + foreign + "; cancelled=" + cancellations + "; teardown=" + teardowns;
}
function send(state, message) { state.frame.contentWindow.postMessage(message, "*"); }
window.addEventListener("message", event => {
  const state = states.find(item => item.frame.contentWindow === event.source);
  if (!state || event.origin !== "null") { foreign++; summary(); return; }
  const message = event.data;
  if (message?.method === "ui/initialize") {
    // 未带 View session 的旧响应不能完成当前调用。
    send(state, { jsonrpc: "2.0", id: 1, result: { poison: true } });
    send(state, { jsonrpc: "2.0", id: message.id, result: { protocolVersion: "2026-01-26", hostInfo: { name: "Fixture", version: "1" }, hostCapabilities: { serverTools: {}, serverResources: {} }, hostContext: {} } });
  } else if (message?.method === "tools/call") {
    if (message.params.name !== state.callback) throw new Error("Wrong source callback");
    mapped++;
    if (message.params.arguments?.slow) { state.pending = message.id; return; }
    send(state, { jsonrpc: "2.0", id: message.id, result: { content: [], structuredContent: { source: state.label }, _meta: { "fixture/unknown": true } } });
  } else if (message?.method === "resources/read") {
    if (message.params.uri !== state.resource) throw new Error("Wrong source resource");
    send(state, { jsonrpc: "2.0", id: message.id, result: { contents: [{ uri: message.params.uri, blob: "AP8=", mimeType: "application/custom" }] } });
  } else if (message?.method === "ui/notifications/fixture-complete") {
    state.checks = message.params.checks;
    send(state, { jsonrpc: "2.0", id: "close-" + state.label, method: "ui/resource-teardown", params: {} });
  } else if (message?.method === "notifications/cancelled") {
    if (message.params.requestId !== state.pending) throw new Error("Wrong cancellation id");
    cancellations++;
  } else if (!message?.method && message?.id === "close-" + state.label) {
    teardowns++;
  }
  summary();
});
for (const configuration of configurations) {
  const frame = document.createElement("iframe");
  frame.title = configuration.label;
  frame.setAttribute("sandbox", "allow-scripts");
  frame.style.cssText = "width:100%;height:300px;border:1px solid gray";
  states.push({ ...configuration, frame });
  document.getElementById("views").append(frame);
  frame.srcdoc = configuration.html;
}
