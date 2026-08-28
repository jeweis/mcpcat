import { assertCompatibility } from "./compatibility.js";
import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import {
  parseBootstrap,
  parseRegistry,
  parseSkillDetail,
  type BootstrapResponse,
  type RegistryIndex,
  type SkillDetail,
} from "./schema.js";
import { normalizeBaseUrl, resolveUrl } from "./url.js";
import { CLI_VERSION } from "./version.js";

export type Fetch = typeof globalThis.fetch;

export interface RegistryResult {
  notModified: boolean;
  etag?: string;
  registry?: RegistryIndex;
}

export interface DownloadResult {
  bytes: Uint8Array;
  checksumHeader?: string;
  etag?: string;
}

export interface HttpClientOptions {
  fetch?: Fetch;
  timeoutMs?: number;
  allowHttp?: boolean;
}

async function safeBody(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function messageFromBody(body: unknown): string | undefined {
  if (body === null || typeof body !== "object") {
    return undefined;
  }
  const detail = (body as Record<string, unknown>).detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (detail !== null && typeof detail === "object") {
    const message = (detail as Record<string, unknown>).message;
    return typeof message === "string" ? message : undefined;
  }
  return undefined;
}

export class RegistryClient {
  private readonly fetch: Fetch;
  private readonly timeoutMs: number;
  private readonly allowHttp: boolean;

  constructor(options: HttpClientOptions = {}) {
    this.fetch = options.fetch ?? globalThis.fetch;
    this.timeoutMs = options.timeoutMs ?? 15_000;
    this.allowHttp = options.allowHttp ?? false;
  }

  private async request(url: string, headers: RequestInit["headers"] = {}): Promise<Response> {
    try {
      return await this.fetch(url, {
        headers: {
          Accept: "application/json",
          "X-Mcpcat-CLI-Version": CLI_VERSION,
          ...headers,
        },
        signal: AbortSignal.timeout(this.timeoutMs),
      });
    } catch (error) {
      throw new McpcatError(
        ErrorCode.network,
        "无法连接 mcpcat 实例",
        ExitCode.network,
        { url },
        { cause: error },
      );
    }
  }

  private async assertOk(response: Response): Promise<void> {
    if (response.ok) {
      return;
    }
    const body = await safeBody(response);
    if (response.status === 401 || response.status === 403) {
      throw new McpcatError(
        ErrorCode.authRejected,
        "mcpcat 凭证验证失败",
        ExitCode.authentication,
        { status: response.status },
      );
    }
    if (response.status === 426) {
      throw new McpcatError(
        ErrorCode.incompatible,
        messageFromBody(body) ?? "CLI 与目标实例不兼容",
        ExitCode.compatibility,
        { status: response.status, response: body },
      );
    }
    throw new McpcatError(
      ErrorCode.http,
      messageFromBody(body) ?? `mcpcat 返回 HTTP ${response.status}`,
      ExitCode.network,
      { status: response.status },
    );
  }

  async bootstrap(inputUrl: string): Promise<BootstrapResponse> {
    const base = normalizeBaseUrl(inputUrl, this.allowHttp);
    const response = await this.request(resolveUrl(base.url, "/api/skills/bootstrap"));
    await this.assertOk(response);
    const bootstrap = parseBootstrap(await safeBody(response));
    assertCompatibility({
      apiVersion: bootstrap.api_version,
      registrySchemaVersion: bootstrap.registry_schema_version,
      minCliVersion: bootstrap.min_cli_version,
      recommendedCliVersion: bootstrap.recommended_cli_version,
    });
    normalizeBaseUrl(bootstrap.base_url, this.allowHttp);
    return bootstrap;
  }

  async registry(
    bootstrap: BootstrapResponse,
    apiKey: string,
    etag?: string,
  ): Promise<RegistryResult> {
    const base = normalizeBaseUrl(bootstrap.base_url, this.allowHttp);
    const headers: Record<string, string> = {
      [bootstrap.auth_header_name]: apiKey,
    };
    if (etag !== undefined) {
      headers["If-None-Match"] = etag;
    }
    const response = await this.request(resolveUrl(base.url, bootstrap.registry_path), headers);
    if (response.status === 304) {
      return { notModified: true, ...(etag === undefined ? {} : { etag }) };
    }
    await this.assertOk(response);
    const registry = parseRegistry(await safeBody(response));
    assertCompatibility({
      apiVersion: registry.api_version,
      registrySchemaVersion: registry.registry_schema_version,
    });
    const responseEtag = response.headers.get("etag") ?? undefined;
    return {
      notModified: false,
      registry,
      ...(responseEtag === undefined ? {} : { etag: responseEtag }),
    };
  }

  private authHeaders(bootstrap: BootstrapResponse, apiKey: string): Record<string, string> {
    return { [bootstrap.auth_header_name]: apiKey };
  }

  async skillDetail(
    bootstrap: BootstrapResponse,
    apiKey: string,
    slug: string,
  ): Promise<SkillDetail> {
    const base = normalizeBaseUrl(bootstrap.base_url, this.allowHttp);
    const response = await this.request(
      resolveUrl(base.url, `/api/skills/${encodeURIComponent(slug)}`),
      this.authHeaders(bootstrap, apiKey),
    );
    if (response.status === 404) {
      throw new McpcatError(
        ErrorCode.skillNotFound,
        `Skill 不存在：${slug}`,
        ExitCode.configuration,
        { slug },
      );
    }
    await this.assertOk(response);
    return parseSkillDetail(await safeBody(response));
  }

  async downloadSkill(
    bootstrap: BootstrapResponse,
    apiKey: string,
    slug: string,
    version: string,
  ): Promise<DownloadResult> {
    const base = normalizeBaseUrl(bootstrap.base_url, this.allowHttp);
    const response = await this.request(
      resolveUrl(
        base.url,
        `/api/skills/${encodeURIComponent(slug)}/versions/${encodeURIComponent(version)}/download`,
      ),
      {
        ...this.authHeaders(bootstrap, apiKey),
        Accept: "application/zip",
      },
    );
    await this.assertOk(response);
    const checksumHeader = response.headers.get("x-checksum-sha256") ?? undefined;
    const etag = response.headers.get("etag") ?? undefined;
    return {
      bytes: new Uint8Array(await response.arrayBuffer()),
      ...(checksumHeader === undefined ? {} : { checksumHeader }),
      ...(etag === undefined ? {} : { etag }),
    };
  }
}
