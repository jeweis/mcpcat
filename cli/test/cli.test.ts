import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it, vi } from "vitest";

import { runCli } from "../src/cli.js";
import { ErrorCode, ExitCode } from "../src/errors.js";
import { ProfileStore } from "../src/profiles.js";
import { AgentStore, createAgentContext } from "../src/agents.js";

const SECRET = "ci-super-secret-key";

function requestUrl(input: string | URL | Request): string {
  return input instanceof Request ? input.url : String(input);
}

function parseJsonRecord(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Expected JSON object");
  }
  return parsed as Record<string, unknown>;
}

function objectField(record: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = record[key];
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Expected object field ${key}`);
  }
  return value as Record<string, unknown>;
}

function arrayField(record: Record<string, unknown>, key: string): unknown[] {
  const value = record[key];
  if (!Array.isArray(value)) {
    throw new Error(`Expected array field ${key}`);
  }
  return value;
}

function bootstrap(minCliVersion = "0.1.0"): Record<string, unknown> {
  return {
    instance_name: "CI Registry",
    base_url: "https://ci.example.com",
    api_version: "v1",
    registry_schema_version: "1.0.0",
    auth_header_name: "X-CI-Key",
    registry_path: "/api/skills/registry",
    min_cli_version: minCliVersion,
    recommended_cli_version: minCliVersion,
  };
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function profileStore(): Promise<ProfileStore> {
  const directory = await mkdtemp(join(tmpdir(), "mcpcat-cli-command-"));
  return new ProfileStore(join(directory, "profiles.json"));
}

async function agentStore(): Promise<AgentStore> {
  const directory = await mkdtemp(join(tmpdir(), "mcpcat-cli-agent-"));
  return new AgentStore(join(directory, "agents.json"));
}

describe("runCli", () => {
  it("在 CI 使用环境变量非交互连接，且不持久化或输出 API Key", async () => {
    const profiles = await profileStore();
    const stdout: string[] = [];
    const stderr: string[] = [];
    const credentialSet = vi.fn();
    const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith("/bootstrap")) {
        return json(bootstrap());
      }
      expect(new Headers(init?.headers).get("X-CI-Key")).toBe(SECRET);
      return json({ registry_schema_version: "1.0.0", api_version: "v1", skills: [] });
    });

    const exitCode = await runCli(["connect", "--json"], {
      env: {
        CI: "true",
        MCPCAT_URL: "https://ci.example.com",
        MCPCAT_API_KEY: SECRET,
      },
      fetch,
      profileStore: profiles,
      credentialStore: {
        get: async () => undefined,
        set: credentialSet,
        delete: async () => undefined,
      },
      stdinIsTTY: false,
      stdout: (value) => stdout.push(value),
      stderr: (value) => stderr.push(value),
      now: () => new Date("2026-08-29T00:00:00.000Z"),
    });

    expect(exitCode).toBe(ExitCode.success);
    expect(credentialSet).not.toHaveBeenCalled();
    expect(stdout.join("") + stderr.join("")).not.toContain(SECRET);
    expect(parseJsonRecord(stdout.join(""))).toMatchObject({
      ok: true,
      data: { profile: "default", credentialPersistence: "environment" },
    });
    expect(await readFile(profiles.path, "utf8")).not.toContain(SECRET);
  });

  it("非交互模式缺少 API Key 时快速失败", async () => {
    const stdout: string[] = [];
    const exitCode = await runCli(["connect", "https://ci.example.com", "--json"], {
      env: { CI: "true" },
      fetch: async () => json(bootstrap()),
      profileStore: await profileStore(),
      credentialStore: null,
      stdinIsTTY: false,
      stdout: (value) => stdout.push(value),
      stderr: () => undefined,
    });

    expect(exitCode).toBe(ExitCode.nonInteractive);
    expect(parseJsonRecord(stdout.join(""))).toMatchObject({
      ok: false,
      error: { code: ErrorCode.nonInteractiveInput },
    });
  });

  it("不兼容 API 返回稳定 JSON 错误和退出码", async () => {
    const stdout: string[] = [];
    const exitCode = await runCli(["connect", "https://ci.example.com", "--json"], {
      env: { CI: "true", MCPCAT_API_KEY: SECRET },
      fetch: async () => json(bootstrap("2.0.0")),
      profileStore: await profileStore(),
      credentialStore: null,
      stdinIsTTY: false,
      stdout: (value) => stdout.push(value),
      stderr: () => undefined,
    });

    expect(exitCode).toBe(ExitCode.compatibility);
    expect(parseJsonRecord(stdout.join(""))).toMatchObject({
      ok: false,
      error: { code: ErrorCode.incompatible },
    });
    expect(stdout.join("")).not.toContain(SECRET);
  });

  it("交互凭证在 Keychain 不可用时仅本次使用并明确警告", async () => {
    const stdout: string[] = [];
    const stderr: string[] = [];
    const exitCode = await runCli(["connect", "personal", "https://ci.example.com", "--json"], {
      env: {},
      fetch: async (input) =>
        requestUrl(input).endsWith("/bootstrap")
          ? json(bootstrap())
          : json({ registry_schema_version: "1.0.0", api_version: "v1", skills: [] }),
      profileStore: await profileStore(),
      credentialStore: null,
      prompt: async () => SECRET,
      stdinIsTTY: true,
      stdout: (value) => stdout.push(value),
      stderr: (value) => stderr.push(value),
    });

    expect(exitCode).toBe(ExitCode.success);
    expect(parseJsonRecord(stdout.join(""))).toMatchObject({
      data: { profile: "personal", credentialPersistence: "session-only" },
    });
    expect(stderr.join("")).toContain("Keychain 不可用");
    expect(stdout.join("") + stderr.join("")).not.toContain(SECRET);
  });

  it("skills list 使用 Bootstrap/Registry 并隐藏环境变量凭证", async () => {
    const stdout: string[] = [];
    const stderr: string[] = [];
    const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
      const url = requestUrl(input);
      if (url.endsWith("/bootstrap")) {
        return json(bootstrap());
      }
      expect(new Headers(init?.headers).get("X-CI-Key")).toBe(SECRET);
      return new Response(
        JSON.stringify({
          registry_schema_version: "1.0.0",
          api_version: "v1",
          skills: [
            {
              slug: "mysql-tools",
              display_name: "MySQL Tools",
              description: "Manage MySQL from agents",
              source_type: "mcp-generated",
              status: "published",
              latest_published_version: "1.0.0",
              compatibility: { mcporter: ">=0.1.0" },
              sha256: "abc123",
              size: 100,
              download_url: "https://ci.example.com/api/skills/mysql-tools/versions/1.0.0/download",
            },
          ],
        }),
        {
          status: 200,
          headers: { "Content-Type": "application/json", ETag: '"registry-v1"' },
        },
      );
    });

    const exitCode = await runCli(["skills", "list", "--json"], {
      env: {
        MCPCAT_URL: "https://ci.example.com",
        MCPCAT_API_KEY: SECRET,
      },
      fetch,
      profileStore: await profileStore(),
      credentialStore: null,
      stdinIsTTY: false,
      stdout: (value) => stdout.push(value),
      stderr: (value) => stderr.push(value),
    });

    expect(exitCode).toBe(ExitCode.success);
    expect(parseJsonRecord(stdout.join(""))).toMatchObject({
      ok: true,
      data: {
        source: "environment",
        etag: '"registry-v1"',
        skills: [{ slug: "mysql-tools", latest_published_version: "1.0.0" }],
      },
    });
    expect(stdout.join("") + stderr.join("")).not.toContain(SECRET);
  });

  it("agents use/list 管理默认 Agent", async () => {
    const store = await agentStore();
    const stdout: string[] = [];

    const useExitCode = await runCli(["agents", "use", "codex", "--json"], {
      agentStore: store,
      stdout: (value) => stdout.push(value),
      stderr: () => undefined,
    });
    const listExitCode = await runCli(["agents", "list", "--json"], {
      agentStore: store,
      stdout: (value) => stdout.push(value),
      stderr: () => undefined,
    });

    expect(useExitCode).toBe(ExitCode.success);
    expect(listExitCode).toBe(ExitCode.success);
    const events = stdout.map((line) => parseJsonRecord(line));
    expect(events[0]).toMatchObject({ ok: true, data: { defaultAgent: "codex" } });
    const listData = objectField(events[1] ?? {}, "data");
    expect(listData.defaultAgent).toBe("codex");
    expect(arrayField(listData, "agents")).toContainEqual(
      expect.objectContaining({ id: "codex", isDefault: true }),
    );
  });

  it("agents detect 返回当前环境可见的 Agent", async () => {
    const stdout: string[] = [];
    const exitCode = await runCli(["agents", "detect", "--json"], {
      agentContext: createAgentContext({
        cwd: "/workspace/project",
        homeDir: "/home/alice",
        env: {},
        pathExists: async (path) => path === "/home/alice/.claude",
        commandExists: async () => false,
      }),
      stdout: (value) => stdout.push(value),
      stderr: () => undefined,
    });

    expect(exitCode).toBe(ExitCode.success);
    const data = objectField(parseJsonRecord(stdout.join("")), "data");
    expect(arrayField(data, "detections")).toContainEqual(
      expect.objectContaining({ agent: "claude", detected: true }),
    );
    expect(arrayField(data, "detections")).toContainEqual(
      expect.objectContaining({ agent: "generic", detected: false }),
    );
  });
});
