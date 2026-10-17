// 与固定 core 在同一 inline module 中运行；没有运行时 CDN/Node/构建依赖。
const config = JSON.parse(document.getElementById("mcpcat-source-config").textContent);
const router = createRelayRouter(config);
const frame = document.createElement("iframe");
frame.title = config.title;
frame.setAttribute("sandbox", "allow-scripts");
frame.setAttribute("referrerpolicy", "no-referrer");
frame.setAttribute("allow", config.allow);
let parentOrigin, initialized = false, disposed = false, queuedBytes = 0;
const queued = [];
const teardowns = new Set();

function sendChild(message) { frame.contentWindow?.postMessage(message, "*"); }
// opaque sandbox 的 origin 为字符串 "null"，它不是合法 targetOrigin。
// 仍只向精确 parent Window 发送；接收侧的 Window/origin 双校验不放宽。
function sendHost(message) { window.parent.postMessage(message, parentOrigin === "null" ? "*" : parentOrigin ?? "*"); }
function localError(message) {
  const id = message?.id;
  if (typeof id === "string" && id.length <= 256 || Number.isSafeInteger(id)) sendChild({ jsonrpc: "2.0", id, error: { code: -32602, message: "App request unavailable in this source" } });
}

function closeRelay() {
  if (disposed) return;
  disposed = true;
  for (const requestId of router.close()) sendHost({ jsonrpc: "2.0", method: "notifications/cancelled", params: { requestId, reason: "App view closed" } });
  queued.length = 0; queuedBytes = 0;
  window.removeEventListener("message", receive);
  window.removeEventListener("pagehide", closeRelay);
  frame.remove();
}

function receive(event) {
  if (disposed) return;
  if (event.source === window.parent) {
    if (parentOrigin !== undefined && event.origin !== parentOrigin) { closeRelay(); return; }
    try {
      const message = router.fromHost(event.data);
      if (!message) return;
      parentOrigin ??= event.origin;
      if (message.method === "ui/resource-teardown" && !initialized) {
        sendHost({ jsonrpc: "2.0", id: message.id, result: {} });
        closeRelay(); return;
      }
      if (message.method === "ui/resource-teardown") teardowns.add(message.id);
      if (message.method && message.id === undefined && !initialized) {
        const bytes = new TextEncoder().encode(JSON.stringify(message)).byteLength;
        if (queued.length >= 128 || queuedBytes + bytes > MAX_MESSAGE_BYTES) { closeRelay(); return; }
        queued.push(message); queuedBytes += bytes;
      } else {
        sendChild(message);
      }
    } catch {
      const failure = router.rejectHost(event.data);
      if (failure) sendChild(failure);
    }
  } else if (event.source === frame.contentWindow && event.origin === "null") {
    try {
      const message = router.toHost(event.data);
      if (!message) return;
      if (message.method === "ui/notifications/initialized") {
        initialized = true;
        for (const pending of queued) sendChild(pending);
        queued.length = 0; queuedBytes = 0;
      }
      if (message.method === "ui/notifications/size-changed" && Number.isFinite(message.params?.height)) {
        frame.style.height = Math.max(1, Math.min(8192, message.params.height)) + "px";
      }
      sendHost(message);
      if (message.method === undefined && teardowns.has(message.id)) closeRelay();
    } catch { localError(event.data); }
  }
}

window.addEventListener("message", receive);
window.addEventListener("pagehide", closeRelay);
document.body.append(frame);
const original = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(atob(config.html), char => char.charCodeAt(0)));
const policy = config.innerPolicy.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
frame.srcdoc = '<!doctype html><meta http-equiv="Content-Security-Policy" content="' + policy + '">' + original;
