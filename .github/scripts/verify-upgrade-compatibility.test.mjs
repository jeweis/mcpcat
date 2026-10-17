import assert from "node:assert/strict";
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { verifyUpgradeCompatibility } from "./verify-upgrade-compatibility.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const files = ["cli/docs/compatibility-matrix.json", "uv.lock", "requirements.txt",
  "pyproject.toml", "Dockerfile", "app/services/skills_mcp_extension.py",
  "app/services/app_prefab_renderer.py", "app/services/skill_template_renderer.py",
  "app/services/app_html_renderer.py", "alembic/versions"];

function fixture(t) {
  const directory = mkdtempSync(resolve(tmpdir(), "mcpcat-version-gate-"));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  for (const file of files) cpSync(resolve(root, file), resolve(directory, file), { recursive: true });
  return directory;
}

test("actual runtime/content/schema dimensions agree without forcing product versions equal", () => {
  assert.equal(verifyUpgradeCompatibility().database, "0005_app_render_review");
});

for (const [label, file, from, to] of [
  ["Python floor", "pyproject.toml", 'requires-python = ">=3.10"', 'requires-python = ">=3.11"'],
  ["container Python", "Dockerfile", 'FROM python:3.12-', 'FROM python:3.13-'],
  ["SDK lock", "uv.lock", 'name = "mcp"\nversion = "2.3.0"', 'name = "mcp"\nversion = "2.3.1"'],
  ["runtime requirement", "requirements.txt", "fastmcp[apps,code-mode]==4.1.0", "fastmcp[apps,code-mode]==4.2.0"],
  ["project dependency", "pyproject.toml", "pydantic-monty==1.1.0", "pydantic-monty==1.2.0"],
  ["protocol", "app/services/skills_mcp_extension.py", 'MODERN_PROTOCOL = "2026-07-28"', 'MODERN_PROTOCOL = "2099-01-01"'],
  ["renderer", "app/services/app_prefab_renderer.py", 'PREFAB_VERSION = "0.20.2"', 'PREFAB_VERSION = "0.21.0"'],
  ["template", "app/services/skill_template_renderer.py", 'REMOTE_TEMPLATE_VERSION = "1.0.0"', 'REMOTE_TEMPLATE_VERSION = "2.0.0"'],
  ["HTML default", "app/services/app_html_renderer.py", 'else "html-assembly-5"', 'else "html-assembly-6"'],
  ["lost historical HTML", "app/services/app_html_renderer.py", '"html-assembly-2",', '"html-assembly-9",'],
  ["unimplemented capability", "cli/docs/compatibility-matrix.json", '"directory_read": false', '"directory_read": true'],
  ["migration head", "alembic/versions/0005_app_render_review.py", 'revision = "0005_app_render_review"', 'revision = "0006_unreviewed"'],
  ["unsafe rollback", "cli/docs/compatibility-matrix.json", '"downgrade_requires_matching_snapshot": true', '"downgrade_requires_matching_snapshot": false'],
]) {
  test(`rejects ${label} drift`, (t) => {
    const directory = fixture(t);
    const path = resolve(directory, file);
    const content = readFileSync(path, "utf8");
    assert.ok(content.includes(from), "fixture mutation must actually apply");
    writeFileSync(path, content.replace(from, to));
    assert.throws(() => verifyUpgradeCompatibility(directory), /Upgrade compatibility:/);
  });
}
