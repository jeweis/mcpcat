import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

export interface CredentialStore {
  get(profile: string): Promise<string | undefined>;
  set(profile: string, apiKey: string): Promise<void>;
  delete(profile: string): Promise<void>;
}

type KeyringEntry = {
  getPassword(): string | null | Promise<string | null>;
  setPassword(password: string): void | Promise<void>;
  deletePassword(): boolean | void | Promise<boolean | void>;
};

type KeyringModule = {
  Entry?: new (service: string, account: string) => KeyringEntry;
};

const SERVICE_NAME = "mcpcat";
type KeyringLoader = () => Promise<KeyringModule | undefined>;

async function loadKeyring(): Promise<KeyringModule | undefined> {
  try {
    return await import("@napi-rs/keyring");
  } catch {
    return undefined;
  }
}

async function unavailable(): Promise<KeyringModule | undefined> {
  return undefined;
}

export class KeychainCredentialStore implements CredentialStore {
  private readonly load: KeyringLoader;

  constructor(keyringOrLoad: KeyringModule | KeyringLoader = loadKeyring) {
    this.load = typeof keyringOrLoad === "function" ? keyringOrLoad : async () => keyringOrLoad;
  }

  private async entry(profile: string): Promise<KeyringEntry> {
    const keyring = await this.load();
    if (keyring?.Entry === undefined) {
      throw new McpcatError(
        ErrorCode.credentialStore,
        "系统 Keychain 不可用",
        ExitCode.credentialStore,
        { profile },
      );
    }
    return new keyring.Entry(SERVICE_NAME, profile);
  }

  async get(profile: string): Promise<string | undefined> {
    try {
      return (await (await this.entry(profile)).getPassword()) ?? undefined;
    } catch (error) {
      if (error instanceof McpcatError) {
        throw error;
      }
      return undefined;
    }
  }

  async set(profile: string, apiKey: string): Promise<void> {
    try {
      await (await this.entry(profile)).setPassword(apiKey);
    } catch (error) {
      if (error instanceof McpcatError) {
        throw error;
      }
      throw new McpcatError(
        ErrorCode.credentialStore,
        "无法保存系统 Keychain 凭证",
        ExitCode.credentialStore,
        { profile },
        { cause: error },
      );
    }
  }

  async delete(profile: string): Promise<void> {
    try {
      await (await this.entry(profile)).deletePassword();
    } catch (error) {
      if (!(error instanceof McpcatError)) {
        return;
      }
      throw error;
    }
  }
}

export class MemoryCredentialStore implements CredentialStore {
  private readonly values = new Map<string, string>();

  async get(profile: string): Promise<string | undefined> {
    return this.values.get(profile);
  }

  async set(profile: string, apiKey: string): Promise<void> {
    this.values.set(profile, apiKey);
  }

  async delete(profile: string): Promise<void> {
    this.values.delete(profile);
  }
}

export async function createKeychainCredentialStore(
  load: KeyringLoader = loadKeyring,
): Promise<KeychainCredentialStore | undefined> {
  try {
    const keyring = await load();
    if (keyring?.Entry === undefined) {
      return undefined;
    }
    return new KeychainCredentialStore(keyring);
  } catch {
    return undefined;
  }
}

export function createUnavailableCredentialStore(): KeychainCredentialStore {
  return new KeychainCredentialStore(unavailable);
}
