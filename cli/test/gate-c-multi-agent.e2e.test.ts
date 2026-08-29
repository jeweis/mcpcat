import { createHash } from "node:crypto";
import { mkdtemp, readFile, realpath, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { AgentStore, createAgentContext, type AgentContext } from "../src/agents.js";
import { runCli, type CliDependencies } from "../src/cli.js";
import { MemoryCredentialStore } from "../src/credentials.js";
import { InstallationStore } from "../src/install-lock.js";
import { ProfileStore } from "../src/profiles.js";
import { buildZip } from "./zip-fixture.js";

const AGENTS = ["codex", "claude", "openclaw"] as const;
const SCOPES = ["user", "project"] as const;
const SECRET = "gate-c-secret";

function skillZip(version: string): Uint8Array {
  return buildZip([
    {
      name: "demo-skill/SKILL.md",
      content: "---\nname: demo-skill\ndescription: Gate C multi-Agent fixture.\n---\n# Demo\n",
    },
    { name: "demo-skill/VERSION", content: `${version}\n` },
  ]);
}

function requestUrl(input: string | URL | Request): URL {
  return new URL(input instanceof Request ? input.url : String(input));
}

function fakeRegistry(): typeof globalThis.fetch {
  const packages = new Map([
    ["1.0.0", skillZip("1.0.0")],
    ["2.0.0", skillZip("2.0.0")],
  ]);
  return async (input) => {
    const url = requestUrl(input);
    if (url.pathname.endsWith("/download")) {
      const parts = url.pathname.split("/");
      const version = parts[parts.indexOf("versions") + 1];
      const bytes = version === undefined ? undefined : packages.get(version);
      if (bytes === undefined) {
        return new Response(JSON.stringify({ detail: "version missing" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        });
      }
      const sha256 = createHash("sha256").update(bytes).digest("hex");
      return new Response(bytes, { headers: { "X-Checksum-Sha256": sha256 } });
    }
    const versions = [...packages.entries()].map(([version, bytes]) => ({
      version,
      status: "published",
      changelog: version,
      compatibility: {},
      created_at: "2026-08-29T00:00:00Z",
      published_at: "2026-08-29T00:00:00Z",
      artifact: {
        sha256: createHash("sha256").update(bytes).digest("hex"),
        size: bytes.length,
        integrity_status: "ok",
      },
      files: [],
      scripts: [],
    }));
    return new Response(JSON.stringify({
      slug: "demo-skill",
      display_name: "Demo Skill",
      description: "Gate C multi-Agent fixture.",
      source_type: "uploaded",
      source: {},
      status: "published",
      updated_at: "2026-08-29T00:00:00Z",
      versions,
    }), { headers: { "Content-Type": "application/json" } });
  };
}

function skillPath(
  context: AgentContext,
  agent: typeof AGENTS[number],
  scope: typeof SCOPES[number],
): string {
  if (scope === "user") {
    const directory = agent === "codex" ? ".agents" : `.${agent}`;
    return join(context.homeDir, directory, "skills", "demo-skill");
  }
  if (agent === "openclaw") {
    return join(context.cwd, "skills", "demo-skill");
  }
  const directory = agent === "codex" ? ".agents" : `.${agent}`;
  return join(context.cwd, directory, "skills", "demo-skill");
}

describe("Gate C 16.2 multi-Agent/multi-Scope lifecycle E2E", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
  });

  it("同一 Skill 在三种 Agent、user/project 和两个 Profile 中独立安装、pin、update、rollback", async () => {
    const root = await mkdtemp(join(tmpdir(), "mcpcat-gate-c-"));
    directories.push(root);
    const profiles = new ProfileStore(join(root, "state", "profiles.json"));
    const credentials = new MemoryCredentialStore();
    const installations = new InstallationStore(join(root, "state", "installations.json"));
    const agentStore = new AgentStore(join(root, "state", "agents.json"));
    for (const name of ["company", "personal"] as const) {
      await profiles.save({
        name,
        baseUrl: `https://${name}.example.test`,
        instanceName: name,
        apiVersion: "v1",
        registrySchemaVersion: "1.0.0",
        authHeaderName: "Mcpcat-Key",
        registryPath: "/api/skills/registry",
        createdAt: "2026-08-29T00:00:00Z",
        updatedAt: "2026-08-29T00:00:00Z",
      });
      await credentials.set(name, SECRET);
    }
    const contexts = {
      company: createAgentContext({
        env: {},
        homeDir: join(root, "company-home"),
        cwd: join(root, "company-workspace"),
      }),
      personal: createAgentContext({
        env: {},
        homeDir: join(root, "personal-home"),
        cwd: join(root, "personal-workspace"),
      }),
    };
    const output: string[] = [];

    async function execute(
      profile: keyof typeof contexts,
      args: string[],
    ): Promise<void> {
      const dependencies: CliDependencies = {
        env: {},
        fetch: fakeRegistry(),
        profileStore: profiles,
        credentialStore: credentials,
        installationStore: installations,
        agentStore,
        agentContext: contexts[profile],
        stdinIsTTY: false,
        stdout: (value) => output.push(value),
        stderr: (value) => output.push(value),
      };
      await expect(runCli([...args, "--profile", profile, "--json"], dependencies))
        .resolves.toBe(0);
    }

    for (const profile of ["company", "personal"] as const) {
      for (const scope of SCOPES) {
        await execute(profile, [
          "skills",
          "install",
          "demo-skill",
          "--version",
          "1.0.0",
          "--scope",
          scope,
          ...AGENTS.flatMap((agent) => ["--agent", agent]),
          "--non-interactive",
        ]);
      }
    }

    let records = await installations.list();
    expect(records).toHaveLength(12);
    for (const profile of ["company", "personal"] as const) {
      for (const agent of AGENTS) {
        for (const scope of SCOPES) {
          const path = skillPath(contexts[profile], agent, scope);
          await expect(readFile(join(path, "VERSION"), "utf8")).resolves.toBe("1.0.0\n");
          expect(records).toContainEqual(expect.objectContaining({
            profile,
            agent,
            scope,
            targetRealPath: await realpath(path),
            version: "1.0.0",
          }));
        }
      }
    }

    await execute("company", [
      "skills",
      "pin",
      "demo-skill",
      "--agent",
      "codex",
      "--scope",
      "user",
    ]);
    await execute("company", ["skills", "update", "--all"]);
    await execute("company", [
      "skills",
      "rollback",
      "demo-skill",
      "--agent",
      "claude",
      "--scope",
      "project",
    ]);

    records = await installations.list();
    const companyRecord = (agent: typeof AGENTS[number], scope: typeof SCOPES[number]) =>
      records.find((record) =>
        record.profile === "company" && record.agent === agent && record.scope === scope,
      );
    expect(companyRecord("codex", "user")).toMatchObject({
      version: "1.0.0",
      pinnedVersion: "1.0.0",
    });
    expect(companyRecord("claude", "project")).toMatchObject({ version: "1.0.0" });
    expect(companyRecord("openclaw", "user")).toMatchObject({ version: "2.0.0" });
    expect(companyRecord("codex", "project")).toMatchObject({ version: "2.0.0" });
    const personalRecords = records.filter((record) => record.profile === "personal");
    expect(personalRecords).toHaveLength(6);
    for (const agent of AGENTS) {
      for (const scope of SCOPES) {
        expect(personalRecords).toContainEqual(
          expect.objectContaining({ agent, scope, version: "1.0.0" }),
        );
      }
    }
    expect(output.join("")).not.toContain(SECRET);
  });
});
