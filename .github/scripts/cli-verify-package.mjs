import { spawnSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../..");
const cliRoot = resolve(repositoryRoot, "cli");
const expectedRepository = "https://github.com/jeweis/mcpcat.git";
const npmCache = resolve(tmpdir(), "mcpcat-cli-npm-cache");

function fail(message) {
  throw new Error(`CLI package verification failed: ${message}`);
}

function loadPackageManifest() {
  const manifest = JSON.parse(readFileSync(resolve(cliRoot, "package.json"), "utf8"));
  if (manifest.license !== "MIT") {
    fail("package.json license must be MIT");
  }
  if (manifest.repository?.url !== expectedRepository) {
    fail(`repository.url must be ${expectedRepository}`);
  }
  if (manifest.repository?.directory !== "cli") {
    fail("repository.directory must be cli");
  }
  if (manifest.bin?.mcpcat !== "dist/bin.js") {
    fail("the published executable must remain mcpcat -> dist/bin.js");
  }
  return manifest;
}

function verifyDocumentation() {
  const readme = readFileSync(resolve(cliRoot, "README.md"), "utf8");
  const license = readFileSync(resolve(cliRoot, "LICENSE"), "utf8");
  if (readme.trim().length < 200) {
    fail("README.md is missing or too short for a public package");
  }
  if (!license.startsWith("MIT License\n")) {
    fail("the package-local LICENSE is not the MIT license");
  }
}

function inspectPack() {
  const result = spawnSync(
    "npm",
    ["pack", "--dry-run", "--json", "--ignore-scripts"],
    {
      cwd: cliRoot,
      encoding: "utf8",
      env: { ...process.env, npm_config_cache: npmCache },
    },
  );
  if (result.status !== 0) {
    fail(result.stderr.trim() || "npm pack --dry-run failed");
  }
  const report = JSON.parse(result.stdout);
  if (!Array.isArray(report) || report.length !== 1) {
    fail("unexpected npm pack JSON report");
  }
  return report[0].files.map((entry) => entry.path);
}

function verifyFileList(files) {
  const required = ["LICENSE", "README.md", "dist/bin.js", "package.json"];
  for (const path of required) {
    if (!files.includes(path)) {
      fail(`packed tarball is missing ${path}`);
    }
  }

  const forbidden = files.filter(
    (path) =>
      path.startsWith("src/") ||
      path.startsWith("test/") ||
      path.startsWith("node_modules/") ||
      path === ".env" ||
      path.endsWith(".log"),
  );
  if (forbidden.length > 0) {
    fail(`packed tarball contains development or secret-prone files: ${forbidden.join(", ")}`);
  }
}

function scanPackedTextFiles(files) {
  const secretPatterns = [
    ["private key", /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
    ["npm access token", /npm_[A-Za-z0-9]{36}/],
    ["GitHub token", /gh(?:p|o|u|s|r)_[A-Za-z0-9]{36,}/],
    ["AWS access key", /AKIA[0-9A-Z]{16}/],
    ["Slack token", /xox[baprs]-[0-9A-Za-z-]{20,}/],
    ["credential-bearing URL", /https?:\/\/[^\s/:]+:[^\s/@]+@[^\s/]+/],
  ];
  const textExtensions = new Set(["", ".cjs", ".js", ".json", ".md", ".mjs", ".ts"]);

  for (const relativePath of files) {
    if (!textExtensions.has(extname(relativePath))) {
      continue;
    }
    const absolutePath = resolve(cliRoot, relativePath);
    if (!absolutePath.startsWith(`${cliRoot}${sep}`) || statSync(absolutePath).size > 5_000_000) {
      fail(`unsafe or unexpectedly large packed path: ${relativePath}`);
    }
    const content = readFileSync(absolutePath, "utf8");
    for (const [label, pattern] of secretPatterns) {
      if (pattern.test(content)) {
        fail(`${label} detected in ${relativePath}`);
      }
    }
  }
}

const manifest = loadPackageManifest();
verifyDocumentation();
const files = inspectPack();
verifyFileList(files);
scanPackedTextFiles(files);
process.stdout.write(
  `${JSON.stringify({ package: manifest.name, version: manifest.version, files: files.length })}\n`,
);
