import { describe, expect, it } from "vitest";

import { createAgentEnvironment, getAgentAdapter } from "../src/agents.js";

describe("Windows/macOS/Linux Agent 路径契约", () => {
  it("Windows 使用 win32 分隔符和项目路径", () => {
    const environment = createAgentEnvironment({
      platform: "win32",
      env: { CODEX_HOME: "C:\\Users\\alice\\.codex" },
      homeDir: "C:\\Users\\alice",
      cwd: "C:\\work\\project",
    });
    expect(getAgentAdapter("codex").resolveUserDir(environment)).toBe(
      "C:\\Users\\alice\\.codex\\skills",
    );
    expect(getAgentAdapter("claude").resolveProjectDir(environment)).toBe(
      "C:\\work\\project\\.claude\\skills",
    );
  });

  it.each(["darwin", "linux"] as const)("%s 使用 POSIX 用户与项目目录", (platform) => {
    const environment = createAgentEnvironment({
      platform,
      env: {},
      homeDir: "/Users/alice",
      cwd: "/work/project",
    });
    expect(getAgentAdapter("openclaw").resolveUserDir(environment)).toBe(
      "/Users/alice/.openclaw/skills",
    );
    expect(getAgentAdapter("openclaw").resolveProjectDir(environment)).toBe(
      "/work/project/skills",
    );
  });
});
