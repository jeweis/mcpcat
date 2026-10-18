import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import { required, type ManagementArgs } from "./management-args.js";
import { confirmWrite, download, fileQuery, id, object, objects, version, type ManagementContext } from "./management-common.js";
import { jsonInput, uploadInput } from "./management-files.js";
import type { ManagementClient } from "./management-http.js";

async function sourceApps(client: ManagementClient, source: string): Promise<Record<string, unknown>> {
  return object(await client.call("GET", `/api/apps/discovery/sources/${id(source)}`));
}

/** 来源应用只读取声明，不读取 UI、调用入口或修改上游。 */
async function listApps(client: ManagementClient): Promise<unknown> {
  const hosted = await client.call("GET", "/api/apps");
  let sources: Record<string, unknown>[];
  try { sources = objects(await client.call("GET", "/api/apps/discovery/sources")); }
  catch (error) {
    if (error instanceof McpcatError && error.code === ErrorCode.authRejected) throw error;
    return {hosted, sources: [], complete: false, discovery_error: "无法获取来源列表，托管应用结果仍有效"};
  }
  const results: Record<string, unknown>[] = Array.from({length: sources.length}, () => ({}));
  let next = 0;
  async function worker(): Promise<void> {
    while (next < sources.length) {
      const index = next++, source = sources[index];
      if (!source || typeof source.server_name !== "string") throw new McpcatError(ErrorCode.schema, "来源缺少服务名称", ExitCode.schema);
      if (source.status !== "running") {
        results[index] = {server_name: source.server_name, status: source.status, complete: false, items: []};
        continue;
      }
      try { results[index] = await sourceApps(client, source.server_name); }
      catch (error) {
        if (error instanceof McpcatError && error.code === ErrorCode.authRejected) throw error;
        results[index] = {server_name: source.server_name, complete: false, message: "来源暂不可用", items: []};
      }
    }
  }
  await Promise.all(Array.from({length: Math.min(4, sources.length)}, () => worker()));
  return {hosted, sources: results, complete: results.every(result => result.complete === true)};
}

export async function manageApps(args: ManagementArgs, client: ManagementClient,
  context: ManagementContext): Promise<unknown> {
  if (args.values.source) {
    const data = await sourceApps(client, args.values.source);
    if (args.action === "list") return data;
    const app = objects(data.items).find(item => item.name === args.targets[0]);
    if (!app) throw new McpcatError(ErrorCode.usage, "此来源中未发现该应用；标识应使用入口工具名称", ExitCode.usage);
    return {server_name: args.values.source, app, complete: data.complete};
  }
  const root = "/api/apps";
  if (args.action === "list") return listApps(client);
  const path = `${root}/${id(args.targets[0])}`;
  if (args.action === "info") return client.call("GET", path);
  if (args.action === "connections") return client.call("GET", `${path}/connections`);
  const target = `${path}/versions/${version(args)}`;
  if (args.action === "file" || args.action === "download") {
    const url = `${target}/${args.action}${fileQuery(args, false)}`;
    return args.action === "file" ? client.call("GET", url) : download(client, url, required(args, "output"));
  }
  let body: unknown;
  if (args.action === "upload") {
    const displayName = required(args, "display-name");
    const upload = await uploadInput(required(args, "file"), false), form = new FormData();
    form.set("file", new Blob([Uint8Array.from(upload.bytes)]), upload.name);
    form.set("slug", args.targets[0]!); form.set("display_name", displayName);
    form.set("version", required(args, "version"));
    if (args.values.entrypoint) form.set("entrypoint", args.values.entrypoint);
    body = form;
  } else if (["config", "bindings"].includes(args.action)) body = await jsonInput(required(args, "file"));
  else if (args.action === "publish") body = {reviewed: true, public: args.flags.has("public")};
  await confirmWrite(args, context, `apps ${args.action} ${args.targets[0]} @ ${client.baseUrl}\n`
    + (args.flags.has("public") ? "将允许匿名访问该应用的已发布版本，请确认全部公开范围。"
      : "上传仅创建草稿；config/bindings 完整替换对应配置，发布和撤回会改变可用状态。"));
  return client.call(["config", "bindings"].includes(args.action) ? "PUT" : "POST",
    args.action === "upload" ? `${root}/upload` : `${target}/${args.action}`, body);
}
