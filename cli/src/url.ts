import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export interface NormalizedUrl {
  url: string;
  insecure: boolean;
}

function isLocalHostname(hostname: string): boolean {
  const normalized = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (normalized === "localhost" || normalized === "::1") {
    return true;
  }
  if (/^127(?:\.\d{1,3}){3}$/.test(normalized)) {
    return normalized.split(".").every((part) => Number(part) <= 255);
  }
  return false;
}

export function normalizeBaseUrl(input: string, allowHttp = false): NormalizedUrl {
  const trimmed = input.trim();
  if (trimmed.length === 0) {
    throw new McpcatError(ErrorCode.usage, "实例 URL 不能为空", ExitCode.usage);
  }
  const candidate = /^[a-z][a-z\d+.-]*:\/\//i.test(trimmed)
    ? trimmed
    : `https://${trimmed}`;
  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch (error) {
    throw new McpcatError(
      ErrorCode.usage,
      "实例 URL 无效",
      ExitCode.usage,
      { url: trimmed },
      { cause: error },
    );
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new McpcatError(
      ErrorCode.usage,
      "实例 URL 仅支持 HTTPS 或 HTTP",
      ExitCode.usage,
      { protocol: parsed.protocol },
    );
  }
  if (parsed.username !== "" || parsed.password !== "") {
    throw new McpcatError(
      ErrorCode.usage,
      "实例 URL 不得包含凭证",
      ExitCode.usage,
    );
  }
  if (parsed.search !== "" || parsed.hash !== "") {
    throw new McpcatError(
      ErrorCode.usage,
      "实例 URL 不得包含查询参数或片段",
      ExitCode.usage,
    );
  }
  const insecure = parsed.protocol === "http:" && !isLocalHostname(parsed.hostname);
  if (insecure && !allowHttp) {
    throw new McpcatError(
      ErrorCode.insecureTransport,
      "非本地实例必须使用 HTTPS；如已了解风险，请显式传入 --allow-http",
      ExitCode.insecureTransport,
      { hostname: parsed.hostname },
    );
  }
  parsed.pathname = parsed.pathname.replace(/\/+$/, "") || "/";
  return {
    url: parsed.toString().replace(/\/$/, ""),
    insecure,
  };
}

export function resolveUrl(baseUrl: string, path: string): string {
  return `${baseUrl.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
}
