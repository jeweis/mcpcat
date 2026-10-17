import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

// 静态发布门禁，不安装依赖、不连接服务、不修改版本或历史内容。
export function verifyUpgradeCompatibility(directory = root) {
  const read = (path) => readFileSync(resolve(directory, path), "utf8");
  const fail = (message) => { throw new Error(`Upgrade compatibility: ${message}`); };
  const capture = (content, pattern, label) => {
    const value = pattern.exec(content)?.[1];
    if (value === undefined) fail(`cannot read ${label}`);
    return value;
  };
  const same = (actual, expected, label) => {
    if (JSON.stringify(actual) !== JSON.stringify(expected)) {
      fail(`${label} drift: source=${JSON.stringify(actual)} matrix=${JSON.stringify(expected)}`);
    }
  };
  const matrix = JSON.parse(read("cli/docs/compatibility-matrix.json")).versions;
  same(capture(read("pyproject.toml"), /^requires-python = "([^"]+)"/m, "Python floor"), matrix.python.requires, "Python floor");
  same(capture(read("Dockerfile"), /^FROM python:([\d.]+)-/m, "container Python"), matrix.python.container, "container Python");
  const lock = read("uv.lock").split(/^\[\[package\]\]\s*$/m);
  for (const [name, version] of Object.entries(matrix.runtime)) {
    const packages = lock.filter((part) => part.includes(`name = "${name}"\n`));
    same(packages.length, 1, `${name} lock entries`);
    same(capture(packages[0], /^version = "([^"]+)"/m, name), version, name);
    if (name === "mcp") continue; // SDK 是锁文件约束，不伪造顶层精确依赖。
    const requirement = `${name}${name === "fastmcp" ? "[apps,code-mode]" : ""}==${version}`;
    for (const file of ["requirements.txt", "pyproject.toml"]) {
      if (!read(file).includes(requirement)) fail(`${file} missing ${requirement}`);
    }
  }
  const extension = read("app/services/skills_mcp_extension.py");
  same(capture(extension, /MODERN_PROTOCOL = "([^"]+)"/, "protocol"), matrix.mcp_protocol.modern, "protocol");
  same(capture(extension, /SKILLS_EXTENSION = "([^"]+)"/, "extension"), matrix.mcp_protocol.skills_extension, "extension");
  if (matrix.mcp_protocol.directory_read !== false) fail("directoryRead has not been implemented");
  const renderer = read("app/services/app_prefab_renderer.py");
  same(capture(renderer, /PREFAB_VERSION = "([^"]+)"/, "Prefab renderer"), matrix.runtime["prefab-ui"], "Prefab renderer");
  const templates = read("app/services/skill_template_renderer.py");
  for (const profile of ["local", "remote"]) {
    same(capture(templates, new RegExp(`${profile.toUpperCase()}_TEMPLATE_VERSION = "([^"]+)"`), profile), matrix.content_templates[profile], `${profile} template`);
  }
  const html = read("app/services/app_html_renderer.py");
  same(capture(html, /else "(html-assembly-\d+)"/, "HTML draft"), matrix.apps_html.new_draft, "HTML draft");
  same([...new Set(html.match(/html-assembly-\d+/g))].sort(), matrix.apps_html.retained, "retained HTML renderers");
  const revisions = readdirSync(resolve(directory, "alembic/versions"))
    .filter((name) => name.endsWith(".py") && name !== "__init__.py")
    .map((name) => {
      const content = read(`alembic/versions/${name}`);
      return {
        revision: capture(content, /^revision(?:\s*:\s*str)?\s*=\s*"([^"]+)"/m, name),
        parent: capture(content, /^down_revision[^=]*=\s*(None|"[^"]+")/m, name).replaceAll('"', ""),
      };
    });
  const parents = new Set(revisions.map((item) => item.parent));
  same(revisions.filter((item) => !parents.has(item.revision)).map((item) => item.revision), [matrix.database.head], "Alembic head");
  if (!revisions.some((item) => item.revision === matrix.database.baseline)) fail("missing baseline migration");
  if (!matrix.database.downgrade_requires_matching_snapshot) fail("rollback requires a matched full snapshot");
  return { runtime: matrix.runtime, protocol: matrix.mcp_protocol.modern, database: matrix.database.head };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.stdout.write(`${JSON.stringify(verifyUpgradeCompatibility())}\n`);
}
