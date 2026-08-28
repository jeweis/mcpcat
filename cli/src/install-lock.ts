import { createHash, randomUUID } from "node:crypto";
import { open } from "node:fs/promises";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";

import type { AgentId, InstallScope } from "./agents/types.js";
import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import { defaultConfigDir } from "./profiles.js";

export interface InstallationIdentity {
  profile: string;
  agent: AgentId;
  scope: InstallScope;
  targetRealPath: string;
  skill: string;
}

export interface InstallationRecord extends InstallationIdentity {
  version: string;
  sha256: string;
  installedAt: string;
  pinnedVersion?: string;
  backups?: LocalBackup[];
}

export interface LocalBackup {
  version: string;
  sha256: string;
  path: string;
  createdAt: string;
}

interface InstallationDocument {
  schemaVersion: 1;
  installations: Record<string, InstallationRecord>;
}

export function installationKey(identity: InstallationIdentity): string {
  const canonical = JSON.stringify([
    identity.profile,
    identity.agent,
    identity.scope,
    identity.targetRealPath,
    identity.skill,
  ]);
  return createHash("sha256").update(canonical).digest("hex");
}

async function withExclusiveFile<T>(path: string, operation: () => Promise<T>): Promise<T> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  let handle;
  try {
    handle = await open(path, "wx", 0o600);
    await handle.writeFile(`${process.pid}\n`);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "EEXIST") {
      throw new McpcatError(
        ErrorCode.lockBusy,
        "目标正在被另一个 mcpcat 进程安装",
        ExitCode.installation,
        { lock: path },
      );
    }
    throw error;
  }
  try {
    return await operation();
  } finally {
    await handle.close();
    await unlink(path).catch(() => undefined);
  }
}

export class InstallationStore {
  readonly locksDirectory: string;
  readonly backupsDirectory: string;

  constructor(
    readonly path = join(defaultConfigDir(), "installations.json"),
    locksDirectory = join(dirname(path), "locks"),
    backupsDirectory = join(dirname(path), "backups"),
  ) {
    this.locksDirectory = locksDirectory;
    this.backupsDirectory = backupsDirectory;
  }

  private async read(): Promise<InstallationDocument> {
    try {
      const value = JSON.parse(await readFile(this.path, "utf8")) as InstallationDocument;
      if (value.schemaVersion !== 1 || value.installations === undefined) {
        throw new Error("invalid installation document");
      }
      return value;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return { schemaVersion: 1, installations: {} };
      }
      throw new McpcatError(
        ErrorCode.configInvalid,
        "安装锁文件无效",
        ExitCode.configuration,
        { path: this.path },
        { cause: error },
      );
    }
  }

  private async write(document: InstallationDocument): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, `${JSON.stringify(document, null, 2)}\n`, {
        flag: "wx",
        mode: 0o600,
      });
      await rename(temporary, this.path);
    } finally {
      await unlink(temporary).catch(() => undefined);
    }
  }

  async withTargetLock<T>(identity: InstallationIdentity, operation: () => Promise<T>): Promise<T> {
    return withExclusiveFile(
      join(this.locksDirectory, `${installationKey(identity)}.lock`),
      operation,
    );
  }

  async record(record: InstallationRecord): Promise<void> {
    const metadataLock = `${this.path}.lock`;
    await withExclusiveFile(metadataLock, async () => {
      const document = await this.read();
      const key = installationKey(record);
      const previous = document.installations[key];
      document.installations[key] = {
        ...record,
        ...(record.pinnedVersion === undefined && previous?.pinnedVersion !== undefined
          ? { pinnedVersion: previous.pinnedVersion }
          : {}),
        ...(record.backups === undefined && previous?.backups !== undefined
          ? { backups: previous.backups }
          : {}),
      };
      await this.write(document);
    });
  }

  async list(): Promise<InstallationRecord[]> {
    return Object.values((await this.read()).installations);
  }

  async get(identity: InstallationIdentity): Promise<InstallationRecord | undefined> {
    return (await this.read()).installations[installationKey(identity)];
  }

  async setPin(identity: InstallationIdentity, version?: string): Promise<InstallationRecord> {
    const metadataLock = `${this.path}.lock`;
    return withExclusiveFile(metadataLock, async () => {
      const document = await this.read();
      const key = installationKey(identity);
      const record = document.installations[key];
      if (record === undefined) {
        throw new McpcatError(
          ErrorCode.skillNotFound,
          "未找到对应安装记录",
          ExitCode.configuration,
          { identity },
        );
      }
      const updated: InstallationRecord = { ...record };
      if (version === undefined) {
        delete updated.pinnedVersion;
      } else {
        updated.pinnedVersion = version;
      }
      document.installations[key] = updated;
      await this.write(document);
      return updated;
    });
  }

  async addBackup(identity: InstallationIdentity, backup: LocalBackup): Promise<void> {
    const metadataLock = `${this.path}.lock`;
    await withExclusiveFile(metadataLock, async () => {
      const document = await this.read();
      const key = installationKey(identity);
      const record = document.installations[key];
      if (record === undefined) {
        throw new McpcatError(
          ErrorCode.skillNotFound,
          "无法为未知安装记录添加备份",
          ExitCode.configuration,
          { identity },
        );
      }
      document.installations[key] = {
        ...record,
        backups: [backup, ...(record.backups ?? [])],
      };
      await this.write(document);
    });
  }
}
