import { createHash } from "node:crypto";
import { constants } from "node:fs";
import {
  access,
  cp,
  lstat,
  mkdir,
  mkdtemp,
  readFile,
  realpath,
  rename,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import {
  atomicInstallSkill,
  prepareTargetBase,
  type AtomicInstallOperations,
} from "../src/atomic-install.js";
import { createAgentEnvironment } from "../src/agents/types.js";
import { ErrorCode } from "../src/errors.js";
import { RegistryClient } from "../src/http.js";
import { InstallationStore } from "../src/install-lock.js";
import { installSkill } from "../src/installer.js";
import type { BootstrapResponse } from "../src/schema.js";
import { validSkillZip } from "./zip-fixture.js";

const defaultOperations: AtomicInstallOperations = {
  access,
  cp,
  lstat,
  mkdir,
  realpath,
  rename,
  rm,
};

function requestUrl(input: string | URL | Request): string {
  return input instanceof Request ? input.url : String(input);
}

describe("原子安装和本地安装锁", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
  });

  async function temporary(prefix: string): Promise<string> {
    const path = await mkdtemp(join(tmpdir(), prefix));
    directories.push(path);
    return path;
  }

  it("不可写目标返回稳定安装错误", async () => {
    const target = await temporary("mcpcat-unwritable-");
    await expect(
      prepareTargetBase(target, {
        ...defaultOperations,
        access: async () => {
          throw Object.assign(new Error("permission denied"), { code: "EACCES" });
        },
      }),
    ).rejects.toMatchObject({ code: ErrorCode.installFailed });
  });

  it("替换提交失败时恢复原 Skill 目录", async () => {
    const directory = await temporary("mcpcat-atomic-");
    const source = join(directory, "source");
    const targetBase = join(directory, "target");
    const target = join(targetBase, "demo-skill");
    await mkdir(source, { recursive: true });
    await mkdir(target, { recursive: true });
    await writeFile(join(source, "SKILL.md"), "new");
    await writeFile(join(target, "SKILL.md"), "old");

    await expect(
      atomicInstallSkill(
        source,
        await realpath(targetBase),
        "demo-skill",
        async () => undefined,
        {
          ...defaultOperations,
          rename: async (from, to) => {
            if (String(from).includes(".tmp") && String(to).endsWith("/demo-skill")) {
              throw Object.assign(new Error("replace failed"), { code: "EIO" });
            }
            await rename(from, to);
          },
        },
      ),
    ).rejects.toMatchObject({ code: ErrorCode.installFailed });
    await expect(readFile(join(target, "SKILL.md"), "utf8")).resolves.toBe("old");
  });

  it("安装锁按 profile/agent/scope/realpath/skill 分离记录", async () => {
    const directory = await temporary("mcpcat-lock-");
    const store = new InstallationStore(join(directory, "installations.json"));
    const base = {
      profile: "company",
      scope: "user" as const,
      targetRealPath: "/skills/demo-skill",
      skill: "demo-skill",
      version: "1.0.0",
      sha256: "a".repeat(64),
      installedAt: "2026-08-29T00:00:00.000Z",
    };
    await store.record({ ...base, agent: "codex" });
    await store.record({ ...base, agent: "claude" });

    const records = await store.list();
    expect(records).toHaveLength(2);
    expect(records.map((item) => item.agent).sort()).toEqual(["claude", "codex"]);
  });

  it("同一 Skill 多 Agent 安装保留成功并独立报告失败", async () => {
    const directory = await temporary("mcpcat-multi-");
    const zip = validSkillZip();
    const sha256 = createHash("sha256").update(zip).digest("hex");
    const bootstrap: BootstrapResponse = {
      instance_name: "test",
      base_url: "https://mcpcat.example.com",
      api_version: "v1",
      registry_schema_version: "1.0.0",
      auth_header_name: "Mcpcat-Key",
      registry_path: "/api/skills/registry",
      min_cli_version: "0.1.0",
      recommended_cli_version: "0.1.0",
    };
    const client = new RegistryClient({
      fetch: async (input) => requestUrl(input).endsWith("/download")
        ? new Response(zip, { headers: { "X-Checksum-Sha256": sha256 } })
        : new Response(JSON.stringify({
            slug: "demo-skill",
            display_name: "Demo",
            description: "Demo skill",
            source_type: "uploaded",
            source: {},
            status: "published",
            updated_at: "2026-08-29T00:00:00Z",
            versions: [{
              version: "1.0.0",
              status: "published",
              changelog: "initial",
              compatibility: {},
              created_at: "2026-08-29T00:00:00Z",
              published_at: "2026-08-29T00:00:00Z",
              artifact: { sha256, size: zip.length, integrity_status: "ok" },
              files: [],
              scripts: [],
            }],
          }), { headers: { "Content-Type": "application/json" } }),
    });
    const environment = createAgentEnvironment({
      env: {},
      homeDir: directory,
      cwd: directory,
      pathExists: async () => false,
      commandExists: async () => false,
    });
    const results = await installSkill({
      client,
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      slug: "demo-skill",
      targets: [
        { agent: "codex", scope: "user" },
        { agent: "generic", scope: "user" },
      ],
      environment,
      installationStore: new InstallationStore(join(directory, "state", "installations.json")),
      temporaryRoot: join(directory, "temporary"),
    });

    expect(results.map((item) => item.status)).toEqual(["success", "failed"]);
    await expect(readFile(join(directory, ".codex", "skills", "demo-skill", "SKILL.md"), "utf8"))
      .resolves.toContain("demo-skill");
    expect(results[1]?.error?.code).toBe(ErrorCode.targetInvalid);
  });

  it("SHA-256 不匹配时不创建安装目标", async () => {
    const directory = await temporary("mcpcat-integrity-");
    const zip = validSkillZip();
    const bootstrap: BootstrapResponse = {
      instance_name: "test",
      base_url: "https://mcpcat.example.com",
      api_version: "v1",
      registry_schema_version: "1.0.0",
      auth_header_name: "Mcpcat-Key",
      registry_path: "/api/skills/registry",
      min_cli_version: "0.1.0",
      recommended_cli_version: "0.1.0",
    };
    const client = new RegistryClient({
      fetch: async (input) => requestUrl(input).endsWith("/download")
        ? new Response(zip)
        : new Response(JSON.stringify({
            slug: "demo-skill",
            display_name: "Demo",
            description: "Demo skill",
            source_type: "uploaded",
            source: {},
            status: "published",
            updated_at: "2026-08-29T00:00:00Z",
            versions: [{
              version: "1.0.0",
              status: "published",
              changelog: "initial",
              compatibility: {},
              created_at: "2026-08-29T00:00:00Z",
              published_at: "2026-08-29T00:00:00Z",
              artifact: { sha256: "0".repeat(64), size: zip.length, integrity_status: "ok" },
              files: [],
              scripts: [],
            }],
          }), { headers: { "Content-Type": "application/json" } }),
    });
    await expect(installSkill({
      client,
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      slug: "demo-skill",
      targets: [{ agent: "codex", scope: "user" }],
      environment: createAgentEnvironment({ env: {}, homeDir: directory, cwd: directory }),
      installationStore: new InstallationStore(join(directory, "state", "installations.json")),
      temporaryRoot: join(directory, "temporary"),
    })).rejects.toMatchObject({ code: ErrorCode.integrity });
    await expect(access(join(directory, ".codex", "skills"), constants.F_OK)).rejects.toBeDefined();
  });
});
