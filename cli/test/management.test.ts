import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, stat, symlink, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import { runCli, type CliDependencies } from "../src/cli.js";
import { parseManagementArgs } from "../src/management-args.js";
import { ManagementClient, safeManagementData } from "../src/management-http.js";
import { saveNewFile, uploadInput } from "../src/management-files.js";
import { ProfileStore } from "../src/profiles.js";
import { validateAndExtractSkillZip } from "../src/skill-package.js";

const SECRET = "synthetic-management-key";
const dirs: string[] = [];
async function directory(): Promise<string> {
  const path = await mkdtemp(join(tmpdir(), "mcpcat-management-")); dirs.push(path); return path;
}
afterEach(async () => { await Promise.all(dirs.splice(0).map(path => rm(path, {recursive: true, force: true}))); });
function json(data: unknown, status = 200): Response { return new Response(JSON.stringify(data), {status}); }
function url(input: string | URL | Request): string { return input instanceof Request ? input.url : String(input); }
const bootstrap = {instance_name: "Fixture", base_url: "https://untrusted.example", api_version: "v1",
  registry_schema_version: "1.0.0", auth_header_name: "X-Fixture-Key", registry_path: "/api/skills/registry",
  min_cli_version: "1.0.0", recommended_cli_version: "1.0.0"};

async function execute(args: string[], handler: typeof globalThis.fetch = async () => json({ok: true}),
  overrides: CliDependencies = {}): Promise<{code: number; text: string; fetch: ReturnType<typeof vi.fn<typeof globalThis.fetch>>}> {
  const output: string[] = [], path = await directory();
  const fetch = vi.fn<typeof globalThis.fetch>(async (input, init) => {
    if (url(input).endsWith("/api/skills/bootstrap")) return json(bootstrap);
    return handler(input, init);
  });
  const code = await runCli([...args, "--json"], {env: {MCPCAT_URL: "https://fixture.example/gateway", MCPCAT_API_KEY: SECRET},
    fetch, profileStore: new ProfileStore(join(path, "profiles.json")), credentialStore: null,
    stdinIsTTY: false, stdout: value => output.push(value), stderr: value => output.push(value), ...overrides});
  return {code, text: output.join("\n"), fetch};
}

