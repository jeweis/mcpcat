import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { createAgentEnvironment } from "../src/agents/types.js";
import { MemoryCredentialStore } from "../src/credentials.js";
import { runDoctor } from "../src/doctor.js";
import { InstallationStore } from "../src/install-lock.js";
import { ProfileStore } from "../src/profiles.js";

describe("doctor", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
  });

  it("汇总 Node/Profile/凭证/API/路径/mcporter/残留锁诊断且不快速失败", async () => {
    const root = await mkdtemp(join(tmpdir(), "mcpcat-doctor-"));
    directories.push(root);
    const profiles = new ProfileStore(join(root, "profiles.json"));
    await profiles.save({
      name: "default",
      baseUrl: "https://mcpcat.example.com",
      instanceName: "test",
      apiVersion: "v1",
      registrySchemaVersion: "1.0.0",
      authHeaderName: "Mcpcat-Key",
      registryPath: "/api/skills/registry",
      createdAt: "2026-08-29T00:00:00Z",
      updatedAt: "2026-08-29T00:00:00Z",
    });
    const credentials = new MemoryCredentialStore();
    await credentials.set("default", "doctor-secret");
    const installations = new InstallationStore(join(root, "installations.json"));
    await installations.record({
      profile: "default",
      agent: "codex",
      scope: "user",
      targetRealPath: join(root, "missing-skill"),
      skill: "demo-skill",
      version: "1.0.0",
      sha256: "a".repeat(64),
      installedAt: "2026-08-29T00:00:00Z",
    });
    await mkdir(installations.locksDirectory, { recursive: true });
    await writeFile(join(installations.locksDirectory, "stale.lock"), "999999\n");

    const checks = await runDoctor({
      profileStore: profiles,
      credentialStore: credentials,
      installationStore: installations,
      agentEnvironment: createAgentEnvironment({ homeDir: root, cwd: root, env: {} }),
      env: {},
      nodeVersion: "22.22.3",
      fetch: async () => new Response(JSON.stringify({
        registry_schema_version: "1.0.0",
        api_version: "v1",
        skills: [],
      }), { headers: { "Content-Type": "application/json" } }),
      commandRunner: async () => {
        throw new Error("mcporter missing");
      },
    });

    expect(checks).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: "node", status: "error" }),
      expect.objectContaining({ name: "profile", status: "ok" }),
      expect.objectContaining({ name: "credential", status: "ok" }),
      expect.objectContaining({ name: "api", status: "ok" }),
      expect.objectContaining({ name: "agent-paths", status: "error" }),
      expect.objectContaining({ name: "locks", status: "warn" }),
      expect.objectContaining({ name: "mcporter", status: "warn" }),
    ]));
    expect(JSON.stringify(checks)).not.toContain("doctor-secret");
  });
});
