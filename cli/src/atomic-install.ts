import { constants } from "node:fs";
import { access, cp, lstat, mkdir, realpath, rename, rm } from "node:fs/promises";
import { basename, join, resolve, sep } from "node:path";
import { randomUUID } from "node:crypto";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export interface AtomicInstallOperations {
  access: typeof access;
  cp: typeof cp;
  lstat: typeof lstat;
  mkdir: typeof mkdir;
  realpath: typeof realpath;
  rename: typeof rename;
  rm: typeof rm;
}

const DEFAULT_OPERATIONS: AtomicInstallOperations = {
  access,
  cp,
  lstat,
  mkdir,
  realpath,
  rename,
  rm,
};

async function exists(path: string, operations: AtomicInstallOperations): Promise<boolean> {
  try {
    await operations.lstat(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

function installError(message: string, details?: Record<string, unknown>, cause?: unknown): McpcatError {
  return new McpcatError(
    ErrorCode.installFailed,
    message,
    ExitCode.installation,
    details,
    cause === undefined ? undefined : { cause },
  );
}

export async function prepareTargetBase(
  targetBase: string,
  operations: AtomicInstallOperations = DEFAULT_OPERATIONS,
): Promise<string> {
  try {
    await operations.mkdir(targetBase, { recursive: true, mode: 0o755 });
    const stats = await operations.lstat(targetBase);
    if (!stats.isDirectory()) {
      throw installError("Skills 目标不是目录", { target: targetBase });
    }
    await operations.access(targetBase, constants.W_OK | constants.X_OK);
    return await operations.realpath(targetBase);
  } catch (error) {
    if (error instanceof McpcatError) {
      throw error;
    }
    throw installError("Skills 目标目录不可写", { target: targetBase }, error);
  }
}

export async function atomicInstallSkill(
  sourceRoot: string,
  targetBaseRealPath: string,
  skill: string,
  afterCommit: (targetRealPath: string) => Promise<void>,
  operations: AtomicInstallOperations = DEFAULT_OPERATIONS,
): Promise<string> {
  if (basename(skill) !== skill || skill === "." || skill === "..") {
    throw installError("Skill 名称无法安全映射到目标目录", { skill });
  }
  const base = resolve(targetBaseRealPath);
  const target = join(base, skill);
  if (!target.startsWith(`${base}${sep}`)) {
    throw installError("Skill 安装路径越界", { target });
  }
  const staging = join(base, `.mcpcat-${skill}-${randomUUID()}.tmp`);
  const backup = join(base, `.mcpcat-${skill}-${randomUUID()}.backup`);
  let hadPrevious = false;
  let committed = false;
  try {
    await operations.cp(sourceRoot, staging, {
      recursive: true,
      force: false,
      errorOnExist: true,
      preserveTimestamps: false,
    });
    if (await exists(target, operations)) {
      const targetStats = await operations.lstat(target);
      if (targetStats.isSymbolicLink() || !targetStats.isDirectory()) {
        throw installError("已有 Skill 目标不是安全目录", { target });
      }
      await operations.rename(target, backup);
      hadPrevious = true;
    }
    await operations.rename(staging, target);
    committed = true;
    const targetRealPath = await operations.realpath(target);
    if (!targetRealPath.startsWith(`${base}${sep}`)) {
      throw installError("安装后的 Skill realpath 越界", { target: targetRealPath });
    }
    await afterCommit(targetRealPath);
    if (hadPrevious) {
      await operations.rm(backup, { recursive: true, force: true });
    }
    return targetRealPath;
  } catch (error) {
    try {
      if (committed && await exists(target, operations)) {
        await operations.rm(target, { recursive: true, force: true });
      }
      if (hadPrevious && await exists(backup, operations)) {
        await operations.rename(backup, target);
      }
    } catch (restoreError) {
      throw installError(
        "安装失败且旧版本恢复失败",
        { target, restoreError: String(restoreError) },
        error,
      );
    } finally {
      await operations.rm(staging, { recursive: true, force: true }).catch(() => undefined);
    }
    if (error instanceof McpcatError) {
      throw error;
    }
    throw installError("原子安装失败，旧目录已恢复", { target }, error);
  }
}
