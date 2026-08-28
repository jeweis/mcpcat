import { createWriteStream } from "node:fs";
import { lstat, mkdir, readFile } from "node:fs/promises";
import { dirname, isAbsolute, posix, resolve, sep } from "node:path";
import { pipeline } from "node:stream/promises";

import { parseDocument } from "yaml";
import { open, type Entry, type ZipFile } from "yauzl";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";

const MAX_ZIP_BYTES = 50 * 1024 * 1024;
const MAX_EXPANDED_BYTES = 100 * 1024 * 1024;
const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_FILES = 1_000;
const MAX_FRONTMATTER_BYTES = 64 * 1024;
const SKILL_NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

interface CheckedEntry {
  path: string;
  directory: boolean;
  size: number;
}

export interface ValidatedSkillPackage {
  name: string;
  description: string;
  rootDir: string;
  files: string[];
  scripts: string[];
}

function packageError(message: string, details?: Record<string, unknown>): McpcatError {
  return new McpcatError(
    ErrorCode.packageInvalid,
    message,
    ExitCode.integrity,
    details,
  );
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function openZip(path: string): Promise<ZipFile> {
  return new Promise((resolveZip, reject) => {
    open(
      path,
      {
        autoClose: false,
        lazyEntries: true,
        decodeStrings: true,
        strictFileNames: true,
        validateEntrySizes: true,
      },
      (error, zip) => {
        if (error !== null) {
          reject(packageError("ZIP 文件无效", { reason: error.message }));
        } else {
          resolveZip(zip);
        }
      },
    );
  });
}

function checkedPath(entry: Entry): CheckedEntry {
  const value = entry.fileName;
  if (
    value.includes("\\") ||
    value.includes("\0") ||
    isAbsolute(value) ||
    /^[A-Za-z]:/.test(value)
  ) {
    throw packageError("ZIP 包含绝对路径或歧义路径", { path: value });
  }
  const withoutTrailingSlash = value.replace(/\/+$/, "");
  const segments = withoutTrailingSlash.split("/");
  if (
    withoutTrailingSlash.length === 0 ||
    segments.some((segment) => segment === "" || segment === "." || segment === "..") ||
    posix.normalize(withoutTrailingSlash) !== withoutTrailingSlash
  ) {
    throw packageError("ZIP 包含路径穿越或非法路径", { path: value });
  }
  if (entry.isEncrypted()) {
    throw packageError("不支持加密 ZIP 条目", { path: value });
  }
  const unixMode = (entry.externalFileAttributes >>> 16) & 0xffff;
  const fileType = unixMode & 0o170000;
  const directory = value.endsWith("/") || fileType === 0o040000;
  if (fileType !== 0 && fileType !== 0o100000 && fileType !== 0o040000) {
    throw packageError("ZIP 包含符号链接、设备文件或其他特殊文件", { path: value });
  }
  return { path: withoutTrailingSlash, directory, size: entry.uncompressedSize };
}

async function inspectZip(path: string): Promise<CheckedEntry[]> {
  const source = await lstat(path);
  if (!source.isFile() || source.size > MAX_ZIP_BYTES) {
    throw packageError("ZIP 文件过大或不是普通文件", { size: source.size });
  }
  const zip = await openZip(path);
  try {
    return await new Promise<CheckedEntry[]>((resolveEntries, reject) => {
      const entries: CheckedEntry[] = [];
      const seen = new Set<string>();
      let expandedSize = 0;
      zip.on("error", (error: unknown) =>
        reject(packageError("读取 ZIP 失败", { reason: errorMessage(error) })),
      );
      zip.on("entry", (entry: Entry) => {
        try {
          const checked = checkedPath(entry);
          if (seen.has(checked.path)) {
            throw packageError("ZIP 包含重复路径", { path: checked.path });
          }
          seen.add(checked.path);
          if (!checked.directory) {
            expandedSize += checked.size;
            if (checked.size > MAX_FILE_BYTES) {
              throw packageError("ZIP 中单个文件过大", { path: checked.path });
            }
            if (entries.filter((item) => !item.directory).length + 1 > MAX_FILES) {
              throw packageError("ZIP 文件数量超出限制");
            }
            if (expandedSize > MAX_EXPANDED_BYTES) {
              throw packageError("ZIP 展开总大小超出限制");
            }
          }
          entries.push(checked);
          zip.readEntry();
        } catch (error) {
          reject(error instanceof Error ? error : packageError("ZIP 条目校验失败"));
        }
      });
      zip.on("end", () => resolveEntries(entries));
      zip.readEntry();
    });
  } finally {
    zip.close();
  }
}

function destinationPath(root: string, relative: string): string {
  const destination = resolve(root, ...relative.split("/"));
  const normalizedRoot = resolve(root);
  if (destination !== normalizedRoot && !destination.startsWith(`${normalizedRoot}${sep}`)) {
    throw packageError("ZIP 解压路径越界", { path: relative });
  }
  return destination;
}

function streamEntry(zip: ZipFile, entry: Entry): Promise<NodeJS.ReadableStream> {
  return new Promise((resolveStream, reject) => {
    zip.openReadStream(entry, (error, stream) => {
      if (error !== null) {
        reject(packageError("无法读取 ZIP 条目", { path: entry.fileName }));
      } else {
        resolveStream(stream);
      }
    });
  });
}

async function extractZip(path: string, destination: string): Promise<void> {
  const zip = await openZip(path);
  try {
    await new Promise<void>((resolveExtraction, reject) => {
      zip.on("error", (error: unknown) =>
        reject(packageError("解压 ZIP 失败", { reason: errorMessage(error) })),
      );
      zip.on("entry", (entry: Entry) => {
        void (async () => {
          const checked = checkedPath(entry);
          const target = destinationPath(destination, checked.path);
          if (checked.directory) {
            await mkdir(target, { recursive: true, mode: 0o755 });
          } else {
            await mkdir(dirname(target), { recursive: true, mode: 0o755 });
            const stream = await streamEntry(zip, entry);
            await pipeline(stream, createWriteStream(target, { flags: "wx", mode: 0o644 }));
          }
          zip.readEntry();
        })().catch(reject);
      });
      zip.on("end", resolveExtraction);
      zip.readEntry();
    });
  } finally {
    zip.close();
  }
}

function parseFrontmatter(content: string, expectedName: string): { name: string; description: string } {
  const match = /^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/.exec(content);
  if (match?.[1] === undefined || Buffer.byteLength(match[1]) > MAX_FRONTMATTER_BYTES) {
    throw packageError("SKILL.md 缺少有效 YAML frontmatter");
  }
  const document = parseDocument(match[1], { uniqueKeys: true });
  if (document.errors.length > 0) {
    throw packageError("SKILL.md frontmatter YAML 无效");
  }
  const value = document.toJS({ maxAliasCount: 0 }) as unknown;
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw packageError("SKILL.md frontmatter 必须是对象");
  }
  const metadata = value as Record<string, unknown>;
  if (
    typeof metadata.name !== "string" ||
    metadata.name.length > 64 ||
    !SKILL_NAME.test(metadata.name)
  ) {
    throw packageError("SKILL.md name 不符合 Agent Skills slug 规则");
  }
  if (metadata.name !== expectedName) {
    throw packageError("SKILL.md name 必须与根目录一致", {
      name: metadata.name,
      root: expectedName,
    });
  }
  if (typeof metadata.description !== "string" || metadata.description.trim().length === 0) {
    throw packageError("SKILL.md description 必须是非空字符串");
  }
  if (metadata.description.length > 1024) {
    throw packageError("SKILL.md description 不得超过 1024 字符");
  }
  const compatibility = metadata.compatibility;
  if (
    compatibility !== undefined &&
    (typeof compatibility !== "string" || compatibility.length < 1 || compatibility.length > 500)
  ) {
    throw packageError("compatibility 必须是 1-500 字符字符串");
  }
  const customMetadata = metadata.metadata;
  if (
    customMetadata !== undefined &&
    (
      customMetadata === null ||
      typeof customMetadata !== "object" ||
      Array.isArray(customMetadata) ||
      Object.entries(customMetadata).some(
        ([key, value]) => typeof key !== "string" || typeof value !== "string",
      )
    )
  ) {
    throw packageError("metadata 必须是字符串到字符串的映射");
  }
  if (metadata["allowed-tools"] !== undefined && typeof metadata["allowed-tools"] !== "string") {
    throw packageError("allowed-tools 必须是字符串");
  }
  return { name: metadata.name, description: metadata.description.trim() };
}

