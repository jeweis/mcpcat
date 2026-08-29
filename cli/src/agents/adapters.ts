import { ErrorCode, ExitCode, McpcatError } from "../errors.js";
import {
  type AgentAdapter,
  type AgentEnvironment,
  type AgentId,
  normalizeTarget,
  type TargetRequest,
} from "./types.js";

interface AdapterDefinition {
  id: Exclude<AgentId, "generic">;
  displayName: string;
  command: string;
  configDirectory: string;
  projectDirectory?: string;
  projectSkillsDirectory: string;
  detectByConfigDirectory?: boolean;
  userHint: string;
  projectHint: string;
}

class StandardAgentAdapter implements AgentAdapter {
  readonly id: Exclude<AgentId, "generic">;
  readonly displayName: string;

  constructor(private readonly definition: AdapterDefinition) {
    this.id = definition.id;
    this.displayName = definition.displayName;
  }

  async detect(environment: AgentEnvironment): Promise<boolean> {
    const configRoot = this.userConfigRoot(environment);
    if (await environment.commandExists(this.definition.command)) return true;
    return this.definition.detectByConfigDirectory !== false &&
      await environment.pathExists(configRoot);
  }

  private userConfigRoot(environment: AgentEnvironment): string {
    return environment.path.join(environment.homeDir, this.definition.configDirectory);
  }

  resolveUserDir(environment: AgentEnvironment): string {
    return environment.path.join(this.userConfigRoot(environment), "skills");
  }

  resolveProjectDir(environment: AgentEnvironment): string {
    return this.definition.projectDirectory === undefined
      ? environment.path.join(environment.cwd, this.definition.projectSkillsDirectory)
      : environment.path.join(
          environment.cwd,
          this.definition.projectDirectory,
          this.definition.projectSkillsDirectory,
        );
  }

  async validateTarget(request: TargetRequest, environment: AgentEnvironment): Promise<string> {
    const requested = request.targetDir ?? (
      request.scope === "user"
        ? this.resolveUserDir(environment)
        : this.resolveProjectDir(environment)
    );
    return normalizeTarget(requested, environment);
  }

  postInstall(skill: string, target: string): string[] {
    const hint = target.includes(`/${this.definition.configDirectory}/`)
      ? this.definition.userHint
      : this.definition.projectHint;
    return [`${this.displayName} Skill ${skill} 已安装到 ${target}`, hint];
  }
}

class GenericAgentAdapter implements AgentAdapter {
  readonly id = "generic" as const;
  readonly displayName = "Generic Agent";

  async detect(): Promise<boolean> {
    return false;
  }

  resolveUserDir(): undefined {
    return undefined;
  }

  resolveProjectDir(): undefined {
    return undefined;
  }

  async validateTarget(request: TargetRequest, environment: AgentEnvironment): Promise<string> {
    if (request.targetDir === undefined) {
      throw new McpcatError(
        ErrorCode.targetInvalid,
        "Generic Agent 必须显式提供 --target-dir",
        ExitCode.configuration,
      );
    }
    return normalizeTarget(request.targetDir, environment);
  }

  postInstall(skill: string, target: string): string[] {
    return [`Generic Skill ${skill} 已安装到 ${target}`, "请按目标 Agent 文档刷新 Skills。"];
  }
}

export const codexAdapter = new StandardAgentAdapter({
  id: "codex",
  displayName: "Codex",
  command: "codex",
  configDirectory: ".agents",
  projectDirectory: ".agents",
  projectSkillsDirectory: "skills",
  detectByConfigDirectory: false,
  userHint: "请在新 Codex 会话中确认 Skill 可发现。",
  projectHint: "请在当前项目的新 Codex 会话中确认 Skill 可发现。",
});

export const claudeAdapter = new StandardAgentAdapter({
  id: "claude",
  displayName: "Claude Code",
  command: "claude",
  configDirectory: ".claude",
  projectDirectory: ".claude",
  projectSkillsDirectory: "skills",
  userHint: "Claude Code 将从用户 Skills 目录发现此 Skill。",
  projectHint: "若首次创建项目 Skills 目录后未发现，请重启 Claude Code 会话。",
});

export const openClawAdapter = new StandardAgentAdapter({
  id: "openclaw",
  displayName: "OpenClaw",
  command: "openclaw",
  configDirectory: ".openclaw",
  projectSkillsDirectory: "skills",
  userHint: "OpenClaw shared managed Skills 已更新；node-hosted 环境可能需要重启 node host。",
  projectHint: "OpenClaw workspace Skills 已更新。",
});

