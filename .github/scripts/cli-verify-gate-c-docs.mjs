import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const fail = (message) => { throw new Error(`Gate C docs: ${message}`); };

function requireText(content, values, file) {
  for (const value of values) if (!content.includes(value)) fail(`${file} missing ${value}`);
}

function capture(content, pattern, label) {
  const value = pattern.exec(content)?.[1];
  if (value === undefined) fail(`cannot read ${label}`);
  return value;
}

const readme = read("cli/README.md");
requireText(readme, [
  "## 安装", "## 连接远程 mcpcat 与 Profile", "## Keychain 与非交互环境",
  "## Agent 与安装目录", "## mcporter 与 MCP Skill", "## CI 与发布边界",
  "## 排障", "MCPCAT_URL", "MCPCAT_API_KEY", "mcpcat doctor",
], "README");

const matrix = JSON.parse(read("cli/docs/compatibility-matrix.json"));
if (matrix.schema_version !== 1 || !Array.isArray(matrix.scenarios)) fail("invalid matrix schema");
const cliSource = read("cli/src/version.ts");
const distribution = read("app/services/skill_distribution_service.py");
const config = read("app/core/config.py");
const generator = read("app/services/mcp_skill_generator.py");
const actual = {
  service: capture(config, /app_version:\s*str\s*=\s*"([^"]+)"/, "service version"),
  api: capture(distribution, /REGISTRY_API_VERSION\s*=\s*"([^"]+)"/, "API"),
  schema: capture(distribution, /REGISTRY_SCHEMA_VERSION\s*=\s*"([^"]+)"/, "schema"),
  minimum: capture(distribution, /MIN_CLI_VERSION\s*=\s*"([^"]+)"/, "minimum CLI"),
  recommended: capture(distribution, /RECOMMENDED_CLI_VERSION\s*=\s*"([^"]+)"/, "recommended CLI"),
  cli: capture(cliSource, /CLI_VERSION\s*=\s*"([^"]+)"/, "CLI"),
  cliApi: capture(cliSource, /SUPPORTED_API_VERSION\s*=\s*"([^"]+)"/, "CLI API"),
  cliSchemaMajor: Number(capture(cliSource, /SUPPORTED_REGISTRY_SCHEMA_MAJOR\s*=\s*(\d+)/, "CLI schema major")),
  generator: capture(generator, /GENERATOR_VERSION\s*=\s*"([^"]+)"/, "generator"),
  mcporter: capture(generator, /MCPORTER_VERSION\s*=\s*"([^"]+)"/, "mcporter"),
  node: capture(generator, /NODE_COMPATIBILITY\s*=\s*"([^"]+)"/, "Node range"),
};
const expected = {
  service: matrix.versions.service_application.current,
  api: matrix.versions.registry_api.server,
  schema: matrix.versions.registry_schema.server,
  minimum: matrix.versions.cli.server_minimum,
  recommended: matrix.versions.cli.server_recommended,
  cli: matrix.versions.cli.current,
  cliApi: matrix.versions.registry_api.cli_supported,
  cliSchemaMajor: matrix.versions.registry_schema.cli_supported_major,
  generator: matrix.versions.generated_mcp_skill.generator,
  mcporter: matrix.versions.generated_mcp_skill.mcporter,
  node: matrix.versions.generated_mcp_skill.node,
};
for (const key of Object.keys(expected)) {
  if (actual[key] !== expected[key]) fail(`${key} drift: source=${actual[key]} matrix=${expected[key]}`);
}

const ids = new Set(matrix.scenarios.map((scenario) => scenario.id));
for (const id of ["current-baseline", "service-version-independent", "compatible-schema-minor",
  "api-major-mismatch", "schema-major-mismatch", "cli-below-server-minimum",
  "skill-version-independent"]) if (!ids.has(id)) fail(`missing scenario ${id}`);

const matrixDoc = read("cli/docs/compatibility-matrix.md");
requireText(matrixDoc, ["X-Mcpcat-CLI-Version", "HTTP 426", "16.6"], "matrix Markdown");
const drill = read("cli/docs/gate-c-release-rollback-drill.md");
requireText(drill, ["未完成（仅完成本地 dry-run）", "不产生外部状态",
  "正式发布演练模板", "正式回滚演练模板", "16.6 不得勾选"], "drill");

const secrets = [/npm_[A-Za-z0-9]{36}/, /gh(?:p|o|u|s|r)_[A-Za-z0-9]{36,}/,
  /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/];
for (const [file, content] of [["README", readme], ["matrix", matrixDoc], ["drill", drill]]) {
  if (secrets.some((pattern) => pattern.test(content))) fail(`${file} contains a secret pattern`);
}

process.stdout.write(`${JSON.stringify({ matrix: actual, scenarios: matrix.scenarios.length, gateCDrillComplete: false })}\n`);
