import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { resolveDistTag, validateRelease } from "./cli-resolve-release.mjs";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../..");
const ci = readFileSync(resolve(repositoryRoot, ".github/workflows/cli-ci.yml"), "utf8");
const publish = readFileSync(
  resolve(repositoryRoot, ".github/workflows/cli-publish.yml"),
  "utf8",
);

function requireMatch(content, pattern, message) {
  if (!pattern.test(content)) {
    throw new Error(`CLI release policy verification failed: ${message}`);
  }
}

requireMatch(ci, /pull_request:/, "CI must run for pull requests");
requireMatch(ci, /push:\n\s+branches:/, "CI must run for normal branch pushes");
for (const command of ["pnpm lint", "pnpm test", "pnpm build", "npm pack --dry-run"]) {
  requireMatch(ci, new RegExp(command.replaceAll(" ", "\\s+")), `CI is missing ${command}`);
}
if (/npm\s+(?:publish|dist-tag)/.test(ci) || /id-token:\s*write/.test(ci)) {
  throw new Error("CLI release policy verification failed: branch/PR CI can publish");
}

requireMatch(publish, /release:\n\s+types:\s*\[published\]/, "published Release trigger missing");
requireMatch(publish, /push:\n\s+tags:/, "version tag trigger missing");
requireMatch(publish, /v\*\.\*\.\*/, "unified version tag filter missing");
requireMatch(publish, /runs-on:\s*ubuntu-latest/, "publish must use a GitHub-hosted runner");
requireMatch(publish, /environment:\s*npm/, "protected npm environment reference missing");
requireMatch(publish, /id-token:\s*write/, "OIDC id-token permission missing");
requireMatch(publish, /node-version:\s*24/, "publish must use Node.js 24");
requireMatch(publish, /npm publish --access public --provenance --tag/, "provenance publish command missing");
if (/npm\s+dist-tag/.test(publish)) {
  throw new Error(
    "CLI release policy verification failed: dist-tags must move only as part of successful npm publish",
  );
}
if (/NPM_TOKEN|NODE_AUTH_TOKEN|secrets\./.test(publish)) {
  throw new Error(
    "CLI release policy verification failed: publish workflow must use OIDC without a write token",
  );
}

const cases = new Map([
  ["1.2.3", "latest"],
  ["1.2.3-beta.1", "beta"],
  ["1.2.3-next.4", "next"],
]);
for (const [version, expected] of cases) {
  const actual = resolveDistTag(version);
  if (actual !== expected) {
    throw new Error(`CLI release policy verification failed: ${version} mapped to ${actual}`);
  }
  validateRelease({
    eventName: "push",
    prerelease: "",
    tag: `v${version}`,
    version,
  });
}

validateRelease({
  eventName: "release",
  prerelease: "false",
  tag: "v1.2.3",
  version: "1.2.3",
});
validateRelease({
  eventName: "release",
  prerelease: "true",
  tag: "v1.2.3-beta.1",
  version: "1.2.3-beta.1",
});

for (const unsupported of ["1.2.3-alpha.1", "1.2.3-rc.1"]) {
  try {
    resolveDistTag(unsupported);
    throw new Error(`unsupported channel unexpectedly accepted: ${unsupported}`);
  } catch (error) {
    if (String(error).includes("unexpectedly accepted")) {
      throw error;
    }
  }
}

process.stdout.write(
  `${JSON.stringify({ branchPushPublishes: false, channels: Object.fromEntries(cases) })}\n`,
);