export const workBuddyAdapter = new StandardAgentAdapter({
  id: "workbuddy",
  displayName: "WorkBuddy",
  command: "workbuddy",
  configDirectory: ".workbuddy",
  projectDirectory: ".workbuddy",
  projectSkillsDirectory: "skills",
  userHint: "WorkBuddy 用户 Skills 已更新。",
  projectHint: "WorkBuddy 项目 Skills 已更新。",
});

export const codeBuddyAdapter = new StandardAgentAdapter({
  id: "codebuddy",
  displayName: "CodeBuddy",
  command: "codebuddy",
  configDirectory: ".codebuddy",
  projectDirectory: ".codebuddy",
  projectSkillsDirectory: "skills",
  userHint: "CodeBuddy 用户 Skills 已更新。",
  projectHint: "CodeBuddy 项目 Skills 已更新。",
});

export const qoderAdapter = new StandardAgentAdapter({
  id: "qoder",
  displayName: "Qoder",
  command: "qoder",
  configDirectory: ".qoder",
  projectDirectory: ".qoder",
  projectSkillsDirectory: "skills",
  userHint: "Qoder 用户 Skills 已更新。",
  projectHint: "Qoder 项目 Skills 已更新。",
});

export const piAdapter = new StandardAgentAdapter({
  id: "pi",
  displayName: "Pi",
  command: "pi",
  configDirectory: ".agents",
  projectDirectory: ".agents",
  projectSkillsDirectory: "skills",
  detectByConfigDirectory: false,
  userHint: "Pi 可从通用 .agents Skills 目录发现此 Skill。",
  projectHint: "Pi 可从项目 .agents Skills 目录发现此 Skill。",
});

export const dshAdapter = new StandardAgentAdapter({
  id: "dsh",
  displayName: "DeepSeek Harness (DSH)",
  command: "dsh",
  configDirectory: ".agents",
  projectDirectory: ".agents",
  projectSkillsDirectory: "skills",
  detectByConfigDirectory: false,
  userHint: "DeepSeek Harness 可从通用 .agents Skills 目录发现此 Skill。",
  projectHint: "DeepSeek Harness 可从项目 .agents Skills 目录发现此 Skill。",
});

export const cursorAdapter = new StandardAgentAdapter({
  id: "cursor",
  displayName: "Cursor",
  command: "cursor",
  configDirectory: ".agents",
  projectDirectory: ".agents",
  projectSkillsDirectory: "skills",
  detectByConfigDirectory: false,
  userHint: "Cursor 可从通用 .agents Skills 目录发现此 Skill。",
  projectHint: "Cursor 可从项目 .agents Skills 目录发现此 Skill。",
});

export const genericAdapter = new GenericAgentAdapter();

const ADAPTER_ENTRIES: ReadonlyArray<readonly [AgentId, AgentAdapter]> = [
  [codexAdapter.id, codexAdapter],
  [claudeAdapter.id, claudeAdapter],
  [openClawAdapter.id, openClawAdapter],
  [workBuddyAdapter.id, workBuddyAdapter],
  [codeBuddyAdapter.id, codeBuddyAdapter],
  [qoderAdapter.id, qoderAdapter],
  [piAdapter.id, piAdapter],
  [dshAdapter.id, dshAdapter],
  [cursorAdapter.id, cursorAdapter],
  [genericAdapter.id, genericAdapter],
];

export const AGENT_ADAPTERS: ReadonlyMap<AgentId, AgentAdapter> = new Map(ADAPTER_ENTRIES);

export function getAgentAdapter(id: AgentId): AgentAdapter {
  const adapter = AGENT_ADAPTERS.get(id);
  if (adapter === undefined) {
    throw new McpcatError(
      ErrorCode.agentNotFound,
      `Agent Adapter 不存在：${id}`,
      ExitCode.configuration,
    );
  }
  return adapter;
}

export async function detectAgents(environment: AgentEnvironment): Promise<AgentId[]> {
  const detected: AgentId[] = [];
  for (const adapter of AGENT_ADAPTERS.values()) {
    if (adapter.id !== "generic" && await adapter.detect(environment)) {
      detected.push(adapter.id);
    }
  }
  return detected;
}
