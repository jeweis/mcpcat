import { createHash } from "node:crypto";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { atomicInstallSkill, prepareTargetBase } from "./atomic-install.js";
import { getAgentAdapter } from "./agents/adapters.js";
import type { AgentEnvironment, AgentId, InstallScope } from "./agents/types.js";
import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import type { RegistryClient } from "./http.js";
import type { InstallationRecord, InstallationStore } from "./install-lock.js";
import type { BootstrapResponse, SkillDetail, SkillVersionDetail } from "./schema.js";
import { validateAndExtractSkillZip } from "./skill-package.js";

export interface InstallTarget {
  agent: AgentId;
  scope: InstallScope;
  targetDir?: string;
}

export interface InstallTargetResult {
  agent: AgentId;
  scope: InstallScope;
  status: "success" | "failed";
  target?: string;
  version?: string;
  hints?: string[];
  error?: { code: string; message: string };
}

export interface InstallRequest {
  client: RegistryClient;
  bootstrap: BootstrapResponse;
  apiKey: string;
  profileId: string;
  slug: string;
  version?: string;
  targets: InstallTarget[];
  environment: AgentEnvironment;
  installationStore: InstallationStore;
  now?: () => Date;
  temporaryRoot?: string;
}

function selectedVersion(detail: SkillDetail, version?: string): SkillVersionDetail {
  const selected = version === undefined
    ? detail.versions.find((item) => item.status === "published") ?? detail.versions[0]
    : detail.versions.find((item) => item.version === version);
  if (selected === undefined || selected.artifact === null || selected.artifact.integrity_status !== "ok") {
    throw new McpcatError(
      ErrorCode.skillNotFound,
      version === undefined ? "Skill 没有可安装版本" : `Skill 版本不可安装：${version}`,
      ExitCode.configuration,
      { slug: detail.slug, ...(version === undefined ? {} : { version }) },
    );
  }
  return selected;
}

async function prepareArtifact(request: InstallRequest): Promise<{
  temporary: string;
  rootDir: string;
  version: SkillVersionDetail;
  sha256: string;
}> {
  const detail = await request.client.skillDetail(request.bootstrap, request.apiKey, request.slug);
  const version = selectedVersion(detail, request.version);
  const download = await request.client.downloadSkill(
    request.bootstrap,
    request.apiKey,
    request.slug,
    version.version,
  );
  const sha256 = createHash("sha256").update(download.bytes).digest("hex");
  const expected = version.artifact?.sha256;
  if (
    expected === undefined ||
    sha256 !== expected ||
    (download.checksumHeader !== undefined && download.checksumHeader !== expected)
  ) {
    throw new McpcatError(
      ErrorCode.integrity,
      "下载制品 SHA-256 校验失败",
      ExitCode.integrity,
      { expected, actual: sha256, header: download.checksumHeader },
    );
  }
  const parent = request.temporaryRoot ?? tmpdir();
  await mkdir(parent, { recursive: true, mode: 0o700 });
  const temporary = await mkdtemp(join(parent, "mcpcat-skill-"));
  try {
    const zipPath = join(temporary, "artifact.zip");
    await writeFile(zipPath, download.bytes, { flag: "wx", mode: 0o600 });
    const validated = await validateAndExtractSkillZip(zipPath, join(temporary, "extracted"));
    if (validated.name !== request.slug) {
      throw new McpcatError(
        ErrorCode.packageInvalid,
        "下载包 Skill name 与 Registry slug 不一致",
        ExitCode.integrity,
        { expected: request.slug, actual: validated.name },
      );
    }
    return { temporary, rootDir: validated.rootDir, version, sha256 };
  } catch (error) {
    await rm(temporary, { recursive: true, force: true });
    throw error;
  }
}

async function installTarget(
  request: InstallRequest,
  artifact: Awaited<ReturnType<typeof prepareArtifact>>,
  target: InstallTarget,
): Promise<InstallTargetResult> {
  const adapter = getAgentAdapter(target.agent);
  try {
    const targetBase = await adapter.validateTarget(
      {
        scope: target.scope,
        ...(target.targetDir === undefined ? {} : { targetDir: target.targetDir }),
      },
      request.environment,
    );
    const targetBaseRealPath = await prepareTargetBase(targetBase);
    const identity = {
      profile: request.profileId,
      agent: target.agent,
      scope: target.scope,
      targetRealPath: join(targetBaseRealPath, request.slug),
      skill: request.slug,
    } as const;
    const installed = await request.installationStore.withTargetLock(identity, async () =>
      atomicInstallSkill(
        artifact.rootDir,
        targetBaseRealPath,
        request.slug,
        async (targetRealPath) => {
          const record: InstallationRecord = {
            ...identity,
            targetRealPath,
            version: artifact.version.version,
            sha256: artifact.sha256,
            installedAt: (request.now ?? (() => new Date()))().toISOString(),
          };
          await request.installationStore.record(record);
        },
      ),
    );
    return {
      agent: target.agent,
      scope: target.scope,
      status: "success",
      target: installed,
      version: artifact.version.version,
      hints: adapter.postInstall(request.slug, installed),
    };
  } catch (error) {
    const failure = error instanceof McpcatError
      ? error
      : new McpcatError(
          ErrorCode.installFailed,
          "安装目标失败",
          ExitCode.installation,
          undefined,
          { cause: error },
        );
    return {
      agent: target.agent,
      scope: target.scope,
      status: "failed",
      error: { code: failure.code, message: failure.message },
    };
  }
}

export async function installSkill(request: InstallRequest): Promise<InstallTargetResult[]> {
  const artifact = await prepareArtifact(request);
  try {
    const results: InstallTargetResult[] = [];
    for (const target of request.targets) {
      results.push(await installTarget(request, artifact, target));
    }
    return results;
  } finally {
    await rm(artifact.temporary, { recursive: true, force: true });
  }
}
