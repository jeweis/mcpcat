import { spawnSync } from "node:child_process";

function parseVersion(value, label) {
  const match = /^(?:v)?(\d+)\.(\d+)\.(\d+)/.exec(value.trim());
  if (!match) {
    throw new Error(`Unable to parse ${label} version: ${value}`);
  }
  return match.slice(1).map(Number);
}

function atLeast(actual, minimum) {
  for (let index = 0; index < minimum.length; index += 1) {
    if (actual[index] > minimum[index]) return true;
    if (actual[index] < minimum[index]) return false;
  }
  return true;
}

function commandVersion(command) {
  const result = spawnSync(command, ["--version"], { encoding: "utf8" });
  if (result.status !== 0) {
    throw new Error(`${command} --version failed: ${result.stderr.trim()}`);
  }
  return result.stdout.trim();
}

const versions = {
  node: process.version,
  npm: commandVersion("npm"),
  pnpm: commandVersion("pnpm"),
};
const minimums = {
  node: [24, 0, 0],
  npm: [11, 5, 1],
  pnpm: [11, 19, 0],
};

for (const [tool, minimum] of Object.entries(minimums)) {
  const actual = parseVersion(versions[tool], tool);
  if (!atLeast(actual, minimum)) {
    throw new Error(
      `${tool} ${versions[tool]} is too old; require at least ${minimum.join(".")}`,
    );
  }
}

process.stdout.write(`${JSON.stringify(versions)}\n`);
