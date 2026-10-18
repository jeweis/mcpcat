import { createInterface } from "node:readline/promises";
import { stdin, stderr } from "node:process";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import { checkArgs, required, usage, type ManagementArgs } from "./management-args.js";
import { assertNewTarget, saveNewFile } from "./management-files.js";
import type { ManagementClient } from "./management-http.js";
import { parseSemver } from "./semver.js";

export interface ManagementContext {
  interactive: boolean;
  confirm: (message: string) => Promise<boolean>;
}

export async function askManagementConfirmation(message: string): Promise<boolean> {
  const prompt = createInterface({input: stdin, output: stderr});
  try { return (await prompt.question(`${message}\n输入 yes 确认：`)).trim() === "yes"; }
  finally { prompt.close(); }
}

export async function confirmWrite(args: ManagementArgs, context: ManagementContext, impact: string): Promise<void> {
  if (args.flags.has("yes")) return;
  if (!context.interactive) throw new McpcatError(ErrorCode.nonInteractiveInput,
    "此命令会修改远程 mcpcat，非交互模式必须显式 --yes", ExitCode.nonInteractive);
  if (!await context.confirm(impact)) usage("操作已取消，未发送写请求");
}

export function object(value: unknown): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new McpcatError(ErrorCode.schema, "管理响应结构不符合预期", ExitCode.schema);
  }
  return value as Record<string, unknown>;
}

export function objects(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) throw new McpcatError(ErrorCode.schema, "管理列表结构不符合预期", ExitCode.schema);
  return value.map(object);
}

export function id(value: string | undefined): string {
  if (!value || value === "." || value === ".." || value.length > 4096 || /[/\\]/.test(value)
    || [...value].some(char => char.charCodeAt(0) < 32)) usage("资源标识无效");
  return encodeURIComponent(value);
}

export function version(args: ManagementArgs): string {
  const value = required(args, "version");
  try { parseSemver(value); } catch { usage("--version 必须为明确的 SemVer，不能使用 latest"); }
  return encodeURIComponent(value);
}

export function mode(args: ManagementArgs, both = false): string {
  const value = args.values.mode ?? "local";
  if (!(both ? ["local", "remote", "both"] : ["local", "remote"]).includes(value)) usage("--mode 取值无效");
  return value;
}

export function fileQuery(args: ManagementArgs, variants: boolean): string {
  const query = new URLSearchParams();
  if (variants) {
    query.set("profile", mode(args));
    if (args.values.binding) query.set("binding", args.values.binding);
  }
  if (args.action === "file") query.set("path", required(args, "path"));
  return query.size ? `?${query.toString()}` : "";
}

export async function download(client: ManagementClient, path: string, output: string): Promise<unknown> {
  await assertNewTarget(output);
  const result = await client.download(path);
  return {output: await saveNewFile(output, result.bytes), sha256: result.sha256, size: result.bytes.length};
}

/** 解析全部参数后才建立管理会话，未识别命令绝不发送业务请求。 */
export function validateManagement(args: ManagementArgs): void {
  const commands: Record<string, [number, string[]]> = args.group === "servers" ? {
    list: [0, []], info: [1, []], config: [1, ["output"]],
    add: [1, ["file", "yes"]], update: [1, ["file", "yes"]],
    start: [1, ["yes"]], stop: [1, ["yes"]], restart: [1, ["yes"]], delete: [1, ["yes"]],
  } : args.group === "skills" ? {
    list: [0, []], info: [1, []], connections: [1, []],
    upload: [0, ["file", "version", "mode", "binding-kind", "source", "changelog", "yes"]],
    generate: [1, ["mode", "yes"]],
    file: [1, ["version", "path", "mode", "binding"]],
    download: [1, ["version", "output", "mode", "binding"]],
    publish: [1, ["version", "reviewed", "public", "yes"]],
    "withdraw-remote": [1, ["version", "binding", "yes"]],
    deprecate: [1, ["yes"]], archive: [1, ["yes"]], delete: [1, ["yes"]],
  } : {
    list: [0, ["source"]], info: [1, ["source"]], connections: [1, []],
    upload: [1, ["file", "version", "display-name", "entrypoint", "yes"]],
    config: [1, ["file", "version", "yes"]], bindings: [1, ["file", "version", "yes"]],
    publish: [1, ["version", "reviewed", "public", "yes"]], withdraw: [1, ["version", "yes"]],
    file: [1, ["version", "path"]], download: [1, ["version", "output"]],
  };
  const command = commands[args.action];
  if (!command) usage("未知管理命令，请使用 servers --help、skills manage --help 或 apps --help");
  checkArgs(args, command[0], command[1]);
  for (const target of args.targets) id(target);
  if (args.values.source !== undefined) id(args.values.source);
  if (["upload", "file", "download", "publish", "withdraw", "withdraw-remote", "bindings"].includes(args.action)
      || args.group === "apps" && args.action === "config") version(args);
  if (["upload", "add", "update", "bindings"].includes(args.action)
      || args.group === "apps" && args.action === "config") required(args, "file");
  if (args.action === "file") required(args, "path");
  if (args.action === "download") required(args, "output");
  if (args.action === "publish" && !args.flags.has("reviewed")) usage("发布前必须审核内容，并显式 --reviewed");
}
