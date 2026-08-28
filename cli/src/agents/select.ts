import { ErrorCode, ExitCode, McpcatError } from "../errors.js";
import { detectAgents } from "./adapters.js";
import type { AgentStore } from "./store.js";
import {
  assertAgentId,
  type AgentEnvironment,
  type AgentId,
} from "./types.js";

export type AgentChoicePrompt = (agents: readonly AgentId[]) => Promise<AgentId>;

export async function selectAgents(options: {
  explicit: readonly string[];
  allDetected: boolean;
  nonInteractive: boolean;
  environment: AgentEnvironment;
  store: AgentStore;
  prompt: AgentChoicePrompt;
}): Promise<AgentId[]> {
  const explicit = options.explicit.map(assertAgentId);
  const selected = new Set<AgentId>(explicit);
  if (options.allDetected) {
    for (const agent of await detectAgents(options.environment)) {
      selected.add(agent);
    }
  }
  if (selected.size > 0) {
    return [...selected];
  }
  const defaultAgent = await options.store.getDefault();
  if (defaultAgent !== undefined) {
    return [defaultAgent];
  }
  const detected = await detectAgents(options.environment);
  if (detected.length === 1) {
    return detected;
  }
  if (detected.length === 0) {
    throw new McpcatError(
      ErrorCode.agentNotFound,
      "未检测到 Agent；请使用 --agent，Generic 还需 --target-dir",
      ExitCode.configuration,
    );
  }
  if (options.nonInteractive) {
    throw new McpcatError(
      ErrorCode.agentAmbiguous,
      "检测到多个 Agent；非交互模式必须显式提供 --agent",
      ExitCode.nonInteractive,
      { detected },
    );
  }
  return [await options.prompt(detected)];
}