export async function validateAndExtractSkillZip(
  zipPath: string,
  destination: string,
): Promise<ValidatedSkillPackage> {
  const entries = await inspectZip(zipPath);
  const fileEntries = entries.filter((entry) => !entry.directory);
  if (fileEntries.length === 0) {
    throw packageError("ZIP 不得为空");
  }
  const roots = new Set(entries.map((entry) => entry.path.split("/")[0]));
  if (roots.size !== 1) {
    throw packageError("ZIP 必须只包含一个 Skill 根目录");
  }
  const root = [...roots][0];
  if (root === undefined || !SKILL_NAME.test(root) || root.length > 64) {
    throw packageError("Skill 根目录名不符合规范");
  }
  const skillMarkdown = `${root}/SKILL.md`;
  if (!fileEntries.some((entry) => entry.path === skillMarkdown)) {
    throw packageError("ZIP 缺少根目录下的 SKILL.md");
  }
  await mkdir(destination, { recursive: true, mode: 0o700 });
  await extractZip(zipPath, destination);
  const frontmatter = parseFrontmatter(
    await readFile(destinationPath(destination, skillMarkdown), "utf8"),
    root,
  );
  const files = fileEntries.map((entry) => entry.path).sort();
  const scripts = files.filter((path) => /(?:^|\/)(?:scripts\/|[^/]+\.(?:sh|py|js|ts|rb|ps1)$)/i.test(path));
  return {
    ...frontmatter,
    rootDir: destinationPath(destination, root),
    files,
    scripts,
  };
}
