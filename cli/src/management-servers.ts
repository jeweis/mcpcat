import { required, type ManagementArgs } from "./management-args.js";
import { confirmWrite, id, object, type ManagementContext } from "./management-common.js";
import { assertNewTarget, jsonInput, saveNewFile } from "./management-files.js";
import type { ManagementClient } from "./management-http.js";

export async function manageServers(args: ManagementArgs, client: ManagementClient,
  context: ManagementContext): Promise<unknown> {
  const root = "/api/servers";
  if (args.action === "list") return client.call("GET", root);
  const path = `${root}/${id(args.targets[0])}`;
  if (args.action === "info") return client.call("GET", path);
  if (args.action === "config") {
    const target = args.values.output;
    if (target) await assertNewTarget(target);
    const data = await client.call("GET", `${path}/config`);
    if (!target) return data;
    return {output: await saveNewFile(target, Buffer.from(JSON.stringify(object(object(data).config), null, 2))),
      warning: "此私有配置文件可能含凭证，请妥善保管；只读凭证得到的配置可能不完整，不应直接覆盖原配置"};
  }
  const config = ["add", "update"].includes(args.action) ? await jsonInput(required(args, "file")) : undefined;
  await confirmWrite(args, context, `${args.group} ${args.action} ${args.targets[0]} @ ${client.baseUrl}\n`
    + (config ? "配置将完整保存并启动/重启 MCP，可能在服务端运行你配置的代码；不会与旧配置自动合并。"
      : "这会改变远程服务状态，可能中断现有连接；删除不会自动恢复配置。"));
  if (config) return client.call(args.action === "add" ? "POST" : "PUT",
    args.action === "add" ? root : path, {name: args.targets[0], config});
  return client.call(args.action === "delete" ? "DELETE" : "POST",
    args.action === "delete" ? path : `${path}/${args.action}`);
}
