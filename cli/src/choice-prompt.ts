import { createInterface } from "node:readline/promises";

import type { AgentChoicePrompt } from "./agents/select.js";
import type { AgentId } from "./agents/types.js";
import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export function createAgentChoicePrompt(
  input: NodeJS.ReadableStream = process.stdin,
  output: NodeJS.WritableStream = process.stderr,
): AgentChoicePrompt {
  return async (agents: readonly AgentId[]): Promise<AgentId> => {
    const reader = createInterface({ input, output });
    try {
      output.write("检测到多个 Agent：\n");
      agents.forEach((agent, index) => output.write(`  ${index + 1}. ${agent}\n`));
      const answer = await reader.question("请选择 Agent 编号: ");
      const selected = agents[Number(answer) - 1];
      if (selected === undefined) {
        throw new McpcatError(
          ErrorCode.agentAmbiguous,
          "Agent 选择无效",
          ExitCode.usage,
        );
      }
      return selected;
    } finally {
      reader.close();
    }
  };
}
