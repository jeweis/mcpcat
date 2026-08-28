import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { createAgentEnvironment, type AgentId } from "../src/agents/types.js";
import { ErrorCode } from "../src/errors.js";
import { RegistryClient } from "../src/http.js";
import {
  type InstallationRecord,
  InstallationStore,
} from "../src/install-lock.js";
import {
  rollbackInstallations,
  setInstallationPins,
  updateInstallations,
} from "../src/lifecycle.js";
import type { BootstrapResponse } from "../src/schema.js";
import { validSkillZip } from "./zip-fixture.js";

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

function requestUrl(input: string | URL | Request): string {
  return input instanceof Request ? input.url : String(input);
}

function registryClient(
  versions: readonly string[],
  options: { unavailable?: boolean } = {},
): RegistryClient {
  const zip = validSkillZip();
  const sha256 = createHash("sha256").update(zip).digest("hex");
  return new RegistryClient({
    fetch: async (input) => {
      if (options.unavailable) {
        throw new Error("registry unavailable");
      }
      if (requestUrl(input).endsWith("/download")) {
        return new Response(zip, { headers: { "X-Checksum-Sha256": sha256 } });
      }
      return new Response(JSON.stringify({
        slug: "demo-skill",
        display_name: "Demo",
        description: "Lifecycle fixture",
        source_type: "uploaded",
        source: {},
        status: "published",
        updated_at: "2026-08-29T00:00:00Z",
        versions: versions.map((version) => ({
          version,
          status: "published",
          changelog: version,
          compatibility: {},
          created_at: "2026-08-29T00:00:00Z",
          published_at: "2026-08-29T00:00:00Z",
          artifact: { sha256, size: zip.length, integrity_status: "ok" },
          files: [],
          scripts: [],
        })),
      }), { headers: { "Content-Type": "application/json" } });
    },
  });
}

describe("update/pin/rollback 生命周期", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
  });

  async function setup(): Promise<{
    root: string;
    store: InstallationStore;
    environment: ReturnType<typeof createAgentEnvironment>;
  }> {
    const root = await mkdtemp(join(tmpdir(), "mcpcat-lifecycle-"));
    directories.push(root);
    return {
      root,
      store: new InstallationStore(join(root, "state", "installations.json")),
      environment: createAgentEnvironment({ env: {}, homeDir: root, cwd: root }),
    };
  }

  async function installRecord(
    setupValue: Awaited<ReturnType<typeof setup>>,
    agent: AgentId,
    options: { pinned?: boolean } = {},
  ): Promise<InstallationRecord> {
    const target = join(setupValue.root, agent, "demo-skill");
    await mkdir(target, { recursive: true });
    await writeFile(join(target, "SKILL.md"), `old-${agent}`);
    const record: InstallationRecord = {
      profile: "company",
      agent,
      scope: "user",
      targetRealPath: await realpath(target),
      skill: "demo-skill",
      version: "1.0.0",
      sha256: "a".repeat(64),
      installedAt: "2026-08-29T00:00:00.000Z",
      ...(options.pinned ? { pinnedVersion: "1.0.0" } : {}),
    };
    await setupValue.store.record(record);
    return record;
  }

  it("update --all 更新未固定目标并跳过 pinned 安装", async () => {
    const value = await setup();
    const codex = await installRecord(value, "codex");
    await installRecord(value, "claude", { pinned: true });

    const results = await updateInstallations({
      client: registryClient(["1.0.0", "1.10.0", "1.2.0"]),
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      environment: value.environment,
      store: value.store,
      now: () => new Date("2026-08-29T01:00:00.000Z"),
    }, {});

    expect(results.map((result) => result.status).sort()).toEqual(["skipped", "updated"]);
    expect((await value.store.get(codex))?.version).toBe("1.10.0");
    expect((await value.store.get(codex))?.backups?.[0]?.version).toBe("1.0.0");
  });

  it("pin/unpin 独立修改指定 Profile 的安装状态", async () => {
    const value = await setup();
    const record = await installRecord(value, "codex");

    await setInstallationPins(value.store, "company", { slug: "demo-skill" }, true);
    expect((await value.store.get(record))?.pinnedVersion).toBe("1.0.0");
    await setInstallationPins(value.store, "company", { slug: "demo-skill" }, false);
    expect((await value.store.get(record))?.pinnedVersion).toBeUndefined();
  });

  it("相同 Skill 的 Profile 与 Scope 状态互不影响", async () => {
    const value = await setup();
    const company = await installRecord(value, "codex");
    const personal: InstallationRecord = {
      ...company,
      profile: "personal",
      scope: "project",
    };
    await value.store.record(personal);

    await setInstallationPins(value.store, "company", { slug: "demo-skill" }, true);

    expect((await value.store.get(company))?.pinnedVersion).toBe("1.0.0");
    expect((await value.store.get(personal))?.pinnedVersion).toBeUndefined();
  });

  it("优先使用本地备份回滚并在操作前备份当前版本", async () => {
    const value = await setup();
    const record = await installRecord(value, "codex");
    await updateInstallations({
      client: registryClient(["1.0.0", "2.0.0"]),
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      environment: value.environment,
      store: value.store,
    }, { slug: "demo-skill" });

    const results = await rollbackInstallations({
      client: registryClient([]),
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      environment: value.environment,
      store: value.store,
    }, { slug: "demo-skill" });

    expect(results[0]).toMatchObject({ status: "rolled-back", toVersion: "1.0.0" });
    expect((await value.store.get(record))?.version).toBe("1.0.0");
    await expect(readFile(join(record.targetRealPath, "SKILL.md"), "utf8"))
      .resolves.toBe("old-codex");
  });

  it("Registry 不可用和历史版本缺失按目标返回失败", async () => {
    const value = await setup();
    await installRecord(value, "codex");
    const context = {
      client: registryClient([], { unavailable: true }),
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      environment: value.environment,
      store: value.store,
    };
    expect((await updateInstallations(context, { slug: "demo-skill" }))[0]?.status).toBe("failed");

    const rollback = await rollbackInstallations({
      ...context,
      client: registryClient(["1.0.0"]),
    }, { slug: "demo-skill" }, "0.5.0");
    expect(rollback[0]?.status).toBe("failed");
    expect(rollback[0]?.error?.code).toBe(ErrorCode.skillNotFound);
  });

  it("更新在安装记录提交阶段中断时恢复旧目录", async () => {
    const value = await setup();
    const record = await installRecord(value, "codex");
    class InterruptingStore extends InstallationStore {
      failWrites = false;

      override async record(next: InstallationRecord): Promise<void> {
        if (this.failWrites && next.version !== "1.0.0") {
          throw new Error("simulated update interruption");
        }
        await super.record(next);
      }
    }
    const store = new InterruptingStore(value.store.path);
    store.failWrites = true;

    const result = await updateInstallations({
      client: registryClient(["1.0.0", "2.0.0"]),
      bootstrap,
      apiKey: "secret",
      profileId: "company",
      environment: value.environment,
      store,
    }, { slug: "demo-skill" });

    expect(result[0]?.status).toBe("failed");
    await expect(readFile(join(record.targetRealPath, "SKILL.md"), "utf8"))
      .resolves.toBe("old-codex");
    expect((await store.get(record))?.version).toBe("1.0.0");
  });
});
