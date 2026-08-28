import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { resolveConnection } from "../src/connection.js";
import type { CredentialStore } from "../src/credentials.js";
import { ErrorCode } from "../src/errors.js";
import { ProfileStore, type Profile } from "../src/profiles.js";

function profile(name: string, baseUrl: string): Profile {
  return {
    name,
    baseUrl,
    instanceName: name,
    apiVersion: "v1",
    registrySchemaVersion: "1.0.0",
    authHeaderName: "Mcpcat-Key",
    registryPath: "/api/skills/registry",
    createdAt: "2026-08-29T00:00:00.000Z",
    updatedAt: "2026-08-29T00:00:00.000Z",
  };
}

async function store(): Promise<ProfileStore> {
  const directory = await mkdtemp(join(tmpdir(), "mcpcat-cli-profile-"));
  return new ProfileStore(join(directory, "profiles.json"));
}

describe("ProfileStore", () => {
  it("第一个 Profile 自动成为默认项，并可显式切换", async () => {
    const profiles = await store();
    await profiles.save(profile("personal", "https://personal.example.com"));
    await profiles.save(profile("company", "https://company.example.com"));

    expect((await profiles.resolve()).name).toBe("personal");
    await profiles.use("company");
    expect((await profiles.resolve()).name).toBe("company");
    expect((await profiles.resolve("personal")).baseUrl).toBe("https://personal.example.com");
  });

  it("Profile 文件不含凭证", async () => {
    const profiles = await store();
    await profiles.save(profile("default", "https://example.com"));
    const content = await readFile(profiles.path, "utf8");

    expect(content).not.toMatch(/api.?key|password|credential/i);
    expect(content).toContain("https://example.com");
  });

  it("未知显式 Profile 返回稳定错误码", async () => {
    const profiles = await store();
    await expect(profiles.resolve("missing")).rejects.toMatchObject({
      code: ErrorCode.profileNotFound,
    });
  });
});

describe("resolveConnection", () => {
  it("显式 Profile 覆盖默认 Profile 并从 Keychain 取凭证", async () => {
    const profiles = await store();
    await profiles.save(profile("personal", "https://personal.example.com"));
    await profiles.save(profile("company", "https://company.example.com"));
    const credentials: CredentialStore = {
      get: async (name) => `${name}-key`,
      set: async () => undefined,
      delete: async () => undefined,
    };

    await expect(
      resolveConnection(profiles, credentials, {
        profileName: "company",
        env: {},
      }),
    ).resolves.toMatchObject({
      source: "profile",
      baseUrl: "https://company.example.com",
      apiKey: "company-key",
    });
  });

  it("MCPCAT_URL/API_KEY 具有最高优先级且无需本地 Profile", async () => {
    const profiles = await store();
    await expect(
      resolveConnection(profiles, undefined, {
        env: {
          MCPCAT_URL: "ci.example.com",
          MCPCAT_API_KEY: "ci-secret",
        },
      }),
    ).resolves.toEqual({
      source: "environment",
      baseUrl: "https://ci.example.com",
      apiKey: "ci-secret",
    });
  });
});
