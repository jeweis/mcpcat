import { describe, expect, it } from "vitest";

import { ErrorCode, McpcatError } from "../src/errors.js";
import { normalizeBaseUrl } from "../src/url.js";

describe("normalizeBaseUrl", () => {
  it("默认补全 HTTPS 并移除尾随斜杠", () => {
    expect(normalizeBaseUrl("mcpcat.example.com///")).toEqual({
      url: "https://mcpcat.example.com",
      insecure: false,
    });
  });

  it.each([
    "http://localhost:8000/",
    "http://127.0.0.1:8000",
    "http://127.9.8.7:8000",
    "http://[::1]:8000",
  ])("允许本地 HTTP：%s", (url) => {
    expect(normalizeBaseUrl(url).insecure).toBe(false);
  });

  it("拒绝未显式允许的远程 HTTP", () => {
    expect(() => normalizeBaseUrl("http://mcpcat.example.com")).toThrowError(
      expect.objectContaining<Partial<McpcatError>>({ code: ErrorCode.insecureTransport }),
    );
  });

  it("显式允许远程 HTTP 并标记为不安全", () => {
    expect(normalizeBaseUrl("http://mcpcat.example.com/path/", true)).toEqual({
      url: "http://mcpcat.example.com/path",
      insecure: true,
    });
  });

  it.each([
    "ftp://example.com",
    "https://user:secret@example.com",
    "https://example.com?api_key=secret",
    "https://example.com#secret",
  ])("拒绝危险或非 HTTP URL：%s", (url) => {
    expect(() => normalizeBaseUrl(url)).toThrow(McpcatError);
  });
});
