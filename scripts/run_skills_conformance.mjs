/** Run unmodified official scenarios with the required client extension opt-in. */
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import { readFileSync } from "node:fs";

const [source, serverUrl] = process.argv.slice(2);
if (!source || !serverUrl)
  throw new Error("Expected conformance source and fixture URL");
const load = (file) => import(pathToFileURL(resolve(source, file)).href);
const { getClientScenario } = await load("src/scenarios/index.ts");
const { connectFor } = await load("src/connection/index.ts");
const { resetWireValidation, wireSchemaChecks } = await load(
  "src/validation/wire-schema.ts",
);
const pins = JSON.parse(
  readFileSync(
    new URL("./fixtures/skills-conformance.json", import.meta.url),
    "utf8",
  ),
);
const protocol = pins.protocol;
const extension = pins.extension;
const reports = [];
for (const name of pins.conformance.scenarios) {
  const scenario = getClientScenario(name);
  if (!scenario) throw new Error(`Official scenario missing: ${name}`);
  resetWireValidation();
  const checks = await scenario.run({
    serverUrl,
    specVersion: protocol,
    connect: (options = {}) =>
      connectFor(protocol)(serverUrl, {
        ...options,
        capabilities: {
          ...options.capabilities,
          extensions: { ...options.capabilities?.extensions, [extension]: {} },
        },
      }),
  });
  checks.push(...wireSchemaChecks(protocol));
  reports.push({ name, checks });
}
process.stdout.write(JSON.stringify(reports));
if (
  reports.some(({ checks }) =>
    checks.some(({ status }) => status === "FAILURE"),
  )
)
  process.exitCode = 1;
