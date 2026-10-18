import { createHash } from "node:crypto";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import type { Fetch } from "./http.js";
import { normalizeBaseUrl, resolveUrl } from "./url.js";
import { CLI_VERSION } from "./version.js";

/** 不读取未知服务器错误正文；配置中的任意名字也不能绕过容器脱敏。 */
export function safeManagementData(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(safeManagementData);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key,
    /^(?:env|headers|oauth|credentials|command|args|url|error|config)$/i.test(key)
      ? (item === null ? null : "[REDACTED]") : safeManagementData(item)]));
}

async function bodyBytes(response: Response, limit: number): Promise<Uint8Array> {
  if (Number(response.headers.get("content-length")) > limit) {
    await response.body?.cancel();
    throw new McpcatError(ErrorCode.schema, "管理响应超过大小限制", ExitCode.schema);
  }
  const reader: ReadableStreamDefaultReader<Uint8Array> | undefined = response.body?.getReader();
  if (!reader) return new Uint8Array();
  const parts: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.length;
      if (size > limit) throw new McpcatError(ErrorCode.schema, "管理响应超过大小限制", ExitCode.schema);
      parts.push(part.value);
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
  return Buffer.concat(parts);
}

/** 固定实例/部署前缀，无重定向、无重试；写失败不声称未执行。 */
export class ManagementClient {
  readonly baseUrl: string;
  constructor(baseUrl: string, private readonly authHeader: string,
    private readonly key: string, private readonly fetch: Fetch = globalThis.fetch,
    allowHttp = false) {
    this.baseUrl = normalizeBaseUrl(baseUrl, allowHttp).url;
    if (!/^[!#$%&'*+.^_`|~\w-]+$/.test(authHeader) ||
        /^(?:host|content-length|connection|content-type|accept)$/i.test(authHeader)) {
      throw new McpcatError(ErrorCode.schema, "认证头名称无效", ExitCode.schema);
    }
  }

  private async request(method: string, path: string, body?: unknown): Promise<Response> {
    if (!path.startsWith("/api/") || path.includes("#")) {
      throw new McpcatError(ErrorCode.usage, "管理接口路径无效", ExitCode.usage);
    }
    const headers: Record<string, string> = {
      [this.authHeader]: this.key, Accept: "application/json", "X-Mcpcat-CLI-Version": CLI_VERSION,
    };
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) headers["Content-Type"] = "application/json";
    const response = await this.fetch(resolveUrl(this.baseUrl, path), {
      method, headers, redirect: "error", signal: AbortSignal.timeout(method === "GET" ? 45_000 : 600_000),
      ...(body === undefined ? {} : {body: multipart ? body : JSON.stringify(body)}),
    });
    if (!response.ok) {
      await response.body?.cancel();
      const auth = response.status === 401 || response.status === 403;
      const suffix = method === "GET" ? "" : "；请核实平台状态，不要直接重试";
      throw new McpcatError(auth ? ErrorCode.authRejected : ErrorCode.http,
        auth ? "当前凭证无效或权限不足" : `管理请求返回 HTTP ${response.status}${suffix}`,
        auth ? ExitCode.authentication : ExitCode.network, {status: response.status});
    }
    return response;
  }

  async call(method: string, path: string, body?: unknown): Promise<unknown> {
    try {
      const response = await this.request(method, path, body);
      if (response.status === 204) return {status: "completed"};
      const bytes = await bodyBytes(response, 4 * 1024 * 1024);
      try { return JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(bytes)) as unknown; }
      catch { throw new McpcatError(ErrorCode.schema,
        method === "GET" ? "管理响应不是有效 JSON" : "响应无法解析，写入结果未确认；请核实平台状态，不要重试",
        ExitCode.schema); }
    } catch (error) {
      if (method !== "GET" && error instanceof McpcatError && error.code === ErrorCode.schema) {
        throw new McpcatError(ErrorCode.schema, "管理响应无效或超限，写入结果未确认；请核实平台状态，不要重试", ExitCode.schema);
      }
      return this.failure(error, method);
    }
  }

  async download(path: string): Promise<{bytes: Uint8Array; sha256: string}> {
    try {
      const response = await this.request("GET", path);
      const bytes = await bodyBytes(response, 50 * 1024 * 1024);
      const sha256 = createHash("sha256").update(bytes).digest("hex");
      const expected = response.headers.get("x-checksum-sha256");
      if (expected?.toLowerCase() !== sha256) throw new McpcatError(
        ErrorCode.integrity, "下载摘要缺失或校验失败，未保存文件", ExitCode.integrity);
      return {bytes, sha256};
    } catch (error) { return this.failure(error, "GET"); }
  }

  private failure(error: unknown, method: string): never {
    if (error instanceof McpcatError) throw error;
    throw new McpcatError(ErrorCode.network,
      method === "GET" ? "无法读取管理接口，请检查连接；不会自动重试"
        : "写入结果未确认（超时、连接或重定向被拒绝）；请核实平台状态，不要直接重试",
      ExitCode.network);
  }
}
