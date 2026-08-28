import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterEach, describe, expect, it } from "vitest";

import { ErrorCode } from "../src/errors.js";
import { validateAndExtractSkillZip } from "../src/skill-package.js";
import { buildZip, validSkillZip } from "./zip-fixture.js";

describe("安全 Skill ZIP 校验与解压", () => {
  const directories: string[] = [];

  afterEach(async () => {
    await Promise.all(directories.splice(0).map((path) => rm(path, { recursive: true, force: true })));
  });

  async function fixture(bytes: Uint8Array): Promise<{ zip: string; output: string }> {
    const directory = await mkdtemp(join(tmpdir(), "mcpcat-package-"));
    directories.push(directory);
    const zip = join(directory, "skill.zip");
    await writeFile(zip, bytes);
    return { zip, output: join(directory, "output") };
  }

  it("校验并提取单一 Agent Skill 根目录", async () => {
    const paths = await fixture(validSkillZip());
    const result = await validateAndExtractSkillZip(paths.zip, paths.output);

    expect(result.name).toBe("demo-skill");
    expect(result.files).toContain("demo-skill/SKILL.md");
    await expect(readFile(join(result.rootDir, "SKILL.md"), "utf8")).resolves.toContain(
      "Use this Skill",
    );
  });

  it.each([
    buildZip([{ name: "../escape", content: "bad" }]),
    buildZip([{ name: "/absolute", content: "bad" }]),
    buildZip([{ name: "demo-skill/link", content: "target", mode: 0o120777 }]),
    buildZip([{ name: "demo-skill/device", content: "bad", mode: 0o020600 }]),
  ])("拒绝路径越界和特殊文件", async (bytes) => {
    const paths = await fixture(bytes);
    await expect(validateAndExtractSkillZip(paths.zip, paths.output)).rejects.toMatchObject({
      code: ErrorCode.packageInvalid,
    });
  });

  it("拒绝重复路径、多个根目录和超限展开文件", async () => {
    const invalidPackages = [
      buildZip([
        { name: "demo-skill/SKILL.md", content: "one" },
        { name: "demo-skill/SKILL.md", content: "two" },
      ]),
      buildZip([
        { name: "demo-skill/SKILL.md", content: "one" },
        { name: "other-root/file.md", content: "two" },
      ]),
      buildZip([
        { name: "demo-skill/SKILL.md", content: new Uint8Array(10 * 1024 * 1024 + 1) },
      ]),
    ];
    for (const bytes of invalidPackages) {
      const paths = await fixture(bytes);
      await expect(validateAndExtractSkillZip(paths.zip, paths.output)).rejects.toMatchObject({
        code: ErrorCode.packageInvalid,
      });
    }
  });

  it.each([
    "name: demo-skill\ndescription: duplicate\ndescription: duplicate-again",
    "name: demo-skill\ndescription: 7",
    `name: demo-skill\ndescription: ${"x".repeat(1025)}`,
    "name: demo-skill\ndescription: valid\ncompatibility: [bad]",
    "name: demo-skill\ndescription: valid\nmetadata: {author: 7}",
    "name: demo-skill\ndescription: valid\nallowed-tools: [Read]",
  ])("拒绝不兼容服务端契约的 frontmatter：%s", async (frontmatter) => {
    const paths = await fixture(buildZip([{
      name: "demo-skill/SKILL.md",
      content: `---\n${frontmatter}\n---\n`,
    }]));
    await expect(validateAndExtractSkillZip(paths.zip, paths.output)).rejects.toMatchObject({
      code: ErrorCode.packageInvalid,
    });
  });

  it("拒绝 SKILL.md name 与根目录不一致", async () => {
    const paths = await fixture(buildZip([{
      name: "demo-skill/SKILL.md",
      content: "---\nname: another-skill\ndescription: mismatch\n---\n",
    }]));
    await expect(validateAndExtractSkillZip(paths.zip, paths.output)).rejects.toMatchObject({
      code: ErrorCode.packageInvalid,
    });
  });
});