describe("管理入口与旧命令兼容", () => {
  it.each(["list", "info", "install", "update", "pin", "unpin", "rollback"])("skills %s 仍交给旧解析器", command => {
    expect(parseManagementArgs(["skills", command, "thing", "--agent", "codex"])).toBeNull();
  });
  it("帮助无需连接或凭证，说明三组管理能力且没有执行命令", async () => {
    const result = await execute(["apps", "--help"]);
    expect(result.code).toBe(0); expect(result.fetch).not.toHaveBeenCalled();
    expect(result.text).toContain("skills manage"); expect(result.text).not.toContain("apps test");
  });
  it.each([
    ["apps", "delete", "clock"], ["apps", "test", "clock"],
    ["apps", "publish", "clock", "--version", "1.0.0", "--yes"],
    ["apps", "withdraw", "clock", "--version", "1.0.0", "--source", "time", "--yes"],
    ["servers", "stop", "..", "--yes"], ["servers", "list", "--oops"],
    ["skills", "manage", "download", "clock", "--version", "latest", "--output", "x.zip"],
  ])("拒绝无效/未审核/上游写命令 %j 且无请求", async (...args) => {
    const result = await execute(args); expect(result.code).toBe(2); expect(result.fetch).not.toHaveBeenCalled();
  });
  it("非交互写缺 yes 不发送业务写入", async () => {
    const result = await execute(["servers", "stop", "time"]);
    expect(result.code).toBe(10); expect(result.fetch).toHaveBeenCalledTimes(1);
  });
  it("交互取消不会发送写请求", async () => {
    const confirm = vi.fn(async () => false);
    const result = await execute(["servers", "stop", "time"], undefined, {stdinIsTTY: true, managementConfirm: confirm});
    expect(result.code).toBe(2); expect(confirm).toHaveBeenCalledOnce(); expect(result.fetch).toHaveBeenCalledTimes(1);
  });
  it("固定用户实例与部署前缀，忽略 bootstrap 任意 base_url，沿用认证头", async () => {
    const result = await execute(["servers", "list"], async (input, init) => {
      expect(url(input)).toBe("https://fixture.example/gateway/api/servers");
      expect(new Headers(init?.headers).get("X-Fixture-Key")).toBe(SECRET);
      expect(init?.redirect).toBe("error"); return json({servers: {time: {status: "running"}}});
    });
    expect(result.code).toBe(0); expect(result.text).not.toContain(SECRET);
  });
  it("已保存 Profile 复用 Keychain 和自定义认证头，不重建连接", async () => {
    const store = new ProfileStore(join(await directory(), "profiles.json"));
    await store.save({name: "work", baseUrl: "https://profile.example/prefix", instanceName: "Profile",
      apiVersion: "v1", registrySchemaVersion: "1.0.0", authHeaderName: "X-Profile-Key", registryPath: "/api/skills/registry",
      createdAt: "2026-10-18T00:00:00Z", updatedAt: "2026-10-18T00:00:00Z"});
    const credential = vi.fn(async () => SECRET);
    const result = await execute(["servers", "list", "--profile", "work"], async (input, init) => {
      expect(url(input)).toBe("https://profile.example/prefix/api/servers");
      expect(new Headers(init?.headers).get("X-Profile-Key")).toBe(SECRET); return json({servers: {}});
    }, {env: {}, profileStore: store, credentialStore: {get: credential, set: async () => undefined, delete: async () => undefined}});
    expect(result.code).toBe(0); expect(credential).toHaveBeenCalledWith("work"); expect(result.fetch).toHaveBeenCalledOnce();
  });
  it.each([401, 403])("HTTP %i 不降级匿名，不输出上游错误或重试", async status => {
    const result = await execute(["servers", "stop", "time", "--yes"], async () => json({detail: "other-private-secret"}, status));
    expect(result.code).toBe(4); expect(result.text).not.toContain("other-private-secret"); expect(result.fetch).toHaveBeenCalledTimes(2);
  });
  it("超时写入标为结果未确认且不重试", async () => {
    const result = await execute(["servers", "restart", "time", "--yes"], async () => {throw new Error("other-private-secret");});
    expect(result.code).toBe(5); expect(result.text).toContain("结果未确认");
    expect(result.text).not.toContain("other-private-secret"); expect(result.fetch).toHaveBeenCalledTimes(2);
  });
});

