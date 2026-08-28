import type { CredentialStore } from "./credentials.js";
import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import type { Profile, ProfileStore } from "./profiles.js";
import { normalizeBaseUrl } from "./url.js";

export interface ResolvedConnection {
  source: "environment" | "profile";
  baseUrl: string;
  apiKey: string;
  profile?: Profile;
}

export async function resolveConnection(
  profileStore: ProfileStore,
  credentialStore: CredentialStore | undefined,
  options: {
    profileName?: string;
    env?: NodeJS.ProcessEnv;
    allowHttp?: boolean;
  } = {},
): Promise<ResolvedConnection> {
  const env = options.env ?? process.env;
  if (env.MCPCAT_URL !== undefined) {
    if (env.MCPCAT_API_KEY === undefined || env.MCPCAT_API_KEY.length === 0) {
      throw new McpcatError(
        ErrorCode.authRequired,
        "使用 MCPCAT_URL 时必须同时提供 MCPCAT_API_KEY",
        ExitCode.authentication,
      );
    }
    return {
      source: "environment",
      baseUrl: normalizeBaseUrl(env.MCPCAT_URL, options.allowHttp).url,
      apiKey: env.MCPCAT_API_KEY,
    };
  }
  const profile = await profileStore.resolve(options.profileName);
  const environmentKey = env.MCPCAT_API_KEY;
  const apiKey = environmentKey ?? (await credentialStore?.get(profile.name));
  if (apiKey === undefined || apiKey.length === 0) {
    throw new McpcatError(
      ErrorCode.authRequired,
      `Profile ${profile.name} 没有可用凭证；请重新 connect 或设置 MCPCAT_API_KEY`,
      ExitCode.authentication,
      { profile: profile.name },
    );
  }
  return { source: "profile", baseUrl: profile.baseUrl, apiKey, profile };
}
