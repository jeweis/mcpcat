import { randomUUID } from "node:crypto";
import { mkdir, readFile, rename, unlink, writeFile } from "node:fs/promises";
import { homedir, platform } from "node:os";
import { dirname, join } from "node:path";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export interface Profile {
  name: string;
  baseUrl: string;
  instanceName: string;
  apiVersion: string;
  registrySchemaVersion: string;
  authHeaderName: string;
  registryPath: string;
  createdAt: string;
  updatedAt: string;
}

interface ProfileDocument {
  schemaVersion: 1;
  defaultProfile?: string;
  profiles: Record<string, Profile>;
}

const PROFILE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

export function validateProfileName(name: string): string {
  if (!PROFILE_NAME.test(name)) {
    throw new McpcatError(
      ErrorCode.profileInvalid,
      "Profile 名称必须以字母或数字开头，且只能包含字母、数字、点、下划线和短横线",
      ExitCode.configuration,
      { profile: name },
    );
  }
  return name;
}

export function defaultConfigDir(env: NodeJS.ProcessEnv = process.env): string {
  if (platform() === "win32") {
    return join(env.APPDATA ?? join(homedir(), "AppData", "Roaming"), "mcpcat");
  }
  if (platform() === "darwin") {
    return join(homedir(), "Library", "Application Support", "mcpcat");
  }
  return join(env.XDG_CONFIG_HOME ?? join(homedir(), ".config"), "mcpcat");
}

function emptyDocument(): ProfileDocument {
  return { schemaVersion: 1, profiles: {} };
}

function parseDocument(value: unknown): ProfileDocument {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new McpcatError(
      ErrorCode.configInvalid,
      "Profile 配置格式无效",
      ExitCode.configuration,
    );
  }
  const input = value as Record<string, unknown>;
  if (input.schemaVersion !== 1 || input.profiles === null || typeof input.profiles !== "object") {
    throw new McpcatError(
      ErrorCode.configInvalid,
      "Profile 配置版本或内容无效",
      ExitCode.configuration,
    );
  }
  const profiles: Record<string, Profile> = {};
  for (const [name, raw] of Object.entries(input.profiles as Record<string, unknown>)) {
    validateProfileName(name);
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
      throw new McpcatError(ErrorCode.configInvalid, "Profile 条目无效", ExitCode.configuration);
    }
    const candidate = raw as Record<string, unknown>;
    const required = [
      "name",
      "baseUrl",
      "instanceName",
      "apiVersion",
      "registrySchemaVersion",
      "authHeaderName",
      "registryPath",
      "createdAt",
      "updatedAt",
    ] as const;
    if (required.some((field) => typeof candidate[field] !== "string")) {
      throw new McpcatError(ErrorCode.configInvalid, "Profile 字段无效", ExitCode.configuration);
    }
    profiles[name] = candidate as unknown as Profile;
  }
  const defaultProfile = input.defaultProfile;
  if (defaultProfile !== undefined && typeof defaultProfile !== "string") {
    throw new McpcatError(ErrorCode.configInvalid, "默认 Profile 无效", ExitCode.configuration);
  }
  return {
    schemaVersion: 1,
    profiles,
    ...(defaultProfile === undefined ? {} : { defaultProfile }),
  };
}

export class ProfileStore {
  constructor(readonly path = join(defaultConfigDir(), "profiles.json")) {}

  private async read(): Promise<ProfileDocument> {
    try {
      return parseDocument(JSON.parse(await readFile(this.path, "utf8")) as unknown);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") {
        return emptyDocument();
      }
      if (error instanceof McpcatError) {
        throw error;
      }
      throw new McpcatError(
        ErrorCode.configInvalid,
        "无法读取 Profile 配置",
        ExitCode.configuration,
        { path: this.path },
        { cause: error },
      );
    }
  }

  private async write(document: ProfileDocument): Promise<void> {
    await mkdir(dirname(this.path), { recursive: true, mode: 0o700 });
    const temporary = `${this.path}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, `${JSON.stringify(document, null, 2)}\n`, {
        encoding: "utf8",
        flag: "wx",
        mode: 0o600,
      });
      await rename(temporary, this.path);
    } finally {
      await unlink(temporary).catch(() => undefined);
    }
  }

  async list(): Promise<{ defaultProfile?: string; profiles: Profile[] }> {
    const document = await this.read();
    const profiles = Object.values(document.profiles).sort((a, b) => a.name.localeCompare(b.name));
    return {
      profiles,
      ...(document.defaultProfile === undefined ? {} : { defaultProfile: document.defaultProfile }),
    };
  }

  async save(profile: Profile, makeDefault = false): Promise<void> {
    validateProfileName(profile.name);
    const document = await this.read();
    const existing = document.profiles[profile.name];
    document.profiles[profile.name] = {
      ...profile,
      createdAt: existing?.createdAt ?? profile.createdAt,
    };
    if (makeDefault || document.defaultProfile === undefined) {
      document.defaultProfile = profile.name;
    }
    await this.write(document);
  }

  async use(name: string): Promise<Profile> {
    validateProfileName(name);
    const document = await this.read();
    const profile = document.profiles[name];
    if (profile === undefined) {
      throw new McpcatError(
        ErrorCode.profileNotFound,
        `Profile 不存在：${name}`,
        ExitCode.configuration,
        { profile: name },
      );
    }
    document.defaultProfile = name;
    await this.write(document);
    return profile;
  }

  async resolve(explicitName?: string): Promise<Profile> {
    const document = await this.read();
    const name = explicitName ?? document.defaultProfile;
    if (name === undefined || document.profiles[name] === undefined) {
      throw new McpcatError(
        ErrorCode.profileNotFound,
        explicitName === undefined ? "尚未配置默认 Profile" : `Profile 不存在：${explicitName}`,
        ExitCode.configuration,
      );
    }
    return document.profiles[name];
  }
}
