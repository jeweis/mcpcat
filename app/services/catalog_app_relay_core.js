// 固定协议适配器：不执行上游 HTML，不猜测“最近打开”的来源。
const PREFIX = "ui://mcpcat/catalog-apps/v1/";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;
const CALLBACK = /^catapp_[0-9a-f]{48}$/u;
export const MAX_MESSAGE_BYTES = 16 * 1024 * 1024;

export function validateMessage(message) {
  if (!message || typeof message !== "object" || Array.isArray(message) || message.jsonrpc !== "2.0") {
    throw new TypeError("Invalid App protocol message");
  }
  if (new TextEncoder().encode(JSON.stringify(message)).byteLength > MAX_MESSAGE_BYTES) {
    throw new TypeError("App protocol message too large");
  }
  if (message.id !== undefined && !(typeof message.id === "string" && message.id.length <= 256) && !Number.isSafeInteger(message.id)) {
    throw new TypeError("Invalid App protocol request id");
  }
  if (message.method !== undefined) {
    if (typeof message.method !== "string" || !message.method || message.method.length > 256 || "result" in message || "error" in message) {
      throw new TypeError("Invalid App protocol method");
    }
  } else if (message.id === undefined || (("result" in message) === ("error" in message))) {
    throw new TypeError("Invalid App protocol response");
  }
  return message;
}

export function resourceScope(source, generation) {
  if (!UUID.test(source) || !UUID.test(generation)) throw new TypeError("Invalid App source identity");
  return PREFIX + source + "/" + generation + "/";
}

export function wrapResource(scope, uri) {
  if (typeof uri !== "string" || !/^[A-Za-z][A-Za-z0-9+.-]*:/u.test(uri) || /[\s\u0000-\u001f]/u.test(uri)) {
    throw new TypeError("Invalid App resource URI");
  }
  const bytes = new TextEncoder().encode(uri);
  if (bytes.byteLength > 8192) throw new TypeError("App resource URI too large");
  if (new TextDecoder("utf-8", { fatal: true }).decode(bytes) !== uri) throw new TypeError("Invalid App resource encoding");
  new URL(uri); // Validate without normalizing the original text.
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return scope + btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/u, "");
}

export function unwrapResource(scope, uri) {
  if (typeof uri !== "string" || !uri.startsWith(scope)) throw new TypeError("App resource belongs to another source");
  const token = uri.slice(scope.length);
  if (!/^[A-Za-z0-9_-]+$/u.test(token) || token.length > 10923) throw new TypeError("Invalid App resource encoding");
  const binary = atob(token.replaceAll("-", "+").replaceAll("_", "/"));
  const original = new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(binary, char => char.charCodeAt(0)));
  if (wrapResource(scope, original) !== uri) throw new TypeError("Noncanonical App resource encoding");
  return original;
}

export function createRelayRouter(config) {
  const scope = resourceScope(config.source, config.generation);
  if (!config.tools || typeof config.tools !== "object" || Array.isArray(config.tools)) throw new TypeError("Invalid App tool map");
  const tools = new Map(Object.entries(config.tools));
  if (tools.size > 4096 || [...tools.values()].some(value => typeof value !== "string" || !CALLBACK.test(value))) {
    throw new TypeError("Invalid App callback binding");
  }
  const callbacks = new Set(tools.values());
  const session = config.session ?? crypto.randomUUID();
  if (!UUID.test(session)) throw new TypeError("Invalid App relay session");
  const pending = new Map();
  const wireIds = new Map();
  let serial = 0, closed = false;
  return Object.freeze({
    toHost(message) {
      if (closed) throw new TypeError("App relay is closed");
      validateMessage(message);
      if (message.method?.startsWith("ui/notifications/sandbox-")) return null;
      let result = message;
      if (["tools/call", "resources/read"].includes(message.method) && message.id === undefined) throw new TypeError("App call must be a request");
      if (message.method === "tools/call") {
        const name = message.params?.name;
        const target = tools.get(name) ?? (callbacks.has(name) ? name : undefined);
        if (!target) throw new TypeError("App tool is outside the bound source");
        result = { ...message, params: { ...message.params, name: target } };
      } else if (message.method === "resources/read") {
        const uri = message.params?.uri;
        const original = typeof uri === "string" && uri.startsWith(scope) ? unwrapResource(scope, uri) : uri;
        result = { ...message, params: { ...message.params, uri: wrapResource(scope, original) } };
      } else if (message.method === "notifications/cancelled") {
        const wire = pending.get(message.params?.requestId)?.wire;
        if (wire === undefined) return null;
        result = { ...message, params: { ...message.params, requestId: wire } };
      } else if (message.method && !message.method.startsWith("ui/") && !["ping", "notifications/message"].includes(message.method)) {
        throw new TypeError("Unsupported App protocol method");
      }
      if (message.method && message.id !== undefined) {
        if (pending.size >= 256 || pending.has(message.id)) throw new TypeError("App request limit or duplicate id");
        const wire = session + ":" + (++serial);
        pending.set(message.id, { wire, method: message.method });
        wireIds.set(wire, message.id);
        result = { ...result, id: wire };
      }
      return result;
    },
    fromHost(message) {
      if (closed) throw new TypeError("App relay is closed");
      validateMessage(message);
      if (message.method?.startsWith("ui/notifications/sandbox-")) return null;
      if (message.method !== undefined) return message;
      if (!wireIds.has(message.id)) return null; // Stale response from an earlier View.
      const local = wireIds.get(message.id), request = pending.get(local);
      wireIds.delete(message.id);
      pending.delete(local);
      let result = message.result;
      if (request.method === "resources/read" && Array.isArray(result?.contents)) {
        result = { ...result, contents: result.contents.map(content => ({ ...content,
          uri: typeof content.uri === "string" && content.uri.startsWith(scope) ? unwrapResource(scope, content.uri) : content.uri,
        })) };
      }
      return { ...message, id: local, ...(result === undefined ? {} : { result }) };
    },
    rejectHost(message) {
      if (closed || !wireIds.has(message?.id)) return null;
      const local = wireIds.get(message.id);
      wireIds.delete(message.id); pending.delete(local);
      return { jsonrpc: "2.0", id: local, error: { code: -32000, message: "App Host response unavailable" } };
    },
    close() {
      const cancellations = [...pending.values()].map(request => request.wire);
      closed = true; pending.clear(); wireIds.clear();
      return cancellations;
    },
  });
}
