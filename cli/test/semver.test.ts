import { describe, expect, it } from "vitest";

import { compareSemver } from "../src/semver.js";

describe("SemVer 更新比较", () => {
  it("按数值而非字符串比较 minor/patch", () => {
    expect(compareSemver("1.10.0", "1.2.0")).toBeGreaterThan(0);
    expect(compareSemver("1.2.10", "1.2.9")).toBeGreaterThan(0);
  });

  it("稳定版本高于预发布版本", () => {
    expect(compareSemver("2.0.0", "2.0.0-rc.1")).toBeGreaterThan(0);
    expect(compareSemver("2.0.0-rc.2", "2.0.0-rc.1")).toBeGreaterThan(0);
  });
});
