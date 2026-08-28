import { describe, expect, it } from "vitest";

import { SafeLogger } from "../src/logger.js";
import { redact, REDACTED } from "../src/redact.js";

describe("redact", () => {
  it("递归隐藏敏感字段、认证头、环境变量和已知密钥", () => {
    const secret = "mcpcat-super-secret";
    expect(
      redact(
        {
          apiKey: secret,
          nested: {
            Authorization: `Bearer ${secret}`,
            message: `MCPCAT_API_KEY=${secret} value=${secret}`,
          },
        },
        [secret],
      ),
    ).toEqual({
      apiKey: REDACTED,
      nested: {
        Authorization: REDACTED,
        message: `MCPCAT_API_KEY=${REDACTED} value=${REDACTED}`,
      },
    });
  });

  it("SafeLogger 永远在写出前脱敏", () => {
    const lines: string[] = [];
    const logger = new SafeLogger((value) => lines.push(value), true, ["secret-value"]);
    logger.log("error", "request secret-value failed", {
      api_key: "secret-value",
      url: "https://safe.example.com",
    });

    expect(lines.join("")).not.toContain("secret-value");
    expect(lines.join("")).toContain(REDACTED);
  });
});
