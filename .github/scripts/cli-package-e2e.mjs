import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../..");
const cliRoot = resolve(repositoryRoot, "cli");
const temporaryRoot = mkdtempSync(join(tmpdir(), "mcpcat-cli-e2e-"));
const npmCache = resolve(tmpdir(), "mcpcat-cli-npm-cache");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: cliRoot,
    encoding: "utf8",
    env: { ...process.env, npm_config_cache: npmCache },
    timeout: 180_000,
    ...options,
  });
  if (result.error) {
    throw new Error(`${command} ${args.join(" ")} failed: ${result.error.message}`);
  }
  if (result.status !== 0) {
    throw new Error(
      `${command} ${args.join(" ")} failed:\n${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

try {
  const packageReport = JSON.parse(
    run("npm", [
      "pack",
      "--json",
      "--ignore-scripts",
      "--pack-destination",
      temporaryRoot,
    ]),
  );
  const tarballPath = join(temporaryRoot, packageReport[0].filename);
  const installRoot = join(temporaryRoot, "installed");
  run("npm", [
    "install",
    "--prefix",
    installRoot,
    "--ignore-scripts",
    "--no-audit",
    "--no-fund",
    tarballPath,
  ]);

  const packageJson = JSON.parse(readFileSync(resolve(cliRoot, "package.json"), "utf8"));
  const executable =
    process.platform === "win32"
      ? join(installRoot, "node_modules", ".bin", "mcpcat.cmd")
      : join(installRoot, "node_modules", ".bin", "mcpcat");
  const output = run(executable, ["--version", "--json"], { cwd: temporaryRoot });
  const response = JSON.parse(output);
  const reportedVersion = response?.data?.version;
  if (response.ok !== true || reportedVersion !== packageJson.version) {
    throw new Error(
      `packed CLI reported ${reportedVersion}; expected ${packageJson.version}`,
    );
  }
  process.stdout.write(
    `${JSON.stringify({ executable: "mcpcat", version: reportedVersion, packed: true })}\n`,
  );
} finally {
  rmSync(temporaryRoot, { force: true, recursive: true });
}
