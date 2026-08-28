import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  AgentStore,
  createAgentContext,
  getAgentAdapter,
  type AgentId,
} from "../src/agents.js";
import { ErrorCode } from "../src/errors.js";

describe("Agent adapters", () => {
  it("解析 Codex 用户级和项目级 Skills 目录", () => {
    const adapter = getAgentAdapter("codex");
    const context = createAgentContext({
      cwd: "/workspace/project",
      homeDir: "/home/alice",
      env: { CODEX_HOME: "/custom/codex" },
      pathExists: async () => false,
      commandExists: async () => false,
    });

    expect(adapter.resolveUserDir(context)).toBe("/custom/codex/skills");
    expect(adapter.resolveProjectDir(context)).toBe("/workspace/project/.codex/skills");
  });

  it("解析 Claude Code 与 OpenClaw 路径契约", () => {
    const context = createAgentContext({
      cwd: "/workspace/project",
      homeDir: "/home/alice",
      env: {},
      pathExists: async () => false,
      commandExists: async () => false,
    });

    expect(getAgentAdapter("claude").resolveUserDir(context)).toBe(
      "/home/alice/.claude/skills",
    );
    expect(getAgentAdapter("claude").resolveProjectDir(context)).toBe(
      "/workspace/project/.claude/skills",
    );
    expect(getAgentAdapter("openclaw").resolveUserDir(context)).toBe(
      "/home/alice/.openclaw/skills",
    );
    expect(getAgentAdapter("openclaw").resolveProjectDir(context)).toBe("/workspace/project/skills");
  });

  it("Generic Adapter 要求显式 target-dir", async () => {
    const adapter = getAgentAdapter("generic");
    const context = createAgentContext({
      cwd: "/workspace/project",
      homeDir: "/home/alice",
      env: {},
      pathExists: async () => false,
      commandExists: async () => false,
    });

    expect(adapter.resolveUserDir(context)).toBeUndefined();
    expect(adapter.resolveProjectDir(context)).toBeUndefined();
    await expect(adapter.validateTarget({ scope: "user" }, context)).rejects.toMatchObject({
      code: ErrorCode.targetInvalid,
    });
  });

  it("检测已存在的 Agent 根目录", async () => {
    const adapter = getAgentAdapter("codex");
    const context = createAgentContext({
      cwd: "/workspace/project",
      homeDir: "/home/alice",
      env: {},
      pathExists: async (path) => path === "/home/alice/.codex",
      commandExists: async () => false,
    });

    await expect(adapter.detect(context)).resolves.toBe(true);
  });
});

describe("AgentStore", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(
      directories.splice(0).map((directory) => rm(directory, { force: true, recursive: true })),
    );
  });

  it("保存默认 Agent", async () => {
    const directory = await mkdtemp(join(tmpdir(), "mcpcat-agent-store-"));
    directories.push(directory);
    const store = new AgentStore(join(directory, "agents.json"));

    await store.use("codex");

    await expect(store.getDefault()).resolves.toBe("codex");
  });

  it("拒绝未知 Agent", async () => {
    try {
      getAgentAdapter("unknown" as AgentId);
      throw new Error("Expected getAgentAdapter to fail");
    } catch (error) {
      expect(error).toMatchObject({ code: ErrorCode.agentNotFound });
    }
  });
});
