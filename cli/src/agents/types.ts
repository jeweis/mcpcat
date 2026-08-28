import { constants } from "node:fs";
import { access, lstat } from "node:fs/promises";
import { homedir } from "node:os";
import { posix, win32 } from "node:path";

import { ErrorCode, ExitCode, McpcatError } from "../errors.js";

export const AGENT_IDS = ["codex", "claude", "openclaw", "generic"] as const;
export type AgentId = (typeof AGENT_IDS)[number];
export type InstallScope = "user" | "project";

export interface AgentEnvironment {
  env: NodeJS.ProcessEnv;
  homeDir: string;
  cwd: string;
  platform: NodeJS.Platform;
  path: typeof posix;
  pathExists(path: string): Promise<boolean>;
  commandExists(command: string): Promise<boolean>;
}

export interface TargetRequest {
  scope: InstallScope;
  targetDir?: string;
}

export interface AgentAdapter {
  readonly id: AgentId;
  readonly displayName: string;
  detect(environment: AgentEnvironment): Promise<boolean>;
  resolveUserDir(environment: AgentEnvironment): string | undefined;
  resolveProjectDir(environment: AgentEnvironment): string | undefined;
  validateTarget(request: TargetRequest, environment: AgentEnvironment): Promise<string>;
  postInstall(skill: string, target: string): string[];
}

async function pathExists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

async function commandExists(
  command: string,
  env: NodeJS.ProcessEnv,
  platform: NodeJS.Platform,
  pathApi: typeof posix,
): Promise<boolean> {
  const suffixes = platform === "win32"
    ? (env.PATHEXT ?? ".EXE;.CMD;.BAT").split(";")
    : [""];
  for (const directory of (env.PATH ?? "").split(pathApi.delimiter).filter(Boolean)) {
    for (const suffix of suffixes) {
      try {
        await access(pathApi.join(directory, `${command}${suffix}`), constants.X_OK);
        return true;
      } catch {
        // Continue scanning PATH.
      }
    }
  }
  return false;
}

export function createAgentEnvironment(options: {
  env?: NodeJS.ProcessEnv;
  homeDir?: string;
  cwd?: string;
  pathExists?: (path: string) => Promise<boolean>;
  commandExists?: (command: string) => Promise<boolean>;
  platform?: NodeJS.Platform;
} = {}): AgentEnvironment {
  const env = options.env ?? process.env;
  const platform = options.platform ?? process.platform;
  const pathApi = platform === "win32" ? win32 : posix;
  return {
    env,
    homeDir: options.homeDir ?? homedir(),
    cwd: pathApi.resolve(options.cwd ?? process.cwd()),
    platform,
    path: pathApi,
    pathExists: options.pathExists ?? pathExists,
    commandExists:
      options.commandExists ?? ((command) => commandExists(command, env, platform, pathApi)),
  };
}

export function assertAgentId(value: string): AgentId {
  if ((AGENT_IDS as readonly string[]).includes(value)) {
    return value as AgentId;
  }
  throw new McpcatError(
    ErrorCode.agentNotFound,
    `不支持的 Agent：${value}`,
    ExitCode.configuration,
    { agent: value, supported: AGENT_IDS },
  );
}

export function assertScope(value: string): InstallScope {
  if (value === "user" || value === "project") {
    return value;
  }
  throw new McpcatError(
    ErrorCode.targetInvalid,
    "--scope 仅支持 user 或 project",
    ExitCode.configuration,
    { scope: value },
  );
}

export function normalizeTarget(path: string, environment: AgentEnvironment): string {
  const target = environment.path.isAbsolute(path)
    ? environment.path.resolve(path)
    : environment.path.resolve(environment.cwd, path);
  if (target === environment.path.resolve(target, "..")) {
    throw new McpcatError(
      ErrorCode.targetInvalid,
      "拒绝将文件系统根目录作为 Skills 目标",
      ExitCode.configuration,
      { target },
    );
  }
  return target;
}
