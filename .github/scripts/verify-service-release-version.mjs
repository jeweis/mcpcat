import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const read = (path) => readFileSync(resolve(root, path), "utf8");
const capture = (content, pattern, label) => {
  const value = pattern.exec(content)?.[1];
  if (value === undefined) throw new Error(`Cannot read ${label}`);
  return value;
};

const appVersion = capture(
  read("app/version.py"),
  /APP_VERSION\s*=\s*"([^"]+)"/,
  "app/version.py version",
);
const packageVersion = capture(
  read("pyproject.toml"),
  /^version\s*=\s*"([^"]+)"/m,
  "pyproject.toml version",
);
const frontendVersion = JSON.parse(read("static/version.json")).version;

if (appVersion !== packageVersion || appVersion !== frontendVersion) {
  throw new Error(
    `Product version drift: app=${appVersion}, package=${packageVersion}, frontend=${frontendVersion}`,
  );
}

const tag = process.env.MCPCAT_RELEASE_TAG ?? "";
if (tag !== "" && tag !== `v${appVersion}`) {
  throw new Error(`Docker release tag ${tag} must exactly match v${appVersion}`);
}

process.stdout.write(
  `${JSON.stringify({ serviceVersion: appVersion, frontendVersion, tag })}\n`,
);
