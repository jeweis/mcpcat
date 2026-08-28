import { constants } from "node:fs";
import { access, lstat, readdir, realpath } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";

import type { AgentEnvironment } from "./agents/types.js";
import type { CredentialStore } from "./credentials.js";
import { RegistryClient, type Fetch } from "./http.js";
import type { InstallationStore } from "./install-lock.js";
import type { ProfileStore } from "./profiles.js";
import { CLI_VERSION } from "./version.js";

const execFileAsync = promisify(execFile);

export interface DoctorCheck {
  name: string;
  status: "ok" | "warn" | "error";
  message: string;
  details?: Record<string, unknown>;
}

export type CommandRunner = (command: string, args: readonly string[]) => Promise<string>;

export interface DoctorOptions {
  profileStore: ProfileStore;
  credentialStore?: CredentialStore;
  installationStore: InstallationStore;
  agentEnvironment: AgentEnvironment;
  env?: NodeJS.ProcessEnv;
  profileName?: string;
  allowHttp?: boolean;
  fetch?: Fetch;
  nodeVersion?: string;
  commandRunner?: CommandRunner;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function defaultCommandRunner(command: string, args: readonly string[]): Promise<string> {
  const result = await execFileAsync(command, [...args], { timeout: 5_000 });
  return result.stdout.trim();
}

export async function runDoctor(options: DoctorOptions): Promise<DoctorCheck[]> {
  const checks: DoctorCheck[] = [];
  const env = options.env ?? process.env;
  const nodeVersion = options.nodeVersion ?? process.versions.node;
  const nodeMajor = Number(nodeVersion.split(".")[0]);
  checks.push({
    name: "node",
    status: nodeMajor >= 24 ? "ok" : "error",
    message: nodeMajor >= 24
      ? `Node.js ${nodeVersion} 满足 >=24`
      : `Node.js ${nodeVersion} 不满足 CLI 要求 >=24`,
  });

  let baseUrl: string | undefined;
  let apiKey: string | undefined;
  let bootstrap: {
    instance_name: string;
    base_url: string;
    api_version: string;
    registry_schema_version: string;
    auth_header_name: string;
    registry_path: string;
    min_cli_version: string;
    recommended_cli_version: string;
  } | undefined;
  try {
    if (env.MCPCAT_URL !== undefined) {
      baseUrl = env.MCPCAT_URL;
      apiKey = env.MCPCAT_API_KEY;
      checks.push({ name: "profile", status: "ok", message: "使用环境变量连接" });
    } else {
      const profile = await options.profileStore.resolve(options.profileName);
      baseUrl = profile.baseUrl;
      apiKey = env.MCPCAT_API_KEY ?? await options.credentialStore?.get(profile.name);
      bootstrap = {
        instance_name: profile.instanceName,
        base_url: profile.baseUrl,
        api_version: profile.apiVersion,
        registry_schema_version: profile.registrySchemaVersion,
        auth_header_name: profile.authHeaderName,
        registry_path: profile.registryPath,
        min_cli_version: CLI_VERSION,
        recommended_cli_version: CLI_VERSION,
      };
      checks.push({ name: "profile", status: "ok", message: `Profile ${profile.name} 可读取` });
    }
  } catch (error) {
    checks.push({ name: "profile", status: "error", message: errorText(error) });
  }
  checks.push({
    name: "credential",
    status: apiKey === undefined || apiKey.length === 0 ? "error" : "ok",
    message: apiKey === undefined || apiKey.length === 0 ? "未找到可用凭证" : "凭证来源可用",
  });

  if (baseUrl !== undefined) {
    try {
      const client = new RegistryClient({
        ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
        ...(options.allowHttp === undefined ? {} : { allowHttp: options.allowHttp }),
      });
      bootstrap ??= await client.bootstrap(baseUrl);
      if (apiKey !== undefined && apiKey.length > 0) {
        await client.registry(bootstrap, apiKey);
      }
      checks.push({ name: "api", status: "ok", message: "Registry API 可访问且版本兼容" });
    } catch (error) {
      checks.push({ name: "api", status: "error", message: errorText(error) });
    }
  }

  try {
    const records = await options.installationStore.list();
    let inconsistent = 0;
    let unwritable = 0;
    for (const record of records) {
      try {
        const stats = await lstat(record.targetRealPath);
        if (!stats.isDirectory() || stats.isSymbolicLink()) {
          inconsistent += 1;
          continue;
        }
        if (await realpath(record.targetRealPath) !== record.targetRealPath) {
          inconsistent += 1;
        }
        await access(record.targetRealPath, constants.W_OK | constants.X_OK);
      } catch {
        unwritable += 1;
      }
    }
    checks.push({
      name: "agent-paths",
      status: inconsistent > 0 || unwritable > 0 ? "error" : "ok",
      message: inconsistent > 0 || unwritable > 0
        ? `${inconsistent} 个路径不一致，${unwritable} 个路径不存在或不可写`
        : `${records.length} 个安装目标路径正常`,
      details: { count: records.length, inconsistent, unwritable },
    });
  } catch (error) {
    checks.push({ name: "agent-paths", status: "error", message: errorText(error) });
  }

  try {
    const lockFiles = (await readdir(options.installationStore.locksDirectory))
      .filter((name) => name.endsWith(".lock"));
    checks.push({
      name: "locks",
      status: lockFiles.length === 0 ? "ok" : "warn",
      message: lockFiles.length === 0 ? "没有残留安装锁" : `发现 ${lockFiles.length} 个安装锁`,
      ...(lockFiles.length === 0 ? {} : { details: { lockFiles } }),
    });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      checks.push({ name: "locks", status: "ok", message: "安装锁目录尚未创建" });
    } else {
      checks.push({ name: "locks", status: "error", message: errorText(error) });
    }
  }

  try {
    const runner = options.commandRunner ?? defaultCommandRunner;
    const version = await runner("mcporter", ["--version"]);
    checks.push({ name: "mcporter", status: "ok", message: `mcporter 可用：${version}` });
  } catch {
    checks.push({
      name: "mcporter",
      status: "warn",
      message: "mcporter 不可用；MCP Skill 激活后将无法调用远程工具",
    });
  }
  return checks;
}
