import { describe, expect, it, vi } from "vitest";

import { ErrorCode } from "../src/errors.js";
import { RegistryClient } from "../src/http.js";
import type { BootstrapResponse } from "../src/schema.js";
import { CLI_VERSION } from "../src/version.js";

function bootstrap(overrides: Partial<BootstrapResponse> = {}): BootstrapResponse {
  return {
    instance_name: "mcpcat test",
    base_url: "https://mcpcat.example.com",
    api_version: "v1",
    registry_schema_version: "1.0.0",
    auth_header_name: "X-Test-Key",
    registry_path: "/api/skills/registry",
    min_cli_version: "0.1.0",
    recommended_cli_version: "0.1.0",
    ...overrides,
  };
}

function json(value: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(value), {
    ...init,
    headers: { "Content-Type": "application/json", ...init.headers },
  });
}

describe("RegistryClient", () => {
  it("校验 Bootstrap，并声明 CLI 版本", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async (_input, init) => {
      expect(new Headers(init?.headers).get("X-Mcpcat-CLI-Version")).toBe(CLI_VERSION);
      return json(bootstrap());
    });
    const client = new RegistryClient({ fetch });

    await expect(client.bootstrap("https://mcpcat.example.com")).resolves.toEqual(bootstrap());
  });

  it.each([
    { api_version: "v2" },
    { registry_schema_version: "2.0.0" },
    { min_cli_version: "1.0.0" },
  ])("阻止不兼容 Bootstrap：%o", async (override) => {
    const client = new RegistryClient({ fetch: async () => json(bootstrap(override)) });
    await expect(client.bootstrap("https://mcpcat.example.com")).rejects.toMatchObject({
      code: ErrorCode.incompatible,
    });
  });

  it("拒绝缺字段的 Bootstrap 响应", async () => {
    const client = new RegistryClient({ fetch: async () => json({ instance_name: "bad" }) });
    await expect(client.bootstrap("https://mcpcat.example.com")).rejects.toMatchObject({
      code: ErrorCode.schema,
    });
  });

  it("Registry 使用自定义认证头并支持 ETag/304", async () => {
    const calls: Headers[] = [];
    const fetch = vi
      .fn<typeof globalThis.fetch>()
      .mockImplementationOnce(async (_input, init) => {
        calls.push(new Headers(init?.headers));
        return json(
          { registry_schema_version: "1.0.0", api_version: "v1", skills: [] },
          { headers: { ETag: '"registry-v1"' } },
        );
      })
      .mockImplementationOnce(async (_input, init) => {
        calls.push(new Headers(init?.headers));
        return new Response(null, { status: 304, headers: { ETag: '"registry-v1"' } });
      });
    const client = new RegistryClient({ fetch });
    const first = await client.registry(bootstrap(), "secret");
    const second = await client.registry(bootstrap(), "secret", first.etag);

    expect(first).toMatchObject({ notModified: false, etag: '"registry-v1"' });
    expect(second).toEqual({ notModified: true, etag: '"registry-v1"' });
    expect(calls[0]?.get("X-Test-Key")).toBe("secret");
    expect(calls[1]?.get("If-None-Match")).toBe('"registry-v1"');
  });

  it("服务端 426 映射为稳定兼容错误", async () => {
    const client = new RegistryClient({
      fetch: async () =>
        json(
          {
            detail: {
              message: "upgrade required",
              min_cli_version: "2.0.0",
            },
          },
          { status: 426 },
        ),
    });
    await expect(client.registry(bootstrap(), "secret")).rejects.toMatchObject({
      code: ErrorCode.incompatible,
    });
  });
});
