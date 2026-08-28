import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

import { ErrorCode, ExitCode, McpcatError } from "../errors.js";
import { defaultConfigDir } from "../profiles.js";
import { assertAgentId, type AgentId } from "./types.js";

interface AgentDocument {
  schemaVersion: 1;
  defaultAgent?: AgentId;
}

export class AgentStore {
  constructor(readonly path = join(defaultConfigDir(), "agents.json")) {}

  private async read(): Promise<AgentDocument> {
    try {
      const value = JSON.parse(await readFile(this.path, "utf8")) as unknown;
      if (value === null || typeof value !== "object" || Array.isArray(value)) {
        throw new Error("invalid agent document");
      }
      const input = value as Record<string, unknown>;
      if (input.schemaVersion !== 1) {
        throw new Error("invalid agent schema version");
      }
      const defaultAgent = input.defaultAgent;
      return {
        schemaVersion: 1,
        ...(typeof defaultAgent === "string"
          ? { defaultAgent: assertAgentId(defaultAgent) }
          : {}),
      };
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return { schemaVersion: 1 };
      }
      if (error instanceof McpcatError) {
        throw error;
      }
      throw new McpcatError(
        ErrorCode.configInvalid,
        "Agent 配置文件无效",
        ExitCode.configuration,
        { path: this.path },
        { cause: error },
      );
    }
  }

  private async write(document: AgentDocument): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, `${JSON.stringify(document, null, 2)}\n`, {
        flag: "wx",
        mode: 0o600,
      });
      await rename(temporary, this.path);
    } finally {
      await unlink(temporary).catch(() => undefined);
    }
  }

  async getDefault(): Promise<AgentId | undefined> {
    return (await this.read()).defaultAgent;
  }

  async use(agent: AgentId): Promise<void> {
    await this.write({ schemaVersion: 1, defaultAgent: agent });
  }
}
