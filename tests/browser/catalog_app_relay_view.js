// 固定不可信 View 模拟；原工具名/资源名故意在两个来源完全相同。
const requests = new Map(); let serial = 0;
function call(method, params) {
  const id = ++serial;
  return new Promise((resolve, reject) => {
    requests.set(id, { resolve, reject });
    parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
  });
}
window.addEventListener("message", event => {
  if (event.source !== parent) return;
  const message = event.data;
  if (message?.method === "ui/resource-teardown") {
    parent.postMessage({ jsonrpc: "2.0", id: message.id, result: {} }, "*");
  } else if (message && !message.method && requests.has(message.id)) {
    const request = requests.get(message.id); requests.delete(message.id);
    message.error ? request.reject(message.error) : request.resolve(message.result);
  }
});

(async () => {
  try {
    const initialize = await call("ui/initialize", { protocolVersion: "2026-01-26", appInfo: { name: label, version: "1" }, appCapabilities: {} });
    parent.postMessage({ jsonrpc: "2.0", method: "ui/notifications/initialized" }, "*");
    let isolated = false;
    try { parent.document; } catch { isolated = true; }
    const result = await call("tools/call", { name: "save", arguments: { value: 7 } });
    const resource = await call("resources/read", { uri: "asset://same/item?q=1#fragment" });
    let rejected = false;
    try { await call("tools/call", { name: "catapp_" + "f".repeat(48), arguments: {} }); } catch { rejected = true; }
    // 直接绕过固定壳向 Host 发消息；Host 必须按 source Window 拒绝。
    parent.parent.postMessage({ jsonrpc: "2.0", id: "forged", method: "tools/call", params: { name: "save", arguments: {} } }, "*");
    call("tools/call", { name: "save", arguments: { slow: true } }).catch(() => {});
    const checks = {
      isolated, rejected, source: result.structuredContent.source === label,
      rawURI: resource.contents[0].uri === "asset://same/item?q=1#fragment",
      binary: resource.contents[0].blob === "AP8=" && resource.contents[0].mimeType === "application/custom",
      stale: !initialize.poison, metadata: result._meta["fixture/unknown"] === true,
    };
    document.getElementById("result").textContent = label + " " + JSON.stringify(checks);
    parent.postMessage({ jsonrpc: "2.0", method: "ui/notifications/fixture-complete", params: { checks } }, "*");
  } catch {
    document.getElementById("result").textContent = "FAILED " + label;
  }
})();
