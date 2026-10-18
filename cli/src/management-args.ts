import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import type { GlobalOptions } from "./args.js";

const VALUE_FLAGS = new Set(["profile", "file", "version", "mode", "source",
  "binding-kind", "binding", "path", "output", "changelog", "entrypoint", "display-name"]);
const BOOL_FLAGS = new Set(["yes", "reviewed", "public", "json", "non-interactive", "allow-http", "help"]);

export interface ManagementArgs {
  group: "servers" | "skills" | "apps";
  action: string;
  targets: string[];
  values: Record<string, string>;
  flags: Set<string>;
  common: GlobalOptions;
}

export function usage(message: string): never {
  throw new McpcatError(ErrorCode.usage, message, ExitCode.usage);
}

/** 独立解析新增命令；旧安装命令完全交回原解析器。 */
export function parseManagementArgs(args: readonly string[]): ManagementArgs | null {
  const positionals: string[] = [];
  const values: Record<string, string> = {};
  const flags = new Set<string>();
  let invalid: string | undefined;
  for (let i = 0; i < args.length; i++) {
    const token = args[i]!;
    if (token === "-h") { flags.add("help"); continue; }
    if (!token.startsWith("--")) { positionals.push(token); continue; }
    const name = token.slice(2);
    if (VALUE_FLAGS.has(name)) {
      const value = args[i + 1];
      if (value === undefined || value.startsWith("--")) { invalid = `${token} 需要值`; continue; }
      if (values[name] !== undefined) invalid = `${token} 不可重复`;
      values[name] = value;
      i++;
    } else if (BOOL_FLAGS.has(name)) flags.add(name);
    else invalid = `未知管理选项：${token}`;
  }
  const group = positionals[0];
  if (group !== "servers" && group !== "apps" &&
      !(group === "skills" && positionals[1] === "manage")) return null;
  if (invalid !== undefined) usage(invalid);
  const offset = group === "skills" ? 2 : 1;
  return {
    group, action: positionals[offset] ?? "", targets: positionals.slice(offset + 1), values, flags,
    common: {json: flags.has("json"), nonInteractive: flags.has("non-interactive"),
      allowHttp: flags.has("allow-http"), help: flags.has("help"), version: false,
      agents: [], allDetectedAgents: false, all: false,
      ...(values.profile === undefined ? {} : {profile: values.profile})},
  };
}

/** 每条命令拒绝无关选项，尤其禁止上游来源进入托管写入路径。 */
export function checkArgs(args: ManagementArgs, count: number, allowed: string[] = []): void {
  if (args.targets.length !== count) usage(`${args.group} ${args.action} 需要 ${count} 个资源参数；请查看 --help`);
  const common = new Set(["profile", "json", "non-interactive", "allow-http", "help", ...allowed]);
  for (const key of [...Object.keys(args.values), ...args.flags]) {
    if (!common.has(key)) usage(`${args.group} ${args.action} 不支持 --${key}`);
  }
}

export function required(args: ManagementArgs, key: string): string {
  const value = args.values[key];
  if (!value) usage(`必须指定 --${key}`);
  return value;
}

export const MANAGEMENT_HELP = `mcpcat 管理命令（保留所有原安装命令）

servers list
servers info <name>
servers config <name> [--output <新文件>]
servers add|update <name> --file <配置.json> --yes
servers start|stop|restart|delete <name> --yes

skills manage list
skills manage info|connections <slug>
skills manage upload --file <文件夹或ZIP> --version <SemVer>
  [--mode local|remote|both] [--binding-kind independent|service|catalog]
  [--source <服务名>] [--changelog <说明>] --yes
skills manage generate <服务名|catalog> [--mode local|remote|both] --yes
skills manage file <slug> --version <SemVer> --path <包内文件>
skills manage download <slug> --version <SemVer> --output <新文件>
  （file/download 可指定 --mode local|remote 和 --binding <绑定标识>）
skills manage publish <slug> --version <SemVer> --reviewed [--public] --yes
skills manage withdraw-remote <slug> --version <SemVer> [--binding <绑定标识>] --yes
skills manage deprecate|archive|delete <slug> --yes

apps list [--source <MCP服务名>]
apps info <slug> [--source <MCP服务名>]
apps connections <slug>
apps upload <slug> --file <HTML|JSON|ZIP> --version <SemVer>
  --display-name <名称> [--entrypoint <入口路径>] --yes
apps config|bindings <slug> --version <SemVer> --file <配置.json> --yes
apps publish <slug> --version <SemVer> --reviewed [--public] --yes
apps withdraw <slug> --version <SemVer> --yes
apps file <slug> --version <SemVer> --path <包内文件>
apps download <slug> --version <SemVer> --output <新文件>

通用：--profile <连接名> --json --non-interactive --allow-http
写入需明确确认；非交互必须 --yes；发布还需 --reviewed。
update/config/bindings 是完整替换；上传/生成只创建草稿。
上游 Apps 仅查询，不提供测试、渲染或发布命令。
`;
