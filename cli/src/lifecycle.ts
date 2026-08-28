import { randomUUID } from "node:crypto";
import { cp, lstat, mkdir, realpath, rm } from "node:fs/promises";
import { dirname, join } from "node:path";

import { atomicInstallSkill, prepareTargetBase } from "./atomic-install.js";
import type { AgentEnvironment, AgentId, InstallScope } from "./agents/types.js";
import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import type { RegistryClient } from "./http.js";
import {
  installationKey,
  type InstallationIdentity,
  type InstallationRecord,
  type InstallationStore,
  type LocalBackup,
} from "./install-lock.js";
import { installSkill } from "./installer.js";
import type { BootstrapResponse, SkillDetail, SkillVersionDetail } from "./schema.js";
import { compareSemver } from "./semver.js";

export interface LifecycleContext {
  client: RegistryClient;
  bootstrap: BootstrapResponse;
  apiKey: string;
  profileId: string;
  environment: AgentEnvironment;
  store: InstallationStore;
  now?: () => Date;
}

export interface InstallationFilter {
  slug?: string;
  agents?: readonly AgentId[];
  scope?: InstallScope;
}

export interface LifecycleResult {
  installation: InstallationIdentity;
  fromVersion: string;
  toVersion?: string;
  status: "updated" | "rolled-back" | "pinned" | "unpinned" | "skipped" | "failed";
  reason?: string;
  error?: { code: string; message: string };
}

function identity(record: InstallationRecord): InstallationIdentity {
  return {
    profile: record.profile,
    agent: record.agent,
    scope: record.scope,
    targetRealPath: record.targetRealPath,
    skill: record.skill,
  };
}

function matches(record: InstallationRecord, profileId: string, filter: InstallationFilter): boolean {
  return record.profile === profileId &&
    (filter.slug === undefined || record.skill === filter.slug) &&
    (filter.scope === undefined || record.scope === filter.scope) &&
    (filter.agents === undefined || filter.agents.includes(record.agent));
}

export async function matchingInstallations(
  store: InstallationStore,
  profileId: string,
  filter: InstallationFilter,
): Promise<InstallationRecord[]> {
  return (await store.list()).filter((record) => matches(record, profileId, filter));
}

function latestPublished(detail: SkillDetail): SkillVersionDetail {
  const versions = detail.versions.filter(
    (version) => version.status === "published" && version.artifact?.integrity_status === "ok",
  );
  if (versions.length === 0) {
    throw new McpcatError(
      ErrorCode.skillNotFound,
      `Skill 没有可更新的发布版本：${detail.slug}`,
      ExitCode.configuration,
    );
  }
  return versions.reduce((latest, version) =>
    compareSemver(version.version, latest.version) > 0 ? version : latest,
  );
}

async function createBackup(
  store: InstallationStore,
  record: InstallationRecord,
  now: () => Date,
): Promise<LocalBackup> {
  const stats = await lstat(record.targetRealPath);
  if (!stats.isDirectory() || stats.isSymbolicLink()) {
    throw new McpcatError(
      ErrorCode.installFailed,
      "已安装 Skill 目录不存在或不安全",
      ExitCode.installation,
      { target: record.targetRealPath },
    );
  }
  const backupDirectory = join(
    store.backupsDirectory,
    installationKey(record),
    `${now().toISOString().replaceAll(":", "-")}-${randomUUID()}`,
  );
  await mkdir(dirname(backupDirectory), { recursive: true, mode: 0o700 });
  try {
    await cp(record.targetRealPath, backupDirectory, {
      recursive: true,
      force: false,
      errorOnExist: true,
      preserveTimestamps: false,
    });
    const backup: LocalBackup = {
      version: record.version,
      sha256: record.sha256,
      path: await realpath(backupDirectory),
      createdAt: now().toISOString(),
    };
    await store.addBackup(identity(record), backup);
    return backup;
  } catch (error) {
    await rm(backupDirectory, { recursive: true, force: true }).catch(() => undefined);
    throw error;
  }
}

function failureResult(record: InstallationRecord, error: unknown): LifecycleResult {
  const failure = error instanceof McpcatError
    ? error
    : new McpcatError(
        ErrorCode.installFailed,
        "Skill 生命周期操作失败",
        ExitCode.installation,
        undefined,
        { cause: error },
      );
  return {
    installation: identity(record),
    fromVersion: record.version,
    status: "failed",
    error: { code: failure.code, message: failure.message },
  };
}

