import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import { compareSemver, parseSemver } from "./semver.js";
import {
  CLI_VERSION,
  SUPPORTED_API_VERSION,
  SUPPORTED_REGISTRY_SCHEMA_MAJOR,
} from "./version.js";

export interface CompatibilityInfo {
  apiVersion: string;
  registrySchemaVersion: string;
  minCliVersion?: string;
  recommendedCliVersion?: string;
}

export function assertCompatibility(info: CompatibilityInfo): void {
  const schemaMajor = parseSemver(info.registrySchemaVersion).major;
  const cliTooOld =
    info.minCliVersion !== undefined && compareSemver(CLI_VERSION, info.minCliVersion) < 0;
  if (
    info.apiVersion !== SUPPORTED_API_VERSION ||
    schemaMajor !== SUPPORTED_REGISTRY_SCHEMA_MAJOR ||
    cliTooOld
  ) {
    throw new McpcatError(
      ErrorCode.incompatible,
      "CLI 与目标 mcpcat Registry API 不兼容",
      ExitCode.compatibility,
      {
        cliVersion: CLI_VERSION,
        supportedApiVersion: SUPPORTED_API_VERSION,
        apiVersion: info.apiVersion,
        registrySchemaVersion: info.registrySchemaVersion,
        ...(info.minCliVersion === undefined ? {} : { minCliVersion: info.minCliVersion }),
        ...(info.recommendedCliVersion === undefined
          ? {}
          : { recommendedCliVersion: info.recommendedCliVersion }),
      },
    );
  }
}
