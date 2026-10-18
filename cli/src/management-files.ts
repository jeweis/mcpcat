import { constants } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { basename, join, relative, resolve, sep } from "node:path";

import { ErrorCode, ExitCode, McpcatError } from "./errors.js";
import { usage } from "./management-args.js";

/** 普通文件有界读取，不跟随入口或文件符号链接。 */
export async function inputBytes(path: string, limit: number): Promise<Uint8Array> {
  const info = await lstat(path).catch(() => usage("输入文件不可读取"));
  if (!info.isFile() || info.isSymbolicLink() || info.size > limit) usage("输入必须是限额内的普通文件，不能是符号链接");
  const file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await file.stat();
    if (!stat.isFile() || stat.size > limit) usage("输入文件超限或已变化");
    const bytes = Buffer.alloc(limit + 1);
    let offset = 0;
    while (offset <= limit) {
      const {bytesRead} = await file.read(bytes, offset, limit + 1 - offset, null);
      if (!bytesRead) break;
      offset += bytesRead;
    }
    if (offset > limit) usage("输入文件超过限额");
    return bytes.subarray(0, offset);
  } finally { await file.close(); }
}

export async function jsonInput(path: string): Promise<Record<string, unknown>> {
  try {
    const value: unknown = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(await inputBytes(path, 1024 * 1024)));
    if (value !== null && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  } catch (error) { if (error instanceof McpcatError) throw error; }
  return usage("配置文件必须是有效的 UTF-8 JSON 对象");
}

/** wx 不会覆盖既有文件或链接，含凭证的配置导出采用 0600。 */
export async function saveNewFile(path: string, bytes: Uint8Array): Promise<string> {
  try {
    const file = await open(path, "wx", 0o600);
    try { await file.writeFile(bytes); }
    finally { await file.close(); }
    return resolve(path);
  } catch {
    throw new McpcatError(ErrorCode.targetInvalid, "目标文件已存在或不可写，拒绝覆盖（包括符号链接）", ExitCode.configuration);
  }
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function zip(files: {name: string; bytes: Uint8Array}[]): Uint8Array {
  const local: Buffer[] = [], central: Buffer[] = [];
  let offset = 0;
  for (const file of files) {
    const name = Buffer.from(file.name), content = Buffer.from(file.bytes);
    const header = Buffer.alloc(30), entry = Buffer.alloc(46);
    const crc = crc32(content);
    header.writeUInt32LE(0x04034b50); header.writeUInt16LE(20, 4); header.writeUInt16LE(0x800, 6);
    header.writeUInt32LE(crc, 14); header.writeUInt32LE(content.length, 18); header.writeUInt32LE(content.length, 22);
    header.writeUInt16LE(name.length, 26);
    entry.writeUInt32LE(0x02014b50); entry.writeUInt16LE(20, 4); entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x800, 8); entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(content.length, 20); entry.writeUInt32LE(content.length, 24);
    entry.writeUInt16LE(name.length, 28); entry.writeUInt32LE(offset, 42);
    local.push(header, name, content); central.push(entry, name);
    offset += header.length + name.length + content.length;
  }
  const directory = Buffer.concat(central), end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50); end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(directory.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, directory, end]);
}

const EXCLUDED = /^(?:\.git|node_modules|__pycache__|\.venv|\.ssh|\.DS_Store|\.env(?:\..*)?)$|\.(?:pem|key|pyc)$/i;

/** 目录仅在本地打包；原 ZIP 字节保持原样，服务端仍做最终校验。 */
export async function uploadInput(path: string, skill: boolean): Promise<{bytes: Uint8Array; name: string; excluded: string[]}> {
  const info = await lstat(path).catch(() => usage("上传路径不可读取"));
  if (info.isSymbolicLink()) usage("上传入口不能是符号链接");
  if (!info.isDirectory()) {
    const name = basename(path);
    if (!(skill ? /\.zip$/i : /\.(zip|html|json)$/i).test(name)) usage("不支持的上传文件类型");
    return {name, bytes: await inputBytes(path, skill ? 20 * 1024 * 1024 : 16 * 1024 * 1024), excluded: []};
  }
  if (!skill) usage("Apps 请上传已构建 HTML、JSON 或 ZIP，不运行本地构建");
  const root = await realpath(path), name = basename(root);
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name)) usage("Skill 文件夹名称须为小写字母/数字/连字符，并与 frontmatter name 一致");
  const files: {name: string; bytes: Uint8Array}[] = [], excluded: string[] = [];
  let total = 0, scanned = 0;
  async function walk(directory: string, depth: number): Promise<void> {
    if (depth > 16) usage("目录层级超过 16 层");
    for (const entry of (await readdir(directory)).sort()) {
      if (++scanned > 4096) usage("目录扫描数量超过限额");
      const full = join(directory, entry), rel = relative(root, full).split(sep).join("/");
      if (EXCLUDED.test(entry)) { excluded.push(rel); continue; }
      if (entry.includes("\\") || [...entry].some(char => char.charCodeAt(0) < 32)) usage("目录包含不安全路径");
      const stat = await lstat(full);
      if (stat.isSymbolicLink()) usage("Skill 目录不能包含符号链接");
      if (!(await realpath(full)).startsWith(root + sep)) usage("目录路径超出上传根目录");
      if (stat.isDirectory()) { await walk(full, depth + 1); continue; }
      if (files.length >= 512 || stat.size > 10 * 1024 * 1024 || total + stat.size > 16 * 1024 * 1024) usage("目录超过 512 文件 / 16 MiB / 单文件 10 MiB 限额");
      const bytes = await inputBytes(full, 10 * 1024 * 1024);
      total += bytes.length;
      if (total > 16 * 1024 * 1024) usage("目录内容已变化且超限");
      files.push({name: `${name}/${rel}`, bytes});
    }
  }
  await walk(root, 0);
  if (!files.some(file => file.name === `${name}/SKILL.md`)) usage("Skill 根目录缺少 SKILL.md");
  return {bytes: zip(files), name: `${name}.zip`, excluded};
}

/** 下载前拒绝覆盖，传输失败也不触及旧文件。 */
export async function assertNewTarget(path: string): Promise<void> {
  try { await lstat(path); }
  catch (error) { if ((error as NodeJS.ErrnoException).code === "ENOENT") return; throw error; }
  return usage("目标文件已存在，拒绝覆盖");
}