describe("管理命令 REST 映射", () => {
  it.each([
    [["servers", "list"], "GET", "/api/servers"],
    [["servers", "info", "time"], "GET", "/api/servers/time"],
    [["servers", "start", "time", "--yes"], "POST", "/api/servers/time/start"],
    [["servers", "stop", "time", "--yes"], "POST", "/api/servers/time/stop"],
    [["servers", "restart", "time", "--yes"], "POST", "/api/servers/time/restart"],
    [["servers", "delete", "time", "--yes"], "DELETE", "/api/servers/time"],
    [["skills", "manage", "list"], "GET", "/api/skill-variants"],
    [["skills", "manage", "info", "clock"], "GET", "/api/skill-variants/clock"],
    [["skills", "manage", "connections", "clock"], "GET", "/api/skill-variants/clock/connections"],
    [["skills", "manage", "generate", "time", "--mode", "both", "--yes"], "POST", "/api/skill-variants/generate/service/time"],
    [["skills", "manage", "generate", "catalog", "--yes"], "POST", "/api/skill-variants/generate/catalog"],
    [["skills", "manage", "archive", "clock", "--yes"], "POST", "/api/skills/clock/archive"],
    [["skills", "manage", "deprecate", "clock", "--yes"], "POST", "/api/skills/clock/deprecate"],
    [["skills", "manage", "delete", "clock", "--yes"], "DELETE", "/api/skills/clock"],
    [["apps", "info", "clock"], "GET", "/api/apps/clock"],
    [["apps", "connections", "clock"], "GET", "/api/apps/clock/connections"],
    [["apps", "withdraw", "clock", "--version", "1.0.0", "--yes"], "POST", "/api/apps/clock/versions/1.0.0/withdraw"],
  ] as const)("%j", async (args, method, path) => {
    const result = await execute([...args], async (input, init) => {
      expect(url(input)).toBe(`https://fixture.example/gateway${path}`); expect(init?.method).toBe(method); return json({status: "ok"});
    }); expect(result.code).toBe(0); expect(result.fetch).toHaveBeenCalledTimes(2);
  });
  it.each(["servers", "apps"])("%s 配置文件完整传输而日志脱敏", async group => {
    const path = join(await directory(), "config.json");
    const config = group === "servers" ? {command: "node", args: ["server.js"], env: {CUSTOM: "private-value"}} : {bindings: []};
    await writeFile(path, JSON.stringify(config));
    const args = group === "servers" ? ["servers", "update", "time"] : ["apps", "bindings", "clock", "--version", "1.0.0"];
    const result = await execute([...args, "--file", path, "--yes"], async (_input, init) => {
      expect(init?.method).toBe("PUT");
      if (typeof init?.body !== "string") throw new Error("Expected JSON body");
      expect(JSON.parse(init.body)).toEqual(group === "servers" ? {name: "time", config} : config);
      return json({config});
    }); expect(result.code).toBe(0); expect(result.text).not.toContain("private-value");
  });
  it.each(["skills", "apps"])("%s 发布额外携带审核和公开范围", async group => {
    const args = group === "skills" ? ["skills", "manage"] : ["apps"];
    const result = await execute([...args, "publish", "clock", "--version", "1.0.0", "--reviewed", "--public", "--yes"], async (_input, init) => {
      if (typeof init?.body !== "string") throw new Error("Expected JSON body");
      expect(JSON.parse(init.body)).toEqual(group === "skills" ? {remote_public: true} : {reviewed: true, public: true}); return json({status: "published"});
    }); expect(result.code).toBe(0);
  });
  it("文件路径和绑定仅编码进查询，撤远程明确发送绑定", async () => {
    const result = await execute(["skills", "manage", "file", "clock", "--version", "1.0.0", "--path", "clock/指引.md", "--mode", "remote", "--binding", "service:time"], async input => {
      const query = new URL(url(input)).searchParams;
      expect(query.get("path")).toBe("clock/指引.md"); expect(query.get("binding")).toBe("service:time");
      return json({previewable: false, reason: "binary"});
    }); expect(result.code).toBe(0); expect(result.text).toContain("binary");
    const withdrawn = await execute(["skills", "manage", "withdraw-remote", "clock", "--version", "1.0.0", "--binding", "service:time", "--yes"], async (input, init) => {
      expect(url(input)).toContain("/versions/1.0.0/withdraw-remote");
      expect(init?.body).toBe('{"binding":"service:time"}'); return json({status: "withdrawn"});
    }); expect(withdrawn.code).toBe(0);
  });
  it("上游 Apps 部分失败仍保留各来源，同名不合并", async () => {
    const result = await execute(["apps", "list"], async input => {
      const path = url(input);
      if (path.endsWith("/api/apps")) return json([{slug: "hosted"}]);
      if (path.endsWith("/sources")) return json(["one", "two", "bad"].map(server_name => ({server_name, status: "running"})));
      if (path.endsWith("/bad")) return json({}, 502);
      return json({server_name: path.split("/").at(-1), items: [{name: "clock"}], complete: true});
    });
    expect(result.code).toBe(0); expect(result.text).toContain('"complete":false');
    expect(result.text).toContain("hosted"); expect(result.text).toContain("one"); expect(result.text).toContain("two");
  });
  it("来源应用详情仅发现声明，不调用工具/读取UI", async () => {
    const result = await execute(["apps", "info", "clock", "--source", "time"], async input => {
      expect(url(input)).toContain("/api/apps/discovery/sources/time");
      return json({items: [{name: "clock", resource_uri: "ui://clock"}], complete: true});
    }); expect(result.code).toBe(0); expect(result.fetch).toHaveBeenCalledTimes(2);
  });
});

