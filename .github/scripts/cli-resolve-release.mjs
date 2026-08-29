import { appendFileSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(scriptDirectory, "../..");
const packagePath = resolve(repositoryRoot, "cli/package.json");

const VERSION_PATTERN = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$/;

export function resolveDistTag(version) {
  const match = VERSION_PATTERN.exec(version);
  if (!match) {
    throw new Error(`CLI version is not valid SemVer: ${version}`);
  }

  const prerelease = match[4];
  if (!prerelease) {
    return "latest";
  }
  if (/^beta(?:\.|$)/.test(prerelease)) {
    return "beta";
  }
  if (/^next(?:\.|$)/.test(prerelease)) {
    return "next";
  }
  throw new Error(
    `Unsupported prerelease channel in ${version}; use -beta.N or -next.N`,
  );
}

export function validateRelease({ eventName, prerelease, tag, version }) {
  const expectedTag = `v${version}`;
  if (tag !== expectedTag) {
    throw new Error(`Release tag ${tag} must exactly match ${expectedTag}`);
  }
  if (eventName !== "push" && eventName !== "release") {
    throw new Error(`Unsupported release event: ${eventName}`);
  }

  const distTag = resolveDistTag(version);
  if (eventName === "release") {
    if (prerelease !== "true" && prerelease !== "false") {
      throw new Error(`Invalid GitHub Release prerelease flag: ${prerelease}`);
    }
    const markedPrerelease = prerelease === "true";
    const expectedPrerelease = distTag !== "latest";
    if (markedPrerelease !== expectedPrerelease) {
      throw new Error(
        `GitHub Release prerelease=${prerelease} does not match npm dist-tag ${distTag}`,
      );
    }
  }

  return { distTag, expectedTag };
}

function writeOutput(name, value) {
  const outputPath = process.env.GITHUB_OUTPUT;
  if (outputPath) {
    appendFileSync(outputPath, `${name}=${value}\n`, "utf8");
  }
}

function main() {
  const packageJson = JSON.parse(readFileSync(packagePath, "utf8"));
  const version = packageJson.version;
  const eventName = process.env.MCPCAT_RELEASE_EVENT ?? "";
  const tag = process.env.MCPCAT_RELEASE_TAG ?? "";
  const prerelease = process.env.MCPCAT_RELEASE_PRERELEASE ?? "";
  const { distTag } = validateRelease({ eventName, prerelease, tag, version });

  writeOutput("package_name", packageJson.name);
  writeOutput("version", version);
  writeOutput("dist_tag", distTag);
  process.stdout.write(
    `${JSON.stringify({ package: packageJson.name, version, distTag, tag })}\n`,
  );
}

const isEntryPoint =
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (isEntryPoint) {
  main();
}
