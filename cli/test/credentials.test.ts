import { describe, expect, it } from "vitest";

import {
  createKeychainCredentialStore,
  KeychainCredentialStore,
} from "../src/credentials.js";

class MemoryEntry {
  static readonly values = new Map<string, string>();
  private readonly key: string;

  constructor(service: string, account: string) {
    this.key = `${service}:${account}`;
  }

  setPassword(password: string): void {
    MemoryEntry.values.set(this.key, password);
  }

  getPassword(): string {
    const value = MemoryEntry.values.get(this.key);
    if (value === undefined) {
      throw new Error("No entry");
    }
    return value;
  }

  deletePassword(): boolean {
    return MemoryEntry.values.delete(this.key);
  }
}

describe("KeychainCredentialStore", () => {
  it("使用系统 Keychain 接口保存、读取和删除", async () => {
    const store = new KeychainCredentialStore({ Entry: MemoryEntry });
    await store.set("company", "secret");
    await expect(store.get("company")).resolves.toBe("secret");
    await store.delete("company");
    await expect(store.get("company")).resolves.toBeUndefined();
  });

  it("Keychain 模块不可用时安全返回 undefined，不创建文件回退", async () => {
    await expect(
      createKeychainCredentialStore(async () => {
        throw new Error("native module unavailable");
      }),
    ).resolves.toBeUndefined();
  });
});
