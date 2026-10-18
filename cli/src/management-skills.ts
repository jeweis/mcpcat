import { required, usage, type ManagementArgs } from "./management-args.js";
import { confirmWrite, download, fileQuery, id, mode, version, type ManagementContext } from "./management-common.js";
import { uploadInput } from "./management-files.js";
import type { ManagementClient } from "./management-http.js";

export async function manageSkills(args: ManagementArgs, client: ManagementClient,
  context: ManagementContext): Promise<unknown> {
  const root = "/api/skill-variants";
  if (args.action === "list") return client.call("GET", root);
  if (args.action === "upload") {
    const selection = mode(args, true), bindingKind = args.values["binding-kind"] ?? "independent";
    if (!["independent", "service", "catalog"].includes(bindingKind)) usage("--binding-kind 无效");
    if (bindingKind === "service") required(args, "source");
    if (bindingKind !== "service" && args.values.source) usage("仅 service 绑定接受 --source");
    const upload = await uploadInput(required(args, "file"), true);
    const form = new FormData();
    form.set("file", new Blob([Uint8Array.from(upload.bytes)], {type: "application/zip"}), upload.name);
    form.set("version", required(args, "version")); form.set("selection", selection);
    form.set("binding_kind", bindingKind); form.set("source_name", args.values.source ?? "");
    form.set("changelog", args.values.changelog ?? "");
    await confirmWrite(args, context, `上传 Skill 草稿 @ ${client.baseUrl}；不会执行内容或自动发布。\n默认排除：${JSON.stringify(upload.excluded)}`);
    return {result: await client.call("POST", `${root}/upload`, form), excluded: upload.excluded};
  }
  const path = `${root}/${id(args.targets[0])}`;
  if (args.action === "info") return client.call("GET", path);
  if (args.action === "connections") return client.call("GET", `${path}/connections`);
  if (args.action === "file" || args.action === "download") {
    const target = `${path}/versions/${version(args)}/${args.action}${fileQuery(args, true)}`;
    return args.action === "file" ? client.call("GET", target) : download(client, target, required(args, "output"));
  }
  const selection = args.action === "generate" ? mode(args, true) : undefined;
  await confirmWrite(args, context, `skills manage ${args.action} ${args.targets[0]} @ ${client.baseUrl}\n`
    + (args.flags.has("public") ? "将允许匿名远程访问审核内容，请确认公开范围。" : "这会修改服务端资源状态，不是本地安装回滚；不会自动重试。"));
  if (selection) return client.call("POST", `${root}/generate/${args.targets[0] === "catalog" ? "catalog" : `service/${id(args.targets[0])}`}`, {selection});
  if (args.action === "publish") return client.call("POST", `${path}/versions/${version(args)}/publish`, {remote_public: args.flags.has("public")});
  if (args.action === "withdraw-remote") return client.call("POST", `${path}/versions/${version(args)}/withdraw-remote`, {binding: args.values.binding ?? ""});
  return client.call(args.action === "delete" ? "DELETE" : "POST",
    `/api/skills/${id(args.targets[0])}${args.action === "delete" ? "" : `/${args.action}`}`);
}