export async function updateInstallations(
  context: LifecycleContext,
  filter: InstallationFilter,
): Promise<LifecycleResult[]> {
  const records = await matchingInstallations(context.store, context.profileId, filter);
  const now = context.now ?? (() => new Date());
  const results: LifecycleResult[] = [];
  for (const record of records) {
    try {
      if (record.pinnedVersion !== undefined) {
        results.push({
          installation: identity(record),
          fromVersion: record.version,
          toVersion: record.pinnedVersion,
          status: "skipped",
          reason: "pinned",
        });
        continue;
      }
      const detail = await context.client.skillDetail(
        context.bootstrap,
        context.apiKey,
        record.skill,
      );
      const latest = latestPublished(detail);
      if (compareSemver(record.version, latest.version) >= 0) {
        results.push({
          installation: identity(record),
          fromVersion: record.version,
          toVersion: latest.version,
          status: "skipped",
          reason: "up-to-date",
        });
        continue;
      }
      await createBackup(context.store, record, now);
      const installed = await installSkill({
        client: context.client,
        bootstrap: context.bootstrap,
        apiKey: context.apiKey,
        profileId: context.profileId,
        slug: record.skill,
        version: latest.version,
        targets: [{
          agent: record.agent,
          scope: record.scope,
          targetDir: dirname(record.targetRealPath),
        }],
        environment: context.environment,
        installationStore: context.store,
        now,
      });
      const targetResult = installed[0];
      if (targetResult?.status !== "success") {
        throw new McpcatError(
          ErrorCode.installFailed,
          targetResult?.error?.message ?? "更新目标失败",
          ExitCode.installation,
        );
      }
      results.push({
        installation: identity(record),
        fromVersion: record.version,
        toVersion: latest.version,
        status: "updated",
      });
    } catch (error) {
      results.push(failureResult(record, error));
    }
  }
  return results;
}

export async function setInstallationPins(
  store: InstallationStore,
  profileId: string,
  filter: InstallationFilter,
  pinned: boolean,
  requestedVersion?: string,
): Promise<LifecycleResult[]> {
  const records = await matchingInstallations(store, profileId, filter);
  const results: LifecycleResult[] = [];
  for (const record of records) {
    if (pinned && requestedVersion !== undefined && requestedVersion !== record.version) {
      results.push({
        installation: identity(record),
        fromVersion: record.version,
        toVersion: requestedVersion,
        status: "failed",
        error: {
          code: ErrorCode.installFailed,
          message: "只能固定当前已安装版本；请先安装明确版本",
        },
      });
      continue;
    }
    await store.setPin(identity(record), pinned ? (requestedVersion ?? record.version) : undefined);
    results.push({
      installation: identity(record),
      fromVersion: record.version,
      ...(pinned ? { toVersion: requestedVersion ?? record.version } : {}),
      status: pinned ? "pinned" : "unpinned",
    });
  }
  return results;
}

async function rollbackLocal(
  context: LifecycleContext,
  record: InstallationRecord,
  backup: LocalBackup,
  now: () => Date,
): Promise<void> {
  await createBackup(context.store, record, now);
  const refreshed = await context.store.get(identity(record));
  if (refreshed === undefined) {
    throw new McpcatError(
      ErrorCode.configInvalid,
      "回滚前安装记录消失",
      ExitCode.configuration,
    );
  }
  const base = await prepareTargetBase(dirname(record.targetRealPath));
  await context.store.withTargetLock(identity(record), async () =>
    atomicInstallSkill(backup.path, base, record.skill, async (targetRealPath) => {
      await context.store.record({
        ...refreshed,
        targetRealPath,
        version: backup.version,
        sha256: backup.sha256,
        installedAt: now().toISOString(),
      });
    }),
  );
}

export async function rollbackInstallations(
  context: LifecycleContext,
  filter: InstallationFilter,
  requestedVersion?: string,
): Promise<LifecycleResult[]> {
  const records = await matchingInstallations(context.store, context.profileId, filter);
  const now = context.now ?? (() => new Date());
  const results: LifecycleResult[] = [];
  for (const record of records) {
    try {
      const backup = requestedVersion === undefined
        ? record.backups?.[0]
        : record.backups?.find((item) => item.version === requestedVersion);
      if (backup !== undefined) {
        await rollbackLocal(context, record, backup, now);
        results.push({
          installation: identity(record),
          fromVersion: record.version,
          toVersion: backup.version,
          status: "rolled-back",
          reason: "local-backup",
        });
        continue;
      }
      if (requestedVersion === undefined) {
        throw new McpcatError(
          ErrorCode.skillNotFound,
          "没有可用的本地备份；请显式指定 Registry 历史版本",
          ExitCode.configuration,
        );
      }
      await createBackup(context.store, record, now);
      const installed = await installSkill({
        client: context.client,
        bootstrap: context.bootstrap,
        apiKey: context.apiKey,
        profileId: context.profileId,
        slug: record.skill,
        version: requestedVersion,
        targets: [{
          agent: record.agent,
          scope: record.scope,
          targetDir: dirname(record.targetRealPath),
        }],
        environment: context.environment,
        installationStore: context.store,
        now,
      });
      const targetResult = installed[0];
      if (targetResult?.status !== "success") {
        throw new McpcatError(
          ErrorCode.installFailed,
          targetResult?.error?.message ?? "Registry 历史版本安装失败",
          ExitCode.installation,
        );
      }
      results.push({
        installation: identity(record),
        fromVersion: record.version,
        toVersion: requestedVersion,
        status: "rolled-back",
        reason: "registry-history",
      });
    } catch (error) {
      results.push(failureResult(record, error));
    }
  }
  return results;
}
