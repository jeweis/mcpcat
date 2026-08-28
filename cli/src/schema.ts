import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export interface BootstrapResponse {
  instance_name: string;
  base_url: string;
  api_version: string;
  registry_schema_version: string;
  auth_header_name: string;
  registry_path: string;
  min_cli_version: string;
  recommended_cli_version: string;
}

export interface RegistrySkill {
  slug: string;
  display_name: string;
  description: string;
  source_type: string;
  status: string;
  latest_published_version: string;
  compatibility: Record<string, unknown>;
  sha256: string;
  size: number;
  download_url: string;
}

export interface RegistryIndex {
  registry_schema_version: string;
  api_version: string;
  skills: RegistrySkill[];
}

export interface SkillVersionDetail {
  version: string;
  status: string;
  changelog: string;
  compatibility: Record<string, unknown>;
  created_at: string;
  published_at: string | null;
  artifact: {
    sha256: string;
    size: number;
    integrity_status: string;
  } | null;
  files: unknown[];
  scripts: unknown[];
}

export interface SkillDetail {
  slug: string;
  display_name: string;
  description: string;
  source_type: string;
  source: Record<string, unknown>;
  status: string;
  updated_at: string;
  versions: SkillVersionDetail[];
}

function invalid(path: string): never {
  throw new McpcatError(
    ErrorCode.schema,
    `服务端响应格式无效：${path}`,
    ExitCode.schema,
    { path },
  );
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return invalid(path);
  }
  return value as Record<string, unknown>;
}

function stringField(value: Record<string, unknown>, key: string, path: string): string {
  const field = value[key];
  return typeof field === "string" && field.length > 0 ? field : invalid(`${path}.${key}`);
}

function numberField(value: Record<string, unknown>, key: string, path: string): number {
  const field = value[key];
  return typeof field === "number" && Number.isSafeInteger(field) && field >= 0
    ? field
    : invalid(`${path}.${key}`);
}

function nullableStringField(
  value: Record<string, unknown>,
  key: string,
  path: string,
): string | null {
  const field = value[key];
  return field === null || typeof field === "string" ? field : invalid(`${path}.${key}`);
}

export function parseBootstrap(value: unknown): BootstrapResponse {
  const input = record(value, "bootstrap");
  return {
    instance_name: stringField(input, "instance_name", "bootstrap"),
    base_url: stringField(input, "base_url", "bootstrap"),
    api_version: stringField(input, "api_version", "bootstrap"),
    registry_schema_version: stringField(input, "registry_schema_version", "bootstrap"),
    auth_header_name: stringField(input, "auth_header_name", "bootstrap"),
    registry_path: stringField(input, "registry_path", "bootstrap"),
    min_cli_version: stringField(input, "min_cli_version", "bootstrap"),
    recommended_cli_version: stringField(input, "recommended_cli_version", "bootstrap"),
  };
}

function parseSkill(value: unknown, index: number): RegistrySkill {
  const path = `registry.skills[${index}]`;
  const input = record(value, path);
  return {
    slug: stringField(input, "slug", path),
    display_name: stringField(input, "display_name", path),
    description: stringField(input, "description", path),
    source_type: stringField(input, "source_type", path),
    status: stringField(input, "status", path),
    latest_published_version: stringField(input, "latest_published_version", path),
    compatibility: record(input.compatibility, `${path}.compatibility`),
    sha256: stringField(input, "sha256", path),
    size: numberField(input, "size", path),
    download_url: stringField(input, "download_url", path),
  };
}

export function parseRegistry(value: unknown): RegistryIndex {
  const input = record(value, "registry");
  if (!Array.isArray(input.skills)) {
    return invalid("registry.skills");
  }
  return {
    registry_schema_version: stringField(input, "registry_schema_version", "registry"),
    api_version: stringField(input, "api_version", "registry"),
    skills: input.skills.map(parseSkill),
  };
}


function parseSkillVersion(value: unknown, index: number): SkillVersionDetail {
  const path = `skill.versions[${index}]`;
  const input = record(value, path);
  const artifactValue = input.artifact;
  let artifact: SkillVersionDetail["artifact"] = null;
  if (artifactValue !== null) {
    const artifactRecord = record(artifactValue, `${path}.artifact`);
    artifact = {
      sha256: stringField(artifactRecord, "sha256", `${path}.artifact`),
      size: numberField(artifactRecord, "size", `${path}.artifact`),
      integrity_status: stringField(
        artifactRecord,
        "integrity_status",
        `${path}.artifact`,
      ),
    };
  }
  if (!Array.isArray(input.files) || !Array.isArray(input.scripts)) {
    return invalid(`${path}.files`);
  }
  return {
    version: stringField(input, "version", path),
    status: stringField(input, "status", path),
    changelog: typeof input.changelog === "string" ? input.changelog : invalid(`${path}.changelog`),
    compatibility: record(input.compatibility, `${path}.compatibility`),
    created_at: stringField(input, "created_at", path),
    published_at: nullableStringField(input, "published_at", path),
    artifact,
    files: input.files,
    scripts: input.scripts,
  };
}

export function parseSkillDetail(value: unknown): SkillDetail {
  const input = record(value, "skill");
  if (!Array.isArray(input.versions)) {
    return invalid("skill.versions");
  }
  return {
    slug: stringField(input, "slug", "skill"),
    display_name: stringField(input, "display_name", "skill"),
    description: stringField(input, "description", "skill"),
    source_type: stringField(input, "source_type", "skill"),
    source: record(input.source, "skill.source"),
    status: stringField(input, "status", "skill"),
    updated_at: stringField(input, "updated_at", "skill"),
    versions: input.versions.map(parseSkillVersion),
  };
}
