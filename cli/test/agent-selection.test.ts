import { describe, expect, it } from "vitest";

import { selectAgents } from "../src/agents/select.js";
import { AgentStore } from "../src/agents/store.js";
import { createAgentEnvironment } from "../src/agents/types.js";
import { ErrorCode } from "../src/errors.js";

function environment() {
  return createAgentEnvironment({
    env: {},
    homeDir: "/home/test",
    cwd: "/workspace",
    pathExists: async (path) => path.endsWith(".codex") || path.endsWith(".claude"),
    commandExists: async () => false,
  });
}

describe("多 Agent 选择", () => {
  it("重复 --agent 去重并保留多个目标", async () => {
    await expect(selectAgents({
      explicit: ["codex", "claude", "codex"],
      allDetected: false,
      nonInteractive: true,
      environment: environment(),
      store: new AgentStore("/nonexistent/agents.json"),
      prompt: async () => "codex",
    })).resolves.toEqual(["codex", "claude"]);
  });

  it("多检测结果在非交互模式要求显式 Agent", async () => {
    await expect(selectAgents({
      explicit: [],
      allDetected: false,
      nonInteractive: true,
      environment: environment(),
      store: { getDefault: async () => undefined } as AgentStore,
      prompt: async () => "codex",
    })).rejects.toMatchObject({ code: ErrorCode.agentAmbiguous });
  });

  it("交互模式使用用户选择", async () => {
    await expect(selectAgents({
      explicit: [],
      allDetected: false,
      nonInteractive: false,
      environment: environment(),
      store: { getDefault: async () => undefined } as AgentStore,
      prompt: async (agents) => agents[1] ?? "codex",
    })).resolves.toEqual(["claude"]);
  });
});