describe("上传与下载安全", () => {
  it("目录打包为可验证 ZIP，保留二进制，排除环境秘密及缓存", async () => {
    const root = await directory(), folder = join(root, "clock"); await mkdir(folder);
    await writeFile(join(folder, "SKILL.md"), "---\nname: clock\ndescription: Show world time.\n---\n# Clock\n");
    await writeFile(join(folder, "image.bin"), new Uint8Array([0, 255, 128]));
    await writeFile(join(folder, ".env"), "private"); await mkdir(join(folder, "node_modules"));
    const upload = await uploadInput(folder, true), packed = join(root, "clock.zip");
    expect(upload.excluded).toEqual([".env", "node_modules"]); await writeFile(packed, upload.bytes);
    await validateAndExtractSkillZip(packed, join(root, "extracted"));
    expect(Buffer.from(upload.bytes).includes(Buffer.from("private"))).toBe(false);
    expect(Buffer.from(upload.bytes).includes(Buffer.from([0, 255, 128]))).toBe(true);
    expect((await uploadInput(packed, true)).bytes).toEqual(upload.bytes);
  });
  it("拒绝上传入口和内部符号链接", async () => {
    const root = await directory(), file = join(root, "source.zip"), link = join(root, "link.zip");
    await writeFile(file, "zip"); await symlink(file, link); await expect(uploadInput(link, true)).rejects.toThrow();
    const folder = join(root, "clock"); await mkdir(folder); await symlink(file, join(folder, "nested.zip"));
    await expect(uploadInput(folder, true)).rejects.toThrow();
  });
  it.each(["skills", "apps"])("%s 上传仅创建草稿，使用 multipart，不附加发布", async group => {
    const file = join(await directory(), group === "skills" ? "clock.zip" : "clock.html"); await writeFile(file, "content");
    const args = group === "skills" ? ["skills", "manage", "upload", "--mode", "both", "--binding-kind", "service", "--source", "time"]
      : ["apps", "upload", "clock", "--display-name", "Clock"];
    const result = await execute([...args, "--file", file, "--version", "1.0.0", "--yes"], async (input, init) => {
      expect(url(input)).toContain(group === "skills" ? "/api/skill-variants/upload" : "/api/apps/upload");
      expect(init?.body).toBeInstanceOf(FormData); expect(new Headers(init?.headers).has("Content-Type")).toBe(false);
      const form = init?.body as FormData; expect(form.get("version")).toBe("1.0.0");
      expect(form.get("file")).toBeInstanceOf(Blob); return json({status: "draft"});
    }); expect(result.code).toBe(0); expect(result.fetch).toHaveBeenCalledTimes(2);
  });
  it("校验摘要后保存私有新文件，拒绝覆盖和符号链接", async () => {
    const root = await directory(), output = join(root, "clock.zip"), bytes = Buffer.from("synthetic-zip");
    const digest = createHash("sha256").update(bytes).digest("hex");
    const args = ["skills", "manage", "download", "clock", "--version", "1.0.0", "--mode", "remote", "--binding", "service:time", "--output", output];
    const result = await execute(args, async input => {
      expect(url(input)).toContain("profile=remote&binding=service%3Atime");
      return new Response(bytes, {headers: {"X-Checksum-Sha256": digest}});
    }); expect(result.code).toBe(0); expect(await readFile(output)).toEqual(bytes);
    if (process.platform !== "win32") expect((await stat(output)).mode & 0o777).toBe(0o600);
    const again = await execute(args); expect(again.code).toBe(2); expect(again.fetch).toHaveBeenCalledTimes(1);
    const link = join(root, "link"); await symlink(output, link); await expect(saveNewFile(link, bytes)).rejects.toThrow();
  });
  it("缺少/错误摘要不落地，读取配置仅显式导出，未知字段也不输出", async () => {
    const root = await directory(), output = join(root, "clock.zip");
    const result = await execute(["apps", "download", "clock", "--version", "1.0.0", "--output", output], async () => new Response("bad"));
    expect(result.code).toBe(12); await expect(stat(output)).rejects.toThrow();
    const configPath = join(root, "private.json");
    const exported = await execute(["servers", "config", "time", "--output", configPath], async () => json({config: {arbitrary: "private-value"}}));
    expect(exported.code).toBe(0); expect(JSON.parse(await readFile(configPath, "utf8"))).toEqual({arbitrary: "private-value"});
    expect(exported.text).not.toContain("private-value");
    expect(safeManagementData({config: {arbitrary: "private-value"}, env: {UNKNOWN: "private-value"}})).toEqual({config: "[REDACTED]", env: "[REDACTED]"});
  });
  it("拒绝跨站重定向与超限响应", async () => {
    const fetch = vi.fn<typeof globalThis.fetch>(async (_input, init) => {
      expect(init?.redirect).toBe("error"); return new Response("", {headers: {"Content-Length": "999999999"}});
    });
    const client = new ManagementClient("https://fixture.example", "X-Key", SECRET, fetch);
    await expect(client.call("POST", "/api/servers")).rejects.toThrow("写入结果未确认"); expect(fetch).toHaveBeenCalledOnce();
  });
});
